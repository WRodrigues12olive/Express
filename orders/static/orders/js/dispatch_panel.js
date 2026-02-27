function getCSRFToken() {
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';');
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            if (cookie.substring(0, 10) === ('csrftoken=')) {
                cookieValue = decodeURIComponent(cookie.substring(10));
                break;
            }
        }
    }
    if (!cookieValue) {
        const tokenInput = document.querySelector('[name=csrfmiddlewaretoken]');
        if (tokenInput) cookieValue = tokenInput.value;
    }
    return cookieValue;
}

// ====================================================
// LÓGICA DE SLA (TEMPO NA FILA)
// ====================================================
function updateSLAs() {
    const now = Math.floor(Date.now() / 1000);
    document.querySelectorAll('.sla-tracker').forEach(card => {
        const timestamp = parseInt(card.getAttribute('data-timestamp'));
        if(!timestamp) return;
        
        const diffMinutes = Math.floor((now - timestamp) / 60);
        const badge = card.querySelector('.sla-badge');
        const text = card.querySelector('.sla-text');
        
        if(text) text.innerText = diffMinutes + 'm';
        
        if (diffMinutes >= 60) {
            card.classList.add('sla-critical');
            if(badge) {
                badge.classList.replace('bg-light', 'bg-danger');
                badge.classList.replace('text-secondary', 'text-white');
            }
            if(text) text.innerText = diffMinutes + 'm 🔥';
        } else if (diffMinutes >= 30) {
            card.classList.add('sla-warning');
            if(badge) {
                badge.classList.replace('bg-light', 'bg-warning');
                badge.classList.replace('text-secondary', 'text-dark');
            }
        }
    });
}
setInterval(updateSLAs, 60000);
setTimeout(updateSLAs, 1000);

// ====================================================
// MODAL DE RESOLUÇÃO DE PROBLEMAS E ATRIBUIÇÃO
// ====================================================
let problemOsId = null;
let currentModalOsId = null;

function abrirModalResolver(osId, osNumber, notes) {
    problemOsId = osId;
    document.getElementById('modalProblemOsNumber').innerText = osNumber;
    document.getElementById('modalProblemNotes').innerText = notes || "Nenhuma observação registrada pelo motoboy.";
    
    var modal = new bootstrap.Modal(document.getElementById('resolveProblemModal'));
    modal.show();
}

function submitResolveAction(action) {
    if (!problemOsId) return;
    
    if (action === 'cancel') {
        if(confirm("Tem a certeza que deseja CANCELAR esta OS? A empresa será notificada.")) {
            document.body.style.cursor = 'wait';
            fetch(`/os/${problemOsId}/cancelar/`, {
                method: 'POST',
                headers: {'X-CSRFToken': getCSRFToken()}
            }).then(res => window.location.reload());
        }
        return;
    }

    document.body.style.cursor = 'wait';
    fetch(`/painel-despacho/resolver/${problemOsId}/`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCSRFToken()
        },
        body: JSON.stringify({ action: action })
    }).then(res => window.location.reload());
}

function assignMotoboySecurely(osId, motoboyId) {
    document.body.style.cursor = 'wait';
    const form = document.createElement('form');
    form.method = 'POST';
    form.action = `/painel-despacho/atribuir/${osId}/`;
    
    const inputCsrf = document.createElement('input');
    inputCsrf.type = 'hidden';
    inputCsrf.name = 'csrfmiddlewaretoken';
    inputCsrf.value = getCSRFToken();
    form.appendChild(inputCsrf);
    
    const inputMotoboy = document.createElement('input');
    inputMotoboy.type = 'hidden';
    inputMotoboy.name = 'motoboy_id';
    inputMotoboy.value = motoboyId;
    form.appendChild(inputMotoboy);
    
    document.body.appendChild(form);
    form.submit();
}

function openDispatchModal(id, number, status, company, priority, date, originName, originAddress) {
    currentModalOsId = id;
    document.getElementById('modalDispOsNumber').innerText = number;
    document.getElementById('modalDispOsStatus').innerText = status;
    document.getElementById('modalDispCompany').innerText = company;
    document.getElementById('modalDispPriority').innerText = priority;
    document.getElementById('modalDispDate').innerText = date;
    document.getElementById('modalDispOriginName').innerText = originName;
    document.getElementById('modalDispOriginAddress').innerText = originAddress;

    const assignBox = document.getElementById('modalAssignBox');
    if (status === 'PENDENTE') assignBox.classList.remove('d-none');
    else assignBox.classList.add('d-none');

    fetchAndRenderStops(id);

    var modal = new bootstrap.Modal(document.getElementById('dispatchOsModal'));
    modal.show();
}

