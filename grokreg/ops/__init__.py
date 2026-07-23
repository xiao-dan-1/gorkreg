"""Ops: ledger helpers + CLI command modules.

Public API (prefer from scripts):
  ledger_ops:
    SSO_ROSTER_FILE, read_sso_roster, append_sso_roster,
    record_register_success, migrate_sso_roster_ensure_passwords,
    account_evidence_dir, iter_account_evidence_paths,
    migrate_account_evidence_to_subdir,
    recover_sso_roster_from_output, audit_sso_ledgers, select_mint_todos,
    save_result, summary_error_bucket, resolve_auth_path
  env_cmds: run_proxy_preflight
"""
from __future__ import annotations

from .env_cmds import run_proxy_preflight
from .ledger_ops import (  # noqa: F401
    ACCOUNT_EVIDENCE_SUBDIR,
    SSO_ROSTER_FILE,
    account_evidence_dir,
    append_sso_roster,
    audit_sso_ledgers,
    existing_pool_emails,
    iter_account_evidence_paths,
    migrate_account_evidence_to_subdir,
    migrate_sso_roster_ensure_passwords,
    read_sso_roster,
    record_register_success,
    recover_sso_roster_from_output,
    resolve_auth_path,
    save_result,
    select_mint_todos,
    summary_error_bucket,
)

__all__ = [
    "ACCOUNT_EVIDENCE_SUBDIR",
    "SSO_ROSTER_FILE",
    "account_evidence_dir",
    "append_sso_roster",
    "audit_sso_ledgers",
    "existing_pool_emails",
    "iter_account_evidence_paths",
    "migrate_account_evidence_to_subdir",
    "migrate_sso_roster_ensure_passwords",
    "read_sso_roster",
    "record_register_success",
    "recover_sso_roster_from_output",
    "resolve_auth_path",
    "run_proxy_preflight",
    "save_result",
    "select_mint_todos",
    "summary_error_bucket",
]
