const bridge = window.AstrBotPluginPage;
const status = document.querySelector("#connection-status");
const accountStatus = document.querySelector("#account-status");
const connectAccount = document.querySelector("#connect-account");
const disconnectAccount = document.querySelector("#disconnect-account");
const loadedInventories = new Set();

const t = (key, fallback) => bridge?.t?.(key) || fallback;
const text = (key, fallback) => t(key, fallback);
const interpolate = (template, values) => Object.entries(values).reduce(
  (message, [key, value]) => message.replaceAll(`{${key}}`, String(value)),
  template,
);

const escapeHtml = (value) => String(value)
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");

const payloadOf = (response) => {
  if (response?.data && response?.ok === undefined) return response.data;
  return response;
};

const markdownHtml = (value) => escapeHtml(value)
  .replace(/^### (.+)$/gm, "<h3>$1</h3>")
  .replace(/^## (.+)$/gm, "<h2>$1</h2>")
  .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
  .replace(/`(.+?)`/g, "<code>$1</code>")
  .replace(/\n/g, "<br>");

const displayValue = (value) => {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return `<pre>${escapeHtml(JSON.stringify(value, null, 2))}</pre>`;
  return escapeHtml(value);
};

const errorCard = (message) => `<article class="result-card error">${escapeHtml(message)}</article>`;
const loading = () => `<p class="loading">${escapeHtml(text("page.loading", "Loading FXMacroData data…"))}</p>`;

const renderContent = (response, target) => {
  const payload = payloadOf(response);
  if (!payload?.ok) {
    target.innerHTML = errorCard(payload?.error || text("page.queryFailed", "The hosted MCP could not complete this request."));
    return;
  }
  const cards = (payload.content || []).map((item) => {
    if (item.data !== undefined) {
      const rows = Object.entries(item.data || {}).map(([key, value]) => `<div class="data-row"><span class="data-key">${escapeHtml(key)}</span><span class="data-value">${displayValue(value)}</span></div>`).join("");
      return `<article class="result-card"><div class="data-tree">${rows || displayValue(item.data)}</div></article>`;
    }
    if (item.uri) {
      const label = item.name || text("page.openResource", "Open public resource");
      return `<article class="result-card"><a class="resource-link" href="${escapeHtml(item.uri)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)} ↗</a></article>`;
    }
    return `<article class="result-card markdown">${markdownHtml(item.text || "")}</article>`;
  });
  target.innerHTML = cards.join("") || `<article class="result-card">${escapeHtml(text("page.empty", "No public result returned."))}</article>`;
};

const formParameters = (form) => Object.fromEntries(
  [...new FormData(form).entries()]
    .map(([key, value]) => [key, String(value).trim()])
    .filter(([, value]) => value),
);

const runQuery = async (form) => {
  const result = form.parentElement.querySelector(".result");
  result.innerHTML = loading();
  try {
    renderContent(await bridge.apiGet(form.dataset.endpoint, formParameters(form)), result);
  } catch (error) {
    result.innerHTML = errorCard(error?.message || text("page.queryFailed", "The hosted MCP could not complete this request."));
  }
};

const activatePanel = (panelId) => {
  document.querySelectorAll(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.panel === panelId));
  document.querySelectorAll(".panel").forEach((panel) => panel.classList.toggle("active", panel.id === panelId));
  if (panelId === "prompts") loadPrompts();
  if (panelId === "resources") loadResources();
  if (panelId === "tools") loadTools();
};

const card = (item, actionLabel, action) => {
  const node = document.createElement("article");
  node.className = "tool-card";
  node.innerHTML = `<code>${escapeHtml(item.native_name || item.hosted_name)}</code><h3>${escapeHtml(item.hosted_name)}</h3><p>${escapeHtml(item.description || "")}</p>`;
  if (actionLabel) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "secondary-button";
    button.textContent = actionLabel;
    button.addEventListener("click", action);
    node.append(button);
  }
  return node;
};

