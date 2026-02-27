const myOrders = window.MOTOBOY_ORDERS || [];
let activeOs = null;

function renderList() {
    const container = document.getElementById('os-list-container');
    let totalEntregasPendentes = 0; 
    container.innerHTML = '';

    if (myOrders.length === 0) {
        container.innerHTML = `
            <div class="d-flex flex-column align-items-center justify-content-center h-100 text-slate-400 mt-5">
                <i class="bi bi-check-circle fs-1 mb-2 opacity-50"></i>
                <p class="small fw-bold">Tudo limpo por aqui!</p>
            </div>
        `;
        document.getElementById('kpi-entregas').innerText = "0";
        return;
    }

    myOrders.forEach((os, index) => {
        const entregasPendentes = os.stops.filter(s => !s.is_completed && s.type === 'ENTREGA');
        totalEntregasPendentes += entregasPendentes.length;
        
        const paradasPendentes = os.stops.filter(s => !s.is_completed);
        const nextStop = paradasPendentes.length > 0 ? paradasPendentes[0] : null;
        
        const isColeta = nextStop && nextStop.type === 'COLETA';
        const iconBgClass = isColeta ? 'bg-warning bg-opacity-10' : 'bg-primary bg-opacity-10';
        const iconColorClass = isColeta ? 'text-warning' : 'text-primary';
        const iconClass = isColeta ? 'bi-box-seam' : 'bi-geo-alt';

        const isLocked = index > 0;

        const card = document.createElement('div');
        card.className = `bg-white p-3 rounded-4 shadow-sm border border-slate-200 mb-3 position-relative overflow-hidden transition-all ${isLocked ? 'opacity-75' : ''}`;
        card.style.cursor = isLocked ? "not-allowed" : "pointer";
        
        card.onclick = () => {
            if (isLocked) {
                showToast('🚨 Conclua a sua OS atual antes de iniciar a próxima!', false);
            } else {
                openOS(os.id);
            }
        };

        const mescladaBadge = os.has_children 
            ? `<div class="rounded-3 px-2 py-1 mb-2 d-inline-block small fw-bold w-100" style="background-color: #f3e8ff; color: #6b21a8; font-size: 0.7rem;"><i class="bi bi-diagram-3"></i> Múltiplas Entregas: Inclui ${os.child_numbers}</div>` 
            : '';

        card.innerHTML = `
            ${mescladaBadge}
            <div class="d-flex justify-content-between align-items-start mb-2">
                <span class="font-monospace fw-bold fs-5 text-dark">${os.os_number}</span>
                <span class="badge badge-prioridade-${os.priority} px-2 py-1" style="font-size: 0.65rem;">${os.priorityDisplay}</span>
            </div>
            
            <div class="d-flex justify-content-between align-items-center mb-3">
                <span class="badge bg-primary bg-opacity-10 text-primary border border-primary border-opacity-25 px-2 py-1" style="font-size: 0.7rem;">${os.status}</span>
                <span class="small fw-bold text-slate-500" style="font-size: 0.75rem;">${entregasPendentes.length} entregas aqui</span>
            </div>

            ${nextStop ? `
            <div class="bg-slate-50 p-2 rounded-3 border border-light d-flex align-items-start gap-3 mt-2 ${isLocked ? 'grayscale' : ''}">
                <div class="${iconBgClass} rounded-circle d-flex align-items-center justify-content-center mt-1 flex-shrink-0" style="width: 28px; height: 28px;">
                    <i class="bi ${iconClass} ${iconColorClass}"></i>
                </div>
                <div class="flex-grow-1 text-truncate">
                    <p class="text-slate-400 fw-bold text-uppercase mb-0" style="font-size: 0.6rem; letter-spacing: 1px;">Próxima Ação: ${nextStop.type}</p>
                    <p class="text-dark fw-bold text-truncate mb-0" style="font-size: 0.85rem;">${nextStop.name}</p>
                    <p class="text-slate-500 text-truncate mb-0" style="font-size: 0.7rem;">${nextStop.address.split('-')[0]}</p>
                </div>
            </div>
            ` : `<div class="alert alert-success py-2 mb-0 mt-2 small fw-bold text-center"><i class="bi bi-check-circle"></i> Rota Finalizada</div>`}
            
            ${isLocked ? `
            <div class="w-100 py-2 mt-3 bg-secondary bg-opacity-10 text-secondary border border-secondary border-opacity-25 rounded-3 small fw-bold d-flex align-items-center justify-content-center gap-2">
                <i class="bi bi-lock-fill"></i> Aguardando OS anterior
            </div>
            ` : `
            <div class="w-100 py-2 mt-3 bg-slate-900 text-white rounded-3 small fw-bold d-flex align-items-center justify-content-center gap-2 shadow-sm">
                Iniciar Roteiro <i class="bi bi-chevron-right"></i>
            </div>
            `}
        `;
        container.appendChild(card);
    });

    document.getElementById('kpi-entregas').innerText = totalEntregasPendentes;
}

