import json
from django.contrib.auth import logout
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_POST
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from .models import ServiceOrder, OSItem, OSDestination, ItemDistribution, RouteStop
from django.contrib import messages
from accounts.models import CustomUser
from .forms import ServiceOrderForm
from django.utils import timezone
from logistics.models import MotoboyProfile
from django.core.cache import cache
from django.db.models import Q
from django.db import transaction

@login_required
def root_redirect(request):
    user = request.user
    
    # PRIMEIRO checa se é Admin ou Superuser
    if user.type == 'ADMIN' or user.is_superuser:
        return redirect('admin_dashboard') # Vai para o novo painel
    
    elif user.type == 'COMPANY':
        return redirect('company_dashboard')
    
    elif user.type == 'MOTOBOY':
        return redirect('motoboy_tasks')
    
    elif user.type == 'DISPATCHER':
        return redirect('dispatch_dashboard')
        
    return redirect('login')

@login_required
@require_POST
def cancel_os_view(request, os_id):
    # Busca a OS no banco
    os = get_object_or_404(ServiceOrder, id=os_id)
    
    # Validação de Segurança: Quem pode cancelar?
    # 1. A empresa dona da OS
    # 2. O Despachante ou Admin
    if request.user.type == 'COMPANY' and os.client != request.user:
        return JsonResponse({'status': 'error', 'message': 'Você não tem permissão para cancelar esta OS.'}, status=403)
        
    # Regra de negócio: Só cancela se não estiver com o motoboy em rota avançada (opcional, mas recomendado)
    if os.status in ['COLETADO', 'ENTREGUE']:
        return JsonResponse({'status': 'error', 'message': 'Esta OS já está em rota ou foi entregue e não pode ser cancelada.'}, status=400)
        
    # Efetua o cancelamento
    os.status = 'CANCELADO'
    os.motoboy = None # Retira do motoboy, se houver
    os.save()
    
    messages.success(request, f'A OS {os.os_number} foi cancelada com sucesso.')
    return JsonResponse({'status': 'success'})

@login_required
def admin_dashboard_view(request):
    # Garante que só admin entra aqui
    if not (request.user.type == 'ADMIN' or request.user.is_superuser):
        return redirect('root')

    context = {
        'total_users': CustomUser.objects.count(),
        'total_os': ServiceOrder.objects.count(),
        'os_pending': ServiceOrder.objects.filter(status='PENDENTE').count(),
        'os_completed': ServiceOrder.objects.filter(status='ENTREGUE').count(),
        # Trazemos as últimas 5 OS para visualização rápida
        'recent_orders': ServiceOrder.objects.all().order_by('-created_at')[:5]
    }
    return render(request, 'orders/admin_dashboard.html', context)

