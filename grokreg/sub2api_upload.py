"""sub2api remote import — compat re-export.

Canonical: ``grokreg.ops.sub2api_upload``.
"""
from __future__ import annotations

from .ops.sub2api_upload import *  # noqa: F403
from .ops import sub2api_upload as _mod

__all__ = [n for n in dir(_mod) if not n.startswith("_")]
