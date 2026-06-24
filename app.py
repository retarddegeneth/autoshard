#!/usr/bin/env python3
import sqlite3, json, re, os, urllib.request
from datetime import datetime, timezone
from flask import Flask, render_template_string, request, redirect, url_for, jsonify

app = Flask(__name__)
DB_PATH = os.environ.get("B20_DB", "b20.db")
BASE_RPC = os.environ.get("BASE_RPC", "https://mainnet.base.org")
BASE_IDS = {"base": "0x2105", "base-sepolia": "0x14a34"}
SELECTORS = {
    "name": "0x06fdde03",
    "symbol": "0x95d89b41",
    "decimals": "0x313ce567",
}

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AutoShard</title>
<style>
  :root { --bg:#050505; --green:#33ff33; --muted:#3a3a3a; --amber:#ffb300; --red:#ff3333; }
  * { box-sizing: border-box; }
  html, body { background: var(--bg); color: var(--green); font-family: 'Courier New', Courier, monospace; margin:0; padding:0; font-size:15px; }
  .wrap { max-width: 1100px; margin: 0 auto; padding: 18px 16px; }
  header { border: 1px solid #1a8c1a; padding: 12px; margin-bottom: 18px; }
  table { width:100%; border-collapse: collapse; margin: 14px 0; font-size:13px; }
  th, td { border: 1px solid var(--muted); padding: 8px 10px; text-align: left; }
  th { color: var(--amber); background: #0a0a0a; }
  tr:nth-child(even) { background: #070707; }
  form { border: 1px solid var(--muted); padding: 12px; margin: 18px 0; max-width: 600px; }
  input, select { font-family: inherit; color: var(--green); background: #0a0a0a; border: 1px solid var(--muted); padding: 6px; width: 100%; margin-bottom: 8px; }
  button { background: var(--green); color: var(--bg); border: 1px solid var(--green); font-weight: bold; padding: 7px 12px; cursor: pointer; font-family: inherit; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="logo">[AUTOSHARD v1]</div>
    <div class="tagline">> base b20 token risk agent</div>
    <div class="cli-prompt">{{ timestamp }}</div>
  </header>
  {% if error %}
  <div style="color:var(--red);border:1px solid var(--red);padding:8px;">[ERROR] {{ error }}</div>
  {% endif %}
  <form method="post" action="/scan">
    <label>TOKEN ADDRESS</label>
    <input name="address" required placeholder="0x..." value="{{ prefill or '' }}">
    <label>NAME</label>
    <input name="name" placeholder="auto-fetched">
    <label>SYMBOL</label>
    <input name="symbol" placeholder="auto-fetched">
    <label>CHAIN</label>
    <select name="chain">
      <option value="base">BASE MAINNET</option>
      <option value="base-sepolia">BASE-SEPOLIA</option>
    </select>
    <button type="submit">SCAN</button>
  </form>
  <div class="cli-prompt">[ LEDGER — {{ count || 0 }} entries ]</div>
  <table>
    <tr><th>ADDRESS</th><th>NAME</th><th>SYMBOL</th><th>SCORE</th><th>CLASS</th><th>LAST</th></tr>
    {% for t in tokens %}
    <tr>
      <td><a href="/token/{{ t.address }}">{{ t.address[:10] }}..{{ t.address[-6:] }}</a></td>
      <td>{{ t.name or '-' }}</td>
      <td>{{ t.symbol or '-' }}</td>
      <td>{{ "%.1f"|format(t.risk_score) }}</td>
      <td>{{ t.classification.upper() }}</td>
      <td>{{ t.last_scanned or '-' }}</td>
    </tr>
    {% endfor %}
  </table>
  <p><a href="/refresh">REFRESH ALL</a> • <a href="/export">EXPORT JSON</a></p>
</div>
</body>
</html>
"""

TOKEN_TMPL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>AutoShard — {{ t.symbol or t.address[:8] }}</title>
<style>
  :root { --bg:#050505; --green:#33ff33; --amber:#ffb300; --muted:#3a3a3a; }
  * { box-sizing: border-box; }
  html, body { background: var(--bg); color: var(--green); font-family: 'Courier New', Courier, monospace; margin:0; padding:0; }
  .wrap { max-width:900px; margin:0 auto; padding:18px 16px; }
  a { color: var(--green); }
  table { width:100%; border-collapse: collapse; margin-top:12px; font-size:13px; }
  th, td { border: 1px solid var(--muted); padding: 7px; text-align: left; }
  th { color: var(--amber); }
  pre { background:#0a0a0a; border:1px solid var(--muted); padding:10px; overflow:auto; font-size:12px; }
</style>
</head>
<body>
<div class="wrap">
  <a href="/">[BACK]</a>
  <table>
    <tr><th>FIELD</th><th>VALUE</th></tr>
    <tr><td>ADDRESS</td><td>{{ t.address }}</td></tr>
    <tr><td>NAME</td><td>{{ t.name or '-' }}</td></tr>
    <tr><td>SYMBOL</td><td>{{ t.symbol or '-' }}</td></tr>
    <tr><td>RISK_SCORE</td><td>{{ "%.1f"|format(t.risk_score) }} / 100</td></tr>
    <tr><td>CLASSIFICATION</td><td>{{ t.classification.upper() }}</td></tr>
    <tr><td>SCANNED</td><td>{{ t.last_scanned }}</td></tr>
  </table>
  <h3 style="color:var(-- amber)">RISK VECTORS</h3>
  <pre>{{ factors_pretty }}</pre>
</div>
</body>
</html>
"""

def init_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS tokens (
        address TEXT PRIMARY KEY, name TEXT, symbol TEXT,
        risk_score REAL DEFAULT 0, classification TEXT DEFAULT 'unknown',
        extra_json TEXT, last_scanned TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    con.commit(); con.close()

def rpc(method, params, chain="base", timeout=15):
    payload = json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params}).encode()
    req = urllib.request.Request(BASE_RPC, data=payload, headers={
        "Content-Type":"application/json",
        "X-Chain-ID": BASE_IDS.get(chain, "0x2105"),
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())["result"]

def base_call(address, data, chain="base"):
    return rpc("eth_call", [{"to": address, "data": data}, "latest"], chain=chain)

def decode_hex(hex_str):
    if not hex_str or hex_str == "0x":
        return ""
    try:
        return bytes.fromhex(hex_str[130:]).decode("utf-8", errors="ignore").strip("\x00")
    except Exception:
        return ""

def fetch_token_live(address, chain="base"):
    err = {"rpc": 0}
    name = decode_hex(base_call(address, SELECTORS["name"], chain))
    if not name: err["rpc"] += 1; err["name_err"] = True
    symbol = decode_hex(base_call(address, SELECTORS["symbol"], chain))
    if not symbol: err["rpc"] += 1; err["sym_err"] = True
    decimals = 18
    try:
        raw_dec = base_call(address, SELECTORS["decimals"], chain)
        if raw_dec and raw_dec != "0x":
            decimals = int(raw_dec, 16)
    except Exception:
        err["rpc"] += 1; err["dec_err"] = True

    now_block = 0
    try:
        now_block = int(rpc("eth_blockNumber", [], chain=chain), 16)
    except Exception:
        err["rpc"] += 1; err["block_err"] = True

    block_age = now_block if now_block else 10**9
    total_supply = 0
    try:
        total_supply = int(base_call(address, "0x18160ddd", [], chain), 16) / (10 ** decimals)
    except Exception:
        pass

    hooks = []
    for label, sig in {"pause":"0x8456cb59","mint":"0x40c10f19","set_fee":"0x9e6b44b"}.items():
        try:
            res = base_call(address, sig, chain)
            if res and res not in ("0x", "0x0"):
                hooks.append(label)
        except Exception:
            pass
    factors = {"block_age": block_age, "top_holder_pct": 0, "volume_24h": 0, "liquidity_usd": 0, "hooks": hooks, "decimals": decimals, "total_supply": total_supply, "rpc_errors": err}
    return name, symbol, factors

def signature_risk(factors):
    score = 100.0
    reasons = []
    if factors.get("block_age", 10**9) < 100:
        score -= 40; reasons.append("EXTREMELY_NEW_CONTRACT (<100 blocks)")
    elif factors.get("block_age", 10**9) < 10000:
        score -= 20; reasons.append("NEW_CONTRACT (<10k blocks)")
    if factors.get("top_holder_pct", 0) > 60:
        score -= 30; reasons.append("CONCENTRATED_TOP_HOLDER(>60%)")
    elif factors.get("top_holder_pct", 0) > 30:
        score -= 15; reasons.append("TOP_HOLDER(>30%)")
    liq = float(factors.get("liquidity_usd", 0) or 0)
    vol = float(factors.get("volume_24h", 0) or 0)
    if liq == 0:
        score -= 25; reasons.append("NO_REPORTED_LIQUIDITY")
    elif vol > 0 and liq > 0 and vol > liq * 10:
        score -= 10; reasons.append("VOLUME_ILLIQUID_RATIO_SUSPICIOUS")
    for hook in (factors.get("hooks") or []):
        if hook == "pause": score -= 10; reasons.append("PAUSE_HOOK_DETECTED")
        if hook == "mint": score -= 15; reasons.append("DYNAMIC_MINT_HOOK_DETECTED")
        if hook == "set_fee": reasons.append("SET_FEE_HOOK_DETECTED")
    score = max(0.0, min(100.0, score))
    if score >= 70: cls = "safe"
    elif score >= 35: cls = "warn"
    else: cls = "danger"
    return score, cls, reasons

def scan_token(address, name="", symbol="", chain="base"):
    clean = address.strip()
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", clean):
        raise ValueError("Invalid address")
    live_name, live_symbol, factors = fetch_token_live(clean, chain=chain)
    if not name: name = live_name
    if not symbol: symbol = live_symbol
    risk, cls, reasons = signature_risk(factors)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    extra = {"factors": factors, "reasons": reasons, "chain": chain}
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""INSERT INTO tokens (address, name, symbol, risk_score, classification, extra_json, last_scanned)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(address) DO UPDATE SET
            name=excluded.name, symbol=excluded.symbol, risk_score=excluded.risk_score,
            classification=excluded.classification, extra_json=excluded.extra_json, last_scanned=excluded.last_scanned
    """, (clean, name, symbol, risk, cls, json.dumps(extra), now))
    con.commit(); con.close()
    return risk, cls, factors, reasons

def get_all_tokens():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT address, name, symbol, risk_score, classification, extra_json, last_scanned FROM tokens")
    rows = cur.fetchall(); con.close()
    out = []
    for r in rows:
        out.append({"address":r[0],"name":r[1],"symbol":r[2],"risk_score":r[3],"classification":r[4],
                    "extra_json":r[5] or "{}","last_scanned":r[6],
                    "factors":json.loads(r[5] or "{}").get("factors", {}),
                    "reasons":json.loads(r[5] or "{}").get("reasons", [])})
    return out

@app.route("/", methods=["GET"])
def index():
    init_db()
    prefill = request.args.get("address", "")
    tokens = get_all_tokens()
    return render_template_string(TEMPLATE, tokens=tokens, error=None, prefill=prefill, count=len(tokens),
                                  timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))

@app.route("/scan", methods=["POST"])
def scan():
    init_db()
    address = (request.form.get("address") or "").strip()
    name = (request.form.get("name") or "").strip()
    symbol = (request.form.get("symbol") or "").strip()
    chain = (request.form.get("chain") or "base").strip()
    try:
        scan_token(address, name, symbol, chain=chain)
    except Exception as e:
        return render_template_string(TEMPLATE, tokens=get_all_tokens(), error=str(e), prefill=address,
                                      count=len(get_all_tokens()),
                                      timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    return redirect(url_for("index", address=address))

@app.route("/token/<address>", methods=["GET"])
def token_sheet(address):
    init_db()
    tokens = get_all_tokens()
    t = next((x for x in tokens if x["address"].lower() == address.lower()), None)
    if not t: return redirect(url_for("index"))
    return render_template_string(TOKEN_TMPL, t=t, factors_pretty=json.dumps(t.get("factors", {}), indent=2))

@app.route("/refresh", methods=["GET"])
def refresh():
    init_db()
    tokens = get_all_tokens()
    for t in tokens:
        risk, cls, reasons = signature_risk(t.get("factors", {}))
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        extra = {"factors": t.get("factors", {}), "reasons": reasons}
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
        cur.execute("UPDATE tokens SET risk_score=?, classification=?, extra_json=?, last_scanned=? WHERE address=?",
                    (risk, cls, json.dumps(extra), now, t["address"]))
        con.commit(); con.close()
    return redirect(url_for("index"))

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")})

def run(host="0.0.0.0", port=8080, debug=False):
    init_db()
    app.run(host=host, port=port, debug=debug, use_reloader=False)

if __name__ == "__main__":
    run()