function openOS(id) {
    sessionStorage.setItem('reopenOsId', id); 
    
    activeOs = myOrders.find(o => o.id === id);
    
    const currentStopIndex = activeOs.stops.findIndex(s => !s.is_completed);
    const currentStop = currentStopIndex !== -1 ? activeOs.stops[currentStopIndex] : null;
    const isDelivery = currentStop && currentStop.type === 'ENTREGA';

    document.getElementById('exec-os-number').innerText = activeOs.os_number;
    document.getElementById('exec-etapa').innerText = `Etapa ${currentStopIndex !== -1 ? currentStopIndex + 1 : activeOs.stops.length} de ${activeOs.stops.length}`;

    const cardContainer = document.getElementById('exec-current-card');
    const bottomAction = document.getElementById('exec-bottom-action');

    if (currentStop) {
        const badgeClass = isDelivery ? 'bg-primary bg-opacity-10 text-primary' : 'bg-warning bg-opacity-10 text-warning';
        const itemsTitle = isDelivery ? 'Itens para Entregar Aqui' : 'Itens para Coletar';
        
        let itemsHTML = '<ul class="list-group list-group-flush mb-0">';
        currentStop.items_details.forEach(item => {
            itemsHTML += `
            <li class="list-group-item bg-transparent px-0 py-2 d-flex justify-content-between align-items-center border-bottom border-light">
                <div class="text-truncate me-2">
                    <span class="fw-black text-dark fs-6">${item.qty}x</span> 
                    <span class="text-secondary fw-bold ms-1" style="font-size: 0.85rem;">${item.desc}</span>
                </div>
                <span class="badge bg-secondary bg-opacity-10 text-secondary border border-secondary border-opacity-25 px-2 py-1">${item.type}</span>
            </li>`;
        });
        itemsHTML += '</ul>';
        
        cardContainer.innerHTML = `
            <div class="d-flex justify-content-between align-items-center mb-3">
                <span class="badge ${badgeClass} border fw-bold text-uppercase" style="font-size: 0.65rem;">Próxima Ação: ${currentStop.type}</span>
                <span class="text-slate-400 fw-bold" style="font-size: 0.7rem;">Parada ${currentStop.sequence}</span>
            </div>
            
            <h3 class="fw-bold text-dark mb-2">${currentStop.name}</h3>
            
            <div class="d-flex align-items-start gap-2 mt-3 mb-4">
                <i class="bi bi-geo-alt fs-5 text-slate-400 mt-1"></i>
                <div>
                    <p class="text-dark fw-bold mb-0" style="font-size: 0.85rem;">${currentStop.address}</p>
                    ${currentStop.reference ? `<p class="badge bg-warning bg-opacity-10 text-warning border border-warning border-opacity-25 mt-1 mb-0 p-1 text-wrap text-start">Ref: ${currentStop.reference}</p>` : ''}
                </div>
            </div>

            <div class="bg-slate-50 p-3 rounded-4 border border-light mb-3">
                <p class="text-slate-400 fw-bold text-uppercase mb-2" style="font-size: 0.65rem; letter-spacing: 1px;">
                    <i class="bi bi-box-seam text-dark"></i> ${itemsTitle}
                </p>
                ${itemsHTML}
            </div>

            <div class="bg-slate-50 p-3 rounded-4 border border-light mb-4 d-flex justify-content-between align-items-center">
                <div>
                    <p class="text-slate-400 fw-bold text-uppercase mb-0" style="font-size: 0.65rem;">Contato no local</p>
                    ${currentStop.contact ? `<p class="fw-bold text-dark mb-0">${currentStop.contact}</p>` : `<p class="fw-bold text-slate-400 mb-0">Não informado</p>`}
                </div>
                ${currentStop.contact ? `<a href="tel:${currentStop.contact.replace(/\D/g, '')}" class="btn btn-primary btn-sm rounded-pill fw-bold px-3 py-2 shadow-sm d-flex align-items-center gap-1"><i class="bi bi-telephone"></i> Ligar</a>` : ''}
            </div>

            <div class="d-flex gap-2">
                <a href="https://www.google.com/maps/dir/?api=1&destination=${encodeURIComponent(currentStop.address)}" target="_blank" class="btn btn-light text-primary fw-bold w-50 py-2 d-flex justify-content-center align-items-center gap-2 border shadow-sm">
                    <i class="bi bi-cursor"></i> Navegar
                </a>
                <button type="button" onclick="copiarReferencia('${currentStop.reference || ''}')" class="btn btn-light text-secondary fw-bold w-50 py-2 d-flex justify-content-center align-items-center gap-2 border shadow-sm">
                    <i class="bi bi-copy"></i> Copiar Ref.
                </button>
            </div>
        `;

        const btnConfirmar = document.getElementById('btn-confirmar');
        document.getElementById('btn-confirmar-text').innerText = `Confirmar ${currentStop.type === 'COLETA' ? 'Coleta' : 'Entrega'}`;
        
        if (isDelivery) {
            btnConfirmar.className = "btn btn-primary w-100 py-3 rounded-4 fw-bold fs-6 text-white d-flex align-items-center justify-content-center gap-2 shadow-sm transition-transform";
        } else {
            btnConfirmar.className = "btn btn-warning w-100 py-3 rounded-4 fw-bold fs-6 text-dark d-flex align-items-center justify-content-center gap-2 shadow-sm transition-transform";
        }
        
        document.getElementById('form-confirmar-etapa').action = `/minhas-entregas/atualizar/${currentStop.id}/`;
        bottomAction.classList.remove('d-none');

    } else {
        cardContainer.innerHTML = `
            <div class="bg-success text-white p-4 rounded-4 text-center shadow-sm">
                <i class="bi bi-check-circle fs-1 mb-2"></i>
                <h4 class="fw-bold">OS Finalizada!</h4>
                <p class="small text-white-50 mb-0">Todas as etapas concluídas com sucesso.</p>
            </div>
        `;
        bottomAction.classList.add('d-none');
    }

    const timeline = document.getElementById('exec-timeline');
    timeline.innerHTML = '<div class="timeline-line"></div>';

    activeOs.stops.forEach((stop, index) => {
        const isCompleted = stop.is_completed;
        const isCurrent = index === currentStopIndex;
        
        const dotClass = isCompleted ? 'bg-success border-success' : (isCurrent ? 'bg-white border-primary border-3' : 'bg-slate-50 border-secondary border-2');
        const iconHTML = isCompleted ? '<i class="bi bi-check text-white" style="font-size: 0.8rem;"></i>' : (isCurrent ? '<div class="bg-primary rounded-circle" style="width: 8px; height: 8px;"></div>' : '');
        const typeBadge = stop.type === 'COLETA' ? 'bg-warning bg-opacity-10 text-warning' : 'bg-primary bg-opacity-10 text-primary';

        timeline.innerHTML += `
            <div class="d-flex gap-3 position-relative z-1 mb-4 ${isCompleted ? 'opacity-50' : ''}">
                <div class="${dotClass} rounded-circle d-flex align-items-center justify-content-center mt-1 flex-shrink-0" style="width: 22px; height: 22px; z-index: 2;">
                    ${iconHTML}
                </div>
                <div class="flex-grow-1">
                    <div class="d-flex align-items-center gap-2 mb-1">
                        <span class="fw-bold text-slate-400" style="font-size: 0.7rem;">#${index + 1}</span>
                        <span class="badge ${typeBadge} px-2 border" style="font-size: 0.6rem;">${stop.type}</span>
                        ${activeOs.has_children ? `<span class="badge bg-light text-secondary border px-1" style="font-size: 0.55rem;">${stop.os_origem}</span>` : ''}
                    </div>
                    <p class="fw-bold mb-0 ${isCurrent ? 'text-dark fs-6' : 'text-slate-500 small'}">${stop.name}</p>
                    <p class="text-slate-400 mb-0 text-truncate" style="font-size: 0.7rem; max-width: 250px;">${stop.address.split('-')[0]}</p>
                </div>
            </div>
        `;
    });

    document.getElementById('view-list').classList.remove('active');
    document.getElementById('view-execution').classList.add('active');
}