@login_required
def os_create_view(request):
    if request.user.type != 'COMPANY':
        return redirect('root')

    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            
            with transaction.atomic():
                # 1. SALVA A CAPA DA OS E COLETA (Agora com os campos novos)
                os = ServiceOrder.objects.create(
                    client=request.user,
                    requester_name=data.get('requester_name', ''),
                    requester_phone=data.get('requester_phone', ''),
                    company_cnpj=data.get('company_cnpj', ''),       # NOVO
                    company_email=data.get('company_email', ''),     # NOVO
                    delivery_type=data.get('delivery_type', ''),     # NOVO
                    vehicle_type=data.get('vehicle_type', 'MOTO'),
                    priority=data.get('priority', 'NORMAL'),
                    payment_method=data.get('payment_method', 'FATURADO'),
                    operational_notes=data.get('general_notes', ''), # Observações Gerais
                    
                    origin_name=data.get('origin_name', ''),
                    origin_street=data.get('origin_street', ''),
                    origin_number=data.get('origin_number', ''),
                    origin_district=data.get('origin_district', ''),
                    origin_city=data.get('origin_city', ''),
                    origin_state=data.get('origin_state', ''),       # NOVO
                    origin_zip_code=data.get('origin_zip_code', ''),
                    is_multiple_delivery=len(data.get('destinations', [])) > 1
                )

                # 2. SALVA OS ITENS (Agora com Peso, Dimensões, Notas e Tipo)
                items_dict = {} 
                for item_data in data.get('items', []):
                    # Como o peso pode vir vazio da tela, tratamos para não dar erro no banco decimal
                    peso_str = item_data.get('weight', '')
                    peso_val = float(peso_str) if peso_str else None
                    
                    novo_item = OSItem.objects.create(
                        order=os,
                        description=item_data['description'],
                        total_quantity=item_data['quantity'],
                        item_type=item_data.get('type', ''),         # NOVO
                        weight=peso_val,                             # NOVO
                        dimensions=item_data.get('dimensions', ''),  # NOVO
                        item_notes=item_data.get('notes', '')        # NOVO
                    )
                    items_dict[item_data['id']] = novo_item

                # 3. SALVA OS DESTINOS (Agora com Complemento, Referência e UF)
                dest_dict = {}
                for dest_data in data.get('destinations', []):
                    novo_dest = OSDestination.objects.create(
                        order=os,
                        destination_name=dest_data['name'],
                        destination_phone=dest_data['phone'],
                        destination_street=dest_data['street'],
                        destination_number=dest_data['number'],
                        destination_complement=dest_data.get('complement', ''), # NOVO
                        destination_district=dest_data['district'],
                        destination_city=dest_data['city'],
                        destination_state=dest_data.get('state', ''),           # NOVO
                        destination_zip_code=dest_data.get('cep', ''),          # NOVO
                        destination_reference=dest_data.get('reference', '')    # NOVO
                    )
                    dest_dict[dest_data['id']] = novo_dest

                # 4. SALVA A DISTRIBUIÇÃO
                for dist_data in data.get('distributions', []):
                    ItemDistribution.objects.create(
                        item=items_dict[dist_data['item_id']],
                        destination=dest_dict[dist_data['dest_id']],
                        quantity_allocated=dist_data['quantity']
                    )

                # ========================================================
                # 5. NOVO: GERA OS PONTOS DE PARADA (ROTEIRIZAÇÃO BASE)
                # ========================================================
                from orders.models import RouteStop # Importe no topo se preferir
                
                # Cria a Parada de Coleta (Sempre a Sequência 1 por padrão)
                RouteStop.objects.create(
                    service_order=os,
                    stop_type='COLETA',
                    sequence=1
                )
                
                # Cria as Paradas de Entrega para cada destino (Sequência 2, 3...)
                seq = 2
                for dest_obj in dest_dict.values():
                    RouteStop.objects.create(
                        service_order=os,
                        stop_type='ENTREGA',
                        destination=dest_obj,
                        sequence=seq
                    )

            return JsonResponse({'status': 'success', 'os_number': os.os_number})
            
        except Exception as e:
            # Imprime o erro no terminal do Django pra ajudar a debugar se algo falhar
            print("ERRO AO SALVAR OS:", str(e)) 
            return JsonResponse({'status': 'error', 'message': str(e)}, status=400)

    return render(request, 'orders/os_create.html')

@login_required
def dispatch_dashboard_view(request):
    if request.user.type != 'DISPATCHER' and not request.user.is_superuser:
        return redirect('root')

    # 1. Coluna 2: Busca OS Pendentes (Aguardando Atribuição)
    pending_orders = ServiceOrder.objects.filter(status='PENDENTE').order_by('-priority', 'created_at')
    
    # 2. Busca Motoboys e prepara os dados
    from logistics.models import MotoboyProfile
    motoboys = MotoboyProfile.objects.all()
    
    motoboy_data = []
    for mb in motoboys:
        # VERIFICAÇÃO DE ONLINE RESTAURADA: Checa no cache se o usuário foi visto recentemente
        last_seen = cache.get(f'seen_{mb.user.id}')
        
        # O motoboy só aparece como "Livre/Online" se tiver com perfil liberado (is_available) 
        # E tiver acessado o sistema nos últimos minutos (last_seen)
        is_online = mb.is_available and bool(last_seen)

        # Pega as OS ativas especificamente deste motoboy
        ativas = mb.route_stops.filter(is_completed=False).order_by('sequence')
        
        motoboy_data.append({
            'profile': mb,
            'is_online': is_online,
            'load': ativas.count(),
            'max_load': 10, # Capacidade fictícia
            'active_stops': ativas,
        })
        
    # Ordena: Motoboys Online/Ativos primeiro na lista
    motoboy_data.sort(key=lambda x: x['is_online'], reverse=True)

    # 3. Métricas do Topo
    total_ativas = sum(mb['load'] for mb in motoboy_data)
    total_ocorrencias = ServiceOrder.objects.filter(status='OCORRENCIA').count()

    context = {
        'pending_orders': pending_orders,
        'motoboy_data': motoboy_data,
        'total_ativas': total_ativas,
        'total_ocorrencias': total_ocorrencias,
        'now': timezone.now(),
    }
    return render(request, 'orders/dispatch_panel.html', context)

