---
name: autoshard
description: "AutoShard: autonomous B20 token risk scanner agent on Base mainnet. Scans, scores, and tracks token risk with live RPC, SQLite ledger, and terminal UI."
version: 1.0.0
author: retarddegeneth
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [base, b20, scanner, risk, agent, autonomous]
    related_skills: [hermes-agent, quick-web-apps]
---

# AutoShard

An autonomous agent that continuously monitors Base B20 token addresses, collects on-chain signals, computes a 0–100 risk score, and maintains a local ledger.

## Responsibilities
- Scan new token addresses on Base mainnet or base-sepolia
- Pull on-chain metadata (name, symbol, decimals, block age)
- Detect hook patterns: pause, mint, set_fee
- Score risk as SAFE / WARN / DANGER
- Persist every scan in SQLite with factors and reasons
- Expose terminal UI and JSON export

## Startup
```bash
cd /data/data/com.termux/files/home/b20-scanner
python3 app.py
# UI at http://127.0.0.1:8080
# Health at http://127.0.0.1:8080/health
```

## Invocation
- `GET /` — leaderboard and scan form
- `POST /scan` — scan a token; fields: address, name, symbol, chain
- `GET /token/<address>` — per-token detail page
- `GET /refresh` — recalc scores from stored factors
- `GET /export` — JSON dump of ledger

## RPC
- Default endpoint: `https://mainnet.base.org`
- Override with env `BASE_RPC=https://your-rpc`
- Chain header uses `X-Chain-ID`:
  - base: `0x2105`
  - base-sepolia: `0x14a34`

## Selectors
- name: `0x06fdde03`
- symbol: `0x95d89b41`
- decimals: `0x313ce567`

## Risk model
Score starts at 100. Deductions:
- block_age < 100: -40 (EXTREMELY_NEW_CONTRACT)
- block_age < 10000: -20 (NEW_CONTRACT)
- top_holder_pct > 60: -30 (CONCENTRATED_TOP_HOLDER)
- top_holder_pct > 30: -15 (TOP_HOLDER)
- no liquidity: -25 (NO_REPORTED_LIQUIDITY)
- volume/liquidity ratio >10x: -10 (VOLUME_ILLIQUID_RATIO_SUSPICIOUS)
- hooks: pause (-10), mint (-15), set_fee (tracked)
- transfer_tax_cap_extreme: -20 (flagged)

Classification:
- SAFE >= 70
- WARN >= 35
- DANGER < 35

## Data store
- File: `b20.db`
- Table: `tokens (address PK, name, symbol, risk_score, classification, extra_json, last_scanned, created_at)`

## Concurrency rules
- Default port: 8080
- If another app binds 8080, stop it: `pkill -f "python3 app.py"` in the other project dir
- For public access: use `ngrok http 8080` or deploy backend to Vercel/Railway
- Do not expose `bankr-shilling` and `b20-scanner` on the same port simultaneously

## Verification
```bash
curl http://127.0.0.1:8080/health   # expect {"ok":true,...}
curl -I http://127.0.0.1:8080/      # expect 200
python3 -c "import sqlite3; ..."    # confirm tokens table exists and populated
```

## Upgrades
- Add holder concentration via ERC-20 `balanceOf` over a holder set
- Decode transfer tax from pair logs
- Add contract verification check (bytecode vs source)
- Bridge events to external notifier (Telegram, X, Discord)
