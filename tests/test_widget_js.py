"""Runs the widget/overlay JavaScript tests under `pytest -q`, so CI covers them."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_widget_and_overlay_javascript():
    test_file = Path(__file__).with_name("widget_alerts.test.cjs")
    result = subprocess.run(
        [NODE, "--test", str(test_file)], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stdout + result.stderr
