#!/usr/bin/env python3
"""
tip_dashboard.py — Local browser dashboard for the TIP trade plan.
Stdlib only. Reads ~/.schwab/scanner.db (daily_plan + scan_runs), serves
one HTML page at http://localhost:8787. Sortable, filterable, click-to-detail.
Scope: ranked ideas only — sizing + execution stay manual.

Run:  /usr/bin/python3 ~/.schwab/bin/tip_dashboard.py
Then open http://localhost:8787
"""
import csv, json, sqlite3, subprocess, sys, threading
from pathlib import Path
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HOME = Path.home()
DB = str(HOME / ".schwab" / "scanner.db")
SCAN = str(HOME / ".schwab" / "bin" / "daily_morning_scan.py")
PORT = 8787
BACKTEST_DIR = HOME / ".schwab" / "reports" / "backtest"

_scan_lock = threading.Lock()
_scan_state = {"running": False, "last": None, "msg": ""}

def q(sql, args=()):
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute(sql, args).fetchall()]
    finally:
        c.close()

def latest_date():
    r = q("SELECT MAX(scan_date) d FROM daily_plan")
    return r[0]["d"] if r and r[0]["d"] else None

def latest_opportunity_date():
    try:
        r = q("SELECT MAX(scan_date) d FROM tip_opportunities")
        return r[0]["d"] if r and r[0]["d"] else None
    except sqlite3.OperationalError:
        return None

def get_plan(date=None):
    date = date or latest_date()
    opp_date = date or latest_opportunity_date()
    if not date:
        return {"date": None, "cc": [], "csp": [], "opportunities": [], "radar": [], "run": None, "sentiment": []}
    cc = q("SELECT * FROM daily_plan WHERE scan_date=? AND kind='CC' ORDER BY "
           "CASE earn_state WHEN 'CLEAR' THEN 0 WHEN 'NEAR' THEN 1 ELSE 2 END, premium DESC", (date,))
    csp = q("SELECT * FROM daily_plan WHERE scan_date=? AND kind='CSP' ORDER BY "
            "CASE earn_state WHEN 'CLEAR' THEN 0 WHEN 'NEAR' THEN 1 ELSE 2 END, ann_yield DESC", (date,))
    opportunities = get_opportunities(opp_date)
    radar = get_radar(opp_date)
    run = q("SELECT * FROM scan_runs WHERE scan_date=? ORDER BY finished_at DESC LIMIT 1", (date,))
    sentiment = get_sentiment()
    return {
        "date": date, "cc": cc, "csp": csp, "opportunities": opportunities, "radar": radar,
        "run": run[0] if run else None, "sentiment": sentiment,
    }

def get_opportunities(date=None):
    date = date or latest_opportunity_date()
    if not date:
        return []
    try:
        return q("""SELECT *
                    FROM tip_opportunities
                    WHERE scan_date=?
                      AND COALESCE(strategy, '') != 'RADAR'
                      AND TRIM(COALESCE(structure, '')) NOT IN ('', 'sentiment')
                      AND COALESCE(premium, 0) > 0
                    ORDER BY CASE label
                        WHEN 'ACTIONABLE' THEN 0 WHEN 'MONETIZE' THEN 1
                        WHEN 'CONDITIONAL' THEN 2 WHEN 'WATCH' THEN 3
                        WHEN 'LOW_PRIORITY' THEN 4 WHEN 'SKIP' THEN 5 ELSE 6 END,
                        score DESC, symbol, dte""", (date,))
    except sqlite3.OperationalError:
        return []

def get_radar(date=None):
    date = date or latest_opportunity_date()
    if not date:
        return []
    try:
        return q("""SELECT *
                    FROM tip_opportunities
                    WHERE scan_date=? AND strategy='RADAR'
                    ORDER BY score DESC, symbol""", (date,))
    except sqlite3.OperationalError:
        return []

def get_sentiment():
    try:
        return q("""SELECT scan_date, symbol, mentions, bullish, bearish, net_score,
                           bullish_pct, bearish_pct, latest_scan_ts, sources, top_post
                    FROM v_sentiment_daily
                    ORDER BY ABS(net_score) DESC, mentions DESC, symbol""")
    except sqlite3.OperationalError:
        return []

def get_backtest():
    """Load the deployed, read-only QUALITY_DIP_CSP research artifacts."""
    metrics_path = BACKTEST_DIR / "quality_dip_csp_metrics.json"
    trades_path = BACKTEST_DIR / "quality_dip_csp_trades.csv"
    report_path = BACKTEST_DIR / "S4_backtest_results.md"
    missing = [p.name for p in (metrics_path, trades_path, report_path) if not p.exists()]
    if missing:
        return {"available": False, "error": "missing deployed artifacts: " + ", ".join(missing)}
    try:
        metrics = json.loads(metrics_path.read_text())
        numeric = {
            "dte", "strike", "spot", "sma50", "dip_pct", "bid", "ask",
            "spread_pct", "iv", "delta", "bp", "premium", "ann_yield",
            "expiry_close", "pnl", "return_on_bp_pct",
        }
        boolean = {"touched", "assigned", "win"}
        trades = []
        with trades_path.open(newline="") as f:
            for row in csv.DictReader(f):
                for key in numeric:
                    if row.get(key) not in (None, ""):
                        row[key] = float(row[key])
                for key in boolean:
                    row[key] = str(row.get(key, "")).lower() == "true"
                trades.append(row)
        return {
            "available": True,
            "metrics": metrics,
            "trades": trades,
            "report": report_path.read_text(),
        }
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"available": False, "error": f"backtest artifact error: {exc}"}