const loadTools = async (force = false) => {
  if (loadedInventories.has("tools") && !force) return;
  const grid = document.querySelector("#tool-grid");
  const summary = document.querySelector("#capability-summary");
  grid.innerHTML = loading();
  try {
    const response = payloadOf(await bridge.apiGet("tools"));
    if (!response?.ok) throw new Error(response?.error || text("page.queryFailed", "The hosted MCP could not complete this request."));
    const collections = [
      ["tool", response.tools || []],
      ["prompt", response.prompts || []],
      ["resource", response.resources || []],
    ];
    summary.textContent = interpolate(
      text("tools.summary", "{tools} tools · {prompts} prompts · {resources} resources"),
      {
        tools: response.tools?.length || 0,
        prompts: response.prompts?.length || 0,
        resources: response.resources?.length || 0,
      },
    );
    grid.replaceChildren(...collections.flatMap(([kind, items]) => items.map((item) => {
      const node = card(item, null, null);
      const kindLabel = document.createElement("span");
      kindLabel.className = "capability-kind";
      kindLabel.textContent = kind;
      node.prepend(kindLabel);
      return node;
    })));
    loadedInventories.add("tools");
  } catch (error) {
    grid.innerHTML = errorCard(error?.message || text("page.queryFailed", "The hosted MCP could not complete this request."));
  }
};

const renderPromptRunner = (prompt) => {
  const runner = document.querySelector("#prompt-runner");
  runner.hidden = false;
  runner.replaceChildren();
  const heading = document.createElement("h3");
  heading.textContent = prompt.hosted_name;
  const description = document.createElement("p");
  description.textContent = prompt.description || "";
  const form = document.createElement("form");
  form.className = "query-form prompt-form";
  for (const argument of prompt.arguments || []) {
    const label = document.createElement("label");
    const caption = document.createElement("span");
    caption.textContent = `${argument.name}${argument.required ? " *" : ""}`;
    const input = document.createElement("textarea");
    input.name = argument.name;
    input.required = Boolean(argument.required);
    input.maxLength = 4000;
    input.rows = argument.name.includes("json") || argument.name === "request" ? 5 : 2;
    input.placeholder = argument.description || argument.name;
    label.append(caption, input);
    form.append(label);
  }
  const submit = document.createElement("button");
  submit.type = "submit";
  submit.textContent = text("action.runPrompt", "Resolve prompt");
  form.append(submit);
  const result = document.createElement("div");
  result.className = "result";
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    result.innerHTML = loading();
    try {
      renderContent(await bridge.apiGet("prompt", { name: prompt.hosted_name, ...formParameters(form) }), result);
    } catch (error) {
      result.innerHTML = errorCard(error?.message || text("page.queryFailed", "The hosted MCP could not complete this request."));
    }
  });
  runner.append(heading, description, form, result);
  runner.scrollIntoView({ behavior: "smooth", block: "start" });
};

const loadPrompts = async (force = false) => {
  if (loadedInventories.has("prompts") && !force) return;
  const grid = document.querySelector("#prompt-grid");
  grid.innerHTML = loading();
  try {
    const response = payloadOf(await bridge.apiGet("prompts"));
    if (!response?.ok) throw new Error(response?.error || text("page.queryFailed", "The hosted MCP could not complete this request."));
    grid.replaceChildren(...(response.prompts || []).map((prompt) => card(
      prompt,
      text("action.openPrompt", "Use prompt"),
      () => renderPromptRunner(prompt),
    )));
    loadedInventories.add("prompts");
  } catch (error) {
    grid.innerHTML = errorCard(error?.message || text("page.queryFailed", "The hosted MCP could not complete this request."));
  }
};