function closeOS() {
    document.getElementById('view-execution').classList.remove('active');
    document.getElementById('view-list').classList.add('active');
    activeOs = null;
    sessionStorage.removeItem('reopenOsId');
}

function copiarReferencia(texto) {
    if (!texto || texto === 'None' || texto.trim() === '') {
        showToast('Nenhuma referência disponível.', false);
        return;
    }
    navigator.clipboard.writeText(texto).then(() => showToast('Copiado!')).catch(() => showToast('Erro.', false));
}

function confirmarEtapa() {
    if (!activeOs) return;
    const currentStopIndex = activeOs.stops.findIndex(s => !s.is_completed);
    if (currentStopIndex === -1) return;
    const currentStop = activeOs.stops[currentStopIndex];

    if (currentStop.type === 'ENTREGA') {
        const formPod = document.getElementById('form-pod');
        formPod.action = `/minhas-entregas/atualizar/${currentStop.id}/`; 
        
        document.getElementById('receiver_name').value = '';
        document.getElementById('proof_photo').value = '';
        document.getElementById('foto-texto').innerText = 'Toque aqui para abrir a câmera';
        document.getElementById('icone-camera').className = 'bi bi-camera fs-1 text-slate-300';
        document.getElementById('foto-texto').classList.replace('text-success', 'text-slate-400');
        
        const podModal = new bootstrap.Modal(document.getElementById('podModal'));
        podModal.show();
        
    } else {
        const btn = document.getElementById('btn-confirmar');
        btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Processando...';
        btn.disabled = true;
        document.getElementById('form-confirmar-etapa').submit();
    }
}

