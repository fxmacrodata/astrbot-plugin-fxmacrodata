# FXMacroData for AstrBot

This is a native AstrBot marketplace-plugin package. It discovers the hosted
FXMacroData MCP server at startup and registers every currently published MCP
tool, reusable prompt, and concrete resource as an AstrBot `FunctionTool`.
The plugin does not reduce FXMacroData to a single chat command or a manually
maintained subset.

## What it provides

- Every current hosted FXMacroData MCP capability:
  - tools retain their live JSON-schema parameters and are registered as
    `fxmacrodata_<hosted_tool_name>`;
  - reusable MCP prompts become `fxmacrodata_prompt_<hosted_prompt_name>` with
    their live required/optional argument contract; and
  - concrete MCP resources become `fxmacrodata_resource_<hosted_resource_name>`
    and return structured MCP embedded resources rather than flattened HTML.
  This prevents collisions with unrelated AstrBot plugins (for example,
  `fxmacrodata_ping`).
- Native AstrBot function calling, so users can enable, disable, inspect, and
  invoke FXMacroData capabilities in AstrBot's ordinary tool workflow.
- `/fxmacrodata status` to verify the connection and refresh the native tool
  registry; `/fxmacrodata refresh` does the same explicitly after a hosted MCP
  update. `/fxmacrodata connect` starts a personal FXMacroData sign-in and
  `/fxmacrodata disconnect` removes it.
- A bundled **FXMacroData macro research** Skill. AstrBot’s Skills Manager can
  enable it alongside the native tool catalogue, giving models concise,
  source-aware release and macro-research instructions without injecting a
  hand-maintained list of 48+ tool definitions into every prompt.
- The localized, theme-aware **Macro Command Center** plugin page: release
  calendar, research pack, indicator, FX-pair, prompt library, hosted MCP App
  resource gallery, and complete live capability inventory. Interactive MCP
  App resources render only after an explicit user action in a nested sandboxed
  iframe (`allow-scripts` without same-origin, popup, form, or top-navigation
  permissions). The page can connect the current AstrBot dashboard user to
  their own FXMacroData entitlement.
- Native rich Markdown briefings plus opt-in, session-specific release alerts,
  daily briefings, and week-ahead briefings. Scheduling uses AstrBot's built-in
  cron manager and its private plugin KV store—not a background polling loop.
  Users can list or remove only their own chat-session subscriptions.
- A useful no-key baseline for public USD discovery, macro history, and release
  calendar use. Protected tool families use each user's own revocable OAuth
  access; there is no administrator API-key setting and no shared entitlement.

The plugin calls `https://mcp.fxmacrodata.com/mcp` with the standard MCP
Streamable HTTP transport. It packages no FXMacroData service source, data,
ingestion logic, credentials, internal logic, or commercially sensitive code.

## Install locally in AstrBot

1. Place this `astrbot-fxmacrodata` directory in the AstrBot instance's
   `data/plugins/` directory as `astrbot_plugin_fxmacrodata`.
2. Install/enable the plugin in AstrBot's Plugins WebUI. AstrBot installs the
   dependencies declared in `requirements.txt`.
3. Before enabling personal protected access, set
   `FXMACRODATA_ASTRBOT_TOKEN_ENCRYPTION_KEY` in the AstrBot host environment
   to an operator-managed Fernet key. This encrypts OAuth tokens in AstrBot's
   private plugin KV store; it is not an FXMacroData API key and must never be
   committed, placed in plugin configuration, or shared with users.
4. Reload the plugin, then run `/fxmacrodata status`. The response gives the
   exact number of currently registered hosted MCP capabilities.
5. Enable the FXMacroData tools in AstrBot's Function Calling tool management
   screen and use a function-calling-capable model.

Open **Macro Command Center** from the plugin's page in AstrBot to use the
interactive dashboard. Its **Connect FXMacroData** control opens the same
personal sign-in flow for the dashboard account. It is localized through
AstrBot's standard plugin i18n folders and provides English and Simplified
Chinese strings.

## Personal authentication

Each chat user's connection is keyed to their AstrBot platform and sender ID,
never to a group or chat session. Start and complete connection in a **private
chat** with the bot so the short-lived verification code is not posted to a
group. Run:

```text
/fxmacrodata connect
```

The plugin returns a first-party FXMacroData verification link and short-lived
verification code. The user enters their FXMacroData API key only on that
FXMacroData page, approves the connection, then runs:

```text
/fxmacrodata connect status
```

AstrBot receives only a revocable OAuth access/refresh-token pair. The pair is
encrypted before it is written to AstrBot's private plugin KV store, refreshed
as needed, and removed locally plus revoked remotely by:

```text
/fxmacrodata disconnect
```

No API key, OAuth token, encryption key, customer data, or internal FXMacroData
logic is packaged, logged, shown to another chat user, or placed in a tool
response. A user who has not connected continues to receive only the hosted
MCP's public no-key baseline.

## Examples

Once the tool is enabled, ordinary language prompts can invoke the appropriate
native tool, for example:

- “List the available USD macro indicators, then show recent inflation data.”
- “Show the upcoming USD release calendar in Australia/Sydney time.”
- “Compare the EUR and USD policy-rate differential and explain the official
  source metadata.”

Use `/fxmacrodata refresh` after FXMacroData publishes a new MCP tool, prompt,
or resource; it reads the live catalogue and registers the updated complete set
in AstrBot.

### Rich research and alerts

```text
/fxmacrodata briefing now USD inflation EUR USD
/fxmacrodata connect
/fxmacrodata connect status
/fxmacrodata disconnect
/fxmacrodata briefing daily USD inflation 08:00 Australia/Sydney
/fxmacrodata briefing weekly USD inflation mon 08:00 Australia/Sydney
/fxmacrodata alerts subscribe USD inflation
/fxmacrodata alerts list
/fxmacrodata alerts remove all
```

Release alerts use only an exact, source-confirmed release timestamp returned
by `fxmacrodata_release_calendar`; the plugin does not infer schedules from
cadence. The first alert schedules the next confirmed event, then rechecks it
with the hosted calendar before delivery. Scheduled briefings and release
alerts are information services, not personalised investment advice.

The private plugin KV record contains the opted-in subscription's opaque
AstrBot session identifier, a separate personal sender identity used for OAuth
lookup, selected currency/indicator, cadence, and delivery state. Encrypted
OAuth tokens are stored separately. It contains no message history, API key,
pricing/billing data, or FXMacroData internal data. `/fxmacrodata alerts remove
all` erases every notification record owned by the current chat session.

## Development validation

Run from the FXMacroData monorepo root:

```powershell
python -m pytest extensions/astrbot-fxmacrodata/tests -n 0
python -m ruff check extensions/astrbot-fxmacrodata
```

The tests use mocked MCP transport for repeatability. A separate local live
smoke may call the public hosted MCP with no key to confirm its current tool,
prompt, and resource discovery. No marketplace publication is performed by
this package or its tests.
