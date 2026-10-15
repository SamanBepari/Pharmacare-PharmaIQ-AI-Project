/* PharmaIQ chat UI — all dynamic content is inserted with textContent (never innerHTML) so
   database values or document text can never inject markup (XSS-safe). */
(function () {
  const $ = (s, r = document) => r.querySelector(s);
  const CSRF = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
  const thread = $('#thread'), form = $('#askForm'), input = $('#question'), sendBtn = $('#sendBtn');
  if (!thread) return;
  let convId = null, charts = [];

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }
  // **bold** and [n] only — built with DOM nodes, not HTML strings
  function richText(parent, text) {
    String(text || '').split(/(\*\*[^*]+\*\*)/g).forEach(part => {
      if (/^\*\*[^*]+\*\*$/.test(part)) parent.appendChild(el('strong', null, part.slice(2, -2)));
      else parent.appendChild(document.createTextNode(part));
    });
  }
  function scroll() { thread.scrollTop = thread.scrollHeight; }

  function addUser(text) {
    const m = el('div', 'msg user'); m.appendChild(el('div', 'bubble', text)); thread.appendChild(m); scroll();
  }
  function addTyping() {
    const m = el('div', 'msg bot'); const b = el('div', 'bubble typing');
    b.append(el('span'), el('span'), el('span')); m.appendChild(b); thread.appendChild(m); scroll(); return m;
  }

  function buildTable(t) {
    const wrap = el('div', 'result-table mb-2'), table = el('table', 'table table-sm table-hover');
    const thead = el('thead'), hr = el('tr');
    t.columns.forEach(c => hr.appendChild(el('th', null, c.replace(/_/g, ' '))));
    thead.appendChild(hr); table.appendChild(thead);
    const tb = el('tbody');
    t.rows.forEach(r => { const tr = el('tr'); r.forEach(v => tr.appendChild(el('td', null, v === null ? '—' : String(v)))); tb.appendChild(tr); });
    if (!t.rows.length) { const tr = el('tr'), td = el('td', 'text-muted text-center', 'No rows'); td.colSpan = t.columns.length; tr.appendChild(td); tb.appendChild(tr); }
    table.appendChild(tb); wrap.appendChild(table); return wrap;
  }

  function buildChart(spec) {
    const wrap = el('div', 'chart-wrap mb-2'), canvas = document.createElement('canvas'); wrap.appendChild(canvas);
    const palette = ['#1a6b3c', '#2e8b57', '#0d6efd', '#fd7e14', '#6f42c1', '#dc3545', '#20c997', '#ffc107'];
    setTimeout(() => {
      const isLine = spec.type === 'line';
      const ds = spec.datasets.map((d, i) => ({
        label: d.label, data: d.data, borderColor: palette[i % palette.length],
        backgroundColor: isLine ? 'transparent' : palette[i % palette.length] + 'cc',
        borderDash: d.dashed ? [5, 4] : [], tension: .25, pointRadius: isLine ? 2 : 0, spanGaps: false
      }));
      charts.push(new Chart(canvas, { type: spec.type, data: { labels: spec.labels, datasets: ds },
        options: { indexAxis: spec.horizontal ? 'y' : 'x', maintainAspectRatio: false, responsive: true,
                   plugins: { legend: { display: ds.length > 1 } } } }));
    }, 0);
    return wrap;
  }

  function section(label, cls, icon) {
    const l = el('div', 'sect-label ' + cls); l.appendChild(el('i', 'bi ' + icon)); l.appendChild(document.createTextNode(label)); return l;
  }

  function addBot(r) {
    const m = el('div', 'msg bot'), b = el('div', 'bubble'); m.appendChild(b);

    if (r.route === 'refused' || r.route === 'error' || r.route === 'clarify') {
      const box = el('div', r.route === 'refused' ? 'refuse-box' : 'warn-box'); richText(box, r.answer);
      b.appendChild(box);
    } else if (r.route === 'rag') {
      b.appendChild(section('From documents', 'sect-doc', 'bi-book'));
      const p = el('div', 'mb-2', ''); r.answer.split('\n\n').forEach(par => { const d = el('div', 'mb-2'); richText(d, par); p.appendChild(d); });
      b.appendChild(p);
      if (r.sources && r.sources.length) {
        b.appendChild(section('Sources', 'sect-doc', 'bi-quote'));
        r.sources.forEach(s => {
          const c = el('div', 'source-card'), h = el('div', 'fw-semibold');
          h.appendChild(el('span', 'n', s.n)); h.appendChild(document.createTextNode(s.title + (s.section ? ' › ' + s.section : '') + (s.page ? ' · p.' + s.page : '')));
          c.appendChild(h); c.appendChild(el('div', 'text-muted mt-1', s.snippet.length > 230 ? s.snippet.slice(0, 230) + '…' : s.snippet));
          b.appendChild(c);
        });
      }
    } else if (r.route === 'help') {
      const d = el('div'); r.answer.split('\n').forEach(line => { const x = el('div', 'mb-1'); richText(x, line); d.appendChild(x); }); b.appendChild(d);
      if (r.facts && r.facts.length) b.appendChild(el('div', 'assume mt-2', r.facts[0]));
    } else {
      // data answer
      b.appendChild(section('Database facts' + (r.evidence && r.evidence.as_of ? ' · as of ' + r.evidence.as_of : ''), 'sect-db', 'bi-database-check'));
      if (r.title) b.appendChild(el('div', 'fw-semibold mb-1', r.title));
      if (r.facts && r.facts.length) { const ul = el('ul', 'facts mb-2 ps-4'); r.facts.forEach(f => ul.appendChild(el('li', null, f))); b.appendChild(ul); }
      if (r.chart) b.appendChild(buildChart(r.chart));
      (r.evidence && r.evidence.tables || []).forEach(t => {
        if (r.evidence.tables.length > 1) b.appendChild(el('div', 'small fw-semibold mt-2', t.title));
        b.appendChild(buildTable(t));
        if (t.truncated) b.appendChild(el('div', 'assume', 'Showing the first rows only — result was truncated at the row limit.'));
      });
      if (r.assumptions && r.assumptions.length) r.assumptions.forEach(a => b.appendChild(el('div', 'assume', '• ' + a)));
      if (r.warnings && r.warnings.length) { const w = el('div', 'warn-box mt-2'); r.warnings.forEach(x => w.appendChild(el('div', null, '⚠ ' + x))); b.appendChild(w); }
      if (r.explanation && r.explanation.text) {
        const wrap = el('div', 'mt-2');
        wrap.appendChild(section('Explanation · ' + (r.explanation.generated_by === 'rules' ? 'rule-based' : 'AI-generated (' + r.explanation.generated_by + ')'), 'sect-ai', 'bi-stars'));
        wrap.appendChild(el('div', 'explain', r.explanation.text)); b.appendChild(wrap);
      }
      // evidence: SQL + download
      const tools = el('div', 'd-flex flex-wrap gap-2 mt-2 align-items-center');
      if (r.evidence && r.evidence.tables && r.evidence.tables[0] && r.evidence.tables[0].sql) {
        const id = 'sql' + Math.random().toString(36).slice(2, 8);
        const btn = el('button', 'btn btn-sm btn-outline-secondary'); btn.type = 'button'; btn.setAttribute('data-bs-toggle', 'collapse'); btn.setAttribute('data-bs-target', '#' + id);
        btn.appendChild(el('i', 'bi bi-code-slash me-1')); btn.appendChild(document.createTextNode('Show query'));
        tools.appendChild(btn);
        const box = el('div', 'collapse w-100'); box.id = id;
        const pre = el('div', 'sql-box mt-1', r.evidence.tables.map(t => '-- ' + t.title + '\n' + t.sql.replace(/\s+/g, ' ') + (t.params && Object.keys(t.params).length ? '\n-- params: ' + JSON.stringify(t.params) : '')).join('\n\n'));
        box.appendChild(pre); b.appendChild(tools); b.appendChild(box);
      } else b.appendChild(tools);
      if (r.evidence && r.evidence.result_id) {
        ['xlsx', 'csv'].forEach(fmt => {
          const a = el('a', 'btn btn-sm btn-outline-success'); a.href = '/api/ai/export/' + r.evidence.result_id + '.' + fmt;
          a.appendChild(el('i', 'bi bi-download me-1')); a.appendChild(document.createTextNode(fmt.toUpperCase())); tools.appendChild(a);
        });
      }
    }
    if (r.warnings && r.warnings.length && r.route === 'rag') { const w = el('div', 'warn-box mt-2'); r.warnings.forEach(x => w.appendChild(el('div', null, '⚠ ' + x))); b.appendChild(w); }
    if (r.disclaimer) b.appendChild(el('div', 'assume mt-2 fst-italic', r.disclaimer));
    if (r.followups && r.followups.length) {
      const f = el('div', 'mt-2'); r.followups.forEach(q => { const c = el('span', 'chip', q); c.onclick = () => ask(q); f.appendChild(c); }); b.appendChild(f);
    }
    b.appendChild(el('div', 'assume mt-2 text-end', (r.mode || '') + ' · ' + (r.latency_ms || 0) + ' ms' + (r.is_followup ? ' · follow-up' : '')));
    thread.appendChild(m); scroll();
  }

  async function ask(q) {
    q = (q || input.value).trim(); if (!q) return;
    input.value = ''; addUser(q); const typing = addTyping(); sendBtn.disabled = true;
    try {
      const res = await fetch('/api/ai/ask', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF },
                                               body: JSON.stringify({ question: q, conv_id: convId }) });
      const data = await res.json(); typing.remove();
      if (!res.ok) { addBot({ route: 'error', answer: data.message || data.error || 'Request failed.' }); }
      else { convId = data.conv_id; addBot(data); }
    } catch (e) { typing.remove(); addBot({ route: 'error', answer: 'Could not reach the server.' }); }
    sendBtn.disabled = false; input.focus();
  }

  form.addEventListener('submit', e => { e.preventDefault(); ask(); });
  document.querySelectorAll('[data-q]').forEach(c => c.addEventListener('click', () => ask(c.dataset.q)));
  const nc = $('#newChat'); if (nc) nc.addEventListener('click', () => { convId = null; charts.forEach(c => c.destroy()); charts = []; thread.replaceChildren(); addBot(welcome()); });
  function welcome() { return { route: 'help', answer: "Hi! I'm **PharmaIQ**. Ask about stock, expiry and sales (live database) or about leaflets and SOPs (documents).", facts: [], followups: ['Which medicines are expiring within the next 30 days?', 'Show all products with fewer than 10 units', 'What is the return policy?'] }; }
  addBot(welcome());
  input.focus();
})();