const renderResource = (response) => {
  const payload = payloadOf(response);
  const viewer = document.querySelector("#resource-viewer");
  viewer.hidden = false;
  viewer.replaceChildren();
  if (!payload?.ok) {
    viewer.innerHTML = errorCard(payload?.error || text("resources.loadFailed", "The hosted view could not be opened."));
    return;
  }
  const heading = document.createElement("h3");
  heading.textContent = payload.resource?.name || text("resources.view", "Hosted view");
  const description = document.createElement("p");
  description.textContent = payload.resource?.description || "";
  viewer.append(heading, description);
  for (const content of payload.contents || []) {
    if (typeof content.text === "string" && String(content.mime_type || "").startsWith("text/html")) {
      const frame = document.createElement("iframe");
      frame.className = "resource-frame";
      frame.title = heading.textContent;
      frame.sandbox = "allow-scripts";
      frame.referrerPolicy = "no-referrer";
      frame.srcdoc = content.text;
      viewer.append(frame);
      continue;
    }
    if (typeof content.text === "string") {
      const pre = document.createElement("pre");
      pre.className = "resource-text";
      pre.textContent = content.text;
      viewer.append(pre);
      continue;
    }
    if (typeof content.blob === "string") {
      const binary = Uint8Array.from(atob(content.blob), (character) => character.charCodeAt(0));
      const url = URL.createObjectURL(new Blob([binary], { type: content.mime_type || "application/octet-stream" }));
      const link = document.createElement("a");
      link.className = "resource-link";
      link.href = url;
      link.download = payload.resource?.name || "fxmacrodata-resource";
      link.textContent = text("action.downloadResource", "Download resource");
      viewer.append(link);
    }
  }
  viewer.scrollIntoView({ behavior: "smooth", block: "start" });
};

const loadResource = async (resource) => {
  const viewer = document.querySelector("#resource-viewer");
  viewer.hidden = false;
  viewer.innerHTML = loading();
  try {
    renderResource(await bridge.apiGet("resource", { name: resource.hosted_name }));
  } catch (error) {
    viewer.innerHTML = errorCard(error?.message || text("resources.loadFailed", "The hosted view could not be opened."));
  }
};

const loadResources = async (force = false) => {
  if (loadedInventories.has("resources") && !force) return;
  const grid = document.querySelector("#resource-grid");
  grid.innerHTML = loading();
  try {
    const response = payloadOf(await bridge.apiGet("resources"));
    if (!response?.ok) throw new Error(response?.error || text("page.queryFailed", "The hosted MCP could not complete this request."));
    grid.replaceChildren(...(response.resources || []).map((resource) => card(
      resource,
      text("action.openResourceView", "Open view"),
      () => loadResource(resource),
    )));
    loadedInventories.add("resources");
  } catch (error) {
    grid.innerHTML = errorCard(error?.message || text("page.queryFailed", "The hosted MCP could not complete this request."));
  }
};

let authPollTimer = null;
const stopAuthPolling = () => {
  if (authPollTimer) window.clearInterval(authPollTimer);
  authPollTimer = null;
};

const loadAuthStatus = async () => {
  try {
    const response = payloadOf(await bridge.apiGet("auth/status"));
    if (!response?.ok) throw new Error(response?.error || text("auth.unavailable", "Personal access is unavailable"));
    if (response.connected) {
      accountStatus.textContent = text("auth.connected", "Personal FXMacroData access connected");
      connectAccount.hidden = true;
      disconnectAccount.hidden = false;
      stopAuthPolling();
    } else if (response.pending) {
      accountStatus.textContent = text("auth.pending", "Finish approval in the FXMacroData browser page…");
      connectAccount.hidden = true;
      disconnectAccount.hidden = false;
      if (!authPollTimer) authPollTimer = window.setInterval(loadAuthStatus, 5500);
    } else {
      accountStatus.textContent = text("auth.public", "Using public FXMacroData access");
      connectAccount.hidden = false;
      disconnectAccount.hidden = true;
      stopAuthPolling();
    }
  } catch (error) {
    accountStatus.textContent = error?.message || text("auth.unavailable", "Personal access is unavailable");
    connectAccount.hidden = false;
    disconnectAccount.hidden = true;
    stopAuthPolling();
  }
};

