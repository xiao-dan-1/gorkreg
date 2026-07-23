"""Registration pipeline (P1): orchestration extracted from cli."""

from .register import (
    RegisterOptions,
    annotate_result_error,
    register_one,
    result_ok,
    result_retryable,
)

__all__ = [
    "RegisterOptions",
    "annotate_result_error",
    "register_one",
    "result_ok",
    "result_retryable",
]