@login_required
def get_route_stops(request, os_id):
    """Retorna a rota de uma OS em JSON para montar a timeline no Modal"""
    os_alvo = get_object_or_404(ServiceOrder, id=os_id)
    
    # Importante: Usa o Q para pegar paradas da OS principal e das Filhas
    from orders.models import RouteStop
    stops = RouteStop.objects.filter(
        Q(service_order=os_alvo) | Q(service_order__parent_os=os_alvo)
    ).order_by('sequence')
    
    data = []
    for stop in stops:
        # Puxa o nome e endereço originais (da OS dona daquela parada específica)
        if stop.stop_type == 'COLETA':
            location = stop.service_order.origin_name
            address = f"(OS {stop.service_order.os_number}) {stop.service_order.origin_street}, {stop.service_order.origin_number}"
        else:
            location = stop.destination.destination_name
            address = f"(OS {stop.service_order.os_number}) {stop.destination.destination_street}, {stop.destination.destination_number}"
            
        data.append({
            'id': stop.id,
            'type': stop.stop_type,
            'sequence': stop.sequence,
            'location': location,
            'address': address
        })
        
    return JsonResponse({'status': 'success', 'stops': data})

@login_required
@require_POST
def merge_os_view(request):
    """Funde duas Ordens de Serviço Visualmente (A Origem vira Filha do Destino)"""
    if request.user.type != 'DISPATCHER' and not request.user.is_superuser:
        return JsonResponse({'status': 'error', 'message': 'Sem permissão.'}, status=403)

    data = json.loads(request.body)
    source_id = data.get('source_os')
    target_id = data.get('target_os')

    if source_id == target_id:
        return JsonResponse({'status': 'error', 'message': 'Não é possível mesclar uma OS com ela mesma.'})

    source_os = get_object_or_404(ServiceOrder, id=source_id)
    target_os = get_object_or_404(ServiceOrder, id=target_id)

    if source_os.status != 'PENDENTE' or target_os.status != 'PENDENTE':
        return JsonResponse({'status': 'error', 'message': 'Apenas OS PENDENTES podem ser mescladas.'})

    with transaction.atomic():
        # 1. Torna a OS Origem "Filha" da OS Destino
        source_os.parent_os = target_os
        # Muda o status para não aparecer mais na coluna "Aguardando", mas NÃO cancela.
        source_os.status = 'AGRUPADO' 
        source_os.operational_notes += f"\n[AGRUPADA] Viajando junto com a OS {target_os.os_number}."
        source_os.save()

        # 2. Atualiza a numeração da sequência para o Modal
        last_seq = target_os.stops.count()
        for stop in source_os.stops.order_by('sequence'):
            last_seq += 1
            stop.sequence = last_seq
            stop.save()
            # Nota: NÃO mudamos o stop.service_order. As paradas continuam sendo da OS Original!

        # 3. Registra na OS Mãe
        target_os.operational_notes += f"\n[GRUPO] Levando também as entregas da OS {source_os.os_number}."
        target_os.is_multiple_delivery = True
        target_os.save()

    return JsonResponse({'status': 'success'})

