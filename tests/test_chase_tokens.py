"""Vendored Chase tokens must stay on the shared four-model seed."""
from __future__ import annotations

import hashlib
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_TOKENS = _REPO / "src" / "cfbmodel" / "static" / "chase_tokens.css"
SHARED_TOKENS_SHA256 = "13014f566ee570d283b12859a6578d12d179a4cc39aecf8845518700fb85e911"


def test_chase_tokens_is_the_cross_sport_seed():
    digest = hashlib.sha256(_TOKENS.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    assert digest == SHARED_TOKENS_SHA256
