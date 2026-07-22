"""Public-response presentation helpers for the AstrBot integration.

The helpers only reshape data returned by the hosted MCP server.  They do not
contain FXMacroData data, service implementation, credentials, or private
business logic.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from mcp.types import CallToolResult, EmbeddedResource


def text_blocks(result: CallToolResult) -> tuple[str, ...]:
    """Return non-empty public text blocks from an MCP tool result."""

    blocks: list[str] = []
    for item in result.content:
        text = getattr(item, "text", None)
        if isinstance(text, str) and text.strip():
            blocks.append(text.strip())
    return tuple(blocks)


def result_for_page(result: CallToolResult) -> dict[str, Any]:
    """Serialize a hosted MCP response for the authenticated plugin page.

    Text that is valid JSON is decoded so the page can display data in cards
    and tables. Other text remains text for faithful Markdown rendering.
    """

    content: list[dict[str, Any]] = []
    for item in result.content:
        item_type = str(getattr(item, "type", "unknown"))
        text = getattr(item, "text", None)
        if isinstance(text, str):
            try:
                content.append({"type": item_type, "data": json.loads(text)})
            except json.JSONDecodeError:
                content.append({"type": item_type, "text": text})
            continue

        uri = getattr(item, "uri", None)
        name = getattr(item, "name", None)
        if uri is not None:
            public_uri = str(uri)
            if not public_uri.startswith(("https://", "http://")):
                continue
            content.append(
                {
                    "type": item_type,
                    "uri": public_uri,
                    "name": name if isinstance(name, str) else "Open resource",
                }
            )

    return {"ok": not bool(result.isError), "content": content}


def prompt_result_for_page(prompt_response: Any) -> dict[str, Any]:
    """Present MCP prompt messages through the same safe page result shape."""

    messages = getattr(prompt_response, "messages", [])
    return result_for_page(
        CallToolResult(
            content=[message.content for message in messages],
            isError=False,
        )
    )


def prompt_result_for_tool(prompt_response: Any) -> CallToolResult:
    """Keep a resolved hosted prompt structured for AstrBot function calling."""

    return CallToolResult(
        content=[
            message.content for message in getattr(prompt_response, "messages", [])
        ],
        isError=False,
    )


def resource_result_for_tool(resource_response: Any) -> CallToolResult:
    """Keep an MCP resource structured when AstrBot calls its native tool."""

    return CallToolResult(
        content=[
            EmbeddedResource(type="resource", resource=content)
            for content in getattr(resource_response, "contents", [])
        ],
        isError=False,
    )


def resource_result_for_page(resource_response: Any) -> dict[str, Any]:
    """Serialize hosted resource content without executing it in the page host."""

    contents: list[dict[str, Any]] = []
    for resource in getattr(resource_response, "contents", []):
        text = getattr(resource, "text", None)
        blob = getattr(resource, "blob", None)
        item: dict[str, Any] = {
            "uri": str(getattr(resource, "uri", "")),
            "mime_type": getattr(resource, "mimeType", None),
        }
        if isinstance(text, str):
            item["text"] = text
        elif isinstance(blob, str):
            item["blob"] = blob
        contents.append(item)
    return {"ok": True, "contents": contents}


def format_chat_briefing(
    result: CallToolResult,
    *,
    heading: str,
    prefix: str | None = None,
    max_characters: int = 12_000,
) -> str:
    """Create a bounded Markdown-ready briefing for native chat delivery."""

    body = "\n\n".join(text_blocks(result)).strip()
    if not body:
        body = "No public result was returned for this request."
    if len(body) > max_characters:
        body = body[: max_characters - 1].rstrip() + "…"
    sections = [f"## {heading}"]
    if prefix:
        sections.append(prefix)
    sections.append(body)
    return "\n\n".join(sections)


def compact_lines(lines: Iterable[str], *, limit: int = 12) -> str:
    """Join a small number of already-safe display lines."""

    return "\n".join(line for _, line in zip(range(limit), lines))
