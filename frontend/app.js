// Modern UI + resizable table + CN beijing tone. Franglish (ASCII only)
document.addEventListener('DOMContentLoaded', () => {
  const API_BASE = localStorage.getItem('API_BASE') ||
    (location.protocol === 'file:' || (location.port && location.port !== '5000') ? 'http://127.0.0.1:5000' : '');

  const form = document.getElementById('crawlForm');
  const resultsDiv = document.getElementById('results');
  const trainSection = document.getElementById('trainSection');
  const lastBox = document.getElementById('lastBox');

  // toolbar controls
  const btnAutoCols = document.getElementById('btnAutoCols');
  const btnResetCols = document.getElementById('btnResetCols');
  const toggleDense = document.getElementById('toggleDense');

  // state
  let currentArticles = [];
  let currentRunId = null;
  let currentPayloadMeta = { keywords: [], media_names: [] };

  // last review hint
  if (getLastReview()) showLastHint();

  document.getElementById('btnReset').onclick = () => {
    form.reset();
    trainSection.style.display = 'none';
  };

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    infoCard('在爬了，在爬了……');
    trainSection.style.display = 'none';
    currentArticles = [];
    currentRunId = null;

    const payload = collectPayload();
    currentPayloadMeta = { keywords: payload.keywords, media_names: payload.media_names };

    try {
      const res = await fetch(`${API_BASE}/crawl`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const text = await res.text();
      let data = {}; try { data = JSON.parse(text); } catch { data = { error: text }; }
      if (!res.ok) throw new Error(data.error || '后端说累了（500）');

      currentRunId = data.run_id;
      currentArticles = (data.items || []).map(a => ({
        ...a,
        human_label: typeof a.predicted_label === 'number' ? a.predicted_label : 1
      }));

      if (document.getElementById('strict_keywords').checked) {
        const kws = new Set((payload.keywords || []).filter(Boolean));
        currentArticles = currentArticles.filter(a => {
          const t = (a.title || '') + ' ' + (a.excerpt || '');
          return [...kws].some(k => k && t.includes(k));
        });
      }

      renderResults(currentArticles, { readonly:false });
      renderTrainerUI();

      // enable toolbar for table width
      wireTableToolbar();
    } catch (err) {
      errorCard('抓取失败：' + esc(err.message || String(err)) + '。F12 看 /crawl 的 trace 一眼就知道哪儿绊倒了。');
    }
  });

  function collectPayload(){
    const kw = getListValue('keywords');
    const mn = getListValue('media_names');
    const sd = val('start_date') || null;
    const ed = val('end_date') || null;
    const ua = document.getElementById('use_advanced').checked;
    const wx = document.getElementById('allow_wechat').checked;
    return { keywords: kw, media_names: mn, start_date: sd, end_date: ed, use_advanced: ua, allow_wechat: wx };
  }

  function renderTrainerUI(){
    trainSection.innerHTML = `
      <div style="margin-top:12px; display:flex; align-items:center; gap:8px; flex-wrap:wrap">
        <button id="btnAllYes" class="btn">都判“相关”</button>
        <button id="btnAllNo" class="btn">都判“无关”</button>
        <button id="btnSubmit" class="btn primary">给机器也学学</button>
        <button id="btnShowLast" class="btn">瞅瞅上回那茬</button>
        <span id="submitMsg" class="muted" style="margin-left:6px"></span>
      </div>`;
    document.getElementById('btnAllYes').onclick = () => bulkSet(1);
    document.getElementById('btnAllNo').onclick = () => bulkSet(0);
    document.getElementById('btnSubmit').onclick = submitReview;
    document.getElementById('btnShowLast').onclick = showLastSaved;
    trainSection.style.display = 'block';
  }

  async function submitReview(){
    if (!currentRunId || currentArticles.length === 0) return;
    document.querySelectorAll('.lblsel').forEach(sel => {
      const idx = parseInt(sel.dataset.idx); currentArticles[idx].human_label = parseInt(sel.value);
    });
    const payload = {
      run_id: currentRunId,
      items: currentArticles.map(a => ({
        title:a.title, url:a.url, source:a.source, date:a.date,
        excerpt:a.excerpt, predicted_label:a.predicted_label, human_label:a.human_label
      })),
      keywords: currentPayloadMeta.keywords,
      media_names: currentPayloadMeta.media_names
    };
    const msg = document.getElementById('submitMsg');
    msg.textContent = '交卷喽！';
    try{
      const res = await fetch(`${API_BASE}/review`, {
        method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)
      });
      const data = await res.json();
      if(!res.ok) throw new Error(data.error || '后端没接住（review）');

      const relevant = currentArticles.filter(a => parseInt(a.human_label) === 1).map(a => ({
        title:a.title, url:a.url, source:a.source, date:a.date, excerpt:a.excerpt
      }));
      saveLastReview({
        ts:Date.now(), run_id: currentRunId,
        keywords: currentPayloadMeta.keywords, media_names: currentPayloadMeta.media_names,
        csv_url: data.csv_url || null, count_saved: parseInt(data.saved || 0), items: relevant
      });

      msg.innerHTML = `妥了，收录 ${data.saved} 条。${data.csv_url ? `<a href="${API_BASE}${data.csv_url}" target="_blank">CSV</a>`:''}
        &nbsp;|&nbsp;<a href="#" id="aShowLast">查看上次提交</a>`;
      const a = document.getElementById('aShowLast'); if(a) a.onclick = (ev)=>{ev.preventDefault(); showLastSaved();};
      showLastHint();
    }catch(err){
      msg.textContent = err.message || String(err);
    }
  }

  function bulkSet(v){ document.querySelectorAll('.lblsel').forEach(sel => sel.value = String(v)); }

  function renderResults(items, opts={readonly:false}){
    if(!items || items.length===0){ infoCard('空的:(换组词儿再来一遍吧'); return; }
    const readonly = !!opts.readonly;

    const headerCells = readonly
      ? ['#','标题 / 链接','来源','日期','节选','通道']
      : ['#','标题 / 链接','来源','日期','节选','预测','人工','通道'];

    const header = headerCells.map((h,i)=>`<th data-col="${i}">${esc(h)}<span class="col-resizer" draggable="false"></span></th>`).join('');

    const rows = items.map((a,i)=>{
      const base = `
        <td style="width:48px">${i+1}</td>
        <td class="url"><a href="${a.url}" target="_blank" class="badge green">${esc(a.title||'')}</a><br>
          <small>${esc(a.url||'')}</small></td>
        <td>${esc(a.source||'')}</td>
        <td>${esc(a.date||'')}</td>
        <td>${esc(a.excerpt||'')}</td>`;
      if(readonly){
        return `<tr>${base}<td>${esc(a.channel||'')}</td></tr>`;
      }
      return `<tr>${base}
        <td>${a.predicted_label ?? ''}</td>
        <td><select class="lblsel" data-idx="${i}">
              <option value="1" ${parseInt(a.human_label)===1?'selected':''}>相关(1)</option>
              <option value="0" ${parseInt(a.human_label)===0?'selected':''}>无关(0)</option>
            </select></td>
        <td>${esc(a.channel||'')}</td>
      </tr>`;
    }).join('');

    resultsDiv.innerHTML = `
      <div class="table-wrap" id="tableWrap">
        <table id="resultTable">
          <thead><tr>${header}</tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;

    // resizable + autosize wire
    setupResizableTable(document.getElementById('resultTable'));
  }

  // ----- table toolbar -----
  function wireTableToolbar(){
    const table = document.getElementById('resultTable');
    if(!table) return;
    btnAutoCols.onclick = ()=>autoSizeTable(table);
    btnResetCols.onclick = ()=>resetTableCols(table);
    toggleDense.onchange = ()=> {
      const wrap = document.getElementById('tableWrap');
      if(!wrap) return;
      if(toggleDense.checked) wrap.classList.add('compact'); else wrap.classList.remove('compact');
    };
  }

  // ----- resizable columns -----
  function setupResizableTable(table){
    const ths = table.querySelectorAll('thead th');
    ths.forEach((th, idx) => {
      const handle = th.querySelector('.col-resizer');
      let startX=0, startW=0;
      const onDown = (e)=>{
        e.preventDefault();
        startX = e.clientX;
        startW = th.getBoundingClientRect().width;
        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
      };
      const onMove = (e)=>{
        const dx = e.clientX - startX;
        const w = Math.max(90, startW + dx);
        setColWidth(table, idx+1, w);
      };
      const onUp = ()=>{
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
      };
      handle.addEventListener('mousedown', onDown);
    });
    // initial autosize
    autoSizeTable(table);
  }

  function setColWidth(table, colIndex, px){
    // set width on this column (th + all td)
    table.querySelectorAll(`thead tr th:nth-child(${colIndex})`).forEach(el => el.style.width = px+'px');
    table.querySelectorAll(`tbody tr td:nth-child(${colIndex})`).forEach(el => el.style.width = px+'px');
  }

  function resetTableCols(table){
    table.querySelectorAll('thead th, tbody td').forEach(el => el.style.width = '');
    autoSizeTable(table);
  }

  function autoSizeTable(table){
    // heuristic: measure header + first N rows, clamp [120, 520]
    const ctx = document.createElement('canvas').getContext('2d');
    ctx.font = getComputedStyle(document.body).font;
    const ths = table.querySelectorAll('thead th');
    const rows = [...table.querySelectorAll('tbody tr')].slice(0, 20);

    const widths = [];
    ths.forEach((th, i)=>{
      let w = textW(ctx, th.textContent || '') + 28; // header
      rows.forEach(r=>{
        const cell = r.children[i];
        if(!cell) return;
        const txt = (cell.innerText || '').slice(0, 120);
        w = Math.max(w, textW(ctx, txt) * 0.78 + 28);
      });
      widths.push(Math.max(120, Math.min(520, Math.round(w))));
    });
    widths.forEach((w,i)=> setColWidth(table, i+1, w));
  }

  function textW(ctx, s){ return ctx.measureText((s||'').replace(/\s+/g,' ')).width; }

  // ----- info / error cards -----
  function infoCard(msg){ resultsDiv.innerHTML = `<div class="card">${esc(msg)}</div>`; }
  function errorCard(msg){ resultsDiv.innerHTML = `<div class="card" style="color:#ffb0b0">${esc(msg)}</div>`; }

  // ----- last review -----
  function saveLastReview(o){ try{ localStorage.setItem('LAST_REVIEW', JSON.stringify(o)); }catch{} }
  function getLastReview(){ try{ const raw=localStorage.getItem('LAST_REVIEW'); return raw?JSON.parse(raw):null; }catch{return null} }
  function showLastSaved(){
    const last=getLastReview(); if(!last||!Array.isArray(last.items)||!last.items.length){ alert('上回啥也没留。');return; }
    renderResults(last.items,{readonly:true});
    const meta = `<div class="card" style="margin-top:8px">
      <div>run_id: ${esc(last.run_id||'')}</div>
      <div>关键词: ${esc((last.keywords||[]).join(', '))}</div>
      <div>媒体: ${esc((last.media_names||[]).join(', '))}</div>
      ${last.csv_url ? `<div><a href="${API_BASE}${last.csv_url}" target="_blank">CSV 下载</a></div>`:''}
    </div>`;
    resultsDiv.insertAdjacentHTML('beforeend', meta);
    renderTrainerUI();
    wireTableToolbar();
  }
  function showLastHint(){
    const last=getLastReview(); if(!last) return;
    lastBox.innerHTML = `<span class="muted">上回：${new Date(last.ts||Date.now()).toLocaleString()}
      &nbsp;<a href="#" id="aShowLastTop">瞅一眼</a> ${last.csv_url?`&nbsp;|&nbsp;<a href="${API_BASE}${last.csv_url}" target="_blank">CSV</a>`:''}</span>`;
    const a=document.getElementById('aShowLastTop'); if(a) a.onclick=(ev)=>{ev.preventDefault(); showLastSaved();};
  }

  // utils
  function esc(s){return (s||'').replace(/[&<>'"]/g,c=>({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
  function val(id){const el=document.getElementById(id);return el?el.value.trim():'';}
  function getListValue(id){return val(id).split(',').map(x=>x.trim()).filter(Boolean);}
});
