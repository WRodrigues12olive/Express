# orders/services.py
from django.db import transaction, models
from django.utils import timezone
from .models import ServiceOrder, RouteStop, OSItem, Occurrence, DispatcherDecision
from django.db.models import Q, Value
from django.db.models.functions import Concat

@transaction.atomic
def transferir_rota_por_acidente(ocorrencia_id, novo_motoboy_id, local_transferencia_str, despachante_user):
    """
    Executa a transferência de uma OS de um motoboy acidentado para um novo motoboy.
    Trata os 3 cenários de posse de itens com garantia de atomicidade.
    """
    ocorrencia = Occurrence.objects.select_for_update().get(id=ocorrencia_id)
    os_atual = ocorrencia.service_order
    motoboy_antigo = ocorrencia.motoboy
    
    if ocorrencia.resolvida:
        raise ValueError("Esta ocorrência já foi resolvida.")

    # 1. Pega a OS Mãe e as Filhas para não esquecer pacotes mesclados
    root_os = os_atual.parent_os or os_atual
    grouped_orders = ServiceOrder.objects.filter(Q(id=root_os.id) | Q(parent_os=root_os))

    # 2. Verifica quais itens DESTE GRUPO estão no baú do motoboy antigo (Cenários 1 e 2)
    itens_em_posse = OSItem.objects.filter(
        order__in=grouped_orders, 
        posse_atual=motoboy_antigo, 
        status=OSItem.ItemStatus.COLETADO
    )

    tem_itens_na_bag = itens_em_posse.exists()
    
    # Registra a decisão do despachante
    DispatcherDecision.objects.create(
        occurrence=ocorrencia,
        acao=DispatcherDecision.Acao.TRANSFERIR_MOTOBOY,
        detalhes=f"Transferido para motoboy ID {novo_motoboy_id}. Local: {local_transferencia_str}",
        decidido_por=despachante_user
    )

    # Pega todas as paradas que o Motoboy A AINDA NÃO FEZ
    paradas_pendentes = RouteStop.objects.filter(
        service_order__in=grouped_orders, 
        motoboy=motoboy_antigo, 
        is_completed=False
    ).order_by('sequence')

    if not tem_itens_na_bag:
        # ==========================================
        # CENÁRIO 3: Motoboy antigo NÃO coletou nada (Troca Limpa)
        # ==========================================
        # Passa as paradas direto e LIMPA O VÍRUS do erro antigo.
        paradas_pendentes.update(
            motoboy_id=novo_motoboy_id, 
            status=RouteStop.StopStatus.PENDENTE,
            is_failed=False,           # Cura a parada
            failure_reason="",         # Cura a parada
            bloqueia_proxima=False     # Destrava o app do novo motoboy
        )
        
        root_os.operational_notes += f"\n[TRANSFERÊNCIA LIMPA] Rota repassada para outro técnico antes da coleta."
        novo_status_os = 'ACEITO'
        
    else:
        # ==========================================
        # CENÁRIOS 1 e 2: Motoboy antigo tem pacotes no baú!
        # ==========================================
        
        prox_parada = paradas_pendentes.first()
        seq_transferencia = prox_parada.sequence if prox_parada else 99
        
        # 1. Passa as paradas faltantes para o B, empurra a sequência e LIMPA os erros
        for p in paradas_pendentes:
            p.sequence += 1
            p.motoboy_id = novo_motoboy_id
            p.status = RouteStop.StopStatus.PENDENTE
            p.is_failed = False          # Cura a parada
            p.failure_reason = ""        # Cura a parada
            p.bloqueia_proxima = False   # Destrava a tela
            p.save()

        # 2. Cria a parada de RESGATE obrigatória antes de tudo
        RouteStop.objects.create(
            service_order=root_os, # Vincula à mãe
            motoboy_id=novo_motoboy_id,
            stop_type=RouteStop.StopType.TRANSFER,
            sequence=seq_transferencia,
            failure_reason=f"Encontro: {local_transferencia_str}", # <--- CORRIGIDO AQUI!
            status=RouteStop.StopStatus.PENDENTE,
            bloqueia_proxima=True # OBRIGA o Motoboy B a pegar os itens antes de entregar!
        )

        # 3. Coloca os itens lógicamente no "limbo" (Transferido) até o B os pegar
        # AQUI ESTÁ A CORREÇÃO (USO DO CONCAT DO DJANGO EM VEZ DO SINAL DE +)
        itens_em_posse.update(
            status=OSItem.ItemStatus.TRANSFERIDO,
            item_notes=Concat(
                models.F('item_notes'), 
                Value(f"\n[RESGATE] Transferido no local: {local_transferencia_str}")
            )
        )
        
        root_os.operational_notes += f"\n[TRANSFERÊNCIA] Criada parada de resgate em {local_transferencia_str}."
        novo_status_os = 'COLETADO'

    # 4. Resolve a ocorrência (Tira o Motoboy A do castigo sem apagar as paradas que transferimos)
    ocorrencia.resolvida = True
    ocorrencia.save()

    # Atualiza todas as OS agrupadas com o novo motoboy e status
    grouped_orders.update(motoboy_id=novo_motoboy_id, status=novo_status_os)
    
    root_os.status = novo_status_os
    root_os.motoboy_id = novo_motoboy_id
    root_os.save()
    
    return True