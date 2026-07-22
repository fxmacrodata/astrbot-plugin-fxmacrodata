from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from mcp.types import CallToolResult, TextContent

import mcp_client


class FakeSession:
    def __init__(self, read_stream, write_stream):
        self.read_stream = read_stream
        self.write_stream = write_stream
        self.initialized = False
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def initialize(self):
        self.initialized = True

    async def list_tools(self):
        return SimpleNamespace(
            tools=[
                SimpleNamespace(
                    name="data_catalogue",
                    description="List public data.",
                    inputSchema={"type": "object", "properties": {}},
                ),
                SimpleNamespace(
                    name="ping",
                    description=None,
                    inputSchema=None,
                ),
            ]
        )

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return CallToolResult(
            content=[TextContent(type="text", text="ok")], isError=False
        )

    async def list_prompts(self):
        return SimpleNamespace(
            prompts=[
                SimpleNamespace(
                    name="macro_briefing",
                    description="Prepare a macro briefing.",
                    arguments=[
                        SimpleNamespace(
                            name="currency",
                            description="ISO currency code.",
                            required=True,
                        )
                    ],
                )
            ]
        )

    async def get_prompt(self, name, arguments):
        self.calls.append(("prompt", name, arguments))
        return SimpleNamespace(
            messages=[
                SimpleNamespace(content=TextContent(type="text", text="Prompt result"))
            ]
        )

    async def list_resources(self):
        return SimpleNamespace(
            resources=[
                SimpleNamespace(
                    name="indicator_chart_view",
                    uri="ui://fxmacrodata/indicator-chart.html",
                    description="Interactive indicator chart.",
                    mimeType="text/html;profile=mcp-app",
                )
            ]
        )

    async def read_resource(self, uri):
        self.calls.append(("resource", uri))
        return SimpleNamespace(
            contents=[
                SimpleNamespace(
                    uri=uri,
                    mimeType="text/html;profile=mcp-app",
                    text="<html><body>Public chart</body></html>",
                )
            ]
        )


@pytest.fixture
def transport(monkeypatch):
    state = {"requests": [], "sessions": []}

    @asynccontextmanager
    async def fake_transport(url, headers, timeout):
        state["requests"].append({"url": url, "headers": headers, "timeout": timeout})
        yield ("read", "write", lambda: None)

    def session_factory(read_stream, write_stream):
        session = FakeSession(read_stream, write_stream)
        state["sessions"].append(session)
        return session

    monkeypatch.setattr(mcp_client, "streamablehttp_client", fake_transport)
    monkeypatch.setattr(mcp_client, "ClientSession", session_factory)
    return state


@pytest.mark.asyncio
async def test_list_tools_preserves_live_schema_and_uses_user_oauth_bearer_auth(
    transport,
):
    client = mcp_client.FXMacroDataMcpClient(
        access_token="opaque-user-oauth-token", timeout_seconds=999
    )

    tools = await client.list_tools()

    assert [tool.name for tool in tools] == ["data_catalogue", "ping"]
    assert tools[0].input_schema == {"type": "object", "properties": {}}
    assert tools[1].input_schema == {"type": "object", "properties": {}}
    assert transport["requests"] == [
        {
            "url": mcp_client.DEFAULT_MCP_URL,
            "headers": {"Authorization": "Bearer opaque-user-oauth-token"},
            "timeout": mcp_client.MAX_TIMEOUT_SECONDS,
        }
    ]
    assert transport["sessions"][0].initialized is True


@pytest.mark.asyncio
async def test_call_tool_passes_arguments_without_auth_when_user_is_not_connected(
    transport,
):
    client = mcp_client.FXMacroDataMcpClient(access_token="", timeout_seconds=1)

    result = await client.call_tool("data_catalogue", {"currency": "usd"})

    assert result.isError is False
    assert transport["requests"][0]["headers"] is None
    assert transport["requests"][0]["timeout"] == mcp_client.MIN_TIMEOUT_SECONDS
    assert transport["sessions"][0].calls == [("data_catalogue", {"currency": "usd"})]


@pytest.mark.asyncio
async def test_prompt_and_resource_methods_preserve_hosted_mcp_primitives(transport):
    client = mcp_client.FXMacroDataMcpClient(access_token="")

    prompts = await client.list_prompts()
    prompt = await client.get_prompt("macro_briefing", {"currency": "USD"})
    resources = await client.list_resources()
    resource = await client.read_resource(resources[0].uri)

    assert prompts[0].name == "macro_briefing"
    assert prompts[0].arguments[0].required is True
    assert prompt.messages[0].content.text == "Prompt result"
    assert resources[0].name == "indicator_chart_view"
    assert resource.contents[0].mimeType == "text/html;profile=mcp-app"
    assert transport["sessions"][1].calls == [
        ("prompt", "macro_briefing", {"currency": "USD"})
    ]
    assert transport["sessions"][3].calls == [
        ("resource", "ui://fxmacrodata/indicator-chart.html")
    ]
