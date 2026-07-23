# Public tree notes (gorkreg)

This directory is a **clean export** for a public GitHub repo named **gorkreg**.

## What is intentionally excluded

| Path | Why |
|------|-----|
| `client/` | Private GrokX Web console (not part of this release) |
| `requirements-client.txt`, `scripts/run_client.py`, `tests/test_client_*` | Client-only |
| `docs/GrokX客户端.md`, `docs/superpowers/` | Client / internal plans |
| Runtime secrets | `.env`, `auth.json`, `sso_roster.txt`, `output/`, `data/`, export dirs |

## What stays private

The full private workspace (with `client/` and full git history) remains at the original project path. **Do not delete it.**

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

This tree has a **new** git history (single initial commit), independent of the private monorepo history.