function submitModalAssign() {
    const motoboyId = document.getElementById('modalCourierSelect').value;
    if (!motoboyId) { alert("⚠️ Selecione um técnico na lista para atribuir a OS."); return; }
    if (!currentModalOsId) return;
    assignMotoboySecurely(currentModalOsId, motoboyId);
}

function confirmCancelOS() {
    if(!currentModalOsId) return;
    if(confirm("🚨 ATENÇÃO: Tem certeza que deseja cancelar definitivamente esta OS? A empresa será notificada.")) {
        fetch(`/os/${currentModalOsId}/cancelar/`, {
            method: 'POST',
            headers: {'X-CSRFToken': getCSRFToken()}
        }).then(response => {
            if(response.ok) window.location.reload();
            else alert('Erro: A OS já está em andamento ou você não tem permissão.');
        });
    }
}

// ====================================================
// ARRASTAR E SOLTAR DA OS E MESCLAGEM
// ====================================================
function drag(ev, osId) { ev.dataTransfer.setData("osId", osId); }
function allowDrop(ev) { ev.preventDefault(); ev.currentTarget.classList.add('drag-over'); }
function dragLeave(ev) { ev.currentTarget.classList.remove('drag-over'); }

function dropAssign(ev, motoboyId) {
    ev.preventDefault();
    ev.currentTarget.classList.remove('drag-over');
    var osId = ev.dataTransfer.getData("osId");
    if (!osId) return;
    assignMotoboySecurely(osId, motoboyId);
}

function allowOsDrop(ev) { ev.preventDefault(); ev.currentTarget.classList.add('drag-over-os'); }
function leaveOsDrop(ev) { ev.currentTarget.classList.remove('drag-over-os'); }

function dropMerge(ev, targetOsId) {
    ev.preventDefault();
    ev.currentTarget.classList.remove('drag-over-os');
    const sourceOsId = ev.dataTransfer.getData("osId");

    if (!sourceOsId || sourceOsId === targetOsId) return;

    if(confirm("🔗 FUSÃO DE ROTAS\n\nDeseja mesclar essas duas Ordens de Serviço? A OS arrastada acompanhará a principal.")) {
        document.body.style.cursor = 'wait';
        
        fetch('/os/mesclar/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json', 
                'X-CSRFToken': getCSRFToken() 
            },
            body: JSON.stringify({ source_os: sourceOsId, target_os: targetOsId })
        })
        .then(res => {
            if (!res.ok) throw new Error("Erro no servidor (Status " + res.status + ")");
            return res.json();
        })
        .then(data => {
            document.body.style.cursor = 'default';
            if(data.status === 'success') window.location.reload(); 
            else alert("Erro do sistema: " + data.message);
        })
        .catch(err => {
            document.body.style.cursor = 'default';
            alert("Falha na mesclagem: " + err.message);
            console.error(err);
        });
    }
}

// ====================================================
// ROTEIRIZAÇÃO E TIMELINE (MODAL)
// ====================================================
let modalSortableInstance = null;
const DISPATCH_PANEL_CONFIG = window.DISPATCH_PANEL_CONFIG || {};