@login_required
def motoboy_tasks_view(request):
    if request.user.type != 'MOTOBOY':
        return redirect('root')

    try:
        perfil = request.user.motoboy_profile
    except Exception:
        from logistics.models import MotoboyProfile
        perfil = MotoboyProfile.objects.create(
            user=request.user, vehicle_plate="Pendente",
            cnh_number=f"Pendente_{request.user.id}", category='TELE', is_available=False
        )

    cnh_invalida = not perfil.cnh_number or 'Pendente' in perfil.cnh_number
    placa_invalida = not perfil.vehicle_plate or 'Pendente' in perfil.vehicle_plate

    if cnh_invalida or placa_invalida:
        return redirect('motoboy_profile')

    # Busca APENAS as OS Principais (Mães) ou Avulsas
    ativas_qs = ServiceOrder.objects.filter(
        motoboy=perfil,
        status__in=['ACEITO', 'COLETADO'],
        parent_os__isnull=True
    ).order_by('created_at')

    ativas_data = []
    for os in ativas_qs:
        # Pega as paradas da OS principal e das filhas agrupadas nela
        stops = RouteStop.objects.filter(
            Q(service_order=os) | Q(service_order__parent_os=os)
        ).order_by('sequence')
        
        filhas = os.child_orders.all()
        
        ativas_data.append({
            'os': os,
            'stops': stops,
            'has_children': filhas.exists(),
            'child_numbers': [f.os_number for f in filhas]
        })

    entregas_concluidas_hoje = RouteStop.objects.filter(
        motoboy=perfil,
        stop_type='ENTREGA',
        is_completed=True,
        completed_at__date=timezone.now().date()
    ).count()

    historico = ServiceOrder.objects.filter(
        motoboy=perfil,
        status__in=['ENTREGUE', 'CANCELADO']
    ).order_by('-created_at')[:10]

    context = {
        'ativas_data': ativas_data,
        'historico': historico,
        'entregas_concluidas': entregas_concluidas_hoje, # Enviando para o HTML
    }
    return render(request, 'orders/motoboy_tasks.html', context)

@login_required
def motoboy_profile_view(request):
    if request.user.type != 'MOTOBOY':
        return redirect('root')

    perfil = getattr(request.user, 'motoboy_profile', None)
    
    # Identifica se é o primeiro acesso (para mudar os textos da tela)
    cnh_invalida = not perfil.cnh_number or 'Pendente' in perfil.cnh_number
    placa_invalida = not perfil.vehicle_plate or 'Pendente' in perfil.vehicle_plate
    is_first_access = cnh_invalida or placa_invalida

    if request.method == 'POST':
        # Salva os dados do perfil
        perfil.cnh_number = request.POST.get('cnh_number', perfil.cnh_number)
        perfil.vehicle_plate = request.POST.get('vehicle_plate', perfil.vehicle_plate)
        perfil.category = request.POST.get('category', perfil.category)
        
        # Pode aproveitar para atualizar telefone ou nome também
        request.user.first_name = request.POST.get('first_name', request.user.first_name)
        request.user.phone = request.POST.get('phone', request.user.phone)
        request.user.save()

        # Re-valida para liberar a conta
        cnh_agora_valida = perfil.cnh_number and 'Pendente' not in perfil.cnh_number
        placa_agora_valida = perfil.vehicle_plate and 'Pendente' not in perfil.vehicle_plate

        if cnh_agora_valida and placa_agora_valida:
            perfil.is_available = True
            
        perfil.save()
        messages.success(request, "Perfil atualizado com sucesso!")
        return redirect('motoboy_tasks')

    context = {
        'perfil': perfil,
        'is_first_access': is_first_access
    }
    return render(request, 'orders/motoboy_profile.html', context)

@login_required
def assign_motoboy_view(request, os_id):
    if request.user.type != 'DISPATCHER' and not request.user.is_superuser:
        return redirect('root')

    if request.method == 'POST':
        motoboy_id = request.POST.get('motoboy_id')
        os = get_object_or_404(ServiceOrder, id=os_id)
        
        from logistics.models import MotoboyProfile
        motoboy = get_object_or_404(MotoboyProfile, id=motoboy_id)
        
        # Atualiza a OS Mãe
        os.motoboy = motoboy
        os.status = 'ACEITO'
        os.save()

        # Atualiza as OS Filhas (para as empresas verem que o motoboy aceitou!)
        child_orders = ServiceOrder.objects.filter(parent_os=os)
        child_orders.update(motoboy=motoboy, status='ACEITO')
        
        # --- MÁGICA DA ROTEIRIZAÇÃO ---
        last_seq = motoboy.route_stops.filter(is_completed=False).count()
        
        # Pega as paradas da Mãe E das Filhas
        stops = RouteStop.objects.filter(
            Q(service_order=os) | Q(service_order__parent_os=os)
        ).order_by('sequence')

        # Joga as paradas na fila do motoboy
        for stop in stops:
            last_seq += 1
            stop.motoboy = motoboy
            stop.sequence = last_seq
            stop.save()
            
        messages.success(request, f"Roteiro da OS #{os.os_number} adicionado à rota de {motoboy.user.first_name}!")
        
    return redirect('dispatch_dashboard')

