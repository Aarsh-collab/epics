"""Diagnostic error codes shared by the stub scaler and measurer."""

from __future__ import annotations


class StubError(Exception):
    """A run stopped for a reason the operator can act on.

    Codes: ``invalid_measurements``, ``unsupported_mesh``,
    ``alignment_ambiguous``, ``section_unsupported``, ``fit_failed``,
    ``export_mismatch``.
    """

    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
