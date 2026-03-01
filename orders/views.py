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
from django.db.models import Q, F
from django.db import transaction
from orders.models import Occurrence, DispatcherDecision
from orders.services import transferir_rota_por_acidente

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
def resolve_occurrence_view(request, occurrence_id):
    """ Processa a decisão do despachante para uma ocorrência """
    if request.user.type != 'DISPATCHER' and not request.user.is_superuser:
        return JsonResponse({'status': 'error', 'message': 'Sem permissão.'}, status=403)

    ocorrencia = get_object_or_404(Occurrence, id=occurrence_id, resolvida=False)
    data = json.loads(request.body)
    acao = data.get('acao')
    
    os_atual = ocorrencia.service_order
    parada = ocorrencia.parada

    try:
        if acao == DispatcherDecision.Acao.TRANSFERIR_MOTOBOY:
            novo_motoboy_id = data.get('novo_motoboy_id')
            local_encontro = data.get('local_encontro', 'Base da Empresa')
            
            if not novo_motoboy_id:
                return JsonResponse({'status': 'error', 'message': 'Selecione um motoboy.'}, status=400)
                
            # Chama a função cirúrgica que criámos no services.py
            transferir_rota_por_acidente(ocorrencia.id, novo_motoboy_id, local_encontro, request.user)
            
            return JsonResponse({'status': 'success', 'message': 'Rota transferida com sucesso!'})

        elif acao == DispatcherDecision.Acao.REAGENDAR:
            # O despachante diz ao motoboy "Tenta de novo" ou "Ignora o erro por agora"
            parada.is_failed = False
            parada.bloqueia_proxima = False
            parada.status = RouteStop.StopStatus.PENDENTE
            parada.failure_reason = ""
            parada.save()
            
            os_atual.status = ServiceOrder.Status.ACCEPTED # Volta ao normal
            os_atual.save()

            DispatcherDecision.objects.create(
                occurrence=ocorrencia, acao=acao, 
                detalhes="O despachante mandou re-tentar ou ignorar o bloqueio.", 
                decidido_por=request.user
            )
            
            ocorrencia.resolvida = True
            ocorrencia.save()

        elif acao == DispatcherDecision.Acao.RETORNAR:
            # Agendar devolução (falha definitiva na entrega)
            parada.is_failed = True
            parada.is_completed = True # Tira da frente do motoboy
            parada.completed_at = timezone.now()
            parada.status = RouteStop.StopStatus.COM_OCORRENCIA
            parada.save()
            
            # Cria a parada extra de devolução
            RouteStop.objects.create(
                service_order=os_atual,
                motoboy=ocorrencia.motoboy,
                stop_type=RouteStop.StopType.RETURN,
                sequence=parada.sequence + 1,
                failure_reason=data.get('endereco_retorno', 'Devolver na Base'),
                status=RouteStop.StopStatus.PENDENTE
            )
            
            DispatcherDecision.objects.create(
                occurrence=ocorrencia, acao=acao, 
                detalhes="Devolução agendada para a base.", 
                decidido_por=request.user
            )
            
            ocorrencia.resolvida = True
            ocorrencia.save()

        else:
            return JsonResponse({'status': 'error', 'message': 'Ação não reconhecida.'}, status=400)

        return JsonResponse({'status': 'success'})

    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

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

    pending_orders = ServiceOrder.objects.filter(
        Q(status='PENDENTE') | Q(status='OCORRENCIA', motoboy__isnull=True)
    ).order_by('-priority', 'created_at')
    
    from logistics.models import MotoboyProfile
    motoboys = MotoboyProfile.objects.all()
    
    motoboy_data = []
    for mb in motoboys:
        last_seen = cache.get(f'seen_{mb.user.id}')
        is_online = mb.is_available and bool(last_seen)

        # CORREÇÃO: Esconde a paragem "Aguardando Socorro" do motoboy antigo no painel do despachante!
        ativas = mb.route_stops.filter(is_completed=False).exclude(failure_reason__icontains='[AGUARDANDO SOCORRO]').order_by('sequence')
        
        motoboy_data.append({
            'profile': mb,
            'is_online': is_online,
            'load': ativas.count(),
            'max_load': 10,
            'active_stops': ativas,
        })
        
    motoboy_data.sort(key=lambda x: x['is_online'], reverse=True)

    total_ativas = sum(mb['load'] for mb in motoboy_data)
    total_ocorrencias = ServiceOrder.objects.filter(status='OCORRENCIA').count()

    context = {
        'pending_orders': pending_orders,
        'motoboy_data': motoboy_data,
        'total_ativas': total_ativas,
        'total_ocorrencias': total_ocorrencias,
        'now': timezone.now(),
    }
    context['ocorrencias_pendentes'] = Occurrence.objects.filter(resolvida=False).order_by('-urgencia', '-criado_em')

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
@require_POST
def unmerge_os_view(request):
    """
    Desfaz a mesclagem de uma OS filha, voltando ela para o estado independente (PENDENTE).
    Somente OS que já foram mescladas (status=AGRUPADO e com parent_os definido) podem ser desfeitas.
    """
    if request.user.type != 'DISPATCHER' and not request.user.is_superuser:
        return JsonResponse({'status': 'error', 'message': 'Sem permissão.'}, status=403)

    data = json.loads(request.body or "{}")
    child_id = data.get('child_os')

    if not child_id:
        return JsonResponse({'status': 'error', 'message': 'OS filha não informada.'}, status=400)

    child_os = get_object_or_404(ServiceOrder, id=child_id)

    # Só permite desfazer se de fato for uma OS mesclada e ainda estiver na fila (sem motoboy)
    if not child_os.parent_os or child_os.status != 'AGRUPADO':
        return JsonResponse({'status': 'error', 'message': 'Esta OS não está mesclada ou já foi atribuída.'}, status=400)

    parent = child_os.parent_os

    with transaction.atomic():
        # 1. Remove o vínculo com a mãe e volta o status para PENDENTE
        child_os.parent_os = None
        child_os.status = 'PENDENTE'

        # Remove tags de log específicas, se existirem
        for marker in ["[AGRUPADA]", "[MESCLADA]"]:
            if child_os.operational_notes and marker in child_os.operational_notes:
                child_os.operational_notes = child_os.operational_notes.replace(marker, "").strip()

        child_os.save()

        # 2. Atualiza o log da mãe (remove referência visual se quiser)
        if parent:
            if parent.operational_notes and "[GRUPO]" in parent.operational_notes:
                # não é crítico limpar tudo, apenas adicionamos uma linha de log
                parent.operational_notes += f"\n[DESFEITO] OS {child_os.os_number} removida do grupo."

            # Se não houver mais filhas, volta o flag de múltiplas entregas
            if not parent.child_orders.exists():
                parent.is_multiple_delivery = False

            parent.save()

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

    if not perfil.cnh_number or 'Pendente' in perfil.cnh_number or not perfil.vehicle_plate or 'Pendente' in perfil.vehicle_plate:
        return redirect('motoboy_profile')

    # 1. Encontra TODAS as OS que têm pelo menos UMA parada para ESTE motoboy específico
    pending_os_ids = RouteStop.objects.filter(
        motoboy=perfil,
        is_completed=False
    ).values_list('service_order_id', flat=True)

    # 2. Busca as OS baseadas nas paradas pendentes
    ativas_qs = ServiceOrder.objects.filter(
        Q(id__in=pending_os_ids) | Q(child_orders__id__in=pending_os_ids),
        status__in=['ACEITO', 'COLETADO', 'OCORRENCIA'], 
        parent_os__isnull=True
    ).distinct().order_by('created_at')

    ativas_data = []
    for os in ativas_qs:
        # 3. MANDA PARA A TELA SÓ AS PARADAS DESTE MOTOBOY (O novo não vê o que o antigo já fez)
        stops = RouteStop.objects.filter(
            (Q(service_order=os) | Q(service_order__parent_os=os)),
            motoboy=perfil
        ).order_by('sequence')
        
        filhas = os.child_orders.all()
        
        ativas_data.append({
            'os': os,
            'stops': stops,
            'has_children': filhas.exists(),
            'child_numbers': [f.os_number for f in filhas]
        })

    entregas_concluidas_hoje = RouteStop.objects.filter(
        motoboy=perfil, stop_type='ENTREGA', is_completed=True, is_failed=False, completed_at__date=timezone.now().date()
    ).count()

    historico = ServiceOrder.objects.filter(motoboy=perfil, status__in=['ENTREGUE', 'CANCELADO']).order_by('-created_at')[:10]

    context = {
        'ativas_data': ativas_data,
        'historico': historico,
        'entregas_concluidas': entregas_concluidas_hoje,
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
    if request.user.type != 'DISPATCHER' and not request.user.is_superuser:
        return JsonResponse({'status': 'error', 'message': 'Sem permissão.'}, status=403)

    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Método inválido.'}, status=405)

    data = json.loads(request.body or "{}")
    raw_ids = data.get('stops', [])

    from orders.models import RouteStop

    # Normaliza os IDs preservando a ordem (o JS manda strings às vezes)
    stop_ids = []
    for sid in raw_ids:
        try:
            stop_ids.append(int(sid))
        except (TypeError, ValueError):
            continue

    if not stop_ids:
        return JsonResponse({'status': 'error', 'message': 'Lista de paradas vazia.'}, status=400)

    stops_meta = list(RouteStop.objects.filter(id__in=stop_ids).values('id', 'stop_type'))
    id_to_type = {s['id']: s['stop_type'] for s in stops_meta}

    # Remove IDs inválidos/ausentes do banco, mantendo a ordem
    stop_ids = [sid for sid in stop_ids if sid in id_to_type]
    if not stop_ids:
        return JsonResponse({'status': 'error', 'message': 'Nenhuma parada válida encontrada.'}, status=400)

    # REGRA: sempre forçar COLETA como 1ª parada na ordenação
    if not any(id_to_type[sid] == 'COLETA' for sid in stop_ids):
        return JsonResponse({'status': 'error', 'message': 'A rota precisa conter uma parada de COLETA.'}, status=400)

    if id_to_type.get(stop_ids[0]) != 'COLETA':
        first_collection_id = next(sid for sid in stop_ids if id_to_type.get(sid) == 'COLETA')
        stop_ids.remove(first_collection_id)
        stop_ids.insert(0, first_collection_id)

    # O Javascript manda a lista de IDs na nova ordem. A gente salva a nova sequência no banco (1, 2, 3...)
    for index, stop_id in enumerate(stop_ids):
        RouteStop.objects.filter(id=stop_id).update(sequence=index + 1)

    return JsonResponse({'status': 'success'})

@login_required
@require_POST
def motoboy_update_status(request, stop_id):
    if request.user.type != 'MOTOBOY':
        return redirect('root')

    current_stop = get_object_or_404(RouteStop, id=stop_id, motoboy__user=request.user)

    if not current_stop.is_completed:
        
        # Lógica de salvar foto da entrega...
        if current_stop.stop_type == 'ENTREGA' and current_stop.destination:
            dest = current_stop.destination
            receiver_name = request.POST.get('receiver_name')
            proof_photo = request.FILES.get('proof_photo')
            if receiver_name: dest.receiver_name = receiver_name
            if proof_photo: dest.proof_photo = proof_photo
            dest.is_delivered = True
            dest.delivered_at = timezone.now()
            dest.save()

        # Conclui a parada do motoboy atual
        current_stop.is_completed = True
        current_stop.completed_at = timezone.now()
        current_stop.save()

        os = current_stop.service_order
        
        # --- A MÁGICA: LIBERA O MOTOBOY ANTIGO ---
        # Se o motoboy NOVO confirmou que pegou a carga no encontro, o ANTIGO é dispensado.
        if current_stop.stop_type == 'TRANSFERENCIA':
            RouteStop.objects.filter(
                Q(service_order=os) | Q(service_order__parent_os=os),
                failure_reason__icontains="[AGUARDANDO SOCORRO]"
            ).update(is_completed=True, completed_at=timezone.now())

        if current_stop.stop_type == 'COLETA':
            os.status = 'COLETADO'
            os.save()
            messages.success(request, f"Coleta confirmada!")
            
        elif current_stop.stop_type in ['ENTREGA', 'TRANSFERENCIA']:
            # Verifica paradas SOMENTE deste motoboy para decidir se finalizou a rota dele
            paradas_restantes = RouteStop.objects.filter(
                Q(service_order=os) | Q(service_order__parent_os=os),
                motoboy=current_stop.motoboy,
                is_completed=False
            ).count()
            
            if paradas_restantes == 0:
                # Só finaliza a OS no painel se NINGUÉM mais tiver paradas (nem o antigo, nem o novo)
                total_geral_restantes = RouteStop.objects.filter(
                    Q(service_order=os) | Q(service_order__parent_os=os), is_completed=False
                ).count()
                
                if total_geral_restantes == 0:
                    os.status = 'ENTREGUE'
                    os.save()
                messages.success(request, f"Todas as suas tarefas desta OS foram concluídas!")
            else:
                messages.success(request, f"Etapa confirmada! Partindo para o próximo destino.")

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
    """ Regista uma ocorrência oficial, salva evidências e decide o estado da rota """
    if request.user.type != 'MOTOBOY':
        return redirect('root')

    from orders.models import RouteStop, Occurrence, ServiceOrder
    
    # 1. Busca as entidades
    current_stop = get_object_or_404(RouteStop, id=stop_id, motoboy__user=request.user)
    os_atual = current_stop.service_order
    motoboy_profile = request.user.motoboy_profile

    # 2. Extrai os dados do formulário
    causa = request.POST.get('causa')
    observacao = request.POST.get('observacao', '')
    evidencia_foto = request.FILES.get('evidencia_foto')
    
    # O motoboy diz se pode continuar a viagem ou se está travado (ex: quebrou a moto)
    pode_seguir = request.POST.get('pode_seguir') == 'on'

    if not causa:
        messages.error(request, "A causa da ocorrência é obrigatória.")
        return redirect('motoboy_tasks')

    # 3. Cria o registro de Ocorrência estruturado
    ocorrencia = Occurrence.objects.create(
        parada=current_stop,
        service_order=os_atual,
        motoboy=motoboy_profile,
        causa=causa,
        observacao=observacao,
        evidencia_foto=evidencia_foto,
        urgencia=Occurrence.Urgencia.ALTA if causa == 'ACIDENTE' else Occurrence.Urgencia.MEDIA
    )

    # 4. Atualiza a Parada (RouteStop)
    current_stop.status = RouteStop.StopStatus.COM_OCORRENCIA
    current_stop.is_failed = True
    current_stop.failure_reason = f"{ocorrencia.get_causa_display()}"
    
    # Se for acidente, bloqueia a rota na marra. Se não for, respeita o que o motoboy marcou.
    current_stop.bloqueia_proxima = True if causa == 'ACIDENTE' else not pode_seguir
    current_stop.save()

    # 5. Atualiza o status da OS Mãe e das filhas para OCORRENCIA (para o despachante ver)
    root_os = os_atual.parent_os or os_atual
    grouped_orders = ServiceOrder.objects.filter(Q(id=root_os.id) | Q(parent_os=root_os))
    grouped_orders.update(status=ServiceOrder.Status.PROBLEM)
    
    # Adiciona no Log da OS Mãe
    nova_nota = f"\n[🚨 OCORRÊNCIA - {current_stop.get_stop_type_display()}] Motivo: {ocorrencia.get_causa_display()}."
    root_os.operational_notes += nova_nota
    root_os.save()

    messages.warning(request, "Ocorrência enviada! O despachante já foi notificado.")
    return redirect('motoboy_tasks')

@login_required
@require_POST
def resolve_os_problem(request, os_id):
    """ Tira a OS do status de Ocorrência após o despachante tomar uma decisão """
    if request.user.type != 'DISPATCHER' and not request.user.is_superuser:
        return JsonResponse({'status': 'error', 'message': 'Sem permissão.'}, status=403)

    os = get_object_or_404(ServiceOrder, id=os_id)
    data = json.loads(request.body)
    action = data.get('action', 'reactivate')

    root_os = os.parent_os or os
    grouped_orders = ServiceOrder.objects.filter(Q(id=root_os.id) | Q(parent_os=root_os))

    if action == 'reactivate':
        # Reativar: O despachante mandou o motoboy tentar de novo.
        group_stops = RouteStop.objects.filter(service_order__in=grouped_orders)
        new_status = 'COLETADO' if group_stops.filter(stop_type='COLETA', is_completed=True).exists() else 'ACEITO'
        
        grouped_orders.update(status=new_status)
        root_os.operational_notes += f"\n[✅ RESOLVIDO] Ocorrência ignorada e rota reativada por {request.user.first_name}."
        
        # Limpa o erro da parada travada para ela voltar ao normal na tela do motoboy
        RouteStop.objects.filter(
            service_order__in=grouped_orders, is_completed=False, is_failed=True
        ).update(is_failed=False, failure_reason="")
        
    elif action == 'unassign':
        # Desvincular: Tira do motoboy e devolve para a fila (Útil se a loja fechou antes dele coletar)
        grouped_orders.update(status='PENDENTE', motoboy=None)

        # Limpa o motoboy e reseta a falha para o próximo assumir a OS limpa
        RouteStop.objects.filter(
            service_order__in=grouped_orders, is_completed=False
        ).update(motoboy=None, is_failed=False, failure_reason="")

        root_os.operational_notes += f"\n[🔄 RETORNOU] Grupo removido do motoboy e voltou para a fila por {request.user.first_name}."

    root_os.save()
    return JsonResponse({'status': 'success'})

@login_required
@require_POST
def transfer_route_view(request, os_id):
    if request.user.type != 'DISPATCHER' and not request.user.is_superuser:
        return JsonResponse({'status': 'error', 'message': 'Sem permissão.'}, status=403)
    
    data = json.loads(request.body)
    new_motoboy_id = data.get('new_motoboy_id')
    transfer_address = (data.get('transfer_address') or '').strip()

    os_obj = get_object_or_404(ServiceOrder, id=os_id)
    os_root = os_obj.parent_os or os_obj
    grouped_orders = ServiceOrder.objects.filter(Q(id=os_root.id) | Q(parent_os=os_root))
    new_motoboy = get_object_or_404(MotoboyProfile, id=new_motoboy_id)

    with transaction.atomic():
        group_stops = RouteStop.objects.filter(service_order__in=grouped_orders)
        is_collected = group_stops.filter(stop_type='COLETA', is_completed=True).exists()

        first_pending = RouteStop.objects.filter(
            service_order__in=grouped_orders,
            is_completed=False
        ).exclude(
            failure_reason__icontains="[AGUARDANDO SOCORRO]"
        ).order_by('sequence').first()

        if not first_pending:
            return JsonResponse({'status': 'error', 'message': 'Nenhuma parada para transferir.'})

        old_motoboy = first_pending.motoboy

        if old_motoboy and old_motoboy != new_motoboy:
            RouteStop.objects.create(
                service_order=os_root,
                motoboy=old_motoboy,
                stop_type='TRANSFERENCIA',
                sequence=99,
                failure_reason="[AGUARDANDO SOCORRO] Veículo avariado."
            )

        if not is_collected:
            pending_real_stops = RouteStop.objects.filter(
                service_order__in=grouped_orders, is_completed=False
            ).exclude(
                failure_reason__icontains="[AGUARDANDO SOCORRO]"
            ).exclude(
                failure_reason__icontains="Encontro:"
            )

            pending_real_stops.update(motoboy=new_motoboy)

            for stop in pending_real_stops:
                if stop.failure_reason and ("avariado" in stop.failure_reason or "OCORRÊNCIA" in stop.failure_reason):
                    stop.failure_reason = ""
                    stop.save()

            new_status = 'ACEITO'
            grouped_orders.update(motoboy=new_motoboy, status=new_status)

            os_root.status = new_status
            os_root.motoboy = new_motoboy
            os_root.operational_notes += (
                f"\n[🚨 SOCORRO] Veículo avariado ANTES da coleta. "
                f"OS reatribuída para {new_motoboy.user.first_name} (coleta no endereço original)."
            )
            os_root.save()

            return JsonResponse({'status': 'success'})

        if not transfer_address:
            return JsonResponse({
                'status': 'error',
                'message': 'Esta OS já foi coletada. Informe o local de encontro para transferir a carga.'
            }, status=400)

        seq_transf = first_pending.sequence
        RouteStop.objects.filter(
            service_order__in=grouped_orders, is_completed=False
        ).exclude(failure_reason__icontains="[AGUARDANDO SOCORRO]").update(sequence=F('sequence') + 1)

        RouteStop.objects.create(
            service_order=os_root, motoboy=new_motoboy, stop_type='TRANSFERENCIA',
            sequence=seq_transf, failure_reason=f"Encontro: {transfer_address}"
        )

        pending_real_stops = RouteStop.objects.filter(
            service_order__in=grouped_orders, is_completed=False
        ).exclude(failure_reason__icontains="[AGUARDANDO SOCORRO]").exclude(failure_reason__icontains="Encontro:")

        pending_real_stops.update(motoboy=new_motoboy)

        for stop in pending_real_stops:
            if stop.failure_reason and ("avariado" in stop.failure_reason or "OCORRÊNCIA" in stop.failure_reason):
                stop.failure_reason = ""
                stop.save()

        new_status = 'COLETADO'
        grouped_orders.update(motoboy=new_motoboy, status=new_status)

        os_root.status = new_status
        os_root.motoboy = new_motoboy
        os_root.operational_notes += f"\n[🚨 SOCORRO] Carga transferida para {new_motoboy.user.first_name}. Ponto de encontro: {transfer_address}"
        os_root.save()

    return JsonResponse({'status': 'success'})

@login_required
@require_POST
def create_return_view(request, os_id):
    if request.user.type != 'DISPATCHER' and not request.user.is_superuser:
        return JsonResponse({'status': 'error'}, status=403)
    
    data = json.loads(request.body)
    return_address = data.get('return_address', 'Base da Transportadora')
    is_priority = data.get('is_priority', False)
    
    root_os = get_object_or_404(ServiceOrder, id=os_id)
    grouped_orders = ServiceOrder.objects.filter(Q(id=root_os.id) | Q(parent_os=root_os))

    with transaction.atomic():
        motoboy = root_os.motoboy
        
        active_stop = motoboy.route_stops.filter(service_order__in=grouped_orders, is_completed=False).order_by('sequence').first()
        if active_stop and active_stop.is_failed:
            active_stop.is_completed = True
            active_stop.completed_at = timezone.now()
            active_stop.save()
            
        sequence_to_use = 99
        if motoboy:
            if is_priority:
                current_active = motoboy.route_stops.filter(is_completed=False).order_by('sequence').first()
                if current_active:
                    sequence_to_use = current_active.sequence + 1
                    motoboy.route_stops.filter(
                        is_completed=False, sequence__gte=sequence_to_use
                    ).update(sequence=F('sequence') + 1)
                else:
                    sequence_to_use = motoboy.route_stops.count() + 1
            else:
                sequence_to_use = motoboy.route_stops.count() + 1

        RouteStop.objects.create(
            service_order=root_os,
            motoboy=motoboy,
            stop_type='DEVOLUCAO',
            sequence=sequence_to_use,
            failure_reason=f"Devolver em: {return_address}" 
        )
        
        tipo_log = "PRIORITÁRIA" if is_priority else "NORMAL"
        root_os.operational_notes += f"\n[🔄 DEVOLUÇÃO {tipo_log}] Agendada devolução para {return_address}."
        
        group_stops = RouteStop.objects.filter(service_order__in=grouped_orders)
        if group_stops.filter(stop_type='COLETA', is_completed=True).exists():
            root_os.status = 'COLETADO'
        else:
            root_os.status = 'ACEITO'
            
        root_os.save()

    return JsonResponse({'status': 'success'})