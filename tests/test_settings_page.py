"""Settings page: the Alerts rows and the desktop-only "This PC" pane (V7).

Hosted mode must never show or reach desktop-only settings, and the page must
never send a desktop patch the user didn't make. Static checks of the rendered
HTML plus a node run of the pane's pure helpers.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from app import shell
from app.auth_store import AuthStore
from app.config import Settings
from app.main import create_app
from app.store import ReadingStore
from tests.conftest import client_for, make_user

NODE = shutil.which("node")


def _page() -> str:
    return shell.render_shell(view="now", title="Today", body="<p>body</p>")


def _outside_template(page: str) -> str:
    """Rendered markup outside the inert template, scripts removed, whitespace collapsed."""
    start = page.index('<template id="desktopSettingsTpl">')
    end = page.index("</template>", start) + len("</template>")
    markup = re.sub(r"<script>[\s\S]*?</script>", "", page[:start] + page[end:])
    return re.sub(r"\s+", " ", markup)


def test_desktop_pane_lives_only_in_an_inert_template():
    page = _page()
    assert page.count('<template id="desktopSettingsTpl">') == 1
    visible = _outside_template(page)
    # No tab, no pane, no desktop control outside the template.
    assert 'data-pane="desktop"' not in visible
    for marker in ("dsFlashTarget", "dsAlarmVolume", "data-hotkey=", "dsControllerMode",
                   'data-key="autostart"', 'data-key="capture_exclusion"', "dsFirstRun"):
        assert marker not in visible, marker
    template = page[page.index('<template id="desktopSettingsTpl">'):]
    assert 'role="tab" data-pane="desktop"' in template
    assert "This PC" in template
    assert "not a medical alarm" in template
    assert "primary alarm" in template


def test_desktop_pane_is_mounted_only_by_the_desktop_code_path():
    js = shell.SHELL_JS
    # mount() only runs from mountDesktopSettings, which needs desktop mode AND the bridge.
    assert js.count(".mount(") == 1
    guard = re.search(r"function mountDesktopSettings\(\) \{(.*?)\n  \}", js, re.S)
    assert guard, "mountDesktopSettings missing"
    body = guard.group(1)
    assert "var invoke = tauriInvoke();" in body
    assert "!desktopMode || !invoke" in body
    assert body.index("return;") < body.index(".mount(")
    # The only caller is the /api/health handler, after the hosted early return.
    calls = [m.start() for m in re.finditer(r"mountDesktopSettings\(\);", js)]
    assert len(calls) == 1
    health = js.index("desktopMode = !!health.desktop_mode;")
    early_return = js.index("if (!desktopMode) return;", health)
    assert health < early_return < calls[0]
    # The helper script itself never invokes anything at load.
    loader = shell.DESKTOP_SETTINGS_JS
    assert "__TAURI__" not in loader
    assert loader.index("function mount(") < loader.index("invoke(")


def test_alerts_pane_rows_for_every_mode():
    visible = _outside_template(_page())
    assert re.search(r'id="alertSnoozeSetting" type="number" min="5" max="120"', visible)
    assert re.search(r'id="alertStaleSetting" type="number" min="30" max="300"', visible)
    assert 'id="awakeChimeSetting" type="checkbox" role="switch"' in visible
    assert "shortest snooze applies" in visible
    js = shell.SHELL_JS
    assert "key: 'alert_snooze_seconds', min: 5, max: 120, def: 15" in js
    assert "key: 'alert_stale_seconds', min: 30, max: 300, def: 60" in js
    assert "patchSelectedAccount({ dashboard_preferences: { awake_chime: wanted } })" in js


def test_ui_defaults_and_volume_bounds_match_the_contract():
    js = shell.DESKTOP_SETTINGS_JS
    assert "unmute_on_red: true, alarm_volume: 60," in js
    assert "controller_mode: 'off', controller_buttons: null," in js
    assert "keep_awake: true, game_mode: 'auto', capture_exclusion: false," in js
    assert ("hotkey_snooze: null, hotkey_acknowledge: null, hotkey_widget: null, "
            "hotkey_click_through: null,") in js
    assert "flash_target: 'secondary', alarm_sound: 'beep', alarm_device: 'default'," in js
    assert "var VOLUME_MIN = 20, VOLUME_MAX = 100;" in js
    assert 'type="range" min="20" max="100"' in shell.DESKTOP_SETTINGS_TEMPLATE
    # Volume is saved on release only.
    assert "volumeInput.addEventListener('change'" in js
    # Controller: only off / chosen exist, and "any" is never sent.
    assert "'any'" not in js
    assert 'value="any"' not in shell.DESKTOP_SETTINGS_TEMPLATE
    # Every desktop save goes through set_desktop_settings with a `patch` arg.
    assert js.count("call('set_desktop_settings', { patch: patch })") == 1
    assert "invoke('set_desktop_settings'" not in js


@pytest.mark.asyncio
async def test_served_page_hides_desktop_settings_in_hosted_mode(tmp_path):
    store = ReadingStore(tmp_path / "owlet.sqlite3")
    await store.init()
    auth = AuthStore(store.db_path)
    _, session = await make_user(auth, "owner@example.test")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    app = create_app(store=store, settings=settings, start_poller=False, auth_store=auth)
    with client_for(app, session) as client:
        assert client.get("/api/health").json()["desktop_mode"] is False
        page = client.get("/night").text
    assert 'id="alertSnoozeSetting"' in page
    assert 'data-pane="desktop"' not in _outside_template(page)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_desktop_helpers_in_node():
    source = re.search(r"<script>([\s\S]*?)</script>", shell.DESKTOP_SETTINGS_JS).group(1)
    harness = """
