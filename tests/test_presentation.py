from __future__ import annotations

from types import SimpleNamespace

from mcp.types import CallToolResult, ResourceLink, TextContent, TextResourceContents

from presentation import (
    format_chat_briefing,
    prompt_result_for_page,
    prompt_result_for_tool,
    resource_result_for_page,
    resource_result_for_tool,
    result_for_page,
)


def test_page_result_decodes_json_and_keeps_public_resource_links():
    result = CallToolResult(
        content=[
            TextContent(type="text", text='{"currency":"USD","value":3.2}'),
            ResourceLink(
                type="resource_link",
                uri="https://example.com/public",
                name="Public chart",
            ),
        ],
        isError=False,
    )

    payload = result_for_page(result)

    assert payload["ok"] is True
    assert payload["content"][0]["data"] == {"currency": "USD", "value": 3.2}
    assert payload["content"][1]["uri"] == "https://example.com/public"


def test_chat_briefing_is_markdown_ready_and_bounded():
    result = CallToolResult(
        content=[TextContent(type="text", text="Official macro result")], isError=False
    )

    message = format_chat_briefing(
        result, heading="USD briefing", prefix="Public result"
    )

    assert message == "## USD briefing\n\nPublic result\n\nOfficial macro result"


def test_prompt_and_resource_helpers_preserve_hosted_mcp_content():
    prompt = SimpleNamespace(
        messages=[
            SimpleNamespace(
                content=TextContent(type="text", text="Reusable prompt message")
            )
        ]
    )
    resource = SimpleNamespace(
        contents=[
            TextResourceContents(
                uri="ui://fxmacrodata/indicator-chart.html",
                mimeType="text/html;profile=mcp-app",
                text="<html><body>Chart</body></html>",
            )
        ]
    )

    assert (
        prompt_result_for_page(prompt)["content"][0]["text"]
        == "Reusable prompt message"
    )
    assert prompt_result_for_tool(prompt).content[0].text == "Reusable prompt message"
    assert (
        resource_result_for_page(resource)["contents"][0]["mime_type"]
        == "text/html;profile=mcp-app"
    )
    assert (
        resource_result_for_tool(resource).content[0].resource.text
        == "<html><body>Chart</body></html>"
    )
