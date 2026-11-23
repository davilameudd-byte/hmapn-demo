/* O modo local é declarado pelo servidor. O site público nunca procura servidores locais. */
(() => {
  'use strict';
  const local = document.querySelector('meta[name="hmapn-mode"]')?.content === 'local';
  let token = '', health = null;
  const clone = value => JSON.parse(JSON.stringify(value));
  const folded = value => String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  function demoItems(params) {
    const data = window.HMAPN_DEMO.items, q = folded(params.get('q')), status = params.get('status');
    let rows = data.rows.filter(row => (!q || folded(`${row.descricao} ${row.material} ${row.grupo}`).includes(q)) && (!status || status === 'todos' || row.status === status) && (!params.get('grupo') || row.grupo === params.get('grupo')));
    const order = {zerado:0, critico:1, atencao:2, normal:3, excesso:4, sem_consumo:5};
    rows.sort((a,b) => order[a.status]-order[b.status] || (a.dias ?? Infinity)-(b.dias ?? Infinity) || a.material.localeCompare(b.material));
    const total = rows.length, offset = Math.max(0, Number(params.get('offset')) || 0), limit = Math.min(10000, Math.max(1, Number(params.get('limit')) || 100));
    return clone({rows:rows.slice(offset, offset + limit),total,latest:data.latest,offset});
  }
  async function remote(url, options = {}) {
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 45000);
    try {
      const response = await fetch(url, {...options,cache:'no-store',credentials:'same-origin',signal:controller.signal,headers:{...options.headers,...(token ? {'X-HMAPN-Token':token} : {})}});
      const data = await response.json();
      if (!response.ok) throw new Error(data.erro || data.error || `Falha na consulta (${response.status}).`);
      return data;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('A consulta demorou demais. Verifique se o serviço local está aberto.');
      throw error;
    } finally { clearTimeout(timeout); }
  }
  async function connect() {
    if (!local) return {ok:true,mode:'demo'};
    health = await remote('/api/health');
    if (!health.ok || health.mode !== 'local' || !health.csrfToken) throw new Error('O servidor local não corresponde a esta versão do painel.');
    token = health.csrfToken;
    return health;
  }
  async function request(url, options = {}) {
    if (local) { if (!token) await connect(); return remote(url, options); }
    const parsed = new URL(url, 'https://demo.invalid');
    if (parsed.pathname === '/api/summary') return clone(window.HMAPN_DEMO.summary);
    if (parsed.pathname === '/api/items') return demoItems(parsed.searchParams);
    if (parsed.pathname === '/api/health') return {ok:true,mode:'demo'};
    if (parsed.pathname === '/api/rescan') return {ok:true,mode:'demo'};
    throw new Error('Esta ação está disponível somente na versão local.');
  }
  function download(blob, filename) {
    const href = URL.createObjectURL(blob), link = document.createElement('a');
    link.href = href; link.download = filename; document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(href), 10000);
  }
  function csvCell(value) {
    let text = String(value ?? '');
    if (/^[=+@\-\t\r]/.test(text.trimStart())) text = "'" + text;
    return '"' + text.replaceAll('"','""') + '"';
  }
  async function exportCSV(filters) {
    if (local) {
      await connect();
      const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 45000);
      try {
        const response = await fetch('/api/export?' + new URLSearchParams(filters), {cache:'no-store',credentials:'same-origin',signal:controller.signal,headers:{'X-HMAPN-Token':token}});
        if (!response.ok) throw new Error('Não foi possível exportar os materiais.');
        download(await response.blob(), 'HMAPN-materiais.csv'); return;
      } catch (error) {
        if (error.name === 'AbortError') throw new Error('A exportação demorou demais. Verifique o serviço local e tente novamente.');
        throw error;
      } finally { clearTimeout(timeout); }
    }
    const data = demoItems(new URLSearchParams({...filters,limit:'10000'}));
    const cols = ['Código','Descrição','Grupo','Unidade','Saldo','CMD','CMM estimado','Cobertura em dias','Valor em estoque','Situação','Origem'];
    const numeric = n => n == null ? '' : String(n).replace('.',',');
    const rows = data.rows.map(i => [i.material,i.descricao,i.grupo,i.unidade,numeric(i.saldo),numeric(i.cmd),numeric(i.cmm_estimado),numeric(i.dias),numeric(i.valor),i.status,'DEMONSTRAÇÃO FICTÍCIA']);
    download(new Blob(['\uFEFF' + [cols,...rows].map(row => row.map(csvCell).join(';')).join('\r\n')], {type:'text/csv;charset=utf-8'}), 'HMAPN-exemplo-ficticio.csv');
  }
  window.HMAPN_API = Object.freeze({local,connect,request,exportCSV,get health(){return health;}});
})();