const startAccountConnection = async () => {
  connectAccount.disabled = true;
  try {
    const response = payloadOf(await bridge.apiPost("auth/start", {}));
    if (!response?.ok || !response.verification_uri_complete) throw new Error(response?.error || text("auth.startFailed", "Could not start sign-in"));
    const opened = window.open(response.verification_uri_complete, "_blank", "noopener,noreferrer");
    accountStatus.textContent = interpolate(
      text("auth.approveCode", "Approve code {code} on FXMacroData, then return here."),
      { code: response.user_code },
    );
    if (!opened) {
      accountStatus.innerHTML = `${escapeHtml(text("auth.popupBlocked", "Open the FXMacroData verification page and approve code {code}."))
        .replace("{code}", `<a href="${escapeHtml(response.verification_uri_complete)}" target="_blank" rel="noopener noreferrer">${escapeHtml(response.user_code)}</a>`)} `;
    }
    disconnectAccount.hidden = false;
    if (!authPollTimer) authPollTimer = window.setInterval(loadAuthStatus, 5500);
  } catch (error) {
    accountStatus.textContent = error?.message || text("auth.startFailed", "Could not start personal access");
    connectAccount.disabled = false;
  }
};

const disconnectAccountConnection = async () => {
  disconnectAccount.disabled = true;
  try {
    await bridge.apiPost("auth/disconnect", {});
  } finally {
    disconnectAccount.disabled = false;
    await loadAuthStatus();
  }
};

const applyTranslations = () => {
  document.querySelectorAll("[data-i18n]").forEach((element) => {
    const fallback = element.dataset.i18nFallback || element.textContent.trim();
    element.dataset.i18nFallback = fallback;
    element.textContent = text(element.dataset.i18n, fallback);
  });
  document.querySelectorAll("[data-i18n-aria-label]").forEach((element) => {
    const fallback = element.dataset.i18nAriaFallback || element.getAttribute("aria-label") || "";
    element.dataset.i18nAriaFallback = fallback;
    element.setAttribute("aria-label", text(element.dataset.i18nAriaLabel, fallback));
  });
};

const applyContext = (context = {}) => {
  const requestedTheme = context.theme || context.theme_mode || context.themeMode;
  const theme = requestedTheme === "light" || requestedTheme === "dark"
    ? requestedTheme
    : window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  document.documentElement.dataset.theme = theme;
  if (typeof context.locale === "string" && context.locale) document.documentElement.lang = context.locale;
  applyTranslations();
};

const initialise = async () => {
  if (!bridge) {
    status.textContent = text("page.bridgeUnavailable", "AstrBot page bridge unavailable");
    status.classList.add("error");
    return;
  }
  const initialContext = await bridge.ready();
  applyContext(initialContext || bridge.context || {});
  bridge.onContext?.(applyContext);
  document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => activatePanel(tab.dataset.panel)));
  document.querySelectorAll(".query-form[data-endpoint]").forEach((form) => form.addEventListener("submit", (event) => { event.preventDefault(); runQuery(form); }));
  document.querySelector("#load-tools").addEventListener("click", () => loadTools(true));
  document.querySelector("#load-prompts").addEventListener("click", () => loadPrompts(true));
  document.querySelector("#load-resources").addEventListener("click", () => loadResources(true));
  connectAccount.addEventListener("click", startAccountConnection);
  disconnectAccount.addEventListener("click", disconnectAccountConnection);
  try {
    const health = payloadOf(await bridge.apiGet("health"));
    status.textContent = `${text("page.connected", "Connected")} · ${health.registered_tool_count || 0} ${text("page.capabilitiesRegistered", "capabilities registered")}`;
    status.classList.add("ready");
  } catch (_) {
    status.textContent = text("page.unavailable", "Public MCP unavailable");
    status.classList.add("error");
  }
  await loadAuthStatus();
};

initialise();
