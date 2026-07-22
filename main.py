"""Native AstrBot integration for the hosted FXMacroData MCP service.

Every hosted MCP tool, prompt, and concrete resource is registered as an
AstrBot FunctionTool. The plugin adds only client-side AstrBot experiences: a
macro command centre, a bundled research skill, rich Markdown briefings, and opt-in session notifications.
It intentionally excludes FXMacroData service code, data, credentials, and
commercially sensitive implementation logic.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, register
try:
    from astrbot.api.web import error_response, json_response, request
except ModuleNotFoundError:  # AstrBot <= 4.25 exposes Quart directly.
    from quart import jsonify, request

    def json_response(data: Any = None, *, status_code: int = 200):
        response = jsonify(data if data is not None else {})
        response.status_code = status_code
        return response

    def error_response(message: str, *, status_code: int = 400):
        response = jsonify({"error": message})
        response.status_code = status_code
        return response
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool
from astrbot.core.astr_agent_context import AstrAgentContext
from mcp.types import CallToolResult, TextContent

from .alerts import (
    ReleaseEvent,
    next_release_event,
    parse_confirmed_release_events,
    validate_currency,
    validate_indicator,
    validate_time,
    validate_timezone,
    validate_weekday,
)
from .mcp_client import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_TIMEOUT_SECONDS,
    MIN_TIMEOUT_SECONDS,
    FXMacroDataMcpClient,
    RemotePrompt,
    RemoteResource,
    RemoteTool,
)
from .oauth import (
    EncryptedOAuthVault,
    FXMacroDataOAuthClient,
    OAuthConfigurationError,
    OAuthError,
    OAuthPendingError,
)
from .presentation import (
    format_chat_briefing,
    prompt_result_for_page,
    prompt_result_for_tool,
    resource_result_for_page,
    resource_result_for_tool,
    result_for_page,
    text_blocks,
)

PLUGIN_NAME = "astrbot_plugin_fxmacrodata"
TOOL_NAME_PREFIX = "fxmacrodata_"
ALERT_STORAGE_KEY = "fxmacrodata_macro_subscriptions_v1"
MAX_PAGE_INPUT_LENGTH = 4_000


class CommandUsageError(ValueError):
    """A user command did not provide a complete, safe public request."""


class SchedulerUnavailableError(RuntimeError):
    """The installed AstrBot version does not expose its cron manager."""


class PageRequestError(ValueError):
    """A Macro Command Centre request did not pass local validation."""


@dataclass
class FXMacroDataTool(FunctionTool):
    """An AstrBot FunctionTool that forwards to one hosted MCP tool."""

    remote_name: str = ""
    client_for_context: (
        Callable[[ContextWrapper[AstrAgentContext]], Awaitable[FXMacroDataMcpClient]]
        | None
    ) = field(default=None, repr=False)

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs: Any
    ) -> CallToolResult:
        """Call the matching hosted MCP tool with AstrBot's validated arguments."""

        if self.client_for_context is None:
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=(
                            "FXMacroData is not connected. Run `/fxmacrodata status` "
                            "and check the plugin connection."
                        ),
                    )
                ],
                isError=True,
            )
        try:
            client = await self.client_for_context(context)
            return await client.call_tool(self.remote_name, kwargs)
        except Exception:
            logger.warning("FXMacroData MCP tool call failed for %s", self.remote_name)
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=(
                            "FXMacroData could not complete this request. Check the "
                            "network connection, then run `/fxmacrodata status`. "
                            "Protected data requires the requesting user to run "
                            "`/fxmacrodata connect` and approve FXMacroData access."
                        ),
                    )
                ],
                isError=True,
            )


@dataclass
class FXMacroDataPromptTool(FunctionTool):
    """An AstrBot FunctionTool that resolves one hosted MCP prompt."""

    remote_name: str = ""
    client_for_context: (
        Callable[[ContextWrapper[AstrAgentContext]], Awaitable[FXMacroDataMcpClient]]
        | None
    ) = field(default=None, repr=False)

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs: Any
    ) -> CallToolResult:
        if self.client_for_context is None:
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text="FXMacroData is not connected. Run `/fxmacrodata status`.",
                    )
                ],
                isError=True,
            )
        try:
            client = await self.client_for_context(context)
            response = await client.get_prompt(
                self.remote_name,
                {name: str(value) for name, value in kwargs.items()},
            )
            return prompt_result_for_tool(response)
        except Exception:
            logger.warning(
                "FXMacroData MCP prompt resolution failed for %s", self.remote_name
            )
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=(
                            "FXMacroData could not resolve this reusable prompt. "
                            "Check the network connection, then run `/fxmacrodata status`."
                        ),
                    )
                ],
                isError=True,
            )


@dataclass
class FXMacroDataResourceTool(FunctionTool):
    """An AstrBot FunctionTool that returns one hosted MCP resource."""

    remote_uri: str = ""
    client_for_context: (
        Callable[[ContextWrapper[AstrAgentContext]], Awaitable[FXMacroDataMcpClient]]
        | None
    ) = field(default=None, repr=False)

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs: Any
    ) -> CallToolResult:
        del kwargs
        if self.client_for_context is None:
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text="FXMacroData is not connected. Run `/fxmacrodata status`.",
                    )
                ],
                isError=True,
            )
        try:
            client = await self.client_for_context(context)
            return resource_result_for_tool(await client.read_resource(self.remote_uri))
        except Exception:
            logger.warning("FXMacroData MCP resource read failed for %s", self.name)
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=(
                            "FXMacroData could not read this hosted resource. Check the "
                            "network connection, then run `/fxmacrodata status`."
                        ),
                    )
                ],
                isError=True,
            )


