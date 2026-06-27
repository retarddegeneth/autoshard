# AutoShard

Base B20 token risk scanner. Headless JSON API. SQLite ledger. No UI.

```bash
python3 app.py
# -> http://127.0.0.1:8080
```

## API

- `GET /health` — status
- `POST /scan` — scan token; JSON body: `{"address":"0x...","name":"","symbol":"","chain":"base"}`
- `GET /ledger` — list all scanned tokens
- `POST /refresh` — recompute scores from stored factors

## Responses

```json
{"address":"0x...","name":"...","symbol":"...","risk_score":75.0,"classification":"safe","last_scanned":"...","factors":{...},"reasons":[...]}
```

## RPC

- Default: `https://mainnet.base.org`
- Env override: `BASE_RPC=https://your-rpc`
- Chain header: `X-Chain-ID` (`base=0x2105`, `base-sepolia=0x14a34`)

## Selectors

- name: `0x06fdde03`
- symbol: `0x95d89b41`
- decimals: `0x313ce567`

## Risk model

Score starts at 100. Deductions:

- block_age < 100: -40
- block_age < 10000: -20
- top_holder_pct > 60: -30
- top_holder_pct > 30: -15
- no liquidity: -25
- volume/liquidity >10x: -10
- hooks: pause (-10), mint (-15), set_fee (flagged)

Classification:

- SAFE >= 70
- WARN >= 35
- DANGER < 35

## Data store

- File: `b20.db`
- Table: `tokens (address PK, name, symbol, risk_score, classification, extra_json, last_scanned, created_at)`

## Verification

```bash
curl http://127.0.0.1:8080/health
curl -X POST -H 'Content-Type: application/json' -d '{"address":"0x4200000000000000000000000000000000000006","chain":"base"}' http://127.0.0.1:8080/scan
curl http://127.0.0.1:8080/ledger
```

## Notes

- No UI. Headless JSON API only.
- No paid APIs required.
- Port: 8080. Stop other flask apps first if needed.
