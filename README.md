# gorkreg

Protocol-only tooling for **Grok / xAI** account registration and OAuth credential lifecycle.

This repository provides a command-line interface to:

1. Register accounts over HTTP (no browser automation as the primary path)
2. Obtain SSO material and mint OAuth tokens via the device-code flow
3. Maintain a local credential ledger (`auth.json`)
4. Refresh tokens, probe availability, and optionally export or upload packs

**Repository name:** `gorkreg`  
**Python package:** `grokreg` (`import grokreg`)

---

## Scope of this public tree

| Included | Not included |
|----------|----------------|
| Protocol CLI (`main.py`, `grokreg/`) | Web control console (`client/`) |
| Production helper scripts | Full internal handbooks (`docs/`) |
| Offline unit / contract tests | Third-party reference dumps (`ref/`) |
| Example env and config | Secrets, rosters, live ledgers |

Runtime artifacts (`.env`, `auth.json`, `sso_roster.txt`, `output/`, export directories) are gitignored and must never be committed.

---

## Architecture (data flow)

```text
Mailbox / CloudMail
        │
        ▼
   Registration  ──►  sso_roster.txt   (SSO index)
        │
        ▼
  Device-code mint ──►  auth.json      (credential source of truth)
        │
        ├── refresh / remint
        ├── probe-quota
        └── export ──► cpa_export | sub2api_export | cockpit_export
                              └── optional remote upload
```

**Ledger-first rule:** mint, refresh, probe, and `--summary` read and write **`auth.json` only**.  
Export packs are derived products, not the credential store.

---

## Requirements

| Item | Notes |
|------|--------|
| Python | 3.11 or newer recommended |
| Local proxy | Mixed port for nested CONNECT (default `127.0.0.1:7890`) |
| Residential chain | `PROXY_DYNAMIC_TEMPLATE` (per-account SID rotation) |
| Captcha provider | CapSolver, YesCaptcha, or 2Captcha |
| Optional | CloudMail admin API; CLIProxy / sub2api credentials for upload |

Dependencies for the protocol path:

```text
curl_cffi, PyYAML
```

Optional browser-based captcha path is listed in `requirements-browser.txt` and is not required for normal operation.

---

## Installation

```bash
git clone git@github.com:xiao-dan-1/gorkreg.git
cd gorkreg
python -m pip install -r requirements.txt
cp .env.example .env
cp config.example.yaml config.yaml
```

On Windows you may use `copy` instead of `cp`.

Configure `.env` before any live run. Prefer placing secrets only in `.env`; leave `config.yaml` as a thin shell when possible. Non-empty environment variables overlay YAML **in memory only** and are never written back to disk.

### Minimal environment

```env
CAPTCHA_BACKEND=capsolver
CAPSOLVER_API_KEY=

LOCAL_PROXY=http://127.0.0.1:7890
PROXY_DYNAMIC_TEMPLATE=user-region-US-sid-xxxxxxxx-t-5:password@host:port

# Required for CloudMail batch registration
CLOUDMAIL_URL=
CLOUDMAIL_ADMIN_EMAIL=
CLOUDMAIL_PASSWORD=
CLOUDMAIL_DOMAINS=

# Optional upload targets
# CPA_BASE_URL=
# CPA_SECRET_KEY=
# SUB2API_BASE_URL=
# SUB2API_ADMIN_EMAIL=
# SUB2API_ADMIN_PASSWORD=
```

---

## Proxy model

```text
Application (curl_cffi)
    → LOCAL_PROXY (local hop, e.g. Clash :7890)
        → dynamic residential upstream (CONNECT)
            → accounts.x.ai / auth.x.ai / …
```

| Traffic | Proxy |
|---------|--------|
| Register, mint, probe | `LOCAL_PROXY` (same hop1) |
| Mail fetch (Graph / IMAP) | Direct by default |
| Batch preflight | Nested CONNECT + full-chain probe; failure aborts with **zero** account burn |

Do **not** use a typical v2rayN HTTP port (e.g. `10808`) as `chain_via`: the second CONNECT often blackholes. System `HTTP_PROXY` / `HTTPS_PROXY` are not required for registration.

Verify:

```bash
python main.py --env-check
python main.py --check-chain
```

Expect `ready_for_register: yes`, `ready_for_mint: yes`, and `check-chain: OK`.

---

## Command reference (operations)

All commands are run from the repository root.

### Environment and chain