def run_scan_async():
    """Trigger a fresh morning scan in the background."""
    def _run():
        with _scan_lock:
            _scan_state["running"] = True
            _scan_state["msg"] = "Scanning…"
        try:
            p = subprocess.run([sys.executable, SCAN], capture_output=True,
                               text=True, timeout=900)
            ok = (p.returncode == 0)
            _scan_state["msg"] = "Scan complete" if ok else f"Scan exit {p.returncode}"
        except Exception as e:
            _scan_state["msg"] = f"Scan error: {e}"
        finally:
            _scan_state["running"] = False
            _scan_state["last"] = datetime.now().isoformat(timespec='seconds')
    if _scan_state["running"]:
        return {"started": False, "msg": "Scan already running"}
    threading.Thread(target=_run, daemon=True).start()
    return {"started": True, "msg": "Scan started"}

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass  # quiet

    def _send(self, code, body, ctype="application/json"):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif u.path == "/app.js":
            self._send(200, APPJS, "application/javascript; charset=utf-8")
        elif u.path == "/api/plan":
            qs = parse_qs(u.query)
            date = qs.get("date", [None])[0]
            self._send(200, json.dumps(get_plan(date)))
        elif u.path == "/api/opportunities":
            qs = parse_qs(u.query)
            date = qs.get("date", [None])[0]
            self._send(200, json.dumps(get_opportunities(date)))
        elif u.path == "/api/radar":
            qs = parse_qs(u.query)
            date = qs.get("date", [None])[0]
            self._send(200, json.dumps(get_radar(date)))
        elif u.path == "/api/sentiment":
            self._send(200, json.dumps(get_sentiment()))
        elif u.path == "/api/backtest":
            self._send(200, json.dumps(get_backtest()))
        elif u.path == "/api/dates":
            self._send(200, json.dumps([r["scan_date"] for r in
                       q("SELECT DISTINCT scan_date FROM daily_plan ORDER BY scan_date DESC")]))
        elif u.path == "/api/scan_status":
            self._send(200, json.dumps(_scan_state))
        elif u.path == "/api/rescan":
            self._send(200, json.dumps(run_scan_async()))
        else:
            self._send(404, json.dumps({"error": "not found"}))

PAGE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TIP — Trade Plan</title>
<style>
:root{
  --bg:#0a0e14; --panel:#11161f; --panel2:#161d28; --line:#1f2937;
  --txt:#d7dee8; --dim:#7b8794; --accent:#4ea1ff;
  --clear:#2fbf71; --near:#e6a23c; --block:#e15b5b; --unknown:#6b7280;
  --watch:#35c2d6; --skip:#9aa4b2;
  --mono:'SFMono-Regular',ui-monospace,'JetBrains Mono',Menlo,monospace;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);
  font-family:var(--mono);font-size:13px;line-height:1.4}
header{display:flex;align-items:baseline;gap:18px;padding:12px 18px;
  border-bottom:1px solid var(--line);background:var(--panel);position:sticky;top:0;z-index:5}
header h1{font-size:15px;margin:0;letter-spacing:.14em;font-weight:600}
header h1 .t{color:var(--accent)}
.acct{display:flex;gap:20px;margin-left:auto;flex-wrap:wrap}
.acct .kv{display:flex;flex-direction:column;align-items:flex-end}
.acct .kv b{font-size:14px;font-weight:600}
.acct .kv span{font-size:10px;color:var(--dim);letter-spacing:.08em;text-transform:uppercase}
.neg{color:var(--block)}
.bar{display:flex;align-items:center;gap:10px;padding:8px 18px;border-bottom:1px solid var(--line);background:var(--panel2)}
.tab{padding:5px 14px;border:1px solid var(--line);background:transparent;color:var(--dim);
  cursor:pointer;border-radius:3px;font-family:var(--mono);font-size:12px;letter-spacing:.05em}
.tab.on{color:var(--txt);border-color:var(--accent);background:rgba(78,161,255,.08)}
.bar .sp{margin-left:auto;display:flex;gap:8px;align-items:center}
.flt{display:flex;gap:6px;align-items:center;color:var(--dim);font-size:11px}
.flt input,.flt select{background:var(--bg);border:1px solid var(--line);color:var(--txt);
  font-family:var(--mono);font-size:12px;padding:3px 6px;border-radius:3px}
button.act{background:var(--accent);border:none;color:#04101f;font-weight:600;
  padding:5px 12px;border-radius:3px;cursor:pointer;font-family:var(--mono)}
button.act:disabled{opacity:.5;cursor:wait}
table{width:100%;border-collapse:collapse}
th,td{text-align:right;padding:5px 10px;white-space:nowrap;border-bottom:1px solid var(--line)}
th{position:sticky;top:0;color:var(--dim);font-weight:500;font-size:10.5px;letter-spacing:.06em;
  text-transform:uppercase;cursor:pointer;user-select:none;background:var(--panel)}
