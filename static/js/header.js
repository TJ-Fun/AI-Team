import { $, state, api, formatTime } from "./core.js";

let agentHealth = null;
let appVersion = "";
let healthInFlight = false;
let lastHealthCheck = 0;
const HEALTH_REFRESH_MS = 60000;

function agentDotClass(agents) {
  if (!agents || !agents.total) return "neutral";
  if (agents.up >= agents.total) return "";
  if (agents.up > 0) return "warning";
  return "offline";
}

function renderAgentChip() {
  const dot = $("#agent-status-dot");
  const text = $("#agent-status-text");
  const version = $("#app-version");
  const chip = $("#agent-status-chip");
  if (version) version.textContent = appVersion ? `v${appVersion}` : "v—";
  if (!text) return;
  if (!agentHealth) {
    text.textContent = "Agents …";
    dot?.classList.remove("offline", "warning", "neutral");
    if (chip) chip.title = "Checking AI provider health…";
    return;
  }
  const total = Number(agentHealth.total || 0);
  const up = Number(agentHealth.up || 0);
  text.textContent = total ? `Agents ${up}/${total}` : "Agents 0/0";
  dot?.classList.remove("offline", "warning", "neutral");
  const cls = agentDotClass(agentHealth);
  if (cls) dot?.classList.add(cls);
  const lines = Object.entries(agentHealth.providers || {}).map(([name, info]) => {
    const ok = info && info.up;
    const detail = info && info.error ? info.error : info && info.message ? info.message : "";
    return `${name}: ${ok ? "up" : "down"}${detail ? ` — ${detail}` : ""}`;
  });
  const checked = agentHealth.checked_at ? `Checked ${formatTime(agentHealth.checked_at)}` : "Not checked yet";
  if (chip) {
    chip.title = ["AI provider health", ...lines, checked, appVersion ? `App v${appVersion}` : ""]
      .filter(Boolean)
      .join("\n");
  }
}

async function refreshAgentHealth(force = false) {
  if (healthInFlight) return;
  if (!force && Date.now() - lastHealthCheck < HEALTH_REFRESH_MS) return;
  healthInFlight = true;
  lastHealthCheck = Date.now();
  try {
    const res = await api("/api/providers/health");
    agentHealth = res.agents || null;
    renderAgentChip();
  } catch {
    // keep last known health on transient errors
  } finally {
    healthInFlight = false;
  }
}

$("#agent-status-chip")?.addEventListener("click", () => {
  try {
    localStorage.setItem("ai_team_settings_tab", "connections");
  } catch {}
  $("#open-settings")?.click();
});

export async function loadConfig(providers) {
  const menu = $("#chat-provider-menu");
  try {
    const cfg = await api("/api/config");
    const provider = cfg.chat_provider || "Auto";
    state.chatProvider = provider;
    state.chatModelOverride = cfg.chat_model_override || "";
    if (Array.isArray(cfg.providers) && cfg.providers.length) {
      state.routingProviders = cfg.providers;
    }
    if (menu) menu.innerHTML = providers.chatProviderOptions(provider);
    providers.renderChatModelSelect();
    providers.populateProviderSelects();
  } catch {
    // Chat provider controls keep their last known state on transient errors.
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

    agentHealth = data.agents || agentHealth;
    appVersion = data.app_version || appVersion;
    renderAgentChip();
    void refreshAgentHealth();

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
