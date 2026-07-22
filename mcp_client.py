"""Public MCP transport adapter used by the AstrBot plugin.

This module deliberately contains no FXMacroData service implementation. It
discovers and calls the documented hosted MCP tools, prompts, and resources
over the public MCP transport so AstrBot follows the hosted catalogue instead
of carrying a hand-maintained capability list.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import CallToolResult

DEFAULT_MCP_URL = "https://mcp.fxmacrodata.com/mcp"
DEFAULT_TIMEOUT_SECONDS = 45
MIN_TIMEOUT_SECONDS = 5
MAX_TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class RemoteTool:
    """A tool schema advertised by the hosted FXMacroData MCP server."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class RemotePromptArgument:
    """One user-supplied argument accepted by a hosted MCP prompt."""

    name: str
    description: str
    required: bool


@dataclass(frozen=True)
class RemotePrompt:
    """A prompt advertised by the hosted FXMacroData MCP server."""

    name: str
    description: str
    arguments: tuple[RemotePromptArgument, ...]


@dataclass(frozen=True)
class RemoteResource:
    """A concrete resource advertised by the hosted FXMacroData MCP server."""

    name: str
    uri: str
    description: str
    mime_type: str | None


class FXMacroDataMcpClient:
    """Small, stateless client for the public hosted FXMacroData MCP service."""

    def __init__(
        self,
        *,
        access_token: str | None = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        mcp_url: str = DEFAULT_MCP_URL,
    ) -> None:
        self._access_token = (
            access_token.strip() if isinstance(access_token, str) else ""
        )
        self._timeout_seconds = max(
            MIN_TIMEOUT_SECONDS,
            min(int(timeout_seconds), MAX_TIMEOUT_SECONDS),
        )
        self._mcp_url = mcp_url

    @property
    def mcp_url(self) -> str:
        """Return the fixed public MCP endpoint used by this integration."""

        return self._mcp_url

    def _headers(self) -> dict[str, str] | None:
        """Return an optional user-scoped OAuth bearer token without logging it."""

        if not self._access_token:
            return None
        return {"Authorization": f"Bearer {self._access_token}"}

    async def list_tools(self) -> tuple[RemoteTool, ...]:
        """Discover every tool currently exposed by the hosted MCP server."""

        async with streamablehttp_client(
            self._mcp_url,
            headers=self._headers(),
            timeout=self._timeout_seconds,
        ) as streams:
            read_stream, write_stream, _ = streams
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = []
                cursor: str | None = None
                while True:
                    response = (
                        await session.list_tools()
                        if cursor is None
                        else await session.list_tools(cursor=cursor)
                    )
                    tools.extend(response.tools)
                    cursor = getattr(response, "nextCursor", None)
                    if not cursor:
                        break

        return tuple(
            RemoteTool(
                name=tool.name,
                description=tool.description or f"FXMacroData MCP tool: {tool.name}",
                input_schema=(
                    dict(tool.inputSchema)
                    if isinstance(tool.inputSchema, dict)
                    else {"type": "object", "properties": {}}
                ),
            )
            for tool in tools
        )

    async def list_prompts(self) -> tuple[RemotePrompt, ...]:
        """Discover every hosted reusable prompt, across all result pages."""

        async with streamablehttp_client(
            self._mcp_url,
            headers=self._headers(),
            timeout=self._timeout_seconds,
        ) as streams:
            read_stream, write_stream, _ = streams
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                prompts = []
                cursor: str | None = None
                while True:
                    response = (
                        await session.list_prompts()
                        if cursor is None
                        else await session.list_prompts(cursor=cursor)
                    )
                    prompts.extend(response.prompts)
                    cursor = getattr(response, "nextCursor", None)
                    if not cursor:
                        break

        return tuple(
            RemotePrompt(
                name=prompt.name,
                description=(
                    prompt.description or f"FXMacroData MCP prompt: {prompt.name}"
                ),
                arguments=tuple(
                    RemotePromptArgument(
                        name=argument.name,
                        description=argument.description or argument.name,
                        required=bool(argument.required),
                    )
                    for argument in (prompt.arguments or [])
                ),
            )
            for prompt in prompts
        )

    async def get_prompt(self, name: str, arguments: dict[str, str]):
        """Resolve one hosted prompt with its user-provided arguments."""

        async with streamablehttp_client(
            self._mcp_url,
            headers=self._headers(),
            timeout=self._timeout_seconds,
        ) as streams:
            read_stream, write_stream, _ = streams
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                return await session.get_prompt(name, arguments=arguments)

    async def list_resources(self) -> tuple[RemoteResource, ...]:
        """Discover every concrete hosted MCP resource, across all result pages."""

        async with streamablehttp_client(
            self._mcp_url,
            headers=self._headers(),
            timeout=self._timeout_seconds,
        ) as streams:
            read_stream, write_stream, _ = streams
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                resources = []
                cursor: str | None = None
                while True:
                    response = (
                        await session.list_resources()
                        if cursor is None
                        else await session.list_resources(cursor=cursor)
                    )
                    resources.extend(response.resources)
                    cursor = getattr(response, "nextCursor", None)
                    if not cursor:
                        break

        return tuple(
            RemoteResource(
                name=resource.name,
                uri=str(resource.uri),
                description=(
                    resource.description or f"FXMacroData MCP resource: {resource.name}"
                ),
                mime_type=resource.mimeType,
            )
            for resource in resources
        )

    async def read_resource(self, uri: str):
        """Read a concrete resource that was first advertised by the server."""

        async with streamablehttp_client(
            self._mcp_url,
            headers=self._headers(),
            timeout=self._timeout_seconds,
        ) as streams:
            read_stream, write_stream, _ = streams
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                return await session.read_resource(uri)  # type: ignore[arg-type]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> CallToolResult:
        """Call one hosted tool and preserve its structured MCP response."""

        async with streamablehttp_client(
            self._mcp_url,
            headers=self._headers(),
            timeout=self._timeout_seconds,
        ) as streams:
            read_stream, write_stream, _ = streams
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                return await session.call_tool(name, arguments=arguments)
