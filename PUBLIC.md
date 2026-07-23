# Public tree notes (gorkreg)

This directory is a **clean export** for a public GitHub repo named **gorkreg**.

## What is intentionally excluded

| Path | Why |
|------|-----|
| `client/` | Private GrokX Web console |
| `requirements-client.txt`, `scripts/run_client.py`, `tests/test_client_*` | Client-only |
| **`docs/`** | Private ops handbooks / architecture notes |
| **`ref/`** | Third-party reference slices (not product runtime) |
| Runtime secrets | `.env`, `auth.json`, `sso_roster.txt`, `output/`, `data/`, export dirs |

## What is included

Protocol CLI only: `grokreg/`, `main.py`, `scripts/` (batch/mint helpers), offline `tests/`, Docker samples, `.env.example` / `config.example.yaml`, root `README.md`.

## What stays private

The full private workspace (with `client/`, `docs/`, `ref/`, and full git history) remains at the original project path. **Do not delete it.**

## Package vs repo name

| Name | Role |
|------|------|
| **gorkreg** | Public repository name |
| **grokreg/** | Python package import path (`import grokreg`) |

## First push (when you are ready)

```bash
cd /path/to/gorkreg
# create empty GitHub repo "gorkreg" (private or public)
git remote add origin git@github.com:<you>/gorkreg.git
git push -u origin main
```

This tree has a **new** git history, independent of the private monorepo history.