```bash
python main.py --env-check
python main.py --check-chain
python main.py --check-proxy --check-proxy-times 3
```

### Registration

```bash
# CloudMail batch (recommended)
CAPTCHA_BACKEND=capsolver \
  python scripts/prod_cloudmail_batch.py -n 20 -j 6 --account-timeout 90 --ascii-log

# Single CloudMail account
python main.py --register-cloudmail -v

# File batch (e.g. Outlook lines: email----password----client_id----refresh_token)
python main.py --batch mails.txt --region US -j 2 --ascii-log
```

Successful registrations append to `sso_roster.txt` in the form:

```text
email----password----sso
```

**Concurrency guidance**

| Mode | Jobs (`-j`) |
|------|-------------|
| Stable | 2–4 |
| Throughput | 6–8 |
| Long runs (hundreds+) | Prefer 6–8; avoid j=12 as default |

Primary KPIs: success rate (`ok%`) and per-success latency. Aggregate throughput can be skewed by a few slow accounts.

### Mint (ledger ingress)

```bash
python main.py --mint all --mint-missing --no-probe -j 4 --ascii-log
python main.py --mint all --mint-missing --limit 10 --no-probe -j 2 --ascii-log
```

- `--mint-missing` processes roster emails absent from `auth.json`
- `--limit` applies after filtering; mint-missing prefers **newest** SSO entries
- Default mint publishes tokens to **`auth.json` only** (`packs=[]`)

### Status, probe, refresh

```bash
python main.py --summary
python main.py --auth-status all --needs-refresh-only

python main.py --probe-quota all --probe-mode models -j 6
python main.py --probe-quota EMAIL --probe-mode billing
python main.py --probe-quota EMAIL --probe-mode chat

python main.py --refresh all --needs-refresh-only --remint-on-revoke --no-probe -j 8
```

| Mode | Purpose |
|------|---------|
| `models` | Token alive (fast) |
| `billing` | xAI billing windows |
| `chat` / `quota` | Usage-style limits from response headers |

Access tokens are short-lived (~6 hours). Revoked refresh tokens require remint with SSO; do not loop refresh alone.

### Export and upload

```bash
python main.py --export cpa
python main.py --export sub2api
python main.py --export cockpit

python main.py --cpa-upload all --cpa-missing -j 20
python main.py --sub2api-upload all --sub2api-on-exists overwrite
```

Upload endpoints and credentials are taken from the environment. Do not target production systems without explicit authorization.

### Roster maintenance

```bash
python main.py --sso-audit
python main.py --recover-sso-roster --recover-dry-run
python main.py --recover-sso-roster
```

| Audit signal | Typical action |
|--------------|----------------|
| `in_cli_not_auth` | `--mint all --mint-missing` |
| `in_output_not_cli` | `--recover-sso-roster` |

---

## Captcha backends

| `CAPTCHA_BACKEND` | Behavior |
|-------------------|----------|
| `capsolver` | CapSolver only (`AntiTurnstileTaskProxyLess`) |
| `yescaptcha` | YesCaptcha only |
| `twocaptcha` | 2Captcha only |
| `auto` | Yes → Cap → 2C with balance soft-skip |

Resolution order: **CLI flag > environment > config.yaml > auto**.

Balance is checked before paid work; zero-balance conditions fuse the batch. Prefetch timeouts wait on already-paid tasks where possible to reduce double charging.

---

## Repository layout

```text
main.py                 CLI entry
grokreg/                Protocol core, OAuth, backends, ops
  pipeline/             Registration pipeline
  oauth/                Device-code mint, refresh, probe
  backends/             mail / captcha / export factories
  ops/                  Command implementations
scripts/                Batch and maintenance helpers
tests/                  Offline tests (no network)
.env.example
config.example.yaml
Dockerfile
docker-compose.yml
.github/workflows/      CI
```

---

## Development

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
```

Tests are intended to be offline contract checks. Full CLI help:

```bash
python main.py --help
```

---

## Security

- Never commit `.env`, `auth.json`, `sso_roster.txt`, refresh tokens, or management secrets
- Treat `sso_roster.txt` as sensitive (includes passwords when present)
- Do not paste credentials into issues or chat logs

---

## License

This project is released under the [MIT License](LICENSE).

---

## Disclaimer

This software interacts with third-party services under terms controlled by those providers. You are responsible for compliance with applicable terms of service, local law, and operational safety (rate limits, captcha cost, proxy policy). The maintainers provide the code as-is without warranty.
