"""Runs the widget/overlay JavaScript tests under `pytest -q`, so CI covers them,
and checks the widget page's static promises: theme values, fonts, no network."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app import widget_page

NODE = shutil.which("node")
ROOT = Path(__file__).resolve().parents[1]
THEME = ROOT / "app" / "static" / "theme.css"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_widget_and_overlay_javascript():
    test_file = Path(__file__).with_name("widget_alerts.test.cjs")
    result = subprocess.run(
        [NODE, "--test", str(test_file)], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _tokens(block: str) -> dict[str, str]:
    return {
        name: " ".join(value.split())
        for name, value in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", block)
    }


def _dark_theme_tokens() -> dict[str, str]:
    css = THEME.read_text(encoding="utf-8")
    block = re.search(r':root,\s*:root\[data-theme="dark"\]\s*\{(.*?)\n\}', css, re.S)
    assert block, "dark token block not found in theme.css"
    return _tokens(block.group(1))


def _widget_root_tokens() -> dict[str, str]:
    html = widget_page.render()
    block = re.search(r":root\s*\{(.*?)\}", html, re.S)
    assert block, "widget :root block not found"
    return _tokens(block.group(1))


def test_widget_colours_are_the_theme_dark_tokens():
    dark = _dark_theme_tokens()
    widget = _widget_root_tokens()
    assert widget, "widget declares no tokens"
    for name, value in widget.items():
        assert name in dark, f"{name} is a private widget token, not a theme.css token"
        assert value == dark[name], f"{name}: widget {value!r} != theme.css dark {dark[name]!r}"


def test_widget_has_no_literal_colours_outside_its_token_block():
    html = widget_page.render()
    style = re.search(r"<style>(.*?)</style>", html, re.S).group(1)
    outside = re.sub(r":root\s*\{.*?\}", "", style, count=1, flags=re.S)
    script = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
    for where, text in (("CSS", outside), ("JS", script)):
        found = re.findall(r"(?<!&)#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", text)
        assert not found, f"literal colour in widget {where}: {found}"


def test_widget_stays_dark_whatever_the_app_theme():
    html = widget_page.render()
    # No theme switch and no light block: the page never reads data-theme or
    # prefers-color-scheme, and it does not load the themeable stylesheet.
    assert "data-theme" not in html
    assert "prefers-color-scheme" not in html
    assert not re.search(r"<link\b|@import", html)
    assert 'name="color-scheme" content="dark"' in html


def test_widget_requests_only_its_own_server_and_bundled_fonts():
    html = widget_page.render()
    assert not re.search(r"(https?:)?//[a-z0-9.-]+\.[a-z]{2,}", html, re.I), "external URL in widget"
    fonts = re.findall(r"url\('(/fonts/[^']+)'\)", html)
    assert fonts, "widget does not declare the bundled font"
    for url in fonts:
        assert (ROOT / "app" / "static" / url.lstrip("/")).is_file(), url
    assert "'Source Sans 3', \"Segoe UI\"" in html


def test_widget_default_markup_reads_before_any_data():
    html = widget_page.render()
    assert 'id="status">Connecting…<' in html
    assert ">Snooze 15 s<" in html  # replaced by snooze_seconds on the first answer