@login_required
def reorder_stops_view(request):
    if request.method == 'POST':
        data = json.loads(request.body)
        stop_ids = data.get('stops', [])
        
        from orders.models import RouteStop
        
        # O Javascript manda a lista de IDs na nova ordem. A gente salva a nova sequência no banco (1, 2, 3...)
        for index, stop_id in enumerate(stop_ids):
            RouteStop.objects.filter(id=stop_id).update(sequence=index + 1)
            
        return JsonResponse({'status': 'success'})

@login_required
@require_POST
def motoboy_update_status(request, stop_id):
    """ O motoboy agora confirma a PARADA (RouteStop) específica """
    if request.user.type != 'MOTOBOY':
        return redirect('root')

    # Pega exatamente a parada que ele clicou
    current_stop = get_object_or_404(RouteStop, id=stop_id, motoboy__user=request.user)

    if not current_stop.is_completed:
        
        # 1. SE FOR ENTREGA: Salva os dados do Comprovante (POD)
        if current_stop.stop_type == 'ENTREGA' and current_stop.destination:
            dest = current_stop.destination
            
            # Pega o nome digitado e a foto enviada pelo formulário
            receiver_name = request.POST.get('receiver_name')
            proof_photo = request.FILES.get('proof_photo')
            
            if receiver_name:
                dest.receiver_name = receiver_name
            if proof_photo:
                dest.proof_photo = proof_photo
                
            dest.is_delivered = True
            dest.delivered_at = timezone.now()
            dest.save()

        # 2. Conclui a Etapa
        current_stop.is_completed = True
        current_stop.completed_at = timezone.now()
        current_stop.save()

        # 3. Atualiza o Status Geral da OS
        os = current_stop.service_order
        if current_stop.stop_type == 'COLETA':
            os.status = 'COLETADO'
            os.save()
            messages.success(request, f"Coleta confirmada!")
            
        elif current_stop.stop_type == 'ENTREGA':
            paradas_restantes = os.stops.filter(is_completed=False).count()
            if paradas_restantes == 0:
                os.status = 'ENTREGUE'
                os.save()
                messages.success(request, f"OS finalizada com sucesso!")
            else:
                messages.success(request, f"Entrega confirmada! Partindo para o próximo destino.")

    return redirect('motoboy_tasks')

@login_required
def motoboy_heartbeat_view(request):
    """ Recebe o sinal do aplicativo/tela do motoboy para mantê-lo online """
    if request.user.type == 'MOTOBOY':
        # Mantém ele online no Cache por 2 minutos (120 segundos)
        cache.set(f'seen_{request.user.id}', True, timeout=300)
        return JsonResponse({'status': 'online'})
    return JsonResponse({'status': 'ignored'})

@login_required
def dashboard(request):
    # Se for ADMIN, vê tudo. Se for Empresa, vê só as suas.
    if request.user.type == 'ADMIN':
        orders = ServiceOrder.objects.all().order_by('-created_at')
    elif request.user.type == 'COMPANY':
        orders = ServiceOrder.objects.filter(client=request.user).order_by('-created_at')
    else:
        # Lógica do Motoboy (faremos depois)
        orders = ServiceOrder.objects.filter(motoboy__user=request.user).order_by('-created_at')

    return render(request, 'orders/dashboard.html', {'orders': orders})

@login_required
def company_dashboard_view(request):
    if request.user.type != 'COMPANY':
        return redirect('root')

    # Busca todas as OS desta empresa
    minhas_os = ServiceOrder.objects.filter(client=request.user).order_by('-created_at')

    # Calcula as métricas reais do banco de dados
    metrics = {
        'pending': minhas_os.filter(status='PENDENTE').count(),
        'in_progress': minhas_os.filter(status__in=['ACEITO', 'COLETADO']).count(),
        'delivered': minhas_os.filter(status='ENTREGUE').count(),
        'canceled': minhas_os.filter(status='CANCELADO').count(),
        'total': minhas_os.count()
    }

    # Separa as ativas para a tabela principal (Exclui as finalizadas)
    ativas = minhas_os.exclude(status__in=['ENTREGUE', 'CANCELADO'])
    
    # Pega as 5 últimas para a barra lateral direita
    recentes = minhas_os[:5]

    context = {
        'metrics': metrics,
        'ativas': ativas,
        'recentes': recentes,
        'company_initials': request.user.first_name[:2].upper() if request.user.first_name else 'EM'
    }
    
    return render(request, 'orders/company_dashboard.html', context)

