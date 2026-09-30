"""The dashboard (single self-contained page, vanilla JS + inline SVG — no build step).

Two views, hash-routed:
  * `#/`         — watchlist home: a card grid across all companies (price, P/E,
                   reverse-DCF implied growth, cheap/fair/expensive verdict, alert
                   badge) plus the cross-company alert feed.
  * `#/TICKER`   — company detail: Overview (headline + SVG trend charts + market &
                   valuation), Statements, Comps, Filings, Ask (chat).

Charts are hand-drawn SVG so the UI needs no CDN and works fully offline. Every
figure traces to the cited JSON API.
"""

DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Equity Research</title>
<style>
  :root { --bg:#0f1115; --card:#181b22; --card2:#1e222b; --line:#262b36; --txt:#e6e8ec;
          --dim:#9aa3b2; --accent:#5b8def; --warn:#e0a93b; --high:#e0533b; --info:#4b9b6e;
          --cheap:#4b9b6e; --exp:#e0533b; --fair:#9aa3b2; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--txt);
         font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif; }
  a { color:var(--accent); text-decoration:none; } a:hover { text-decoration:underline; }
  header { padding:14px 24px; border-bottom:1px solid var(--line); display:flex;
           gap:14px; align-items:center; position:sticky; top:0; background:var(--bg); z-index:5; }
  h1 { font-size:16px; margin:0; font-weight:600; cursor:pointer; }
  .crumb { color:var(--dim); }
  button, input, select { background:var(--card); color:var(--txt);
           border:1px solid var(--line); border-radius:8px; padding:8px 12px; font-size:14px; }
  button { cursor:pointer; } button:hover { border-color:var(--accent); }
  main { max-width:1140px; margin:0 auto; padding:20px 24px 60px; }
  h2 { font-size:13px; color:var(--dim); text-transform:uppercase; letter-spacing:.05em;
       margin:24px 0 10px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:12px; }
  .cards { display:grid; grid-template-columns:repeat(auto-fill,minmax(260px,1fr)); gap:14px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; }
  .co-card { cursor:pointer; transition:border-color .12s, transform .12s; }
  .co-card:hover { border-color:var(--accent); transform:translateY(-2px); }
  .co-head { display:flex; justify-content:space-between; align-items:flex-start; }
  .tk { font-size:18px; font-weight:700; } .nm { color:var(--dim); font-size:12px; }
  .label { color:var(--dim); font-size:11px; text-transform:uppercase; letter-spacing:.04em; }
  .big { font-size:24px; font-weight:600; margin-top:4px; }
  .stat { display:flex; gap:18px; flex-wrap:wrap; margin-top:12px; }
  .stat .v { font-weight:600; } .sub { color:var(--dim); font-size:12px; margin-top:2px; }
  .tag { font-size:11px; padding:2px 9px; border-radius:999px; font-weight:600; text-transform:uppercase; }
  .tag.cheap{background:rgba(75,155,110,.18);color:var(--cheap);}
  .tag.expensive{background:rgba(224,83,59,.18);color:var(--exp);}
  .tag.fair{background:rgba(154,163,178,.15);color:var(--fair);}
  table { width:100%; border-collapse:collapse; }
  td,th { text-align:left; padding:6px 10px; border-bottom:1px solid var(--line); font-size:13px; }
  th { color:var(--dim); font-weight:500; } td.num,th.num { text-align:right; font-variant-numeric:tabular-nums; }
  .alert { border-left:3px solid var(--info); padding:9px 14px; margin-bottom:8px;
           background:var(--card); border-radius:0 8px 8px 0; display:flex; justify-content:space-between; gap:12px; }
  .alert.warn{border-color:var(--warn);} .alert.high{border-color:var(--high);}
  .alert .k{color:var(--dim);font-size:12px;} .alert button{padding:4px 10px;font-size:12px;}
  .tabs{display:flex;gap:6px;margin:18px 0 6px;flex-wrap:wrap;}
  .tab{padding:7px 14px;border-radius:8px;cursor:pointer;color:var(--dim);border:1px solid transparent;}
  .tab.active{color:var(--txt);background:var(--card);border-color:var(--line);}
  .chat-log{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;min-height:70px;white-space:pre-wrap;}
  .row{display:flex;gap:8px;margin-top:10px;} .row input{flex:1;}
  .muted{color:var(--dim);} .hidden{display:none;} .pill{display:inline-block;font-size:11px;padding:2px 8px;border-radius:999px;background:#222733;color:var(--dim);margin-left:8px;}
  .badge{background:var(--high);color:#fff;border-radius:999px;font-size:11px;padding:1px 7px;}
  .charts{display:grid;grid-template-columns:1fr 1fr;gap:12px;} @media(max-width:760px){.charts{grid-template-columns:1fr;}}
  svg text{fill:var(--dim);font-size:10px;}
</style>
</head>
<body>
<header>
  <h1 onclick="location.hash='#/'">Equity Research</h1>
  <span class="crumb" id="crumb"></span>
  <span style="flex:1"></span>
  <button id="refresh">↻ Refresh alerts</button>
</header>
<main id="app"></main>

<script>
const fmt = v => { if(v==null) return '—'; const a=Math.abs(v);
  if(a>=1e9) return (v/1e9).toFixed(2)+'B'; if(a>=1e6) return (v/1e6).toFixed(2)+'M'; return (+v).toFixed(2); };
const pct = v => v==null?'—':(v*100).toFixed(1)+'%';
const usd = v => v==null?'—':'$'+(+v).toFixed(2);
async function j(u,o){ const r=await fetch(u,o); if(!r.ok) throw new Error((await r.json()).detail||r.status); return r.json(); }

/* ---- inline SVG charts (no deps) ---- */
function barLine(labels, bars, line, barColor, lineColor){
  const W=320,H=150,P=24, n=labels.length; if(!n) return '';
  const bmax=Math.max(...bars.filter(x=>x!=null),1);
  const lvals=line.filter(x=>x!=null); const lmax=Math.max(...lvals,1), lmin=Math.min(...lvals,0);
  const bw=(W-2*P)/n*0.6, step=(W-2*P)/n;
  const x=i=>P+step*i+step*0.2, yb=v=>H-P-(v/bmax)*(H-2*P), yl=v=>H-P-((v-lmin)/(lmax-lmin||1))*(H-2*P);
  let s=`<svg viewBox="0 0 ${W} ${H}" width="100%">`;
  bars.forEach((v,i)=>{ if(v==null)return; s+=`<rect x="${x(i)}" y="${yb(v)}" width="${bw}" height="${H-P-yb(v)}" fill="${barColor}" rx="2"/>`; });
  let pts=line.map((v,i)=>v==null?null:`${x(i)+bw/2},${yl(v)}`).filter(Boolean);
  if(pts.length>1) s+=`<polyline points="${pts.join(' ')}" fill="none" stroke="${lineColor}" stroke-width="2"/>`;
  line.forEach((v,i)=>{ if(v!=null) s+=`<circle cx="${x(i)+bw/2}" cy="${yl(v)}" r="2.5" fill="${lineColor}"/>`; });
  labels.forEach((l,i)=>{ s+=`<text x="${x(i)+bw/2}" y="${H-8}" text-anchor="middle">${String(l).slice(-2)}</text>`; });
  return s+'</svg>';
}
function multiLine(labels, series){
  const W=320,H=150,P=24, n=labels.length; if(!n) return '';
  const all=series.flatMap(s=>s.vals.filter(x=>x!=null)); const mx=Math.max(...all,0.01), mn=Math.min(...all,0);
  const step=(W-2*P)/(n>1?n-1:1), x=i=>P+step*i, y=v=>H-P-((v-mn)/(mx-mn||1))*(H-2*P);
  let s=`<svg viewBox="0 0 ${W} ${H}" width="100%">`;
  series.forEach(se=>{ let pts=se.vals.map((v,i)=>v==null?null:`${x(i)},${y(v)}`).filter(Boolean);
    if(pts.length>1) s+=`<polyline points="${pts.join(' ')}" fill="none" stroke="${se.color}" stroke-width="2"/>`;
    se.vals.forEach((v,i)=>{ if(v!=null) s+=`<circle cx="${x(i)}" cy="${y(v)}" r="2.5" fill="${se.color}"/>`; }); });
  labels.forEach((l,i)=>{ s+=`<text x="${x(i)}" y="${H-8}" text-anchor="middle">${String(l).slice(-2)}</text>`; });
  s+=series.map((se,i)=>`<text x="${P+i*90}" y="14" fill="${se.color}">● ${se.name}</text>`).join('');
  return s+'</svg>';
}

/* ---- router ---- */
function router(){
  const h=location.hash.replace('#/','').trim();
  document.getElementById('crumb').textContent='';
  if(h) renderCompany(h); else renderHome();
}
window.addEventListener('hashchange', router);

document.getElementById('refresh').onclick = async () => {
  const b=document.getElementById('refresh'); b.textContent='↻ Refreshing…';
  try { await j('/api/refresh',{method:'POST'}); router(); } finally { b.textContent='↻ Refresh alerts'; }
};

/* ---- home (watchlist) ---- */
async function renderHome(){
  const app=document.getElementById('app');
  app.innerHTML='<h2>Watchlist</h2><div id="cards" class="cards muted">Loading…</div><h2>Alert feed <span id="unread"></span></h2><div id="feed" class="muted">Loading…</div>';
  let w; try { w=await j('/api/watchlist'); } catch(e){ document.getElementById('cards').textContent=e.message; return; }
  document.getElementById('cards').innerHTML = w.companies.map(c=>`
    <div class="card co-card" onclick="location.hash='#/${c.ticker}'">
      <div class="co-head"><div><div class="tk">${c.ticker}</div><div class="nm">${(c.name||'').slice(0,30)}</div></div>
        <div style="text-align:right">${c.verdict?`<span class="tag ${c.verdict}">${c.verdict}</span>`:''}
          ${c.unread_alerts?`<div class="sub"><span class="badge">${c.unread_alerts} new</span></div>`:''}</div></div>
      <div class="stat">
        <div><div class="label">price</div><div class="v">${usd(c.price)}</div></div>
        <div><div class="label">P/E</div><div class="v">${c.pe_ratio?c.pe_ratio.toFixed(1):'—'}</div></div>
        <div><div class="label">DCF base</div><div class="v">${c.dcf_base?'$'+c.dcf_base.toFixed(0):'—'}</div></div>
        <div><div class="label">implied growth</div><div class="v">${pct(c.implied_fcf_growth)}</div></div>
      </div>
      <div class="sub" style="margin-top:8px">Revenue $${fmt(c.revenue)} · FY${c.latest_fiscal_year}</div>
    </div>`).join('') || '<div class="muted">No companies ingested.</div>';
  loadFeed();
}
async function loadFeed(){
  let f; try { f=await j('/api/feed'); } catch(e){ return; }
  const u=document.getElementById('unread'); if(u) u.innerHTML=f.unread?`<span class="badge">${f.unread} new</span>`:'';
  const el=document.getElementById('feed'); if(!el) return;
  el.innerHTML = f.alerts.length ? f.alerts.map(a=>`
    <div class="alert ${a.severity}"><div><a href="#/${a.ticker}"><b>${a.ticker}</b></a> · ${a.message}
      <div class="k">${a.kind} · ${a.severity}${a.sources&&a.sources.length?' · '+a.sources.join(', '):''}</div></div>
      ${a.acknowledged?'<span class="muted">ack’d</span>':`<button onclick="ack('${a.fingerprint}')">Acknowledge</button>`}</div>`).join('')
    : '<div class="muted">No alerts recorded. Click “Refresh alerts”.</div>';
}
async function ack(fp){ await j(`/api/alerts/${fp}/ack`,{method:'POST'}); loadFeed(); }

/* ---- company detail ---- */
const TABS=['overview','statements','comps','filings','chat'];
async function renderCompany(ticker){
  window._t=ticker;
  document.getElementById('crumb').innerHTML=`<a href="#/">Watchlist</a> › ${ticker}`;
  const app=document.getElementById('app');
  app.innerHTML=`<div class="tabs">${TABS.map((t,i)=>`<div class="tab ${i==0?'active':''}" data-tab="${t}">${t[0].toUpperCase()+t.slice(1)}</div>`).join('')}</div>
    <section id="s-overview"></section>
    <section id="s-statements" class="hidden muted">—</section>
    <section id="s-comps" class="hidden muted">—</section>
    <section id="s-filings" class="hidden muted">—</section>
    <section id="s-chat" class="hidden">
      <div id="chatlog" class="chat-log muted">Ask a question about ${ticker}.</div>
      <div class="row"><input id="q" placeholder="e.g. Is growth accelerating? What are the key risks?"/><button id="ask">Ask</button></div>
    </section>`;
  document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{
    document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active')); t.classList.add('active');
    TABS.forEach(s=>document.getElementById('s-'+s).classList.toggle('hidden', s!==t.dataset.tab));
    if(t.dataset.tab==='statements') loadStatements();
    if(t.dataset.tab==='comps') loadComps();
    if(t.dataset.tab==='filings') loadFilings();
  });
  document.getElementById('ask').onclick=ask;
  loadOverview();
}

async function loadOverview(){
  const el=document.getElementById('s-overview'); el.innerHTML='<div class="muted">Loading…</div>';
  let o; try { o=await j(`/api/companies/${window._t}/overview`); } catch(e){ el.textContent=e.message; return; }
  const H=o.headline, m=o.market, v=o.valuation;
  let verdict='', vcolor='var(--dim)';
  if(v&&m&&m.price){ const up=(v.per_share.base-m.price)/m.price;
    verdict = up>0.15?`DCF base $${v.per_share.base.toFixed(0)} is ${(up*100).toFixed(0)}% above price — screens cheap`
            : up<-0.15?`DCF base $${v.per_share.base.toFixed(0)} is ${(-up*100).toFixed(0)}% below price — screens expensive`
            : `DCF base $${v.per_share.base.toFixed(0)} ≈ price — fairly valued`;
    vcolor=up>0.15?'var(--cheap)':up<-0.15?'var(--exp)':'var(--dim)'; }
  el.innerHTML=`
    <div class="sub" style="margin:4px 0 12px">${o.name} · ${o.exchange||''} · FY end ${o.fiscal_year_end||'?'} · latest FY${o.latest_fiscal_year}</div>
    <div class="grid">${Object.keys(H).map(k=>`<div class="card"><div class="label">${k.replace(/_/g,' ')}</div>
      <div class="big">${k.includes('eps')?usd(H[k].value):'$'+fmt(H[k].value)}</div>
      <div class="sub">FY${H[k].fiscal_year} · ${(H[k].sources||[]).join(', ')||'—'}</div></div>`).join('')}</div>
    <h2>Trends</h2><div class="charts">
      <div class="card"><div class="label">Revenue (bars) &amp; free cash flow (line)</div><div id="chart-rev"></div></div>
      <div class="card"><div class="label">Operating &amp; net margin</div><div id="chart-mgn"></div></div></div>
    <h2>Market &amp; valuation</h2>
    <div class="card">${m?`<div class="stat" style="gap:24px">
        <div><div class="label">price</div><div class="big">${usd(m.price)}</div><div class="sub">${m.source||''} ${m.price_as_of||''}</div></div>
        <div><div class="label">P/E</div><div class="big">${m.pe_ratio?m.pe_ratio.toFixed(1):'—'}</div></div>
        <div><div class="label">FCF yield</div><div class="big">${pct(m.fcf_yield)}</div></div>
        <div><div class="label">implied FCF growth</div><div class="big">${pct(m.implied_fcf_growth)}</div><div class="sub">reverse DCF — what price implies</div></div>
      </div><div style="margin-top:10px;color:${vcolor}">${verdict}</div>`:'<span class="muted">Price unavailable.</span>'}</div>
    <h2>Intrinsic value (DCF range)</h2>
    <div class="card">${v?`<div class="big">$${v.per_share.bear.toFixed(0)} – $${v.per_share.bull.toFixed(0)} / share
      <span class="pill">base $${v.per_share.base.toFixed(0)}</span></div>
      <div class="sub">${v.method} · base FCF $${fmt(v.base_fcf)} · ${(v.fcf_sources||[]).join(', ')}</div>`:'No valuation.'}</div>
    <h2>Key ratios (latest FY)</h2>
    <div class="card">${o.ratios&&o.ratios.values?'<table>'+Object.entries(o.ratios.values).map(([k,val])=>
      `<tr><th>${k.replace(/_/g,' ')}</th><td class="num">${(k.includes('margin')||k.includes('return')||k.includes('growth')||k.includes('tax'))?pct(val):(+val).toFixed(2)}</td></tr>`).join('')+'</table>':'No ratios.'}</div>
    <h2>Company alerts</h2><div>${o.alerts.length?o.alerts.map(a=>`<div class="alert ${a.severity}"><div>${a.message}<div class="k">${a.kind} · ${a.severity}</div></div></div>`).join(''):'<div class="muted">No active alerts.</div>'}</div>`;
  loadTrends();
}
async function loadTrends(){
  let d; try { d=await j(`/api/companies/${window._t}/trends`); } catch(e){ return; }
  document.getElementById('chart-rev').innerHTML = barLine(d.years, d.revenue.map(x=>x?x/1e9:null), d.free_cash_flow.map(x=>x?x/1e9:null), 'var(--accent)', 'var(--info)');
  document.getElementById('chart-mgn').innerHTML = multiLine(d.years, [
    {name:'operating', color:'var(--accent)', vals:d.operating_margin},
    {name:'net', color:'var(--info)', vals:d.net_margin}]);
}
async function loadStatements(){
  const el=document.getElementById('s-statements'); el.textContent='loading…';
  let d; try { d=await j(`/api/companies/${window._t}/statements`); } catch(e){ el.textContent=e.message; return; }
  let html='';
  for(const [name,metrics] of Object.entries(d.statements)){
    const years=[...new Set(Object.values(metrics).flat().map(r=>r.fiscal_year))].sort();
    html+=`<h2>${name.replace(/_/g,' ')}</h2><table><tr><th>Metric</th>${years.map(y=>`<th class="num">FY${y}</th>`).join('')}</tr>`;
    for(const [mname,rows] of Object.entries(metrics)){ const by={}; rows.forEach(r=>by[r.fiscal_year]=r.value);
      html+=`<tr><th>${mname.replace(/_/g,' ')}</th>${years.map(y=>`<td class="num">${y in by?(mname.includes('eps')?(+by[y]).toFixed(2):fmt(by[y])):'·'}</td>`).join('')}</tr>`; }
    html+='</table>'; }
  el.innerHTML=html;
}
async function loadComps(){
  const el=document.getElementById('s-comps'); el.textContent='loading peers (live, a few seconds)…';
  let d; try { d=await j(`/api/companies/${window._t}/comps`); } catch(e){ el.textContent=e.message; return; }
  const cols=[['revenue','Revenue'],['revenue_growth','Rev growth'],['gross_margin','Gross'],['operating_margin','Op'],['net_margin','Net'],['fcf_margin','FCF'],['return_on_equity','ROE']];
  const cell=(m,k)=>{ if(!m||m[k]==null) return '·'; return k==='revenue'?'$'+fmt(m[k]):pct(m[k]); };
  el.innerHTML='<table><tr><th>Ticker</th><th>Name</th>'+cols.map(c=>`<th class="num">${c[1]}</th>`).join('')+'</tr>'+
    d.rows.map(r=>`<tr><td><b>${r.ticker}</b></td><td>${r.note?`<span class="muted">${r.note}</span>`:(r.name||'').slice(0,24)}</td>`+
      cols.map(c=>`<td class="num">${cell(r.metrics,c[0])}</td>`).join('')+'</tr>').join('')+'</table>'+
    '<div class="sub" style="margin-top:8px">Live SEC XBRL · cached 6h · non-US/IFRS filers shown without data.</div>';
}
async function loadFilings(){
  const el=document.getElementById('s-filings'); el.textContent='loading…';
  let d; try { d=await j(`/api/companies/${window._t}/filings`); } catch(e){ el.textContent=e.message; return; }
  el.innerHTML='<table><tr><th>Form</th><th>Filed</th><th>Period</th><th>Document</th></tr>'+
    d.filings.map(f=>`<tr><td>${f.form}</td><td>${f.filed_date||''}</td><td>${f.report_date||'·'}</td>
      <td>${f.document_url?`<a href="${f.document_url}" target="_blank" rel="noopener">${f.accession}</a>`:f.accession}</td></tr>`).join('')+'</table>';
}
async function ask(){
  const q=document.getElementById('q').value.trim(); if(!q) return;
  const log=document.getElementById('chatlog'); log.classList.remove('muted'); log.textContent='Thinking…';
  try { const r=await j(`/api/companies/${window._t}/chat`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({question:q})});
    const cov=r.citation_coverage!=null?`\n\n— citation coverage ${(r.citation_coverage*100).toFixed(0)}%`:'';
    log.textContent=r.answer+cov;
  } catch(e){ log.textContent='Error: '+e.message; }
}
router();
</script>
</body>
</html>
"""
