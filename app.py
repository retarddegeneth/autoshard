#!/usr/bin/env python3
import sqlite3, json, re, os, urllib.request
from datetime import datetime, timezone
from flask import Flask, request, jsonify

app = Flask(__name__)
DB_PATH = os.environ.get("B20_DB", "b20.db")
BASE_RPC = os.environ.get("BASE_RPC", "https://mainnet.base.org")
BASE_IDS = {"base": "0x2105", "base-sepolia": "0x14a34"}
SELECTORS = {
    "name": "0x06fdde03",
    "symbol": "0x95d89b41",
    "decimals": "0x313ce567",
}


def init_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS tokens (
        address TEXT PRIMARY KEY,
        name TEXT,
        symbol TEXT,
        risk_score REAL DEFAULT 0,
        classification TEXT DEFAULT 'unknown',
        extra_json TEXT,
        last_scanned TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    con.commit()
    con.close()


def rpc(method, params, chain="base", timeout=15):
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(
        BASE_RPC,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Chain-ID": BASE_IDS.get(chain, "0x2105"),
            "User-Agent": "autoshard/1.0 (+https://github.com/retarddegeneth/autoshard)",
        },
    )
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
    if not name:
        err["rpc"] += 1
        err["name_err"] = True
    symbol = decode_hex(base_call(address, SELECTORS["symbol"], chain))
    if not symbol:
        err["rpc"] += 1
        err["sym_err"] = True
    decimals = 18
    try:
        raw_dec = base_call(address, SELECTORS["decimals"], chain)
        if raw_dec and raw_dec != "0x":
            decimals = int(raw_dec, 16)
    except Exception:
        err["rpc"] += 1
        err["dec_err"] = True

    now_block = 0
    try:
        now_block = int(rpc("eth_blockNumber", [], chain=chain), 16)
    except Exception:
        err["rpc"] += 1
        err["block_err"] = True

    creation_block = 0
    block_age = now_block - creation_block if now_block and creation_block else None
    total_supply = 0
    try:
        total_supply = int(base_call(address, "0x18160ddd", [], chain), 16) / (10 ** decimals)
    except Exception:
        err["rpc"] += 1
        err["supply_err"] = True

    hooks = []
    for label, sig in {"pause": "0x8456cb59", "mint": "0x40c10f19", "set_fee": "0x9e6b44b"}.items():
        try:
            res = base_call(address, sig, chain)
            if res and res not in ("0x", "0x0"):
                hooks.append(label)
        except Exception:
            err["hook_rpc"] = err.get("hook_rpc", 0) + 1

    factors = {
        "block_age": block_age,
        "top_holder_pct": 0,
        "volume_24h": 0,
        "liquidity_usd": 0,
        "hooks": hooks,
        "decimals": decimals,
        "total_supply": total_supply,
        "rpc_errors": err,
        "data_completeness": err["rpc"] == 0,
    }
    return name, symbol, factors


def signature_risk(factors):
    if not factors.get("data_completeness", False):
        return 0.0, "unknown", ["INSUFFICIENT_ONCHAIN_DATA"]

    score = 100.0
    reasons = []

    block_age = factors.get("block_age")
    if block_age is None:
        return 0.0, "unknown", ["MISSING_BLOCK_AGE"]

    if block_age < 100:
        score -= 40
        reasons.append("EXTREMELY_NEW_CONTRACT (<100 blocks)")
    elif block_age < 10000:
        score -= 20
        reasons.append("NEW_CONTRACT (<10k blocks)")

    top_holder_pct = factors.get("top_holder_pct", 0)
    if top_holder_pct == 0 and factors.get("top_holder_missing", False):
        return 0.0, "unknown", ["MISSING_HOLDER_DATA"]

    if top_holder_pct > 60:
        score -= 30
        reasons.append("CONCENTRATED_TOP_HOLDER(>60%)")
    elif top_holder_pct > 30:
        score -= 15
        reasons.append("TOP_HOLDER(>30%)")

    liq = float(factors.get("liquidity_usd", 0) or 0)
    vol = float(factors.get("volume_24h", 0) or 0)
    if liq == 0:
        score -= 25
        reasons.append("NO_REPORTED_LIQUIDITY")
    elif vol > 0 and liq > 0 and vol > liq * 10:
        score -= 10
        reasons.append("VOLUME_ILLIQUID_RATIO_SUSPICIOUS")

    for hook in (factors.get("hooks") or []):
        if hook == "pause":
            score -= 10
            reasons.append("PAUSE_HOOK_DETECTED")
        if hook == "mint":
            score -= 15
            reasons.append("DYNAMIC_MINT_HOOK_DETECTED")
        if hook == "set_fee":
            reasons.append("SET_FEE_HOOK_DETECTED")

    score = max(0.0, min(100.0, score))
    if score >= 70:
        cls = "safe"
    elif score >= 35:
        cls = "warn"
    else:
        cls = "danger"
    return score, cls, reasons


