(() => {
'use strict';
const $ = id => document.getElementById(id);
const API = window.HMAPN_API;
const number = v => new Intl.NumberFormat('pt-BR',{maximumFractionDigits:0}).format(Number(v||0));
const money = v => new Intl.NumberFormat('pt-BR',{style:'currency',currency:'BRL',maximumFractionDigits:2}).format(Number(v||0));
const shortMoney = v => {
 const n=Number(v||0);return n>=1e6?'R$ '+(n/1e6).toLocaleString('pt-BR',{maximumFractionDigits:2})+' mi': n>=1e3?'R$ '+(n/1e3).toLocaleString('pt-BR',{maximumFractionDigits:1})+' mil':money(n);
};
const fmt = v => Number(v||0).toLocaleString('pt-BR',{maximumFractionDigits:2});
const date = v => {if(!v)return '—';const d=new Date(v);return Number.isNaN(+d)?'Data não informada':d.toLocaleString('pt-BR',{day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'});};
const escapeHTML = v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels={zerado:'Zerado',critico:'Crítico',atencao:'Atenção',normal:'Normal',excesso:'Excesso',sem_consumo:'Sem consumo'};
const colors={zerado:'#fb6273',critico:'#f9956e',atencao:'#f6c86c',normal:'#36d497',excesso:'#48a2f7',sem_consumo:'#8795ab'};
let summary=null, active='geral',loading=null,searchTimer=null,messageTimer=null,page=0,itemRequest=0,priorityRequest=0,totalItems=0;
const pageSize=100;
const filters=()=>({q:$('search').value,status:$('status-filter').value,grupo:$('group-filter').value});

function flash(msg){clearTimeout(messageTimer);const n=$('message');n.textContent=msg;n.hidden=false;messageTimer=setTimeout(()=>{n.hidden=true},10000);}
const getJSON=(url,opts)=>API.request(url,opts);
function pill(key){return `<span class="pill pill-${escapeHTML(key)}">${labels[key]||escapeHTML(key)}</span>`;}
function rowItems(rows,overview=false){return rows.map(x=>overview?`<tr><td><span class="item-title" title="${escapeHTML(x.descricao)}">${escapeHTML(x.descricao)}</span><span class="item-sub">Código ${escapeHTML(x.material)} · ${escapeHTML(x.unidade||'Unidade não informada')}</span></td><td>${fmt(x.saldo)}</td><td>${fmt(x.cmd)}</td><td class="nowrap">${x.dias==null?'—':fmt(x.dias)+' dias'}</td><td>${pill(x.status)}</td></tr>`:
 `<tr><td>${escapeHTML(x.material)}</td><td><span class="item-title" title="${escapeHTML(x.descricao)}">${escapeHTML(x.descricao)}</span><span class="item-sub">${escapeHTML(x.grupo||'Sem grupo')} · ${escapeHTML(x.unidade||'Unidade não informada')}</span></td><td>${fmt(x.saldo)}</td><td>${fmt(x.cmd)}</td><td>${fmt(x.cmm_estimado)}</td><td class="nowrap">${x.dias==null?'—':fmt(x.dias)+' dias'}</td><td>${money(x.valor)}</td><td>${pill(x.status)}</td></tr>`).join('');}
function drawDonut(status,total){const svg=$('donut-chart');svg.innerHTML='<circle cx="70" cy="70" r="49" fill="none" stroke="#19374f" stroke-width="17" />';const keys=['zerado','critico','atencao','normal','excesso','sem_consumo'];const circumference=2*Math.PI*49;let offset=0;
 for(const k of keys){const n=status[k]||0;if(!n||!total)continue;const len=n/total*circumference;const c=document.createElementNS('http://www.w3.org/2000/svg','circle');c.setAttribute('cx','70');c.setAttribute('cy','70');c.setAttribute('r','49');c.setAttribute('fill','none');c.setAttribute('stroke',colors[k]);c.setAttribute('stroke-width','17');c.setAttribute('stroke-dasharray',`${len} ${circumference-len}`);c.setAttribute('stroke-dashoffset',`${-offset}`);svg.appendChild(c);offset+=len;}
 $('donut-total').textContent=number(total);
 $('status-legend').innerHTML=keys.map(k=>`<div class="legend-row"><span class="legend-left"><span class="legend-dot" style="background:${colors[k]}"></span>${labels[k]}</span><span class="legend-num">${number(status[k]||0)}</span></div>`).join('');
}
function drawTrend(id,history){const holder=$(id);const data=history||[];if(!data.length){holder.innerHTML='<div class="empty-graph-caption">Aguardando primeiro relatório de estoque.</div>';holder.classList.add('empty-graph');return;}
 holder.classList.remove('empty-graph');
 const viewW=750,viewH=245,left=60,right=16,top=18,bottom=38,plotW=viewW-left-right,plotH=viewH-top-bottom;
 const values=data.map(r=>r.valor||0);const min=Math.max(0,Math.min(...values)*.86);const max=Math.max(...values)*1.13||1;const range=Math.max(1,max-min);const px=i=>left+(data.length===1?plotW/2:i/(data.length-1)*plotW);const py=v=>top+(max-v)/range*plotH;
 const coords=data.map((r,i)=>[px(i),py(r.valor||0)]);
 const pts=coords.map(p=>p.map(v=>v.toFixed(1)).join(',')).join(' ');
 const area=coords.length>1?`<polygon points="${left},${top+plotH} ${pts} ${left+plotW},${top+plotH}" fill="url(#${id}-fill)" opacity=".25"/>`:'';
 const lines=Array.from({length:5},(_,i)=>{const y=top+i*plotH/4;const val=max-i*range/4;return `<line class="grid-line" x1="${left}" x2="${left+plotW}" y1="${y}" y2="${y}"/><text class="chart-label" x="${left-12}" y="${y+4}" text-anchor="end">${escapeHTML(shortMoney(val).replace('R$ ',''))}</text>`}).join('');
 const indices=new Set([0,data.length-1,Math.floor((data.length-1)/2)]);
 const ticks=[...indices].sort((a,b)=>a-b).map(i=>`<text class="chart-label" x="${px(i)}" y="${viewH-8}" text-anchor="middle">${escapeHTML(new Date(data[i].file_modified).toLocaleDateString('pt-BR',{day:'2-digit',month:'2-digit'}))}</text>`).join('');
 const dots=coords.map((p,i)=>`<circle cx="${p[0]}" cy="${p[1]}" r="${data.length>1?3.7:6}" fill="#36d5da" stroke="#d4ffff" stroke-width="1.3"><title>${escapeHTML(date(data[i].file_modified))} — ${escapeHTML(money(data[i].valor))}</title></circle>`).join('');
 const line=data.length>1?`<polyline points="${pts}" fill="none" stroke="#28d5df" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>`:'';
 const note=data.length===1?'<div class="empty-graph-caption" style="position:absolute;bottom:2px;left:15px;right:0">1 coleta recebida. Salve novos relatórios para formar a curva histórica.</div>':'';
 holder.innerHTML=`<svg viewBox="0 0 ${viewW} ${viewH}" preserveAspectRatio="none" role="img" aria-label="Evolução do valor do estoque. Valores detalhados na aba Série histórica."><defs><linearGradient id="${id}-fill" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#22d4e0" stop-opacity=".7"/><stop offset="1" stop-color="#22d4e0" stop-opacity="0"/></linearGradient></defs>${lines}${area}${line}${dots}${ticks}</svg>${note}`;
}
function displaySummary(data){summary=data;const available=Boolean(data.ultimo);
 $('status-source').textContent=API.local?(data.scanned?.last_scan_error?'FALHA NO MONITORAMENTO':available?'DADOS IMPORTADOS':'AGUARDANDO RELATÓRIOS'):'DADOS FICTÍCIOS';
 $('latest-file').textContent=available?data.ultimo.filename:'Nenhum relatório importado';
 $('last-update').textContent=date(data.atualizado);$('scan-time').textContent=date(API.local?data.scanned?.last_scan:new Date().toISOString());
 document.body.classList.remove('disconnected');
 $('metric-valor').textContent=available?shortMoney(data.valor_total):'—';$('metric-valor').title=money(data.valor_total);
 $('metric-itens').textContent=available?number(data.total_itens):'—';
 $('metric-zerados').textContent=available?number(data.status.zerado||0):'—';
 $('metric-criticos').textContent=available?number(data.status.critico||0):'—';
 $('metric-atencao').textContent=available?number(data.status.atencao||0):'—';
 $('metric-consumo').textContent=available?shortMoney(data.consumo_estimado_reais_mes):'—';
 $('history-count').textContent=data.history.length+' '+(data.history.length===1?'coleta':'coletas');
 drawTrend('trend-chart',data.history);drawTrend('history-chart-large',data.history);drawDonut(data.status,data.total_itens);
 const max=Math.max(1,...data.grupos.map(g=>g.valor));
 $('groups-content').innerHTML=data.grupos.length?data.grupos.slice(0,6).map(g=>`<div class="group-item"><div class="group-item-top"><span title="${escapeHTML(g.nome)}">${escapeHTML(g.nome)}</span><strong>${shortMoney(g.valor)}</strong></div><div class="group-track"><div style="width:${Math.min(100,g.valor/max*100)}%"></div></div></div>`).join(''):'<div class="table-empty">Aguardando relatório.</div>';
 $('history-rows').innerHTML=data.history.length?[...data.history].reverse().map(i=>`<tr><td>${date(i.file_modified)}</td><td>${escapeHTML(i.filename)}</td><td>${number(i.qtd)}</td><td>${money(i.valor)}</td><td>${number(i.zerados)}</td><td>${number(i.criticos)}</td></tr>`).join(''):'<tr><td colspan="6" class="table-empty">Ainda sem histórico.</td></tr>';
 $('events-rows').innerHTML=data.events.length?data.events.map(x=>`<tr><td class="nowrap">${date(x.created_at)}</td><td>${escapeHTML(x.file_name)}</td><td>${escapeHTML(x.status)}</td><td>${escapeHTML(x.message)}</td></tr>`).join(''):'<tr><td colspan="4" class="table-empty">Sem registros.</td></tr>';
 $('folder-path').textContent=data.pasta; $('health-last').textContent=date(data.scanned?.last_scan);$('health-files').textContent=number(data.scanned?.files_seen||0);$('health-imports').textContent=number(data.import_count);$('health-error').textContent=data.scanned?.last_scan_error||'Nenhuma';
 const select=$('group-filter'),selected=select.value;const opts=['<option value="">Todos os grupos</option>',...data.grupos.map(x=>`<option value="${escapeHTML(x.nome)}">${escapeHTML(x.nome)}</option>`)];select.innerHTML=opts.join('');if(data.grupos.some(g=>g.nome===selected))select.value=selected;
 if(API.local){
  $('origin-detail').textContent='SQLite · neste computador';
  $('connection-browser').textContent='Conectado ao serviço local';$('connection-db').textContent='SQLite ativo';$('connection-db').classList.remove('neutral');
  $('connection-files').textContent=data.scanned?.last_scan_error?'Requer atenção':data.ultimo?'Relatórios importados':'Aguardando arquivos';
  $('connection-files-note').textContent=data.scanned?.last_scan_error||'A pasta local é verificada automaticamente. Novas posições atualizam os indicadores.';
 }
 const limits=data.limites||{critico:7,atencao:30,excesso:120};
 document.querySelector('.accent-orange .metric-detail').textContent='Até '+limits.critico+' dias de cobertura';
 document.querySelector('.accent-yellow .metric-detail').textContent='Acima de '+limits.critico+' e até '+limits.atencao+' dias';
 $('risk-note').textContent='Críticos: saldo positivo e até '+limits.critico+' dias. Atenção: até '+limits.atencao+' dias. Excesso: acima de '+limits.excesso+' dias. Zerados são mostrados separadamente.';
}
async function displayItems(priority=false){
 const request=priority?++priorityRequest:++itemRequest;
 const chosen=priority?{q:'',status:'todos',grupo:''}:filters();
 const chosenPage=priority?0:page;
 const data=await getJSON('/api/items?'+new URLSearchParams({...chosen,limit:priority?'8':String(pageSize),offset:String(chosenPage*pageSize)}));
 if(priority){if(request!==priorityRequest)return;const urgent=data.rows.filter(x=>['zerado','critico','atencao'].includes(x.status));$('priority-rows').innerHTML=urgent.length?rowItems(urgent,true):'<tr><td colspan="5" class="table-empty">Nenhum material em alerta nesta consulta.</td></tr>';return;}
 if(request!==itemRequest||JSON.stringify(chosen)!==JSON.stringify(filters())||chosenPage!==page)return;
 totalItems=data.total;
 if(page>0&&!data.rows.length){page=Math.max(0,Math.ceil(totalItems/pageSize)-1);return displayItems();}
 $('material-rows').innerHTML=data.rows.length?rowItems(data.rows,false):'<tr><td colspan="8" class="table-empty">Nenhum material encontrado.</td></tr>';
 $('materials-count').textContent=data.total?`${number(page*pageSize+1)}–${number(page*pageSize+data.rows.length)} de ${number(data.total)} itens`:'Nenhum material encontrado';
 $('previous-page').disabled=page===0;$('next-page').disabled=(page+1)*pageSize>=totalItems;
}
function connectionError(ex){document.body.classList.add('disconnected');$('status-source').textContent='SEM CONEXÃO';$('connection-browser').textContent='Serviço indisponível';$('connection-db').textContent='Conexão não verificada';$('connection-files').textContent='Conexão não verificada';$('origin-detail').textContent='Serviço indisponível';flash('Não foi possível atualizar os dados. Os valores exibidos são da última consulta. '+ex.message);}
function refresh(){if(loading)return loading;loading=(async()=>{try{if(API.local)await API.connect();const s=await getJSON('/api/summary');displaySummary(s);await Promise.all([displayItems(true),displayItems(false)]);return true;}catch(ex){connectionError(ex);return false;}finally{loading=null;}})();return loading;}
function tab(name){active=name;document.querySelectorAll('.nav-item').forEach(x=>{x.classList.toggle('active',x.dataset.tab===name);if(x.dataset.tab===name)x.setAttribute('aria-current','page');else x.removeAttribute('aria-current');});document.querySelectorAll('.tab-section').forEach(x=>x.classList.toggle('active',x.id==='tab-'+name));const names={geral:['VISÃO EXECUTIVA','Central de Inteligência Logística','Estoque, cobertura e prioridades para acompanhar a operação.'],materiais:['ESTOQUE DETALHADO','Materiais e cobertura','Consulte saldos, consumo informado e níveis de atenção.'],historico:['INTELIGÊNCIA HISTÓRICA','Série histórica','Compare as posições de estoque preservadas pelo sistema.'],arquivos:['ENTRADA DE RELATÓRIOS','Central de importações',API.local?'Acompanhe a pasta monitorada e os arquivos recebidos.':'Conheça o fluxo de importação disponível na versão local.'],conexoes:['FONTES DE DADOS','Conexões do sistema','Veja de onde vêm os dados e quais conexões estão disponíveis.']};const [b,t,sub]=names[name];$('breadcrumb-current').textContent=b;$('page-title').innerHTML=escapeHTML(t)+'<span class="title-period">.</span>';$('page-subtitle').textContent=sub;window.scrollTo({top:0,behavior:'instant'});}
for(const btn of document.querySelectorAll('.nav-item'))btn.addEventListener('click',()=>tab(btn.dataset.tab));
for(const btn of document.querySelectorAll('[data-go]'))btn.addEventListener('click',()=>tab(btn.dataset.go));
$('rescan').addEventListener('click',async()=>{const btn=$('rescan');btn.disabled=true;btn.textContent='Atualizando…';try{if(loading)await loading;if(API.local){await API.connect();await getJSON('/api/rescan',{method:'POST'});}if(await refresh())flash(API.local?'Consulta atualizada. Confira o resultado na aba Importações.':'Demonstração atualizada. Os dados continuam fictícios.');}catch(ex){connectionError(ex)}finally{btn.disabled=false;btn.textContent=API.local?'Verificar relatórios':'Atualizar painel';}});
function changed(){page=0;displayItems(false).catch(connectionError);}
$('search').addEventListener('input',()=>{++itemRequest;clearTimeout(searchTimer);searchTimer=setTimeout(changed,250)});
$('status-filter').addEventListener('change',changed);$('group-filter').addEventListener('change',changed);
$('clear-filters').addEventListener('click',()=>{$('search').value='';$('status-filter').value='todos';$('group-filter').value='';changed();});
$('previous-page').addEventListener('click',()=>{if(page>0){page--;displayItems().catch(connectionError);}});
$('next-page').addEventListener('click',()=>{if((page+1)*pageSize<totalItems){page++;displayItems().catch(connectionError);}});
$('export').addEventListener('click',async()=>{const btn=$('export');btn.disabled=true;try{await API.exportCSV(filters());flash('CSV gerado com todos os resultados dos filtros atuais.');}catch(ex){flash(ex.message);}finally{btn.disabled=false;}});
$('upload').addEventListener('change',async event=>{const file=event.target.files?.[0];if(!file)return;event.target.disabled=true;try{if(!API.local)throw new Error('A importação está disponível somente na versão local.');if(!/\.(csv|xlsx|xls)$/i.test(file.name))throw new Error('Selecione um arquivo CSV, XLSX ou XLS.');if(!file.size||file.size>25*1024*1024)throw new Error('O arquivo deve ter conteúdo e no máximo 25 MB.');await API.connect();const result=await getJSON('/api/import?name='+encodeURIComponent(file.name),{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file});if(loading)await loading;if(await refresh())flash(result.message||'Relatório recebido. Confira o registro de importação.');}catch(ex){flash('Falha na importação: '+ex.message);}finally{event.target.value='';event.target.disabled=false;}});
if(API.local){
 $('mode-badge').textContent='OPERAÇÃO LOCAL';$('mode-note').textContent='Dados do servidor local · importação de relatórios · sem API direta do SALUX';
 $('origin-title').textContent='Relatórios locais';$('origin-detail').textContent='Verificando conexão…';$('origin-note').textContent='Os indicadores usam os arquivos importados neste computador. Não há envio para o site público.';
 $('rescan').textContent='Verificar relatórios';$('scan-label').textContent='Verificação:';$('upload-area').hidden=false;
 $('health-caption').textContent='Situação real da pasta e das coletas.';$('events-caption').textContent='Arquivos novos, repetidos e falhas ficam registrados.';
 $('connection-browser').textContent='Conectando…';$('connection-browser-note').textContent='Este navegador consulta o servidor aberto no próprio computador.';
 $('connection-db').textContent='Verificando…';$('connection-db-note').textContent='As posições são preservadas em SQLite no computador. A base não é publicada no GitHub.';
 $('footer-mode').textContent='Dados locais · conexão por relatórios';
}else{
 document.body.classList.add('demo-mode');$('import-title').textContent='Como importar na versão local';$('folder-path').textContent='Nenhuma pasta conectada nesta demonstração';
 $('import-instruction').textContent='Esta página pública não recebe relatórios reais. Para importar, abra a versão local pelo iniciador disponível no repositório.';
}
refresh().then(ok=>{if(ok&&API.local)$('origin-detail').textContent='SQLite · neste computador';});
if(API.local)setInterval(refresh,30000);
})();
