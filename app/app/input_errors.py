"""Validation errors that carry a machine-readable code next to the operator message.

``InputError`` is an ``HTTPException`` (status 400 by default), so classic handlers keep showing ``detail``
while the new interface API returns ``{code, message}`` and translates the code.
"""
from __future__ import annotations

from fastapi import HTTPException


class InputError(HTTPException):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(status_code, message)
        self.code = code

    def __str__(self) -> str:
        # The operator message alone (HTTPException would prefix the status), so ``str(exc)`` in classic handlers stays readable.
        return str(self.detail)