def scan_token(address, name="", symbol="", chain="base"):
    clean = address.strip()
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", clean):
        raise ValueError("Invalid address")
    live_name, live_symbol, factors = fetch_token_live(clean, chain=chain)
    if not name:
        name = live_name
    if not symbol:
        symbol = live_symbol
    risk, cls, reasons = signature_risk(factors)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    extra = {"factors": factors, "reasons": reasons, "chain": chain}
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        """INSERT INTO tokens (address, name, symbol, risk_score, classification, extra_json, last_scanned)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(address) DO UPDATE SET
               name=excluded.name, symbol=excluded.symbol, risk_score=excluded.risk_score,
               classification=excluded.classification, extra_json=excluded.extra_json, last_scanned=excluded.last_scanned
        """,
        (clean, name, symbol, risk, cls, json.dumps(extra), now),
    )
    con.commit()
    con.close()
    return {"address": clean, "name": name, "symbol": symbol, "risk_score": risk, "classification": cls, "last_scanned": now, "factors": factors, "reasons": reasons}


def get_all_tokens():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT address, name, symbol, risk_score, classification, extra_json, last_scanned FROM tokens")
    rows = cur.fetchall()
    con.close()
    out = []
    for r in rows:
        out.append({
            "address": r[0],
            "name": r[1],
            "symbol": r[2],
            "risk_score": r[3],
            "classification": r[4],
            "extra_json": r[5] or "{}",
            "last_scanned": r[6],
            "factors": json.loads(r[5] or "{}").get("factors", {}),
            "reasons": json.loads(r[5] or "{}").get("reasons", []),
        })
    return out


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")})


@app.route("/scan", methods=["POST"])
def scan():
    data = request.get_json(silent=True) or {}
    address = (data.get("address") or request.form.get("address") or "").strip()
    name = (data.get("name") or request.form.get("name") or "").strip()
    symbol = (data.get("symbol") or request.form.get("symbol") or "").strip()
    chain = (data.get("chain") or request.form.get("chain") or "base").strip()
    try:
        result = scan_token(address, name, symbol, chain=chain)
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(result)


@app.route("/ledger", methods=["GET"])
def ledger():
    return jsonify(get_all_tokens())


@app.route("/token/<address>", methods=["GET"])
def token(address):
    clean = address.strip()
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", clean):
        return jsonify({"error": "Invalid address"}), 400
    tokens = get_all_tokens()
    t = next((x for x in tokens if x["address"].lower() == clean.lower()), None)
    if not t:
        return jsonify({"error": "Not found"}), 404
    return jsonify(t)


@app.route("/refresh", methods=["POST"])
def refresh():
    tokens = get_all_tokens()
    updated = []
    for t in tokens:
        risk, cls, reasons = signature_risk(t.get("factors", {}))
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        extra = {"factors": t.get("factors", {}), "reasons": reasons}
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
        cur.execute("UPDATE tokens SET risk_score=?, classification=?, extra_json=?, last_scanned=? WHERE address=?",
                    (risk, cls, json.dumps(extra), now, t["address"]))
        con.commit()
        con.close()
        updated.append({"address": t["address"], "risk_score": risk, "classification": cls})
    return jsonify({"updated": updated})


def run(host="0.0.0.0", port=8080, debug=False):
    init_db()
    app.run(host=host, port=port, debug=debug, use_reloader=False)


if __name__ == "__main__":
    run()