@register(
    PLUGIN_NAME,
    "FXMacroData",
    "Full FXMacroData MCP coverage with native AstrBot research, dashboard, and alerts.",
    "v1.3.0",
)
class FXMacroDataPlugin(Star):
    """Register the live hosted tool catalogue and complete AstrBot experience."""

    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config = config
        self._request_timeout_seconds = self._timeout_config()
        # The public client enables the no-key baseline. User-scoped clients
        # are created only from encrypted OAuth tokens held in private KV.
        self.public_client = FXMacroDataMcpClient(
            timeout_seconds=self._request_timeout_seconds
        )
        self.oauth_vault = EncryptedOAuthVault(
            self,
            FXMacroDataOAuthClient(timeout_seconds=self._request_timeout_seconds),
        )
        self._registered_tool_names: tuple[str, ...] = ()
        self._scheduled_job_ids: set[str] = set()
        self._jobs_by_subscription: dict[str, set[str]] = {}
        self._register_page_apis()

    def _timeout_config(self) -> int:
        value = self.config.get("request_timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        try:
            timeout = int(value)
        except (TypeError, ValueError):
            return DEFAULT_TIMEOUT_SECONDS
        return max(MIN_TIMEOUT_SECONDS, min(timeout, MAX_TIMEOUT_SECONDS))

    def _register_page_apis(self) -> None:
        """Register authenticated bridge routes for the bundled plugin page."""

        register_web_api = getattr(self.context, "register_web_api", None)
        if not callable(register_web_api):
            return
        prefix = f"/{PLUGIN_NAME}"
        register_web_api(f"{prefix}/health", self.page_health, ["GET"], "Plugin health")
        register_web_api(
            f"{prefix}/tools", self.page_tools, ["GET"], "Hosted tool inventory"
        )
        register_web_api(
            f"{prefix}/calendar", self.page_calendar, ["GET"], "Release calendar query"
        )
        register_web_api(
            f"{prefix}/indicator", self.page_indicator, ["GET"], "Indicator query"
        )
        register_web_api(
            f"{prefix}/research", self.page_research, ["GET"], "Macro research pack"
        )
        register_web_api(f"{prefix}/pair", self.page_pair, ["GET"], "FX pair research")
        register_web_api(
            f"{prefix}/prompts", self.page_prompts, ["GET"], "Hosted prompt inventory"
        )
        register_web_api(
            f"{prefix}/prompt", self.page_prompt, ["GET"], "Resolve a hosted prompt"
        )
        register_web_api(
            f"{prefix}/resources",
            self.page_resources,
            ["GET"],
            "Hosted resource inventory",
        )
        register_web_api(
            f"{prefix}/resource", self.page_resource, ["GET"], "Read a hosted resource"
        )
        register_web_api(
            f"{prefix}/auth/start", self.page_auth_start, ["POST"], "Start user sign-in"
        )
        register_web_api(
            f"{prefix}/auth/status",
            self.page_auth_status,
            ["GET"],
            "User sign-in status",
        )
        register_web_api(
            f"{prefix}/auth/disconnect",
            self.page_auth_disconnect,
            ["POST"],
            "Disconnect user",
        )

    async def initialize(self) -> None:
        """Discover hosted tools and rehydrate opt-in local notifications."""

        try:
            count = await self.refresh_tools()
            logger.info("FXMacroData registered %d live MCP tools", count)
        except Exception:
            logger.warning(
                "FXMacroData tool discovery failed; run /fxmacrodata status after "
                "checking network access."
            )
        await self._restore_subscriptions()

    async def refresh_tools(self) -> int:
        """Refresh every hosted MCP capability through native AstrBot tools."""

        remote_tools, remote_prompts, remote_resources = await asyncio.gather(
            self.public_client.list_tools(),
            self.public_client.list_prompts(),
            self.public_client.list_resources(),
        )
        native_tools = [
            *(self._build_tool(tool) for tool in remote_tools),
            *(self._build_prompt_tool(prompt) for prompt in remote_prompts),
            *(self._build_resource_tool(resource) for resource in remote_resources),
        ]
        if not native_tools:
            raise RuntimeError("The FXMacroData MCP server returned no capabilities.")
        self.context.add_llm_tools(*native_tools)
        self._registered_tool_names = tuple(tool.name for tool in native_tools)
        return len(native_tools)

    def _build_tool(self, tool: RemoteTool) -> FXMacroDataTool:
        """Convert one public MCP schema into an AstrBot FunctionTool."""

        return FXMacroDataTool(
            name=f"{TOOL_NAME_PREFIX}{tool.name}",
            description=f"FXMacroData: {tool.description}",
            parameters=tool.input_schema,
            remote_name=tool.name,
            client_for_context=self._client_for_tool_context,
        )

    def _build_prompt_tool(self, prompt: RemotePrompt) -> FXMacroDataPromptTool:
        """Expose each hosted MCP prompt in AstrBot's normal tool registry."""

        required = [argument.name for argument in prompt.arguments if argument.required]
        return FXMacroDataPromptTool(
            name=f"{TOOL_NAME_PREFIX}prompt_{prompt.name}",
            description=f"FXMacroData reusable prompt: {prompt.description}",
            parameters={
                "type": "object",
                "properties": {
                    argument.name: {
                        "type": "string",
                        "description": argument.description,
                    }
                    for argument in prompt.arguments
                },
                "required": required,
                "additionalProperties": False,
            },
            remote_name=prompt.name,
            client_for_context=self._client_for_tool_context,
        )

    def _build_resource_tool(self, resource: RemoteResource) -> FXMacroDataResourceTool:
        """Expose each concrete hosted MCP resource in AstrBot's tool registry."""

        return FXMacroDataResourceTool(
            name=f"{TOOL_NAME_PREFIX}resource_{resource.name}",
            description=(
                f"FXMacroData hosted resource: {resource.description} "
                f"(MIME type: {resource.mime_type or 'unspecified'})."
            ),
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            remote_uri=resource.uri,
            client_for_context=self._client_for_tool_context,
        )

    @staticmethod
    def _event_identity(event: AstrMessageEvent) -> str | None:
        """Build a personal, not group/session-scoped, AstrBot identity."""

        sender_getter = getattr(event, "get_sender_id", None)
        platform_getter = getattr(event, "get_platform_name", None)
        sender_id = sender_getter() if callable(sender_getter) else None
        platform = platform_getter() if callable(platform_getter) else None
        if not isinstance(sender_id, str) or not sender_id.strip():
            return None
        if not isinstance(platform, str) or not platform.strip():
            platform = "astrbot"
        return f"chat:{platform.strip()}:{sender_id.strip()}"

    async def _client_for_identity(self, identity: str | None) -> FXMacroDataMcpClient:
        if identity:
            try:
                access_token = await self.oauth_vault.access_token(identity)
            except OAuthConfigurationError:
                access_token = None
            if access_token:
                return FXMacroDataMcpClient(
                    access_token=access_token,
                    timeout_seconds=self._request_timeout_seconds,
                )
        return self.public_client

    async def _client_for_event(self, event: AstrMessageEvent) -> FXMacroDataMcpClient:
        return await self._client_for_identity(self._event_identity(event))

    async def _client_for_tool_context(
        self, context: ContextWrapper[AstrAgentContext]
    ) -> FXMacroDataMcpClient:
        agent_context = getattr(context, "context", None)
        event = getattr(agent_context, "event", None)
        return (
            await self._client_for_event(event)
            if event is not None
            else self.public_client
        )

    def _markdown_result(self, event: AstrMessageEvent, message: str):
        """Produce a native rich message, with a plain fallback for old adapters."""

        make_result = getattr(event, "make_result", None)
        if not callable(make_result):
            return event.plain_result(message)
        result = make_result().message(message)
        result.use_markdown(True)
        return result

    @filter.command("fxmacrodata")
    async def fxmacrodata(self, event: AstrMessageEvent):
        """Provide status, on-demand research, and explicit notification controls."""

        parts = (event.message_str or "").strip().split()
        action = parts[1].lower() if len(parts) > 1 else "status"
        try:
            if action in {"status", "refresh"}:
                yield await self._status_or_refresh(event)
                return
            if action == "connect":
                yield await self._connect_command(event, parts[2:])
                return
            if action == "disconnect":
                yield await self._disconnect_command(event)
                return
            if action == "briefing":
                yield await self._briefing_command(event, parts[2:])
                return
            if action == "alerts":
                yield await self._alerts_command(event, parts[2:])
                return
        except CommandUsageError as exc:
            yield event.plain_result(f"{exc}\n\n{self._command_usage()}")
            return
        except SchedulerUnavailableError:
            yield event.plain_result(
                "This AstrBot installation does not expose the built-in cron manager "
                "required for opt-in FXMacroData notifications. Upgrade AstrBot, then "
                "run the command again."
            )
            return
        except Exception:
            logger.warning("FXMacroData command failed for action %s", action)
            yield event.plain_result(
                "FXMacroData could not complete this command. Check network access and "
                "the plugin configuration, then try again."
            )
            return

        yield event.plain_result(self._command_usage())

    async def _status_or_refresh(self, event: AstrMessageEvent):
        count = await self.refresh_tools()
        identity = self._event_identity(event)
        try:
            connected = bool(identity) and await self.oauth_vault.is_connected(identity)
        except OAuthConfigurationError:
            connected = False
            auth_status = (
                "Public data is available. Protected access is unavailable until the "
                "AstrBot operator configures encrypted token storage."
            )
        else:
            auth_status = (
                "Personal FXMacroData access is connected."
                if connected
                else "Public data is available. Run `/fxmacrodata connect` for your protected FXMacroData access."
            )
        return event.plain_result(
            f"FXMacroData is connected. {count} hosted MCP tools are registered for "
            f"AstrBot function calling. {auth_status}"
        )

    async def _connect_command(self, event: AstrMessageEvent, args: list[str]):
        private_chat = getattr(event, "is_private_chat", None)
        if not callable(private_chat) or not private_chat():
            raise CommandUsageError(
                "For your security, start and complete FXMacroData connection in a private chat with this bot. You can still use your personal access in group chats afterwards."
            )
        identity = self._event_identity(event)
        if not identity:
            raise CommandUsageError(
                "AstrBot did not provide a personal sender ID, so it cannot safely link protected FXMacroData access."
            )
        action = args[0].lower() if args else "start"
        if action in {"status", "complete"}:
            try:
                await self.oauth_vault.complete(identity)
            except OAuthPendingError as exc:
                return event.plain_result(
                    f"FXMacroData connection is waiting for approval. {exc}"
                )
            except OAuthError as exc:
                return event.plain_result(str(exc))
            return event.plain_result(
                "FXMacroData is now connected to your personal AstrBot identity. Protected MCP tools and future opt-in briefings will use your plan."
            )
        if action != "start" or len(args) > 1:
            raise CommandUsageError("Use `connect` or `connect status`.")
        authorization = await self.oauth_vault.start(identity)
        return self._markdown_result(
            event,
            "## Connect your FXMacroData access\n\n"
            "Open this first-party FXMacroData link, approve the connection, then return here and run `/fxmacrodata connect status`. "
            "AstrBot never receives your API key.\n\n"
            f"[Open FXMacroData verification]({authorization.verification_uri_complete})\n\n"
            f"Verification code: `{authorization.user_code}`\n\n"
            f"This sign-in request expires in {max(authorization.expires_in // 60, 1)} minutes.",
        )

    async def _disconnect_command(self, event: AstrMessageEvent):
        identity = self._event_identity(event)
        if not identity:
            raise CommandUsageError(
                "AstrBot did not provide a personal sender ID, so no protected FXMacroData connection can be removed."
            )
        await self.oauth_vault.disconnect(identity)
        return event.plain_result(
            "Your FXMacroData connection has been removed from AstrBot and its refresh token has been revoked when reachable. Public data remains available."
        )

    def _command_usage(self) -> str:
        return (
            "Usage:\n"
            "/fxmacrodata [status|refresh]\n"
            "/fxmacrodata connect [status]\n"
            "/fxmacrodata disconnect\n"
            "/fxmacrodata briefing now <currency> <indicator> [base] [quote]\n"
            "/fxmacrodata briefing daily <currency> <indicator> [HH:MM] [IANA timezone]\n"
            "/fxmacrodata briefing weekly <currency> <indicator> [weekday] [HH:MM] [IANA timezone] (week-ahead)\n"
            "/fxmacrodata alerts subscribe <currency> [indicator]\n"
            "/fxmacrodata alerts list\n"
            "/fxmacrodata alerts remove <subscription-id|all>\n\n"
            "Examples: `/fxmacrodata briefing now USD inflation EUR USD`, "
            "`/fxmacrodata briefing daily USD inflation 08:00 Australia/Sydney`, "
            "`/fxmacrodata alerts subscribe USD inflation`. Notifications are opt-in "
            "for this chat session and can be removed at any time. Protected data is linked to each sender, not to a group chat."
        )

    async def _briefing_command(self, event: AstrMessageEvent, args: list[str]):
        if not args:
            raise CommandUsageError(
                "Choose `now`, `daily`, or `weekly` after briefing."
            )
        cadence = args[0].lower()
        if cadence == "now":
            if len(args) < 3:
                raise CommandUsageError(
                    "`briefing now` requires currency and indicator."
                )
            currency = validate_currency(args[1])
            indicator = validate_indicator(args[2])
            payload: dict[str, Any] = {"currency": currency, "indicator": indicator}
            if len(args) > 3:
                payload["base"] = validate_currency(args[3])
            if len(args) > 4:
                payload["quote"] = validate_currency(args[4])
            if len(args) > 5:
                raise CommandUsageError(
                    "`briefing now` accepts at most base and quote after indicator."
                )
            client = await self._client_for_event(event)
            result = await client.call_tool("macro_research_pack_task", payload)
            return self._markdown_result(
                event,
                format_chat_briefing(
                    result,
                    heading=f"FXMacroData briefing · {currency} {indicator}",
                    prefix="Hosted MCP research pack. Information only; not investment advice.",
                ),
            )

        if cadence not in {"daily", "weekly"}:
            raise CommandUsageError(
                "Briefing cadence must be `now`, `daily`, or `weekly`."
            )
        if len(args) < 3:
            raise CommandUsageError(
                f"`briefing {cadence}` requires currency and indicator."
            )
        currency = validate_currency(args[1])
        indicator = validate_indicator(args[2])
        if cadence == "daily":
            hour, minute = validate_time(args[3] if len(args) > 3 else "08:00")
            zone = validate_timezone(args[4] if len(args) > 4 else "UTC")
            if len(args) > 5:
                raise CommandUsageError(
                    "`briefing daily` accepts optional HH:MM and IANA timezone only."
                )
            subscription = self._new_subscription(
                event,
                kind="daily_briefing",
                currency=currency,
                indicator=indicator,
                schedule={"hour": hour, "minute": minute, "timezone": zone},
            )
        else:
            weekday = validate_weekday(args[3] if len(args) > 3 else "mon")
            hour, minute = validate_time(args[4] if len(args) > 4 else "08:00")
            zone = validate_timezone(args[5] if len(args) > 5 else "UTC")
            if len(args) > 6:
                raise CommandUsageError(
                    "`briefing weekly` accepts optional weekday, HH:MM, and IANA timezone only."
                )
            subscription = self._new_subscription(
                event,
                kind="weekly_briefing",
                currency=currency,
                indicator=indicator,
                schedule={
                    "weekday": weekday,
                    "hour": hour,
                    "minute": minute,
                    "timezone": zone,
                },
            )
        await self._add_subscription(subscription)
        cadence_label = "daily" if cadence == "daily" else "weekly"
        return event.plain_result(
            f"FXMacroData {cadence_label} briefing subscribed for {currency} {indicator}. "
            f"Subscription ID: `{subscription['id']}`. Use `/fxmacrodata alerts list` "
            "to review it or `/fxmacrodata alerts remove <subscription-id>` to stop it."
        )

    async def _alerts_command(self, event: AstrMessageEvent, args: list[str]):
        if not args:
            raise CommandUsageError(
                "Choose `subscribe`, `list`, or `remove` after alerts."
            )
        action = args[0].lower()
        if action == "subscribe":
            if len(args) < 2:
                raise CommandUsageError("`alerts subscribe` requires a currency.")
            if len(args) > 3:
                raise CommandUsageError(
                    "`alerts subscribe` accepts currency and an optional indicator only."
                )
            currency = validate_currency(args[1])
            indicator = validate_indicator(args[2]) if len(args) == 3 else None
            subscription = self._new_subscription(
                event,
                kind="release_alert",
                currency=currency,
                indicator=indicator,
                schedule={},
            )
            await self._add_subscription(subscription)
            label = f"{currency} {indicator}" if indicator else f"{currency} releases"
            next_event = subscription.get("next_event_name")
            when = subscription.get("next_event_at")
            next_text = (
                f" Next confirmed release: {next_event} at {when}."
                if isinstance(next_event, str) and isinstance(when, str)
                else " No confirmed upcoming row is currently available; the subscription will recheck when AstrBot reloads."
            )
            return event.plain_result(
                f"FXMacroData release alerts subscribed for {label}. Subscription ID: "
                f"`{subscription['id']}`.{next_text} Use `/fxmacrodata alerts remove "
                f"{subscription['id']}` to stop it."
            )
        if action == "list":
            subscriptions = await self._session_subscriptions(event.unified_msg_origin)
            if not subscriptions:
                return event.plain_result(
                    "No FXMacroData alerts or briefings are subscribed for this chat session."
                )
            lines = ["## FXMacroData session subscriptions"]
            for subscription in subscriptions:
                schedule = self._subscription_schedule_label(subscription)
                indicator = subscription.get("indicator") or "all calendar releases"
                lines.append(
                    f"- `{subscription['id']}` · {subscription['kind']} · "
                    f"{subscription['currency']} {indicator} · {schedule}"
                )
            return self._markdown_result(event, "\n".join(lines))
        if action == "remove":
            if len(args) != 2:
                raise CommandUsageError(
                    "`alerts remove` requires a subscription ID or `all`."
                )
            removed = await self._remove_subscriptions(
                event.unified_msg_origin, args[1]
            )
            if not removed:
                return event.plain_result(
                    "No matching FXMacroData subscription was found for this chat session."
                )
            suffix = "s" if len(removed) != 1 else ""
            return event.plain_result(
                f"Removed {len(removed)} FXMacroData subscription{suffix} from this chat session."
            )
        raise CommandUsageError(
            "Alerts action must be `subscribe`, `list`, or `remove`."
        )

    def _new_subscription(
        self,
        event: AstrMessageEvent,
        *,
        kind: str,
        currency: str,
        indicator: str | None,
        schedule: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "id": uuid.uuid4().hex[:12],
            "kind": kind,
            "session": event.unified_msg_origin,
            "auth_identity": self._event_identity(event),
            "currency": currency,
            "indicator": indicator,
            "schedule": schedule,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_event_key": "",
        }

    async def _client_for_subscription(
        self, subscription: dict[str, Any]
    ) -> FXMacroDataMcpClient:
        identity = subscription.get("auth_identity")
        return await self._client_for_identity(
            identity if isinstance(identity, str) else None
        )

    async def _load_subscriptions(self) -> dict[str, dict[str, Any]]:
        raw = await self.get_kv_data(ALERT_STORAGE_KEY, {})
        if not isinstance(raw, dict):
            return {}
        valid: dict[str, dict[str, Any]] = {}
        for subscription_id, value in raw.items():
            if not isinstance(subscription_id, str) or not isinstance(value, dict):
                continue
            if not isinstance(value.get("session"), str) or not isinstance(
                value.get("currency"), str
            ):
                continue
            valid[subscription_id] = dict(value)
        return valid

    async def _save_subscriptions(
        self, subscriptions: dict[str, dict[str, Any]]
    ) -> None:
        await self.put_kv_data(ALERT_STORAGE_KEY, subscriptions)

    async def _add_subscription(self, subscription: dict[str, Any]) -> None:
        subscriptions = await self._load_subscriptions()
        subscriptions[subscription["id"]] = subscription
        await self._schedule_subscription(subscription, replace=True)
        await self._save_subscriptions(subscriptions)

    async def _session_subscriptions(self, session: str) -> list[dict[str, Any]]:
        subscriptions = await self._load_subscriptions()
        return [
            item for item in subscriptions.values() if item.get("session") == session
        ]

    async def _remove_subscriptions(self, session: str, selector: str) -> list[str]:
        subscriptions = await self._load_subscriptions()
        if selector.lower() == "all":
            identifiers = [
                key
                for key, value in subscriptions.items()
                if value.get("session") == session
            ]
        else:
            identifiers = [
                selector
                if selector in subscriptions
                and subscriptions[selector].get("session") == session
                else ""
            ]
        removed = [identifier for identifier in identifiers if identifier]
        for identifier in removed:
            subscriptions.pop(identifier, None)
            await self._cancel_subscription_jobs(identifier)
        if removed:
            await self._save_subscriptions(subscriptions)
        return removed

    def _cron_manager(self):
        manager = getattr(self.context, "cron_manager", None)
        if manager is None or not callable(getattr(manager, "add_basic_job", None)):
            raise SchedulerUnavailableError
        return manager

    async def _restore_subscriptions(self) -> None:
        """Recreate ephemeral cron jobs from persistent opt-in plugin storage."""

        try:
            subscriptions = await self._load_subscriptions()
            for subscription in subscriptions.values():
                await self._schedule_subscription(subscription, replace=True)
            if subscriptions:
                await self._save_subscriptions(subscriptions)
        except SchedulerUnavailableError:
            logger.warning("FXMacroData alerts require AstrBot's cron manager.")
        except Exception:
            logger.warning(
                "FXMacroData alert restoration failed; subscriptions remain stored."
            )

    async def _schedule_subscription(
        self, subscription: dict[str, Any], *, replace: bool
    ) -> None:
        if replace:
            await self._cancel_subscription_jobs(subscription["id"])
        kind = subscription.get("kind")
        if kind == "release_alert":
            await self._schedule_next_release_alert(subscription)
        elif kind in {"daily_briefing", "weekly_briefing"}:
            await self._schedule_recurring_briefing(subscription)

    async def _schedule_next_release_alert(self, subscription: dict[str, Any]) -> None:
        event = await self._next_release_from_hosted_calendar(subscription)
        if event is None:
            subscription.pop("next_event_key", None)
            subscription.pop("next_event_at", None)
            subscription.pop("next_event_name", None)
            return
        subscription["next_event_key"] = event.key
        subscription["next_event_at"] = event.when.isoformat()
        subscription["next_event_name"] = event.release_name
        run_at = event.when.astimezone(timezone.utc)
        expression = f"{run_at.minute} {run_at.hour} {run_at.day} {run_at.month} *"
        job = await self._cron_manager().add_basic_job(
            name=f"FXMacroData release alert {subscription['id']}",
            cron_expression=expression,
            handler=self._dispatch_release_alert,
            description="Opt-in FXMacroData release alert.",
            timezone="UTC",
            payload={
                "subscription_id": subscription["id"],
                "event_key": event.key,
            },
            persistent=False,
        )
        self._remember_job(subscription["id"], job.job_id)

    async def _schedule_recurring_briefing(self, subscription: dict[str, Any]) -> None:
        schedule = subscription.get("schedule", {})
        if not isinstance(schedule, dict):
            raise CommandUsageError("The stored briefing schedule is invalid.")
        hour = int(schedule["hour"])
        minute = int(schedule["minute"])
        timezone_name = validate_timezone(str(schedule["timezone"]))
        if subscription["kind"] == "daily_briefing":
            expression = f"{minute} {hour} * * *"
        else:
            weekday = validate_weekday(str(schedule["weekday"]))
            expression = f"{minute} {hour} * * {weekday}"
        job = await self._cron_manager().add_basic_job(
            name=f"FXMacroData {subscription['kind']} {subscription['id']}",
            cron_expression=expression,
            handler=self._dispatch_briefing,
            description="Opt-in FXMacroData scheduled briefing.",
            timezone=timezone_name,
            payload={"subscription_id": subscription["id"]},
            persistent=False,
        )
        self._remember_job(subscription["id"], job.job_id)

    def _remember_job(self, subscription_id: str, job_id: str) -> None:
        self._scheduled_job_ids.add(job_id)
        self._jobs_by_subscription.setdefault(subscription_id, set()).add(job_id)

    async def _cancel_subscription_jobs(self, subscription_id: str) -> None:
        manager = self._cron_manager()
        for job_id in tuple(self._jobs_by_subscription.pop(subscription_id, set())):
            try:
                await manager.delete_job(job_id)
            except Exception:
                logger.warning("FXMacroData could not remove an obsolete cron job.")
            self._scheduled_job_ids.discard(job_id)

    async def _retire_jobs_after_handler(
        self, subscription_id: str, job_ids: set[str]
    ) -> None:
        """Delete one-shot cron rows after their active handler has returned."""

        if not job_ids:
            return
        await asyncio.sleep(1)
        manager = self._cron_manager()
        for job_id in job_ids:
            try:
                await manager.delete_job(job_id)
            except Exception:
                logger.warning("FXMacroData could not retire a completed alert job.")
            self._scheduled_job_ids.discard(job_id)
            jobs = self._jobs_by_subscription.get(subscription_id)
            if jobs:
                jobs.discard(job_id)

    async def _next_release_from_hosted_calendar(
        self, subscription: dict[str, Any]
    ) -> ReleaseEvent | None:
        today = datetime.now(timezone.utc).date()
        payload: dict[str, Any] = {
            "currency": subscription["currency"],
            "start_date": today.isoformat(),
            "end_date": (today + timedelta(days=366)).isoformat(),
        }
        if subscription.get("indicator"):
            payload["indicator"] = subscription["indicator"]
        client = await self._client_for_subscription(subscription)
        result = await client.call_tool("release_calendar", payload)
        if result.isError:
            return None
        return next_release_event("\n".join(text_blocks(result)))

    async def _event_remains_confirmed(
        self, subscription: dict[str, Any], event_key: str
    ) -> bool:
        try:
            stamp = event_key.split("|", 1)[0]
            when = datetime.fromisoformat(stamp)
        except ValueError:
            return False
        payload: dict[str, Any] = {
            "currency": subscription["currency"],
            "start_date": (when - timedelta(days=1)).date().isoformat(),
            "end_date": (when + timedelta(days=1)).date().isoformat(),
        }
        if subscription.get("indicator"):
            payload["indicator"] = subscription["indicator"]
        client = await self._client_for_subscription(subscription)
        result = await client.call_tool("release_calendar", payload)
        if result.isError:
            return False
        return event_key in {
            event.key
            for event in parse_confirmed_release_events("\n".join(text_blocks(result)))
        }

    async def _dispatch_release_alert(
        self, subscription_id: str, event_key: str
    ) -> None:
        subscriptions = await self._load_subscriptions()
        subscription = subscriptions.get(subscription_id)
        prior_jobs = set(self._jobs_by_subscription.get(subscription_id, set()))
        if not subscription or subscription.get("last_event_key") == event_key:
            asyncio.create_task(
                self._retire_jobs_after_handler(subscription_id, prior_jobs)
            )
            return
        try:
            confirmed = await self._event_remains_confirmed(subscription, event_key)
        except Exception:
            confirmed = False
        if not confirmed:
            await self._schedule_subscription(subscription, replace=False)
            await self._save_subscriptions(subscriptions)
            asyncio.create_task(
                self._retire_jobs_after_handler(subscription_id, prior_jobs)
            )
            return

        release_name = str(
            subscription.get("next_event_name") or "Scheduled macro release"
        )
        message = (
            f"## FXMacroData release alert · {subscription['currency']}\n\n"
            f"**{release_name}** is due now. Scheduled time: "
            f"{subscription.get('next_event_at', event_key.split('|', 1)[0])}."
        )
        if subscription.get("indicator"):
            try:
                client = await self._client_for_subscription(subscription)
                result = await client.call_tool(
                    "macro_research_pack_task",
                    {
                        "currency": subscription["currency"],
                        "indicator": subscription["indicator"],
                    },
                )
                message = format_chat_briefing(
                    result,
                    heading=f"FXMacroData release alert · {subscription['currency']} {subscription['indicator']}",
                    prefix=(
                        f"**{release_name}** is due now. Scheduled time: "
                        f"{subscription.get('next_event_at', event_key.split('|', 1)[0])}."
                    ),
                )
            except Exception:
                logger.warning("FXMacroData could not enrich a release alert.")
        try:
            chain = MessageChain().message(message).use_markdown(True)
            await self.context.send_message(subscription["session"], chain)
        except Exception:
            logger.warning("FXMacroData could not deliver a release alert.")
            return

        subscription["last_event_key"] = event_key
        await self._schedule_subscription(subscription, replace=False)
        await self._save_subscriptions(subscriptions)
        asyncio.create_task(
            self._retire_jobs_after_handler(subscription_id, prior_jobs)
        )

    async def _dispatch_briefing(self, subscription_id: str) -> None:
        subscriptions = await self._load_subscriptions()
        subscription = subscriptions.get(subscription_id)
        if not subscription:
            return
        try:
            client = await self._client_for_subscription(subscription)
            result = await client.call_tool(
                "macro_research_pack_task",
                {
                    "currency": subscription["currency"],
                    "indicator": subscription["indicator"],
                },
            )
            message = format_chat_briefing(
                result,
                heading=(
                    f"FXMacroData scheduled briefing · {subscription['currency']} "
                    f"{subscription['indicator']}"
                ),
                prefix="Opt-in session briefing. Information only; not investment advice.",
            )
            if subscription.get("kind") == "weekly_briefing":
                week_start = datetime.now(timezone.utc).date()
                calendar = await client.call_tool(
                    "release_calendar",
                    {
                        "currency": subscription["currency"],
                        "start_date": week_start.isoformat(),
                        "end_date": (week_start + timedelta(days=7)).isoformat(),
                    },
                )
                week_ahead = "\n\n".join(text_blocks(calendar)).strip()
                if week_ahead:
                    message += f"\n\n## Week-ahead release calendar\n\n{week_ahead}"
            chain = MessageChain().message(message).use_markdown(True)
            await self.context.send_message(subscription["session"], chain)
        except Exception:
            logger.warning("FXMacroData could not deliver a scheduled briefing.")

    def _subscription_schedule_label(self, subscription: dict[str, Any]) -> str:
        if subscription.get("kind") == "release_alert":
            return str(
                subscription.get("next_event_at") or "awaiting next confirmed release"
            )
        schedule = subscription.get("schedule", {})
        if not isinstance(schedule, dict):
            return "invalid schedule"
        time = (
            f"{int(schedule.get('hour', 0)):02d}:{int(schedule.get('minute', 0)):02d}"
        )
        if subscription.get("kind") == "weekly_briefing":
            return f"{schedule.get('weekday', 'mon')} {time} {schedule.get('timezone', 'UTC')}"
        return f"daily {time} {schedule.get('timezone', 'UTC')}"

    def _page_value(self, name: str, *, required: bool = False) -> str | None:
        value = request.query.get(name, "")
        if not isinstance(value, str):
            raise PageRequestError(f"{name} must be text.")
        candidate = value.strip()
        if required and not candidate:
            raise PageRequestError(f"{name} is required.")
        if len(candidate) > MAX_PAGE_INPUT_LENGTH:
            raise PageRequestError(f"{name} is too long.")
        return candidate or None

    def _page_currency(self, name: str, *, required: bool = True) -> str | None:
        value = self._page_value(name, required=required)
        return validate_currency(value) if value else None

    def _page_indicator(self, name: str, *, required: bool = False) -> str | None:
        value = self._page_value(name, required=required)
        return validate_indicator(value) if value else None

    def _page_identity(self, *, required: bool = False) -> str | None:
        """Return the authenticated AstrBot dashboard identity, if available."""

        username = getattr(request, "username", None)
        if isinstance(username, str) and username.strip():
            return f"dashboard:{username.strip()}"
        if required:
            raise PageRequestError(
                "Sign in to the AstrBot dashboard before connecting protected FXMacroData access."
            )
        return None

    async def _page_call(self, tool: str, arguments: dict[str, Any]):
        try:
            client = await self._client_for_identity(self._page_identity())
            result = await client.call_tool(tool, arguments)
            payload = result_for_page(result)
            return json_response(payload, status_code=200 if payload["ok"] else 422)
        except Exception:
            logger.warning(
                "FXMacroData Macro Command Center request failed for %s", tool
            )
            return error_response("FXMacroData MCP request failed.", status_code=503)

    async def page_health(self):
        return json_response(
            {
                "status": "ready",
                "registered_tool_count": len(self._registered_tool_names),
                "page_data_scope": "public baseline or the signed-in dashboard user's OAuth scope",
            }
        )

    async def page_tools(self):
        try:
            tools, prompts, resources = await asyncio.gather(
                self.public_client.list_tools(),
                self.public_client.list_prompts(),
                self.public_client.list_resources(),
            )
            return json_response(
                {
                    "ok": True,
                    "tools": [
                        {
                            "native_name": f"{TOOL_NAME_PREFIX}{tool.name}",
                            "hosted_name": tool.name,
                            "description": tool.description,
                            "input_schema": tool.input_schema,
                        }
                        for tool in tools
                    ],
                    "prompts": [
                        {
                            "native_name": f"{TOOL_NAME_PREFIX}prompt_{prompt.name}",
                            "hosted_name": prompt.name,
                            "description": prompt.description,
                            "arguments": [
                                {
                                    "name": argument.name,
                                    "description": argument.description,
                                    "required": argument.required,
                                }
                                for argument in prompt.arguments
                            ],
                        }
                        for prompt in prompts
                    ],
                    "resources": [
                        {
                            "native_name": f"{TOOL_NAME_PREFIX}resource_{resource.name}",
                            "hosted_name": resource.name,
                            "description": resource.description,
                            "mime_type": resource.mime_type,
                        }
                        for resource in resources
                    ],
                }
            )
        except Exception:
            logger.warning("FXMacroData hosted tool inventory request failed.")
            return error_response(
                "FXMacroData hosted tool inventory is unavailable.", status_code=503
            )

    async def _remote_prompt(self, name: str) -> RemotePrompt:
        prompts = await self.public_client.list_prompts()
        for prompt in prompts:
            if prompt.name == name:
                return prompt
        raise PageRequestError("The requested hosted prompt is not available.")

    async def _remote_resource(self, name: str) -> RemoteResource:
        resources = await self.public_client.list_resources()
        for resource in resources:
            if resource.name == name:
                return resource
        raise PageRequestError("The requested hosted resource is not available.")

    async def page_prompts(self):
        try:
            prompts = await self.public_client.list_prompts()
            return json_response(
                {
                    "ok": True,
                    "prompts": [
                        {
                            "native_name": f"{TOOL_NAME_PREFIX}prompt_{prompt.name}",
                            "hosted_name": prompt.name,
                            "description": prompt.description,
                            "arguments": [
                                {
                                    "name": argument.name,
                                    "description": argument.description,
                                    "required": argument.required,
                                }
                                for argument in prompt.arguments
                            ],
                        }
                        for prompt in prompts
                    ],
                }
            )
        except Exception:
            logger.warning("FXMacroData hosted prompt inventory request failed.")
            return error_response(
                "FXMacroData hosted prompt inventory is unavailable.", status_code=503
            )

    async def page_prompt(self):
        try:
            prompt = await self._remote_prompt(
                self._page_value("name", required=True) or ""
            )
            arguments: dict[str, str] = {}
            for argument in prompt.arguments:
                value = self._page_value(argument.name, required=argument.required)
                if value:
                    arguments[argument.name] = value
            client = await self._client_for_identity(self._page_identity())
            return json_response(
                prompt_result_for_page(await client.get_prompt(prompt.name, arguments))
            )
        except (PageRequestError, ValueError) as exc:
            return error_response(str(exc), status_code=400)
        except Exception:
            logger.warning("FXMacroData hosted prompt request failed.")
            return error_response(
                "FXMacroData hosted prompt is unavailable.", status_code=503
            )

    async def page_resources(self):
        try:
            resources = await self.public_client.list_resources()
            return json_response(
                {
                    "ok": True,
                    "resources": [
                        {
                            "native_name": f"{TOOL_NAME_PREFIX}resource_{resource.name}",
                            "hosted_name": resource.name,
                            "description": resource.description,
                            "mime_type": resource.mime_type,
                        }
                        for resource in resources
                    ],
                }
            )
        except Exception:
            logger.warning("FXMacroData hosted resource inventory request failed.")
            return error_response(
                "FXMacroData hosted resource inventory is unavailable.", status_code=503
            )

    async def page_resource(self):
        try:
            resource = await self._remote_resource(
                self._page_value("name", required=True) or ""
            )
            client = await self._client_for_identity(self._page_identity())
            payload = resource_result_for_page(await client.read_resource(resource.uri))
            payload["resource"] = {
                "name": resource.name,
                "description": resource.description,
                "mime_type": resource.mime_type,
            }
            return json_response(payload)
        except PageRequestError as exc:
            return error_response(str(exc), status_code=400)
        except Exception:
            logger.warning("FXMacroData hosted resource request failed.")
            return error_response(
                "FXMacroData hosted resource is unavailable.", status_code=503
            )

    async def page_calendar(self):
        try:
            arguments: dict[str, Any] = {"currency": self._page_currency("currency")}
            for name in ("indicator", "start_date", "end_date", "timezone"):
                value = self._page_value(name)
                if value:
                    arguments[name] = (
                        validate_indicator(value) if name == "indicator" else value
                    )
            return await self._page_call("release_calendar", arguments)
        except (PageRequestError, ValueError) as exc:
            return error_response(str(exc), status_code=400)

    async def page_indicator(self):
        try:
            arguments = {
                "currency": self._page_currency("currency"),
                "indicator": self._page_indicator("indicator", required=True),
                "limit": 20,
            }
            for name in ("start_date", "end_date"):
                value = self._page_value(name)
                if value:
                    arguments[name] = value
            return await self._page_call("indicator_query", arguments)
        except (PageRequestError, ValueError) as exc:
            return error_response(str(exc), status_code=400)

    async def page_research(self):
        try:
            arguments: dict[str, Any] = {
                "currency": self._page_currency("currency"),
                "indicator": self._page_indicator("indicator", required=True),
            }
            for name in ("base", "quote"):
                value = self._page_currency(name, required=False)
                if value:
                    arguments[name] = value
            return await self._page_call("macro_research_pack_task", arguments)
        except (PageRequestError, ValueError) as exc:
            return error_response(str(exc), status_code=400)

    async def page_pair(self):
        try:
            arguments = {
                "base": self._page_currency("base"),
                "quote": self._page_currency("quote"),
                "horizon_events": 4,
                "include_cot": False,
            }
            return await self._page_call("fx_trade_setup_task", arguments)
        except (PageRequestError, ValueError) as exc:
            return error_response(str(exc), status_code=400)

    async def page_auth_start(self):
        try:
            authorization = await self.oauth_vault.start(
                self._page_identity(required=True)
            )
            return json_response(
                {
                    "ok": True,
                    "verification_uri_complete": authorization.verification_uri_complete,
                    "user_code": authorization.user_code,
                    "expires_in": authorization.expires_in,
                }
            )
        except OAuthConfigurationError as exc:
            return error_response(str(exc), status_code=503)
        except (OAuthError, PageRequestError) as exc:
            return error_response(str(exc), status_code=400)

    async def page_auth_status(self):
        try:
            identity = self._page_identity(required=True)
            pending = False
            try:
                await self.oauth_vault.complete(identity)
            except OAuthPendingError:
                pending = True
            except OAuthError:
                # No pending sign-in is normal once the device code has been
                # consumed or when the page is first opened.
                pass
            connected = await self.oauth_vault.is_connected(identity)
            return json_response(
                {"ok": True, "connected": connected, "pending": pending}
            )
        except OAuthConfigurationError as exc:
            return error_response(str(exc), status_code=503)
        except PageRequestError as exc:
            return error_response(str(exc), status_code=401)

    async def page_auth_disconnect(self):
        try:
            await self.oauth_vault.disconnect(self._page_identity(required=True))
            return json_response({"ok": True, "connected": False})
        except OAuthConfigurationError as exc:
            return error_response(str(exc), status_code=503)
        except PageRequestError as exc:
            return error_response(str(exc), status_code=401)

    async def terminate(self) -> None:
        """Remove runtime cron jobs; opt-in settings remain removable in storage."""

        for subscription_id in tuple(self._jobs_by_subscription):
            try:
                await self._cancel_subscription_jobs(subscription_id)
            except SchedulerUnavailableError:
                break
        self._scheduled_job_ids.clear()
        self._jobs_by_subscription.clear()
