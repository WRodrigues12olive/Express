# orders/services.py
from django.db import transaction, models
from django.utils import timezone
from .models import ServiceOrder, RouteStop, OSItem, Occurrence, DispatcherDecision

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

    # Verifica quais itens desta OS estão sob posse física do motoboy antigo
    itens_em_posse = OSItem.objects.filter(
        order=os_atual, 
        posse_atual=motoboy_antigo, 
        status=OSItem.ItemStatus.COLETADO
    )

    tem_itens_na_bag = itens_em_posse.exists()
    
    # Registra a decisão do despachante
    decisao = DispatcherDecision.objects.create(
        occurrence=ocorrencia,
        acao=DispatcherDecision.Acao.TRANSFERIR_MOTOBOY,
        detalhes=f"Transferido para motoboy ID {novo_motoboy_id}. Local: {local_transferencia_str}",
        decidido_por=despachante_user
    )

    paradas_pendentes = RouteStop.objects.filter(
        service_order=os_atual, 
        motoboy=motoboy_antigo, 
        is_completed=False
    ).order_by('sequence')

    if not tem_itens_na_bag:
        # ==========================================
        # CENÁRIO 1: Motoboy antigo NÃO coletou nada
        # ==========================================
        # Simplesmente passamos as paradas pendentes (incluindo a coleta não feita) para o novo motoboy.
        paradas_pendentes.update(motoboy_id=novo_motoboy_id, status=RouteStop.StopStatus.PENDENTE)
        
        os_atual.operational_notes += f"\n[TRANSFERÊNCIA] Rota repassada direto (sem itens em posse)."
        
    else:
        # ==========================================
        # CENÁRIOS 2 e 3: Motoboy antigo já coletou itens
        # (Pode ou não ter feito algumas entregas, as entregues já estão seguras no DB)
        # ==========================================
        
        # 1. Encontra a sequência para encaixar a transferência (logo antes da próxima parada pendente)
        prox_parada = paradas_pendentes.first()
        seq_transferencia = prox_parada.sequence if prox_parada else 99
        
        # Empurra a sequência das próximas paradas 1 passo para frente para caber a transferência
        for p in paradas_pendentes:
            p.sequence += 1
            p.motoboy_id = novo_motoboy_id # Já repassa a responsabilidade pro novo
            p.status = RouteStop.StopStatus.PENDENTE
            p.save()

        # 2. Cria a parada de TRANSFERÊNCIA para o NOVO motoboy ir buscar a carga
        parada_transf = RouteStop.objects.create(
            service_order=os_atual,
            motoboy_id=novo_motoboy_id,
            stop_type=RouteStop.StopType.TRANSFER,
            sequence=seq_transferencia,
            failure_reason=local_transferencia_str, # Usando o campo para guardar o local de encontro temporariamente
            status=RouteStop.StopStatus.PENDENTE,
            bloqueia_proxima=True # Não pode entregar se não pegar a carga do acidentado antes
        )

        # 3. Transfere a posse lógica dos itens (Eles ficam em status 'TRANSFERIDO' até o novo confirmar a coleta)
        # Nota: A posse_atual passa a ser None temporariamente ou do novo (depende da sua regra, recomendo None até ele aceitar)
        itens_em_posse.update(
            status=OSItem.ItemStatus.TRANSFERIDO,
            item_notes=models.F('item_notes') + f"\n[ACIDENTE] Aguardando resgate em {local_transferencia_str}"
        )
        
        os_atual.operational_notes += f"\n[TRANSFERÊNCIA] Criada parada de resgate em {local_transferencia_str}."

    # Finaliza a ocorrência da parada atual do motoboy acidentado
    parada_acidente = ocorrencia.parada
    parada_acidente.status = RouteStop.StopStatus.CANCELADA
    parada_acidente.is_completed = True # Tira da tela do acidentado
    parada_acidente.completed_at = timezone.now()
    parada_acidente.save()

    ocorrencia.resolvida = True
    ocorrencia.save()
    os_atual.save()
    
    return True