from __future__ import annotations

import json
import re
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PAGE_ROOT = PLUGIN_ROOT / "pages" / "macro-command-center"
I18N_ROOT = PLUGIN_ROOT / ".astrbot-plugin" / "i18n"


def test_plugin_page_uses_astrbot_relative_bridge_routes_and_a_module_script():
    html = (PAGE_ROOT / "index.html").read_text(encoding="utf-8")
    script = (PAGE_ROOT / "app.js").read_text(encoding="utf-8")

    assert '<script type="module" src="./app.js"></script>' in html
    assert 'apiGet("/' not in script
    assert 'apiPost("/' not in script
    assert "apiGet(`/${" not in script
    assert "bridge.onContext?.(applyContext)" in script
    assert 'frame.sandbox = "allow-scripts"' in script
    assert "allow-same-origin" not in script


def test_plugin_page_exposes_tools_prompts_and_resources_with_complete_i18n():
    html = (PAGE_ROOT / "index.html").read_text(encoding="utf-8")
    script = (PAGE_ROOT / "app.js").read_text(encoding="utf-8")
    keys = set(re.findall(r'data-i18n="([^"]+)"', html))
    keys.update(re.findall(r'(?<![\w.])text\("([^"]+)"', script))
    for locale in ("en-US", "zh-CN"):
        translations = json.loads(
            (I18N_ROOT / f"{locale}.json").read_text(encoding="utf-8")
        )
        assert keys.issubset(translations), (locale, sorted(keys - translations.keys()))

    assert 'data-panel="prompts"' in html
    assert 'data-panel="resources"' in html
    assert 'bridge.apiGet("prompt"' in script
    assert 'bridge.apiGet("resource"' in script