const vm = require('node:vm');
const assert = require('node:assert/strict');
const calls = [];
const window = { __TAURI__: { core: { invoke: (...a) => { calls.push(a); return Promise.resolve(null); } } } };
const context = { window, document: { getElementById() { throw new Error('touched DOM'); } } };
vm.createContext(context);
vm.runInContext(SOURCE, context);
assert.equal(calls.length, 0, 'no invoke on load');
const ds = window.owletDesktopSettings;
const ev = (code, mods = {}) => Object.assign({ code, key: mods.key || code,
  ctrlKey: false, altKey: false, shiftKey: false, metaKey: false }, mods);
assert.equal(ds.hotkeyFromEvent(ev('KeyS', { ctrlKey: true, shiftKey: true })).combo, 'Ctrl+Shift+KeyS');
assert.equal(ds.hotkeyFromEvent(ev('F9', { altKey: true })).combo, 'Alt+F9');
assert.equal(ds.hotkeyFromEvent(ev('KeyA', { metaKey: true, shiftKey: true, altKey: true, ctrlKey: true })).combo,
  'Ctrl+Alt+Shift+Super+KeyA');
assert.equal(ds.hotkeyFromEvent(ev('F24')).combo, 'F24');
assert.equal(ds.hotkeyFromEvent(ev('F1')).combo, 'F1');
assert.ok(ds.hotkeyFromEvent(ev('F25')).error);
assert.ok(ds.hotkeyFromEvent(ev('KeyS')).error, 'plain letter needs a modifier');
assert.ok(ds.hotkeyFromEvent(ev('Digit1', { shiftKey: false })).error);
assert.ok(ds.hotkeyFromEvent(ev('ShiftLeft', { shiftKey: true, key: 'Shift' })).modifierOnly);
assert.ok(ds.hotkeyFromEvent(ev('ControlRight', { ctrlKey: true, key: 'Control' })).modifierOnly);
assert.ok(ds.hotkeyFromEvent(ev('MetaLeft', { metaKey: true, key: 'Meta' })).modifierOnly);
assert.equal(ds.hotkeyLabel('Ctrl+Shift+KeyS'), 'Ctrl + Shift + S');
assert.equal(ds.hotkeyLabel('Alt+F9'), 'Alt + F9');
assert.equal(ds.hotkeyLabel('Super+Digit3'), 'Win + 3');
assert.equal(ds.hotkeyLabel(null), 'Not set');
assert.deepEqual(Array.from(ds.buttonNames(0x0040 | 0x0080)), ['Left stick click / L3', 'Right stick click / R3']);
assert.deepEqual(Array.from(ds.buttonNames(0x0010 | 0x0020)), ['Menu / Options', 'View / Share']);
assert.deepEqual(Array.from(ds.buttonNames(0x1000 | 0x8000 | 0x0100)), ['LB / L1', 'A / Cross', 'Y / Triangle']);
assert.deepEqual(Array.from(ds.buttonNames(0x0400 | 0x0800)), ['PS button', 'Touchpad click']);
assert.deepEqual(Array.from(ds.buttonNames(0x2000 | 0x4000 | 0x0200)), ['RB / R1', 'B / Circle', 'X / Square']);
assert.deepEqual(Array.from(ds.buttonNames(0)), []);
assert.deepEqual(Array.from(ds.buttonNames(null)), []);
assert.equal(ds.DEFAULTS.unmute_on_red, true);
assert.equal(ds.DEFAULTS.alarm_volume, 60);
assert.equal(ds.DEFAULTS.controller_mode, 'off');
assert.equal(ds.DEFAULTS.keep_awake, true);
console.log('ok');
""".replace("SOURCE", json.dumps(source))
    result = subprocess.run([NODE, "-e", harness], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
