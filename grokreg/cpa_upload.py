"""CLIProxy management upload — compat re-export.

Canonical: ``grokreg.ops.cpa_upload``.
"CPA" here means CLIProxy auth-file packs, not OAuth protocol.
"""
from __future__ import annotations

from .ops.cpa_upload import *  # noqa: F403
from .ops import cpa_upload as _mod

__all__ = [n for n in dir(_mod) if not n.startswith("_")]
