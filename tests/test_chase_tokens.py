"""Vendored Chase tokens: TIER 1 primitives plus shared aliases."""
from __future__ import annotations

import hashlib
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_STATIC = _REPO / "src" / "cfbmodel" / "static"
_ALIASES = _STATIC / "chase_tokens.css"
_TIER1 = _STATIC / "chase-tokens-v1.css"
SHARED_TOKENS_SHA256 = "d2a929732e081ed8d2d5208aa915829e693c767f2f14fb29e2bffafc188a1fcd"
TIER1_SHA256 = "3cd1f89f4e00618e1f006d2434fadc2f50617e7e220f8509b64be016598e03cf"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def test_chase_tokens_is_the_cross_sport_alias_file():
    assert _digest(_ALIASES) == SHARED_TOKENS_SHA256
    assert "var(--ca-ink-950)" in _ALIASES.read_text(encoding="utf-8")
    assert "@import url" not in _ALIASES.read_text(encoding="utf-8")


def test_tier1_primitives_are_vendored():
    assert _TIER1.is_file()
    assert _digest(_TIER1) == TIER1_SHA256
    text = _TIER1.read_text(encoding="utf-8")
    assert "#08090F" in text and "#9A6BFF" in text