function abrirModalProblema() {
    if (!activeOs) return;
    const currentStopIndex = activeOs.stops.findIndex(s => !s.is_completed);
    if (currentStopIndex === -1) return;
    const currentStop = activeOs.stops[currentStopIndex];

    document.getElementById('form-problem').action = `/minhas-entregas/problema/${currentStop.id}/`;
    
    const problemModal = new bootstrap.Modal(document.getElementById('problemModal'));
    problemModal.show();
}

document.getElementById('proof_photo')?.addEventListener('change', function(e) {
    if (e.target.files.length > 0) {
        document.getElementById('foto-texto').innerText = "Foto Anexada: " + e.target.files[0].name;
        document.getElementById('foto-texto').classList.replace('text-slate-400', 'text-success');
        document.getElementById('icone-camera').className = 'bi bi-check-circle-fill fs-1 text-success';
    }
});

function sendHeartbeat() {
    fetch('/motoboy/heartbeat/', {
        method: 'GET',
        headers: {
            'X-Requested-With': 'XMLHttpRequest'
        }
    }).catch(() => {
        console.log('Sem internet no momento.');
    });
}

function showToast(msg, isSuccess = true) {
    const toast = document.getElementById('toast');
    const icon = document.getElementById('toast-icon');
    document.getElementById('toast-msg').innerText = msg;
    icon.className = isSuccess ? 'bi bi-check-circle text-success' : 'bi bi-exclamation-triangle text-warning';
    toast.classList.remove('d-none');
    setTimeout(() => toast.classList.add('d-none'), 3000);
}

document.addEventListener('DOMContentLoaded', () => {
    renderList();
    
    const savedOsId = sessionStorage.getItem('reopenOsId');
    if (savedOsId) {
        const osExists = myOrders.find(o => o.id === savedOsId);
        
        if (osExists) {
            openOS(savedOsId); 
        } else {
            sessionStorage.removeItem('reopenOsId');
        }
    }

    sendHeartbeat();
    setInterval(sendHeartbeat, 60000);
});