function fetchAndRenderStops(osId) {
    const list = document.getElementById('modalRouteTimeline');
    list.innerHTML = '<li class="list-group-item text-center text-muted border-0"><span class="spinner-border spinner-border-sm"></span> Carregando rota...</li>';

    fetch(`/os/${osId}/stops/`)
    .then(res => res.json())
    .then(data => {
        list.innerHTML = '';
        data.stops.forEach(stop => {
            const isColeta = stop.type === 'COLETA';
            const icon = isColeta ? '<i class="bi bi-box-arrow-up text-danger fs-4"></i>' : '<i class="bi bi-box-arrow-down text-success fs-4"></i>';
            const badgeColor = isColeta ? 'bg-danger' : 'bg-success';
            
            list.innerHTML += `
                <li class="list-group-item d-flex align-items-center gap-3 py-3" data-id="${stop.id}" style="cursor: grab;">
                    <div class="d-flex flex-column align-items-center gap-1">
                        <span class="badge ${badgeColor} rounded-pill shadow-sm">${stop.sequence}º</span>
                    </div>
                    <div>${icon}</div>
                    <div class="flex-grow-1">
                        <strong class="text-dark d-block mb-1">${stop.location}</strong>
                        <small class="text-muted d-flex align-items-center gap-1"><i class="bi bi-geo-alt"></i> ${stop.address}</small>
                    </div>
                    <i class="bi bi-grip-vertical text-muted fs-5"></i>
                </li>
            `;
        });

        if (modalSortableInstance) modalSortableInstance.destroy();

        modalSortableInstance = new Sortable(list, {
            animation: 150,
            ghostClass: 'bg-light',
            onStart: function() { isInteracting = true; },
            onEnd: function() {
                isInteracting = false;
                
                let stopIds = Array.from(list.children)
                    .map(item => item.dataset.id)
                    .filter(id => id !== undefined && id !== null && id !== "");
                
                const url = DISPATCH_PANEL_CONFIG.reorderStopsUrl || '/painel-despacho/reordenar-paradas/';

                fetch(url, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json', 
                        'X-CSRFToken': getCSRFToken()
                    },
                    body: JSON.stringify({ stops: stopIds })
                }).then(res => {
                    if (res.ok) fetchAndRenderStops(osId); 
                    else alert("Erro ao salvar a ordem das rotas no banco.");
                });
            }
        });
    });
}

function switchTab(tabId) {
    document.querySelectorAll('.tab-content-col').forEach(el => { el.classList.remove('d-flex'); el.classList.add('d-none'); });
    const selectedCol = document.getElementById('col-' + tabId);
    selectedCol.classList.remove('d-none'); selectedCol.classList.add('d-flex');
    
    document.querySelectorAll('.mobile-tab').forEach(el => { el.classList.remove('border-bottom', 'border-primary', 'border-3', 'text-primary'); el.classList.add('text-secondary'); });
    const selectedTab = document.getElementById('tab-' + tabId);
    selectedTab.classList.remove('text-secondary'); selectedTab.classList.add('border-bottom', 'border-primary', 'border-3', 'text-primary');
}

// ====================================================
// ATUALIZAÇÃO AUTOMÁTICA DA PÁGINA
// ====================================================
let isInteracting = false;
let isModalOpen = false;

document.addEventListener('mousedown', () => isInteracting = true);
document.addEventListener('mouseup', () => isInteracting = false);
document.addEventListener('dragstart', () => isInteracting = true);
document.addEventListener('dragend', () => isInteracting = false);
document.getElementById('dispatchOsModal')?.addEventListener('show.bs.modal', () => isModalOpen = true);
document.getElementById('resolveProblemModal')?.addEventListener('show.bs.modal', () => isModalOpen = true);
document.getElementById('transferRouteModal')?.addEventListener('show.bs.modal', () => isModalOpen = true);
document.getElementById('createReturnModal')?.addEventListener('show.bs.modal', () => isModalOpen = true);
document.getElementById('dispatchOsModal')?.addEventListener('hidden.bs.modal', () => isModalOpen = false);
document.getElementById('resolveProblemModal')?.addEventListener('hidden.bs.modal', () => isModalOpen = false);
document.getElementById('transferRouteModal')?.addEventListener('hidden.bs.modal', () => isModalOpen = false);
document.getElementById('createReturnModal')?.addEventListener('hidden.bs.modal', () => isModalOpen = false);

function autoRefreshDashboard() {
    if (isInteracting || isModalOpen) return; 

    fetch(window.location.href)
        .then(response => response.text())
        .then(html => {
            const parser = new DOMParser();
            const doc = parser.parseFromString(html, 'text/html');

            document.getElementById('col-frota').innerHTML = doc.getElementById('col-frota').innerHTML;
            document.getElementById('col-aguardando').innerHTML = doc.getElementById('col-aguardando').innerHTML;
            document.getElementById('col-atendimento').innerHTML = doc.getElementById('col-atendimento').innerHTML;
            
            updateSLAs();
        })
        .catch(error => console.log('Silencioso: Falha na autossincronização', error));
}
setInterval(autoRefreshDashboard, 10000);

// ====================================================
// TRANSFERÊNCIA E DEVOLUÇÃO (SOCORRO)
// ====================================================
function openTransferModal() {
    var resolveModal = bootstrap.Modal.getInstance(document.getElementById('resolveProblemModal'));
    if(resolveModal) resolveModal.hide();
    var transferModal = new bootstrap.Modal(document.getElementById('transferRouteModal'));
    transferModal.show();
}

