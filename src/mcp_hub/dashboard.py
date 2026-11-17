"""Embedded dashboard HTML for mcp-hub."""

# ruff: noqa: E501

HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>mcp-hub dashboard</title>
<style nonce="__CSP_NONCE__">
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#0b1020;color:#e7ecff}
header{padding:16px 20px;border-bottom:1px solid #27304d;background:#121833;position:sticky;top:0}
h1{margin:0;font-size:20px}
main{padding:16px 20px;max-width:1100px;margin:0 auto}
.card{background:#121833;border:1px solid #27304d;border-radius:10px;padding:14px;margin-bottom:14px}
.row{display:flex;gap:10px;flex-wrap:wrap}
input,button{padding:8px 10px;border-radius:8px;border:1px solid #334; background:#0f1530;color:#e7ecff}
button{cursor:pointer;background:#2b62ff;border:none}
table{width:100%;border-collapse:collapse} th,td{padding:8px;border-bottom:1px solid #27304d;text-align:left;font-size:14px}
.num{text-align:right}
.muted{color:#9fb0d9}
small{color:#9fb0d9}
pre{white-space:pre-wrap}
</style>
</head>
<body>
<header><h1>mcp-hub <small id="ver" class="muted"></small></h1></header>
<main>
  <div class="card">
    <div class="row">
      <input id="q" placeholder="Search servers..." style="flex:1;min-width:220px"/>
      <button onclick="doSearch()">Search</button>
      <button onclick="loadStats()">Refresh stats</button>
      <button onclick="runSaved()">Run saved searches</button>
    </div>
    <div id="stats" class="muted" style="margin-top:10px"></div>
  </div>
  <div class="card">
    <h3 style="margin-top:0">Results</h3>
    <div id="results" class="muted">Run a search to see results.</div>
  </div>
  <div class="card">
    <h3 style="margin-top:0">Recent batch runs</h3>
    <div id="runs" class="muted">Loading…</div>
  </div>
</main>
<script nonce="__CSP_NONCE__">
const $ = (s)=>document.querySelector(s);
const API_KEY = localStorage.getItem('mcp_hub_api_key') || '';
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]))}
async function fetchJSON(url, opts={}){
  opts.headers = opts.headers || {};
  if (API_KEY) opts.headers['X-API-Key'] = API_KEY;
  const r = await fetch(url, opts);
  if (r.status === 401){
    const k = prompt('This MCP Hub requires an API key:');
    if (k){ localStorage.setItem('mcp_hub_api_key', k); location.reload(); }
    throw new Error('unauthorized');
  }
  if (!r.ok) throw new Error(r.status + ': ' + await r.text());
  return r.json();
}
async function loadStats(){
  try{
    const s = await fetchJSON('/api/stats');
    $('#stats').textContent = `records=${s.records} sources=${s.sources} dupes=${s.duplicate_candidates} enriched=${s.enriched} fts=${s.fts_enabled}`;
  }catch(e){ $('#stats').textContent = 'stats error: '+e.message; }
}
async function doSearch(){
  const q = $('#q').value.trim();
  const url = '/api/search?q='+encodeURIComponent(q)+'&limit=50';
  try{
    const d = await fetchJSON(url);
    if(!d.results || !d.results.length){ $('#results').innerHTML='<p class="muted">No results.</p>'; return; }
    $('#results').innerHTML = '<table><thead><tr><th>ID</th><th>Name</th><th>Publisher</th><th>Categories</th><th>Description</th></tr></thead><tbody>'
      + d.results.map(r=>`<tr><td class="num">${r.id}</td><td>${esc(r.name)}</td><td>${esc(r.publisher||'')}</td><td>${esc(r.categories||'')}</td><td>${esc((r.description||'').slice(0,140))}</td></tr>`).join('')
      + '</tbody></table>';
  }catch(e){ $('#results').textContent = 'search error: '+e.message; }
}
async function loadRuns(){
  try{
    const d = await fetchJSON('/api/scheduler/runs?limit=10');
    if(!d.runs || !d.runs.length){ $('#runs').innerHTML='<p class="muted">No runs yet.</p>'; return; }
    $('#runs').innerHTML = '<table><thead><tr><th>Finished</th><th>Searches</th><th>New</th><th>Failed</th><th>ms</th></tr></thead><tbody>'
      + d.runs.map(r=>`<tr><td><small>${esc(r.finished_at)}</small></td><td class="num">${r.total_searches}</td><td class="num">${r.total_new}</td><td class="num">${r.failed}</td><td class="num">${r.duration_ms||0}</td></tr>`).join('')
      + '</tbody></table>';
  }catch(e){ $('#runs').textContent = 'runs error: '+e.message; }
}
async function runSaved(){
  try{ await fetchJSON('/api/searches/run-all',{method:'POST'}); await loadRuns(); }
  catch(e){ alert('run-all failed: '+e.message); }
}
(async()=>{ try{ const h=await fetchJSON('/api/health'); $('#ver').textContent='v'+(h.version||''); }catch{}; loadStats(); loadRuns(); })();
</script>
</body>
</html>
"""
