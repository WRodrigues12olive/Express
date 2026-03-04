# orders/services.py
from django.db import transaction, models
from django.utils import timezone
from .models import ServiceOrder, RouteStop, OSItem, Occurrence, DispatcherDecision
from django.db.models import Q, Value, F, Max
from django.db.models.functions import Concat

@transaction.atomic
def transferir_rota_por_acidente(ocorrencia_id, novo_motoboy_id, local_transferencia_str, despachante_user, furar_fila=False):
    ocorrencia = Occurrence.objects.select_for_update().get(id=ocorrencia_id)
    os_atual = ocorrencia.service_order
    motoboy_antigo = ocorrencia.motoboy
    
    if ocorrencia.resolvida:
        raise ValueError("Esta ocorrência já foi resolvida.")

    # --- NOVO: Tira o motoboy acidentado de circulação ---
    motoboy_antigo.is_available = False
    motoboy_antigo.save()

    # --- NOVO: Devolve OUTRAS OS que ele faria depois para a Fila ---
    outras_os = ServiceOrder.objects.filter(
        motoboy=motoboy_antigo, 
        status__in=['PENDENTE', 'ACEITO']
    ).exclude(
        Q(id=os_atual.id) | Q(parent_os=os_atual) | Q(id=os_atual.parent_os_id)
    )
    # Tira as paradas e as OS dessas outras rotas do nome dele
    RouteStop.objects.filter(service_order__in=outras_os, is_completed=False).update(motoboy=None, status='PENDENTE')
    outras_os.update(motoboy=None, status='PENDENTE')

    root_os = os_atual.parent_os or os_atual
    grouped_orders = ServiceOrder.objects.filter(Q(id=root_os.id) | Q(parent_os=root_os))

    itens_em_posse = OSItem.objects.filter(
        order__in=grouped_orders, 
        posse_atual=motoboy_antigo, 
        status=OSItem.ItemStatus.COLETADO
    )
    tem_itens_na_bag = itens_em_posse.exists()
    
    DispatcherDecision.objects.create(
        occurrence=ocorrencia,
        acao=DispatcherDecision.Acao.TRANSFERIR_MOTOBOY,
        detalhes=f"Transferido para ID {novo_motoboy_id}. Furou fila? {'Sim' if furar_fila else 'Não'}. Local: {local_transferencia_str}",
        decidido_por=despachante_user
    )

    paradas_pendentes = RouteStop.objects.filter(
        service_order__in=grouped_orders, 
        motoboy=motoboy_antigo, 
        is_completed=False
    ).order_by('sequence')

    # --- NOVO: Lógica de Sequência (Furar Fila ou Ir pro Final) ---
    def calcular_sequencia_novo_motoboy():
        if furar_fila:
            # Empurra todas as paradas atuais do Motoboy B para frente para abrir espaço
            RouteStop.objects.filter(motoboy_id=novo_motoboy_id, is_completed=False).update(sequence=F('sequence') + 10)
            return 1 # Ele assume a prioridade 1
        else:
            # Vai para o final da fila do Motoboy B
            ultima = RouteStop.objects.filter(motoboy_id=novo_motoboy_id).aggregate(Max('sequence'))['sequence__max'] or 0
            return ultima + 1

    seq_transferencia = calcular_sequencia_novo_motoboy()

    if not tem_itens_na_bag:
        # Transferência Limpa
        for index, p in enumerate(paradas_pendentes):
            p.sequence = seq_transferencia + index
            p.motoboy_id = novo_motoboy_id
            p.status = RouteStop.StopStatus.PENDENTE
            p.is_failed = False
            p.failure_reason = ""
            p.bloqueia_proxima = False
            p.save()
            
        root_os.operational_notes += f"\n[TRANSFERÊNCIA LIMPA] Rota repassada para outro técnico antes da coleta."
        novo_status_os = 'ACEITO'
        
    else:
        # --- NOVO: Mantém a OS na tela do motoboy antigo criando uma parada "AGUARDANDO" ---
        RouteStop.objects.create(
            service_order=root_os,
            motoboy=motoboy_antigo,
            stop_type=RouteStop.StopType.TRANSFER,
            sequence=1,
            status=RouteStop.StopStatus.PENDENTE,
            failure_reason=f"AGUARDE O RESGATE AQUI: {local_transferencia_str}",
            bloqueia_proxima=True
        )

        # Repassa o resto das paradas para o Novo Motoboy
        for index, p in enumerate(paradas_pendentes):
            p.sequence = seq_transferencia + index + 1
            p.motoboy_id = novo_motoboy_id
            p.status = RouteStop.StopStatus.PENDENTE
            p.is_failed = False
            p.failure_reason = ""
            p.bloqueia_proxima = False
            p.save()

        # Cria a parada de RESGATE para o Novo Motoboy
        RouteStop.objects.create(
            service_order=root_os,
            motoboy_id=novo_motoboy_id,
            stop_type=RouteStop.StopType.TRANSFER,
            sequence=seq_transferencia,
            failure_reason=f"Resgatar carga de colega acidentado: {local_transferencia_str}",
            status=RouteStop.StopStatus.PENDENTE,
            bloqueia_proxima=True
        )

        itens_em_posse.update(
            status=OSItem.ItemStatus.TRANSFERIDO,
            item_notes=Concat(
                models.F('item_notes'), 
                Value(f"\n[RESGATE] Transferido no local: {local_transferencia_str}")
            )
        )
        novo_status_os = 'COLETADO'

    ocorrencia.resolvida = True
    ocorrencia.save()

    grouped_orders.update(motoboy_id=novo_motoboy_id, status=novo_status_os)
    root_os.status = novo_status_os
    root_os.motoboy_id = novo_motoboy_id
    root_os.save()
    
    return True