function openReturnModal() {
    var resolveModal = bootstrap.Modal.getInstance(document.getElementById('resolveProblemModal'));
    if(resolveModal) resolveModal.hide();
    var returnModal = new bootstrap.Modal(document.getElementById('createReturnModal'));
    returnModal.show();
}

function buscarCepDevolucao(cep) {
    cep = cep.replace(/\D/g, '');
    if (cep.length !== 8) return;
    fetch(`https://viacep.com.br/ws/${cep}/json/`)
        .then(res => res.json())
        .then(data => {
            if (!data.erro) {
                document.getElementById('returnStreet').value = data.logradouro;
                document.getElementById('returnDistrict').value = data.bairro;
                document.getElementById('returnCity').value = data.localidade;
                document.getElementById('returnState').value = data.uf;
                document.getElementById('returnNumber').focus();
            } else alert("CEP não encontrado.");
        }).catch(err => console.error(err));
}

function buscarCepTransferencia(cep) {
    cep = cep.replace(/\D/g, '');
    if (cep.length !== 8) return;
    fetch(`https://viacep.com.br/ws/${cep}/json/`)
        .then(res => res.json())
        .then(data => {
            if (!data.erro) {
                document.getElementById('transferStreet').value = data.logradouro;
                document.getElementById('transferDistrict').value = data.bairro;
                document.getElementById('transferCity').value = data.localidade;
                document.getElementById('transferState').value = data.uf;
                document.getElementById('transferNumber').focus();
            } else alert("CEP não encontrado.");
        }).catch(err => console.error(err));
}

function submitTransferRoute() {
    if (!problemOsId) return;
    
    const newMotoboyId = document.getElementById('transferMotoboySelect').value;
    const cep = document.getElementById('transferCep').value;
    const street = document.getElementById('transferStreet').value;
    const number = document.getElementById('transferNumber').value;
    const complement = document.getElementById('transferComplement').value;
    const district = document.getElementById('transferDistrict').value;
    const city = document.getElementById('transferCity').value;
    const state = document.getElementById('transferState').value;

    if (!newMotoboyId) { alert("⚠️ Por favor, selecione o motoboy socorrista na lista!"); return; }
    if (!street || !number || !district || !city) { alert("⚠️ Por favor, preencha pelo menos a Rua, Número, Bairro e Cidade para o local de encontro!"); return; }

    let transferAddress = `${street}, ${number}`;
    if (complement) transferAddress += ` - ${complement}`;
    transferAddress += ` - Bairro: ${district}, ${city}/${state} - CEP: ${cep}`;

    document.body.style.cursor = 'wait';
    fetch(`/painel-despacho/transferir/${problemOsId}/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
        body: JSON.stringify({ new_motoboy_id: newMotoboyId, transfer_address: transferAddress })
    })
    .then(res => res.json())
    .then(data => {
        if(data.status === 'success') window.location.reload();
        else { alert("Erro ao transferir: " + data.message); document.body.style.cursor = 'default'; }
    });
}

function submitCreateReturn() {
    if (!problemOsId) return;
    
    const cep = document.getElementById('returnCep').value;
    const street = document.getElementById('returnStreet').value;
    const number = document.getElementById('returnNumber').value;
    const complement = document.getElementById('returnComplement').value;
    const district = document.getElementById('returnDistrict').value;
    const city = document.getElementById('returnCity').value;
    const state = document.getElementById('returnState').value;
    
    const priorityElem = document.getElementById('returnPriority');
    const isPriority = priorityElem ? priorityElem.checked : false;

    if (!street || !number || !district || !city) { alert("⚠️ Por favor, preencha pelo menos a Rua, Número, Bairro e Cidade para a devolução!"); return; }

    let returnAddress = `${street}, ${number}`;
    if (complement) returnAddress += ` - ${complement}`;
    returnAddress += ` - Bairro: ${district}, ${city}/${state} - CEP: ${cep}`;

    document.body.style.cursor = 'wait';
    fetch(`/painel-despacho/devolver/${problemOsId}/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
        body: JSON.stringify({ return_address: returnAddress, is_priority: isPriority })
    })
    .then(res => res.json())
    .then(data => {
        if(data.status === 'success') window.location.reload();
        else { alert("Erro ao agendar devolução."); document.body.style.cursor = 'default'; }
    });
}