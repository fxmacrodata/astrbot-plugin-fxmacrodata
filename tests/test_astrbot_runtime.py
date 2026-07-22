from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

import pytest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _load_actual_plugin_module():
    package_name = "astrbot_fxmacrodata_actual_runtime"
    sys.modules.pop(f"{package_name}.main", None)
    sys.modules.pop(package_name, None)
    spec = importlib.util.spec_from_file_location(
        package_name,
        PLUGIN_ROOT / "__init__.py",
        submodule_search_locations=[str(PLUGIN_ROOT)],
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = package
    assert spec.loader is not None
    spec.loader.exec_module(package)
    return importlib.import_module(f"{package_name}.main")


def test_actual_astrbot_runtime_imports_and_registers_native_capabilities():
    pytest.importorskip("astrbot")
    from astrbot.core.agent.tool import FunctionTool

    module = _load_actual_plugin_module()

    class Context:
        def __init__(self):
            self.tools = []
            self.routes = []

        def add_llm_tools(self, *tools):
            self.tools.extend(tools)

        def register_web_api(self, route, handler, methods, description):
            self.routes.append((route, handler, methods, description))

    plugin = module.FXMacroDataPlugin(Context(), {})
    prompt = module.RemotePrompt(
        name="macro_briefing",
        description="Prepare a macro briefing.",
        arguments=(),
    )
    resource = module.RemoteResource(
        name="indicator_chart_view",
        uri="ui://fxmacrodata/indicator-chart.html",
        description="Interactive indicator chart.",
        mime_type="text/html;profile=mcp-app",
    )

    assert isinstance(plugin._build_prompt_tool(prompt), FunctionTool)
    assert isinstance(plugin._build_resource_tool(resource), FunctionTool)
    assert len(plugin.context.routes) == 13