th:first-child,td:first-child{text-align:left}
th.sorted::after{content:' \25BC';font-size:8px}
th.sorted.asc::after{content:' \25B2'}
tr.row{cursor:pointer}
tr.row:hover{background:var(--panel2)}
td.sym{font-weight:600;color:#fff}
.st{display:inline-block;padding:1px 7px;border-radius:10px;font-size:10px;letter-spacing:.05em}
.st.CLEAR{color:var(--clear);background:rgba(47,191,113,.13)}
.st.NEAR{color:var(--near);background:rgba(230,162,60,.13)}
.st.BLOCK{color:var(--block);background:rgba(225,91,91,.13)}
.st.UNKNOWN{color:var(--unknown);background:rgba(107,114,128,.16)}
.lb{display:inline-block;padding:1px 7px;border-radius:3px;font-size:10px;letter-spacing:.05em}
.lb.ACTIONABLE{color:var(--clear);background:rgba(47,191,113,.15)}
.lb.MONETIZE{color:var(--accent);background:rgba(78,161,255,.15)}
.lb.CONDITIONAL{color:var(--near);background:rgba(230,162,60,.15)}
.lb.WATCH{color:var(--watch);background:rgba(53,194,214,.14)}
.lb.SKIP,.lb.LOW_PRIORITY{color:var(--skip);background:rgba(154,164,178,.12)}
.sleeve{color:#b9c4d0;font-size:11px}
.sent{display:inline-flex;align-items:center;gap:5px;font-size:11px}
.sent .score{color:var(--txt)}
.sent.pos .score{color:var(--clear)}
.sent.neg .score{color:var(--block)}
.sent.neu .score{color:var(--dim)}
.sent .contra{color:var(--near);font-size:10px}
.contra-badge{display:inline-block;margin-left:6px;padding:1px 5px;border-radius:3px;
  color:var(--near);background:rgba(230,162,60,.14);font-size:9px;letter-spacing:.04em}
tr.detail td{background:var(--panel2);text-align:left;color:var(--dim);font-size:12px;
  padding:8px 18px;white-space:normal}
tr.detail .grid{display:flex;gap:26px;flex-wrap:wrap}
tr.detail .grid div b{color:var(--txt)}
.empty{padding:40px;text-align:center;color:var(--dim)}
.flag{padding:8px 18px;background:rgba(225,91,91,.1);color:var(--block);border-bottom:1px solid var(--line);display:none}
footer{padding:10px 18px;color:var(--dim);font-size:11px;border-top:1px solid var(--line)}
.lvl{color:var(--clear)} .lvl.warn{color:var(--near)}
.bt{padding:18px;max-width:1500px;margin:0 auto}
.bt-head{display:flex;justify-content:space-between;align-items:flex-end;gap:18px;margin-bottom:14px}
.bt-head h2{margin:0;font-size:18px;letter-spacing:.08em;color:#fff}
.bt-head p{margin:4px 0 0;color:var(--dim);max-width:780px}
.stamp{color:var(--dim);font-size:11px;text-align:right}
.cards{display:grid;grid-template-columns:repeat(6,minmax(130px,1fr));gap:8px;margin-bottom:14px}
.card{background:var(--panel);border:1px solid var(--line);padding:12px;border-radius:5px;min-height:76px}
.card b{display:block;font-size:20px;color:#fff;letter-spacing:-.03em}
.card b.good{color:var(--clear)} .card b.bad{color:var(--block)} .card b.warn{color:var(--near)}
.card span{display:block;margin-top:5px;color:var(--dim);font-size:10px;letter-spacing:.06em;text-transform:uppercase}
.risk{border-left:3px solid var(--block);background:rgba(225,91,91,.08);padding:11px 14px;margin:0 0 14px;color:#cbd3df}
.risk b{color:var(--block)}
.bt-grid{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(400px,.85fr);gap:12px;margin-bottom:12px}
.section{background:var(--panel);border:1px solid var(--line);border-radius:5px;overflow:hidden;margin-bottom:12px}
.section h3{margin:0;padding:10px 12px;border-bottom:1px solid var(--line);font-size:11px;color:#fff;letter-spacing:.08em;text-transform:uppercase}
.section .inside{padding:12px}
.chart{width:100%;height:230px;display:block}
.chart text{fill:var(--dim);font-family:var(--mono);font-size:10px}
.chart .gridline{stroke:var(--line);stroke-width:1}.chart .zero{stroke:var(--dim);stroke-dasharray:4 4}
.chart .equity{fill:none;stroke:var(--accent);stroke-width:2}.chart .area{fill:rgba(78,161,255,.08)}
.chart circle{fill:var(--block);stroke:var(--panel);stroke-width:1}
.pnl-pos{color:var(--clear)} .pnl-neg{color:var(--block)}
.regime{display:grid;grid-template-columns:1.5fr repeat(6,1fr);gap:8px;align-items:center;padding:8px 10px;border-bottom:1px solid var(--line)}
.regime:last-child{border-bottom:0}.regime.head{color:var(--dim);font-size:10px;text-transform:uppercase}
.regime span:not(:first-child){text-align:right}.regime .name{color:#fff}
.sym-table{max-height:520px;overflow:auto}.sym-table th{top:0}.sym-table td{font-size:11px}
.bartrack{position:relative;width:120px;height:6px;background:var(--line);margin-left:auto;border-radius:4px;overflow:hidden}
.bartrack i{position:absolute;top:0;height:100%}.bartrack i.pos{left:50%;background:var(--clear)}.bartrack i.neg{right:50%;background:var(--block)}
.method{display:flex;gap:8px;flex-wrap:wrap}.pill{border:1px solid var(--line);background:var(--panel2);padding:5px 8px;border-radius:3px;color:#b9c4d0;font-size:11px}
.report{white-space:pre-wrap;color:#aeb8c5;font-family:var(--mono);font-size:11px;max-height:600px;overflow:auto;margin:0}
details summary{cursor:pointer;padding:10px 12px;color:var(--accent);border-bottom:1px solid var(--line)}
@media(max-width:1050px){.cards{grid-template-columns:repeat(3,1fr)}.bt-grid{grid-template-columns:1fr}.bar .sp{display:none}}
@media(max-width:620px){.cards{grid-template-columns:repeat(2,1fr)}.bt{padding:10px}.regime{grid-template-columns:1.5fr repeat(3,1fr)}.regime .optional{display:none}}
</style></head>
<body>
<header>
  <h1><span class="t">TIP</span> · MARKET BRAIN</h1>
  <div class="acct" id="acct"></div>
</header>
<div class="flag" id="flag"></div>
<div class="bar">
  <button class="tab on" id="tabIDEAS" onclick="setTab('IDEAS')">IDEAS · underwritten</button>
  <button class="tab" id="tabRADAR" onclick="setTab('RADAR')">RADAR · sentiment</button>
  <button class="tab" id="tabCSP" onclick="setTab('CSP')">CSP · raw puts</button>
  <button class="tab" id="tabCC" onclick="setTab('CC')">CC · covered calls</button>
  <button class="tab" id="tabBACKTEST" onclick="setTab('BACKTEST')">BACKTEST · research</button>
  <div class="sp" id="tradeControls">
    <span class="flt">label
      <select id="fLabel" onchange="render()">
        <option value="">all</option><option value="ACTIONABLE,MONETIZE,CONDITIONAL">actionable+conditional</option>
        <option value="WATCH">watch</option><option value="SKIP,LOW_PRIORITY">skip/low</option></select></span>
    <span class="flt">sleeve <input id="fSleeve" size="10" oninput="render()" placeholder="all"></span>
    <span class="flt"><label><input id="fDistinct" type="checkbox" checked onchange="render()"> 1/sym</label></span>
    <span class="flt">earnings
      <select id="fState" onchange="render()">
        <option value="">all</option><option value="CLEAR">clear only</option>
        <option value="CLEAR,NEAR">clear+near</option></select></span>
    <span class="flt">sym <input id="fSym" size="5" oninput="render()" placeholder="all"></span>
    <span class="flt">max d <input id="fDelta" size="4" oninput="render()" placeholder="—"></span>
    <button class="act" id="rescan" onclick="rescan()">Re-scan</button>
  </div>
</div>
<div id="tbl"></div>
<footer id="foot">loading…</footer>
<script src="/app.js"></script>
</body></html>"""

APPJS = r"""
let DATA={cc:[],csp:[],opportunities:[],radar:[],run:null,date:null,backtest:null};
let TAB='IDEAS';
let sortKey={IDEAS:'score',RADAR:'score',CSP:'ann_yield',CC:'premium'};
let sortAsc={IDEAS:false,RADAR:false,CSP:false,CC:false};
let openRow=null;

const COLS={
  IDEAS:[['symbol','SYM'],['sleeve','SLEEVE'],['strategy','STRAT'],['structure','STRUCT'],
         ['expiry','EXP'],['score','SCORE'],['label','LABEL'],['ann_yield','YLD%'],
         ['delta','Δ'],['iv','IV'],['premium','PREM$'],['earn_state','EARN']],
  RADAR:[['symbol','SYM'],['sleeve','SIGNAL'],['score','SCORE'],['label','LABEL'],
         ['raw_rank_reason','READOUT']],
  CSP:[['sym','SYM'],['exp','EXP'],['dte','DTE'],['strike','STRIKE'],['otm_pct','OTM%'],
       ['delta','Δ'],['iv','IV'],['premium','PREM$'],['bp_reduction','BP RED$'],
       ['notional','NOTIONAL$'],['ann_yield','MARGIN YLD%'],['sentiment','SENT'],['earn_state','EARN']],
  CC:[['sym','SYM'],['exp','EXP'],['dte','DTE'],['strike','STRIKE'],['otm_pct','OTM%'],
      ['delta','Δ'],['iv','IV'],['premium','PREM$'],['earn_state','EARN']],
};
const sentimentBySymbol=()=>Object.fromEntries((DATA.sentiment||[]).map(x=>[x.symbol,x]));
const radarBySymbol=()=>Object.fromEntries((DATA.radar||[]).map(x=>[x.symbol,x]));
const fmt=(v,k)=>{
  if(v===null||v===undefined||v==='')return '—';
  if(k==='premium'||k==='bp_reduction'||k==='notional')return Math.round(v).toLocaleString();
  if(k==='ann_yield'||k==='otm_pct')return (+v).toFixed(0);
  if(k==='delta')return Math.abs(+v).toFixed(2);
  if(k==='iv')return (+v).toFixed(0);
  if(k==='score')return Math.round(+v);
  if(k==='strike')return (+v).toFixed(0);
  return v;
};
const money=v=>v==null?'—':'$'+Math.round(v).toLocaleString();
const signedMoney=v=>`${Number(v)<0?'−':'+'}$${Math.abs(Math.round(Number(v)||0)).toLocaleString()}`;
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const sentimentBadge=x=>{
  const s=sentimentBySymbol()[x.sym];
  if(!s)return '<span class="sent neu"><span class="score">—</span></span>';
  const score=Number(s.net_score)||0;
  const cls=score>.15?'pos':score<-.15?'neg':'neu';
  const contra=x.earn_state==='CLEAR'&&Number(s.bullish_pct)>=80&&Number(s.mentions)>=5;
  const sign=score>0?'+':'';
  const title=`${s.mentions} mentions · ${s.bullish_pct}% bull / ${s.bearish_pct}% bear · ${s.sources||'sentiment'}`;
  return `<span class="sent ${cls}" title="${title}"><span class="score">${sign}${score.toFixed(2)}</span>${contra?'<span class="contra">CONTRA</span>':''}</span>`;
};

async function load(){
  const [p,b]=await Promise.all([
    fetch('/api/plan').then(r=>r.json()),
    fetch('/api/backtest').then(r=>r.json()).catch(e=>({available:false,error:String(e)}))
  ]);
  DATA={...p,backtest:b}; drawAcct(); render(); drawFoot();
}
function drawAcct(){
  const r=DATA.run||{};
  const el=document.getElementById('acct');
  const items=[['NLV',money(r.nlv)],['AVAIL',money(r.avail_funds)],
    ['MAINT REQ',money(r.maint_req)],['MARGIN BAL',money(r.margin_bal)]];
  el.innerHTML=items.map(([s,v])=>{
    const neg=(s==='MARGIN BAL'&&r.margin_bal<0)?' neg':'';
    return `<div class="kv"><b class="${neg.trim()}">${v}</b><span>${s}</span></div>`;}).join('');
}
function drawFoot(){
  const r=DATA.run||{};
  if(TAB==='BACKTEST'){
    const b=DATA.backtest||{},a=(b.metrics||{}).aggregate||{};
    document.getElementById('foot').innerHTML=b.available
      ?`QUALITY_DIP_CSP · ${a.trade_count||0} simulated trades · research only · <span class="lvl warn">no execution</span>`
      :`backtest artifacts unavailable · ${esc(b.error||'not loaded')}`;
    return;
  }
  document.getElementById('foot').innerHTML=
    `scan ${DATA.date||'—'} · ${r.finished_at?r.finished_at.replace('T',' '):'—'} · `+
    `${(DATA.opportunities||[]).length} underwritten · ${(DATA.csp||[]).length} CSP / ${(DATA.cc||[]).length} CC raw · `+
    `<span class="lvl">manual sizing + execution</span>`;
}
function setTab(t){TAB=t;openRow=null;
  ['IDEAS','RADAR','CSP','CC','BACKTEST'].forEach(x=>
    document.getElementById('tab'+x).classList.toggle('on',t===x));
  document.getElementById('tradeControls').style.display=t==='BACKTEST'?'none':'flex';
  render();drawFoot();}
function sortBy(k){if(sortKey[TAB]===k)sortAsc[TAB]=!sortAsc[TAB];
  else{sortKey[TAB]=k;sortAsc[TAB]=(k==='sym'||k==='symbol'||k==='exp'||k==='expiry'||k==='dte');}render();}

function rows(){
  let r=(TAB==='IDEAS'?DATA.opportunities:(TAB==='RADAR'?DATA.radar:(TAB==='CSP'?DATA.csp:DATA.cc))).slice();
  const label=document.getElementById('fLabel').value;
  const sleeve=document.getElementById('fSleeve').value.trim().toUpperCase();
  const st=document.getElementById('fState').value;
  const sym=document.getElementById('fSym').value.trim().toUpperCase();
  const md=parseFloat(document.getElementById('fDelta').value);
  if(st)r=r.filter(x=>st.split(',').includes(x.earn_state));
  if(label)r=r.filter(x=>label.split(',').includes(x.label));
  if(sleeve)r=r.filter(x=>String(x.sleeve||'').includes(sleeve));
  if(sym)r=r.filter(x=>String(x.sym||x.symbol||'').includes(sym));
  if(!isNaN(md))r=r.filter(x=>x.delta==null||Math.abs(x.delta)<=md);
  const k=sortKey[TAB],a=sortAsc[TAB]?1:-1;
  r.sort((x,y)=>{let xv=x[k],yv=y[k];
    if(typeof xv==='number'||k==='strike')return ((xv||0)-(yv||0))*a;
    return String(xv).localeCompare(String(yv))*a;});
  if((TAB==='IDEAS'||TAB==='RADAR')&&document.getElementById('fDistinct').checked){
    const seen=new Set();
    r=r.filter(x=>{const s=x.symbol||x.sym;if(seen.has(s))return false;seen.add(s);return true;});
  }
  return r;
}
function render(){
  if(TAB==='BACKTEST'){renderBacktest();return;}
  const cols=COLS[TAB],data=rows();
  if(!data.length){document.getElementById('tbl').innerHTML='<div class="empty">no ideas match — try widening filters or Re-scan</div>';return;}
  let h='<table><thead><tr>'+cols.map(([k,l])=>{
    const cl=sortKey[TAB]===k?('sorted'+(sortAsc[TAB]?' asc':'')):'';
    return `<th class="${cl}" onclick="sortBy('${k}')">${l}</th>`;}).join('')+'</tr></thead><tbody>';
  data.forEach((x,i)=>{
    h+=`<tr class="row" onclick="toggle(${i})">`+cols.map(([k])=>{
      if(k==='earn_state')return `<td><span class="st ${x[k]}">${x[k]}</span></td>`;
      if(k==='label')return `<td><span class="lb ${x[k]}">${x[k]}</span></td>`;
      if(k==='sleeve')return `<td class="sleeve">${x[k]}</td>`;
      if(k==='sentiment')return `<td>${sentimentBadge(x)}</td>`;
      if(k==='sym'||k==='symbol'){
        const radar=TAB==='IDEAS'?radarBySymbol()[x[k]]:null;
        const badge=radar?`<span class="contra-badge" title="${radar.raw_rank_reason||'sentiment overlap'}">${radar.sleeve==='CONTRARIAN_SELL_PREMIUM'?'CONTRA SELL':'CONTRA DIP'}</span>`:'';
        return `<td class="sym">${x[k]}${badge}</td>`;
      }
      return `<td>${fmt(x[k],k)}</td>`;}).join('')+'</tr>';
    if(openRow===i)h+=detailRow(x,cols.length);
  });
  h+='</tbody></table>';
  document.getElementById('tbl').innerHTML=h;
}
function detailRow(x,span){
  if(TAB==='IDEAS'||TAB==='RADAR')return ideaDetailRow(x,span);
  const s=sentimentBySymbol()[x.sym];
  const sent=s
    ?`<div>Sentiment <b>${(Number(s.net_score)>0?'+':'')+Number(s.net_score).toFixed(2)}</b> · ${s.mentions} mentions · ${s.bullish_pct}% bull</div>`
    :`<div>Sentiment <b>—</b></div>`;
  const collat=x.kind==='CSP'
    ?`<div>BP reduction <b>${money(x.bp_reduction)}</b></div>
      <div>If assigned <b>${money(x.notional)}</b> @ ${fmt(x.strike,'strike')}</div>
      <div>Break-even <b>${(x.strike-(x.premium/100)).toFixed(2)}</b></div>`
    :`<div>Premium <b>${money(x.premium)}</b></div>`;
  return `<tr class="detail"><td colspan="${span}"><div class="grid">
    <div>${x.sym} ${x.exp} · ${x.dte} DTE</div>
    <div>Strike <b>${fmt(x.strike,'strike')}</b> (${fmt(x.otm_pct,'otm_pct')}% OTM)</div>
    <div>Δ <b>${fmt(x.delta,'delta')}</b> · IV <b>${fmt(x.iv,'iv')}</b></div>
    ${collat}
    ${sent}
    <div>Earnings: <b>${x.earn_note||'—'}</b></div>
  </div></td></tr>`;
}
function ideaDetailRow(x,span){
  const bp=x.bp_reduction?`<div>BP reduction <b>${money(x.bp_reduction)}</b></div>`:'';
  const exp=x.expiry?`${x.expiry} · ${x.dte||0} DTE`:'radar';
  return `<tr class="detail"><td colspan="${span}"><div class="grid">
    <div>${x.symbol} ${x.strategy} ${x.structure||''} · ${exp}</div>
    <div>Score <b>${x.score}</b> · Label <b>${x.label}</b> · Sleeve <b>${x.sleeve}</b></div>
    <div>Yield <b>${fmt(x.ann_yield,'ann_yield')}%</b> · Δ <b>${fmt(x.delta,'delta')}</b> · IV <b>${fmt(x.iv,'iv')}</b></div>
    <div>Premium <b>${money(x.premium)}</b></div>
    ${bp}
    <div>Why ranked: <b>${x.raw_rank_reason||'—'}</b></div>
    <div>Assignment: <b>${x.assignment_view||'—'}</b></div>
    <div>Failure point: <b>${x.failure_point||'—'}</b></div>
    <div>Better expression: <b>${x.alternative||'—'}</b></div>
  </div></td></tr>`;
}

const regimeLabel=k=>({
  COVID_AFTERMATH_2020_2021:'COVID · 2020–21',
  BEAR_2022:'Bear · 2022',
  MELT_UP_2023_2025:'Melt-up · 2023–25'
}[k]||k);
const pct=v=>`${Number(v||0).toFixed(2)}%`;
const pnlClass=v=>Number(v)>=0?'pnl-pos':'pnl-neg';
function metricCard(value,label,cls=''){
  return `<div class="card"><b class="${cls}">${value}</b><span>${label}</span></div>`;
}
function equityChart(trades){
  if(!trades.length)return '<div class="empty">no trade ledger</div>';
  const rs=trades.slice().sort((a,b)=>(a.expiry+a.entry_date).localeCompare(b.expiry+b.entry_date));
  let running=0;
  const vals=rs.map(x=>({date:x.expiry,value:(running+=Number(x.pnl)||0),trade:x}));
  const W=900,H=230,L=54,R=14,T=16,B=28;
  const lo=Math.min(0,...vals.map(x=>x.value)),hi=Math.max(0,...vals.map(x=>x.value));
  const span=Math.max(1,hi-lo),x=i=>L+i*(W-L-R)/Math.max(1,vals.length-1);
  const y=v=>T+(hi-v)*(H-T-B)/span;
  const line=vals.map((v,i)=>`${i?'L':'M'}${x(i).toFixed(1)},${y(v.value).toFixed(1)}`).join(' ');
  const zero=y(0),area=`M${x(0).toFixed(1)},${zero.toFixed(1)} ${line.replace(/^M/,'L')} L${x(vals.length-1).toFixed(1)},${zero.toFixed(1)} Z`;
  const grid=[lo,lo+span/2,hi].map(v=>`<line class="gridline" x1="${L}" y1="${y(v)}" x2="${W-R}" y2="${y(v)}"/><text x="2" y="${y(v)+3}">${signedMoney(v)}</text>`).join('');
  const tails=vals.map((v,i)=>Number(v.trade.pnl)<-1000
    ?`<circle cx="${x(i)}" cy="${y(v.value)}" r="3"><title>${esc(v.trade.symbol)} ${esc(v.trade.expiry)} · ${signedMoney(v.trade.pnl)}</title></circle>`:'').join('');
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Cumulative realized expiration P and L">
    ${grid}<line class="zero" x1="${L}" y1="${zero}" x2="${W-R}" y2="${zero}"/>
    <path class="area" d="${area}"/><path class="equity" d="${line}"/>${tails}
    <text x="${L}" y="${H-6}">${esc(vals[0].date)}</text><text text-anchor="end" x="${W-R}" y="${H-6}">${esc(vals[vals.length-1].date)}</text>
  </svg>`;
}
function symbolTable(metrics){
  const syms=Object.entries(metrics.symbols||{}).sort((a,b)=>b[1].total_pnl-a[1].total_pnl);
  const max=Math.max(1,...syms.map(([,x])=>Math.abs(x.total_pnl)));
  return `<div class="sym-table"><table><thead><tr><th>SYM</th><th>WINDOW / FIRST TRADE</th><th>TRADES</th><th>WIN</th><th>TOUCH</th><th>ASSIGN</th><th>P&amp;L</th><th>RELATIVE P&amp;L</th></tr></thead><tbody>${syms.map(([s,x])=>{
    const c=(metrics.coverage||{})[s]||{},w=(c.approved_start||'—')+' → 2025-12-31';
    const width=(Math.abs(x.total_pnl)/max*50).toFixed(1);
    return `<tr><td class="sym">${esc(s)}</td><td>${esc(w)} · ${esc(c.first_trade||'no trade')}</td><td>${x.trade_count}</td><td>${pct(x.win_rate_pct)}</td><td>${pct(x.touch_rate_pct)}</td><td>${pct(x.assignment_rate_pct)}</td><td class="${pnlClass(x.total_pnl)}">${signedMoney(x.total_pnl)}</td><td><div class="bartrack"><i class="${x.total_pnl>=0?'pos':'neg'}" style="width:${width}%"></i></div></td></tr>`;
  }).join('')}</tbody></table></div>`;
}
function worstTrades(trades){
  const rows=trades.slice().sort((a,b)=>a.pnl-b.pnl).slice(0,12);
  return `<table><thead><tr><th>SYM</th><th>ENTRY</th><th>EXPIRY</th><th>STRIKE</th><th>BID</th><th>EXP CLOSE</th><th>TOUCHED</th><th>ASSIGNED</th><th>P&amp;L</th><th>RETURN/BP</th></tr></thead><tbody>${rows.map(x=>`<tr><td class="sym">${esc(x.symbol)}</td><td>${esc(x.entry_date)}</td><td>${esc(x.expiry)}</td><td>${Number(x.strike).toFixed(2)}</td><td>${Number(x.bid).toFixed(2)}</td><td>${Number(x.expiry_close).toFixed(2)}</td><td>${x.touched?'YES':'NO'}</td><td>${x.assigned?'YES':'NO'}</td><td class="${pnlClass(x.pnl)}">${signedMoney(x.pnl)}</td><td class="${pnlClass(x.return_on_bp_pct)}">${pct(x.return_on_bp_pct)}</td></tr>`).join('')}</tbody></table>`;
}
function renderBacktest(){
  const b=DATA.backtest||{};
  if(!b.available){document.getElementById('tbl').innerHTML=`<div class="empty">Backtest data unavailable<br>${esc(b.error||'not loaded')}</div>`;return;}
  const m=b.metrics||{},a=m.aggregate||{},w=a.worst_trade||{},regimes=m.regimes||{};
  const regimeRows=Object.entries(regimes).map(([k,x])=>`<div class="regime">
    <span class="name">${regimeLabel(k)}</span><span>${x.trade_count}</span><span>${pct(x.win_rate_pct)}</span>
    <span>${pct(x.assignment_rate_pct)}</span><span class="optional">${pct(x.avg_ann_yield_on_bp_pct)}</span>
    <span class="optional">${pct(x.max_drawdown_on_committed_capital_pct)}</span>
    <span class="${pnlClass(x.total_pnl)}">${signedMoney(x.total_pnl)}</span></div>`).join('');
  const method=m.methodology||{};
  const methods=[method.dip_rule,`DTE ${(method.dte||[]).join('–')}`,`|Δ| ${(method.delta||[]).join('–')}`,
    `IV ceiling ${Number(method.iv_ceiling||0)*100}%`,`spread ≤ ${Number(method.max_spread||0)*100}%`,
    `OTM ${(method.otm||[]).map(x=>x*100+'%').join('–')}`,`fill: ${method.entry_fill||'—'}`,method.position_rule];
  const splits=(m.split_checks||[]).map(x=>`<tr><td class="sym">${esc(x.symbol)}</td><td>${esc(x.event_date)}</td><td>${x.factor}:1</td><td class="${pnlClass(x.return_pct)}">${Number(x.return_pct)>=0?'+':''}${pct(x.return_pct)}</td><td><span class="st CLEAR">CONTINUOUS</span></td></tr>`).join('');
  document.getElementById('tbl').innerHTML=`<main class="bt">
    <div class="bt-head"><div><h2>QUALITY DIP / CASH-SECURED PUT</h2><p>Historical rule validation · bid fills · assignment marked to expiration close · one contract per symbol</p></div><div class="stamp">GENERATED ${esc((m.generated_at||'').replace('T',' '))}<br>${esc((method.window||[]).join(' → '))}</div></div>
    <div class="cards">
      ${metricCard(a.trade_count,'simulated trades')}
      ${metricCard(pct(a.win_rate_pct),'win rate','good')}
      ${metricCard(pct(a.assignment_rate_pct),'assignment rate','warn')}
      ${metricCard(pct(a.touch_rate_pct),'strike touch rate','warn')}
      ${metricCard(signedMoney(a.total_pnl),'expiration P&L',a.total_pnl>=0?'good':'bad')}
      ${metricCard(pct(a.max_drawdown_on_committed_capital_pct),'max DD / committed BP','bad')}
    </div>
    <div class="risk"><b>TAIL-RISK RESULT</b> · The strategy won ${pct(a.win_rate_pct)} of trades but lost ${money(Math.abs(a.total_pnl))}. Worst case: ${esc(w.symbol)} ${esc(w.expiry)} ${Number(w.strike||0).toFixed(0)}P, ${signedMoney(w.pnl)}. Quoted margin yield was not evidence of edge.</div>
    <div class="bt-grid">
      <section class="section"><h3>Cumulative realized expiration P&amp;L</h3><div class="inside">${equityChart(b.trades||[])}</div></section>
      <section class="section"><h3>Regime comparison</h3><div class="regime head"><span>REGIME</span><span>N</span><span>WIN</span><span>ASSIGN</span><span class="optional">ANN YIELD/BP</span><span class="optional">MAX DD/BP</span><span>P&amp;L</span></div>${regimeRows}</section>
    </div>
    <section class="section"><h3>Per-symbol outcome map</h3>${symbolTable(m)}</section>
    <section class="section"><h3>Worst 12 trades · expiration mark</h3>${worstTrades(b.trades||[])}</section>
    <div class="bt-grid">
      <section class="section"><h3>Methodology contract</h3><div class="inside"><div class="method">${methods.map(x=>`<span class="pill">${esc(x)}</span>`).join('')}</div></div></section>
      <section class="section"><h3>Schwab split-adjustment checks</h3><table><thead><tr><th>SYM</th><th>DATE</th><th>SPLIT</th><th>MOVE</th><th>STATUS</th></tr></thead><tbody>${splits}</tbody></table></section>
    </div>
    <section class="section"><details><summary>Open full S4 research report</summary><div class="inside"><pre class="report">${esc(b.report||'')}</pre></div></details></section>
  </main>`;
}
function toggle(i){openRow=(openRow===i?null:i);render();}

async function rescan(){
  const b=document.getElementById('rescan');b.disabled=true;b.textContent='Scanning…';
  await fetch('/api/rescan');
  const poll=setInterval(async()=>{
    const s=await fetch('/api/scan_status').then(r=>r.json());
    if(!s.running){clearInterval(poll);b.disabled=false;b.textContent='Re-scan';load();}
  },2000);
}
load();
setInterval(load,300000); // refresh every 5 min
"""


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print(f"TIP dashboard -> http://localhost:{PORT}  (Ctrl-C to stop)")
    print(f"DB: {DB}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")

if __name__ == "__main__":
    main()
