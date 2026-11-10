import { $, state, api, formatTime } from "./core.js";

export async function loadConfig(providers) {
  const dot = $("#model-dot") || $(".model-chip .status-dot");
  const nameEl = $("#model-name");
  const menu = $("#chat-provider-menu");
  try {
    const cfg = await api("/api/config");
    const provider = cfg.chat_provider || "Auto";
    state.chatProvider = provider;
    state.chatModelOverride = cfg.chat_model_override || "";
    if (Array.isArray(cfg.providers) && cfg.providers.length) {
      state.routingProviders = cfg.providers;
    }
    nameEl.textContent = `Chat · ${provider}`;
    if (menu) menu.innerHTML = providers.chatProviderOptions(provider);
    providers.renderChatModelSelect();
    dot?.classList.toggle("offline", cfg.chat_configured === false);
    providers.populateProviderSelects();
  } catch {
    nameEl.textContent = "AI chat";
    dot?.classList.add("offline");
  }
}

export async function refreshStatus() {
  try {
    const data = await api("/api/status");
    const cpu = data.cpu_percent;
    $("#cpu-value").textContent = cpu == null ? "—" : `${cpu}%`;
    const bar = $("#cpu-bar");
    if (bar) bar.style.width = cpu == null ? "0%" : `${Math.max(0, Math.min(100, cpu))}%`;
    const track = $(".cpu-track");
    if (track) track.setAttribute("aria-valuenow", String(cpu ?? 0));

    const pdata = data.providers || {};
    const workingNames = Object.keys(pdata).filter((n) => pdata[n] && pdata[n].working);
    const working = Boolean(data.chat_working || workingNames.length);
    state.llmBusy = Boolean(data.chat_working);
    const llmState = $("#llm-state");
    llmState.textContent = data.chat_working
      ? "Chat working"
      : workingNames.length
        ? `${workingNames[0]} working`
        : "Idle";
    llmState.classList.toggle("working", working);
    $("#clear-chat").disabled = state.llmBusy;

    const providerBits = Object.entries(pdata).map(
      ([name, info]) => `${name} ${info && info.configured ? "connected" : "not connected"}`
    );
    $("#agent-status").textContent = [
      ...providerBits,
      `GitHub ${data.github_configured ? "connected" : "not connected"}`,
    ].join(" · ");

    const wd = data.watchdog || {};
    $("#watchdog-info").textContent = wd.error
      ? "Watchdog check had an error"
      : wd.last_run
        ? `Board checked ${formatTime(wd.last_run)} · every 2 hours`
        : "Watchdog starting…";
  } catch {
    // ignore transient status poll error
  }
}