@login_required
@require_POST
def report_problem_view(request, stop_id):
    """ Regista uma ocorrência e trava a OS """
    if request.user.type != 'MOTOBOY':
        return redirect('root')

    # Encontra a parada e a OS associada
    current_stop = get_object_or_404(RouteStop, id=stop_id, motoboy__user=request.user)
    os = current_stop.service_order

    motivo = request.POST.get('motivo', 'Outro')
    detalhes = request.POST.get('detalhes', '')

    # Muda o status para OCORRENCIA para piscar vermelho no painel do despachante
    os.status = 'OCORRENCIA'
    
    # Grava o que aconteceu no histórico
    nova_nota = f"\n[🚨 OCORRÊNCIA - Parada {current_stop.sequence} ({current_stop.stop_type})] Motivo: {motivo}. Detalhes: {detalhes}"
    os.operational_notes += nova_nota

    # Deixa marcado na própria parada qual foi o motivo do problema.
    # A decisão de "pular" ou "reatentar" é tomada depois, em resolve_os_problem.
    current_stop.failure_reason = nova_nota
    current_stop.save(update_fields=['failure_reason'])

    os.save()

    messages.warning(request, f"Ocorrência registada na OS {os.os_number}. O Despachante foi notificado.")
    return redirect('motoboy_tasks')

@login_required
@require_POST
def resolve_os_problem(request, os_id):
    """ Tira a OS do status de Ocorrência após o despachante tomar uma decisão """
    if request.user.type != 'DISPATCHER' and not request.user.is_superuser:
        return JsonResponse({'status': 'error', 'message': 'Sem permissão.'}, status=403)

    os = get_object_or_404(ServiceOrder, id=os_id)
    
    # Lê a ação escolhida pelo Despachante no Modal
    data = json.loads(request.body)
    action = data.get('action', 'reactivate')

    # Se a OS fizer parte de um grupo (Mãe + Filhas), trabalhamos sempre a partir da "raiz" do grupo,
    # garantindo consistência tanto no status quanto nas paradas vinculadas.
    root_os = os.parent_os or os
    grouped_orders = ServiceOrder.objects.filter(
        Q(id=root_os.id) | Q(parent_os=root_os)
    )

    if action == 'reactivate':
        # Caso de "ignorar/reativar rota":
        # - marcamos a parada problemática como concluída com falha,
        # - o motoboy segue automaticamente para o próximo destino.
        from orders.models import RouteStop

        # Consideramos a parada "ativa" como a primeira ainda não concluída no grupo
        problem_stop = RouteStop.objects.filter(
            service_order__in=grouped_orders,
            is_completed=False
        ).order_by('sequence').first()

        if problem_stop:
            problem_stop.is_failed = True
            problem_stop.is_completed = True
            problem_stop.completed_at = timezone.now()
            if not problem_stop.failure_reason:
                problem_stop.failure_reason = f"Marcada como falha/reagendada por {request.user.first_name}."
            problem_stop.save()

        # Volta o grupo para o motoboy continuar de onde parou.
        # Se a coleta já foi concluída em alguma OS do grupo, consideramos a rota como "COLETADO".
        group_stops = RouteStop.objects.filter(service_order__in=grouped_orders)

        if group_stops.filter(stop_type='COLETA', is_completed=True).exists():
            new_status = 'COLETADO'
        else:
            new_status = 'ACEITO'

        grouped_orders.update(status=new_status)
        root_os.operational_notes += f"\n[✅ RESOLVIDO] Parada problemática marcada como falha e rota reativada por {request.user.first_name}."
        
    elif action == 'unassign':
        # Tira do motoboy atual e devolve o grupo (OS Mãe + Filhas) para a fila de 'Aguardando'
        grouped_orders.update(status='PENDENTE', motoboy=None)

        from orders.models import RouteStop
        RouteStop.objects.filter(
            service_order__in=grouped_orders,
            is_completed=False
        ).update(motoboy=None)

        root_os.operational_notes += f"\n[🔄 RETORNOU] Grupo removido do motoboy e voltou para a fila por {request.user.first_name}."

    root_os.save()
    return JsonResponse({'status': 'success'})