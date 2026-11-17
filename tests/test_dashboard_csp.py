"""The dashboard must survive its own CSP.

The page ships a nonce-based policy with no `'unsafe-inline'`, which means an
`onclick="…"` attribute is *blocked by the browser* — the button renders and
does nothing. No HTTP-level test can see that, so this file asserts the shape of
the HTML instead: no inline handlers, and every control wired from the nonce'd
script.
"""

from __future__ import annotations

import re

from mcp_hub.dashboard import HTML

_INLINE_HANDLER = re.compile(
    r"\son(?:click|change|submit|input|load|error|keydown|keyup|mouseover|focus)\s*=",
    re.IGNORECASE,
)


def test_no_inline_event_handler_attributes():
    found = _INLINE_HANDLER.findall(HTML)
    assert found == [], f"CSP without 'unsafe-inline' blocks these: {found}"


def test_every_script_and_style_block_carries_the_nonce():
    assert "<script nonce=" in HTML
    assert "<style nonce=" in HTML
    assert HTML.count("<script") == HTML.count("<script nonce=")


def test_controls_are_wired_from_the_nonce_script():
    for button in ("btn-search", "btn-stats", "btn-runs"):
        assert f'id="{button}"' in HTML
        assert "addEventListener('click'" in HTML
        assert f"$('#{button}').addEventListener" in HTML
