from __future__ import annotations

import importlib
import logging
import sys
import types
from dataclasses import dataclass
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from mcp.types import CallToolResult, TextContent, TextResourceContents


@pytest.fixture
def plugin_module(monkeypatch):
    @dataclass
    class FunctionTool:
        name: str
        description: str
        parameters: dict
        handler_module_path: str | None = None

        @classmethod
        def __class_getitem__(cls, item):
            return cls

    class Star:
        def __init__(self, context):
            self.context = context
            self._kv = {}

        async def get_kv_data(self, key, default):
            return self._kv.get(key, default)

        async def put_kv_data(self, key, value):
            self._kv[key] = value
            return None

    class MessageChain:
        def __init__(self):
            self.messages = []
            self.markdown = None

        def message(self, message):
            self.messages.append(message)
            return self

        def use_markdown(self, value):
            self.markdown = value
            return self

    class Filter:
        @staticmethod
        def command(name):
            def decorate(function):
                return function

            return decorate

    def register(*args):
        def decorate(cls):
            return cls

        return decorate

    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    api.AstrBotConfig = dict
    api.logger = logging.getLogger("astrbot-test")
    event = types.ModuleType("astrbot.api.event")
    event.AstrMessageEvent = object
    event.MessageChain = MessageChain
    event.filter = Filter
    star = types.ModuleType("astrbot.api.star")
    star.Context = object
    star.Star = Star
    star.register = register
    agent = types.ModuleType("astrbot.core.agent")
    agent_tool = types.ModuleType("astrbot.core.agent.tool")
    agent_tool.FunctionTool = FunctionTool
    run_context = types.ModuleType("astrbot.core.agent.run_context")
    run_context.ContextWrapper = object
    agent_context = types.ModuleType("astrbot.core.astr_agent_context")
    agent_context.AstrAgentContext = object
    web = types.ModuleType("astrbot.api.web")
    web.error_response = lambda message, **kwargs: {"error": message, **kwargs}
    web.json_response = lambda data=None, **kwargs: {"data": data, **kwargs}
    web.request = types.SimpleNamespace(query={})

    modules = {
        "astrbot": astrbot,
        "astrbot.api": api,
        "astrbot.api.event": event,
        "astrbot.api.star": star,
        "astrbot.core": types.ModuleType("astrbot.core"),
        "astrbot.core.agent": agent,
        "astrbot.core.agent.tool": agent_tool,
        "astrbot.core.agent.run_context": run_context,
        "astrbot.core.astr_agent_context": agent_context,
        "astrbot.api.web": web,
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    # The plugin is nested under ``extensions/`` in the FXMacroData monorepo,
    # but it is the repository root in the public AstrBot distribution repo.
    package_dir = Path(__file__).resolve().parents[1]
    sys.modules.pop("astrbot_fxmacrodata.main", None)
    sys.modules.pop("astrbot_fxmacrodata", None)

    spec = importlib.util.spec_from_file_location(
        "astrbot_fxmacrodata",
        package_dir / "__init__.py",
        submodule_search_locations=[str(package_dir)],
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules["astrbot_fxmacrodata"] = package
    assert spec.loader is not None
    spec.loader.exec_module(package)
    return importlib.import_module("astrbot_fxmacrodata.main")


class FakeContext:
    def __init__(self):
        self.tools = []
        self.web_apis = []

    def add_llm_tools(self, *tools):
        self.tools.extend(tools)

    def register_web_api(self, route, handler, methods, desc):
        self.web_apis.append((route, handler, methods, desc))


class FakeEvent:
    def __init__(
        self, message_str, sender_id="sender-1", platform="telegram", private=True
    ):
        self.message_str = message_str
        self._sender_id = sender_id
        self._platform = platform
        self._private = private
        self.unified_msg_origin = f"{platform}:group:group-1"

    def get_sender_id(self):
        return self._sender_id

    def get_platform_name(self):
        return self._platform

    def is_private_chat(self):
        return self._private

    def plain_result(self, text):
        return text


async def _client_for_context(client):
    return client


@pytest.mark.asyncio
async def test_plugin_registers_every_discovered_tool(plugin_module):
    context = FakeContext()
    plugin = plugin_module.FXMacroDataPlugin(context, {"request_timeout_seconds": 40})
    remote_tools = (
        plugin_module.RemoteTool(
            name="data_catalogue",
            description="List data.",
            input_schema={"type": "object", "properties": {"currency": {}}},
        ),
        plugin_module.RemoteTool(
            name="release_calendar",
            description="List releases.",
            input_schema={"type": "object", "properties": {"currency": {}}},
        ),
    )
    remote_prompts = (
        plugin_module.RemotePrompt(
            name="macro_briefing",
            description="Prepare a macro briefing.",
            arguments=(
                types.SimpleNamespace(
                    name="currency",
                    description="ISO currency code.",
                    required=True,
                ),
            ),
        ),
    )
    remote_resources = (
        plugin_module.RemoteResource(
            name="indicator_chart_view",
            uri="ui://fxmacrodata/indicator-chart.html",
            description="Interactive indicator chart.",
            mime_type="text/html;profile=mcp-app",
        ),
    )

    async def list_tools():
        return remote_tools

    async def list_prompts():
        return remote_prompts

    async def list_resources():
        return remote_resources

    plugin.public_client.list_tools = list_tools
    plugin.public_client.list_prompts = list_prompts
    plugin.public_client.list_resources = list_resources
    count = await plugin.refresh_tools()

    assert count == len(remote_tools) + len(remote_prompts) + len(remote_resources)
    assert [tool.name for tool in context.tools] == [
        "fxmacrodata_data_catalogue",
        "fxmacrodata_release_calendar",
        "fxmacrodata_prompt_macro_briefing",
        "fxmacrodata_resource_indicator_chart_view",
    ]
    assert context.tools[0].parameters == remote_tools[0].input_schema
    assert context.tools[2].parameters == {
        "type": "object",
        "properties": {
            "currency": {"type": "string", "description": "ISO currency code."}
        },
        "required": ["currency"],
        "additionalProperties": False,
    }
    assert context.tools[3].parameters == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    assert plugin._registered_tool_names == (
        "fxmacrodata_data_catalogue",
        "fxmacrodata_release_calendar",
        "fxmacrodata_prompt_macro_briefing",
        "fxmacrodata_resource_indicator_chart_view",
    )


@pytest.mark.asyncio
async def test_native_tool_preserves_structured_mcp_result(plugin_module):
    expected = CallToolResult(
        content=[TextContent(type="text", text="structured result")], isError=False
    )

    class Client:
        async def call_tool(self, name, arguments):
            assert name == "data_catalogue"
            assert arguments == {"currency": "usd"}
            return expected

    tool = plugin_module.FXMacroDataTool(
        name="data_catalogue",
        description="List data.",
        parameters={"type": "object", "properties": {}},
        remote_name="data_catalogue",
        client_for_context=lambda context: _client_for_context(Client()),
    )

    result = await tool.call(object(), currency="usd")

    assert result is expected


@pytest.mark.asyncio
async def test_native_prompt_and_resource_tools_preserve_mcp_content(plugin_module):
    class Client:
        async def get_prompt(self, name, arguments):
            assert name == "macro_briefing"
            assert arguments == {"currency": "USD"}
            return types.SimpleNamespace(
                messages=[
                    types.SimpleNamespace(
                        content=TextContent(type="text", text="Prompt message")
                    )
                ]
            )

        async def read_resource(self, uri):
            assert uri == "ui://fxmacrodata/indicator-chart.html"
            return types.SimpleNamespace(
                contents=[
                    TextResourceContents(
                        uri=uri,
                        mimeType="text/html",
                        text="<html><body>Chart</body></html>",
                    )
                ]
            )

    client = Client()
    prompt = plugin_module.FXMacroDataPromptTool(
        name="fxmacrodata_prompt_macro_briefing",
        description="Prompt.",
        parameters={"type": "object", "properties": {}},
        remote_name="macro_briefing",
        client_for_context=lambda context: _client_for_context(client),
    )
    resource = plugin_module.FXMacroDataResourceTool(
        name="fxmacrodata_resource_indicator_chart_view",
        description="Resource.",
        parameters={"type": "object", "properties": {}},
        remote_uri="ui://fxmacrodata/indicator-chart.html",
        client_for_context=lambda context: _client_for_context(client),
    )

    prompt_result = await prompt.call(object(), currency="USD")
    resource_result = await resource.call(object())

    assert prompt_result.content[0].text == "Prompt message"
    assert resource_result.content[0].resource.text == "<html><body>Chart</body></html>"


@pytest.mark.asyncio
async def test_plugin_page_exposes_live_prompt_and_resource_catalogues(plugin_module):
    context = FakeContext()
    plugin = plugin_module.FXMacroDataPlugin(context, {})
    prompt = plugin_module.RemotePrompt(
        name="macro_briefing",
        description="Prepare a macro briefing.",
        arguments=(
            types.SimpleNamespace(
                name="currency",
                description="ISO currency code.",
                required=True,
            ),
        ),
    )
    resource = plugin_module.RemoteResource(
        name="indicator_chart_view",
        uri="ui://fxmacrodata/indicator-chart.html",
        description="Interactive indicator chart.",
        mime_type="text/html;profile=mcp-app",
    )

    async def list_prompts():
        return (prompt,)

    async def get_prompt(name, arguments):
        assert (name, arguments) == ("macro_briefing", {"currency": "USD"})
        return types.SimpleNamespace(
            messages=[
                types.SimpleNamespace(
                    content=TextContent(type="text", text="Prompt result")
                )
            ]
        )

    async def list_resources():
        return (resource,)

    async def read_resource(uri):
        assert uri == resource.uri
        return types.SimpleNamespace(
            contents=[
                TextResourceContents(
                    uri=uri,
                    mimeType=resource.mime_type,
                    text="<html><body>Public chart</body></html>",
                )
            ]
        )

    plugin.public_client.list_prompts = list_prompts
    plugin.public_client.get_prompt = get_prompt
    plugin.public_client.list_resources = list_resources
    plugin.public_client.read_resource = read_resource

    plugin_module.request.query = {"name": "macro_briefing", "currency": "USD"}
    prompt_response = await plugin.page_prompt()
    plugin_module.request.query = {"name": "indicator_chart_view"}
    resource_response = await plugin.page_resource()

    assert prompt_response["data"]["content"][0]["text"] == "Prompt result"
    assert resource_response["data"]["resource"]["name"] == "indicator_chart_view"
    assert resource_response["data"]["contents"][0]["text"] == (
        "<html><body>Public chart</body></html>"
    )


@pytest.mark.asyncio
async def test_status_command_recovers_from_initial_discovery_failure(
    plugin_module, monkeypatch
):
    monkeypatch.setenv(
        "FXMACRODATA_ASTRBOT_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode()
    )
    context = FakeContext()
    plugin = plugin_module.FXMacroDataPlugin(context, {})

    async def list_tools():
        return (
            plugin_module.RemoteTool(
                name="ping",
                description="Health check.",
                input_schema={"type": "object", "properties": {}},
            ),
        )

    async def list_prompts():
        return ()

    async def list_resources():
        return ()

    plugin.public_client.list_tools = list_tools
    plugin.public_client.list_prompts = list_prompts
    plugin.public_client.list_resources = list_resources
    responses = [
        item async for item in plugin.fxmacrodata(FakeEvent("/fxmacrodata status"))
    ]

    assert responses == [
        "FXMacroData is connected. 1 hosted MCP tools are registered for AstrBot "
        "function calling. Public data is available. Run `/fxmacrodata connect` for "
        "your protected FXMacroData access."
    ]


@pytest.mark.asyncio
async def test_release_alert_uses_exact_confirmed_calendar_time_and_builtin_cron(
    plugin_module,
):
    class CronManager:
        def __init__(self):
            self.calls = []

        async def add_basic_job(self, **kwargs):
            self.calls.append(kwargs)
            return types.SimpleNamespace(job_id="job-1")

        async def delete_job(self, job_id):
            return None

    context = FakeContext()
    context.cron_manager = CronManager()
    plugin = plugin_module.FXMacroDataPlugin(context, {})
    calendar = CallToolResult(
        content=[
            TextContent(
                type="text",
                text=(
                    "| Time | Release | Source | Confirmed |\n"
                    "|---|---|---|---|\n"
                    "| 2099-07-23T08:30:00-04:00 | Initial Jobless Claims | Official source | Yes |"
                ),
            )
        ],
        isError=False,
    )

    async def call_tool(name, arguments):
        assert name == "release_calendar"
        assert arguments["currency"] == "USD"
        return calendar

    plugin.public_client.call_tool = call_tool
    subscription = {
        "id": "sub-1",
        "kind": "release_alert",
        "session": "platform:friend:session",
        "currency": "USD",
        "indicator": "initial_jobless_claims",
        "schedule": {},
    }

    await plugin._schedule_next_release_alert(subscription)

    assert context.cron_manager.calls[0]["cron_expression"] == "30 12 23 7 *"
    assert context.cron_manager.calls[0]["timezone"] == "UTC"
    assert subscription["next_event_name"] == "Initial Jobless Claims"
    assert plugin.public_client._headers() is None
    assert len(context.web_apis) == 13


@pytest.mark.asyncio
async def test_connect_requires_a_private_chat(plugin_module, monkeypatch):
    monkeypatch.setenv(
        "FXMACRODATA_ASTRBOT_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode()
    )
    plugin = plugin_module.FXMacroDataPlugin(FakeContext(), {})

    responses = [
        item
        async for item in plugin.fxmacrodata(
            FakeEvent("/fxmacrodata connect", private=False)
        )
    ]

    assert "private chat" in responses[0]
