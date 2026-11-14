export const $ = (selector) => document.querySelector(selector);
export const escapeHtml = (value) =>
  String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");

export const state = {
  tasks: [],
  projects: [{ id: "default", name: "Main project", repository: "", context: "", modules: [] }],
  activeProjectId: localStorage.getItem("ai_team_active_project") || "default",
  activeModuleId: localStorage.getItem("ai_team_active_subproject") || "",
  chatProvider: "Gemini",
  statResets: [],
  qualityRows: [],
  notifications: [],
  editingTaskId: null,
  detailTaskId: null,
  detailRenderedSnapshot: "",
  prompts: [],
  contextDocs: [],
  contextProjectId: "",
  connections: [],
  encryptionAvailable: true,
  routing: {},
  routingRoles: [],
  routingProviders: [],
  providers: [],
  providerPresets: [],
  maxRoutes: 25,
  routeModelCache: {},
  routingOpen: {},
  autonomy: {},
  chatModelOverride: "",
  repoMode: "files",
  repoCurrentPath: "",
  pollBusy: false,
  llmBusy: false,
};

export async function api(url, options = {}) {
  let response;
  try {
    response = await fetch(url, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
  } catch (err) {
    if (err instanceof TypeError && /fetch/i.test(err.message)) {
      throw new Error(`Could not reach the application server for ${url}; the request may have been interrupted.`);
    }
    throw err;
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || `Request failed (${response.status})`);
  }
  return data;
}

export function openAppDialog({
  title = "Confirm",
  eyebrow = "PLEASE CONFIRM",
  message = "",
  confirmLabel = "OK",
  cancelLabel = "Cancel",
  showCancel = true,
  input = null,
  danger = false,
} = {}) {
  const dlg = $("#app-dialog");
  if (!dlg) return Promise.resolve({ confirmed: true, value: input || "" });
  $("#app-dialog-title").textContent = title;
  $("#app-dialog-eyebrow").textContent = eyebrow;
  $("#app-dialog-message").textContent = message;
  const wrap = $("#app-dialog-input-wrap");
  const inputEl = $("#app-dialog-input");
  if (input !== null) {
    wrap.hidden = false;
    inputEl.value = input;
  } else {
    wrap.hidden = true;
    inputEl.value = "";
  }
  $("#app-dialog-error").textContent = "";
  const okBtn = $("#app-dialog-ok");
  okBtn.textContent = confirmLabel;
  okBtn.className = danger ? "danger" : "primary";
  const cancelBtn = $("#app-dialog-cancel");
  cancelBtn.textContent = cancelLabel;
  cancelBtn.hidden = !showCancel;
  return new Promise((resolve) => {
    const finish = (confirmed) => {
      dlg.removeEventListener("close", onClose);
      if (dlg.open) dlg.close();
      resolve({ confirmed, value: inputEl.value });
    };
    const onClose = () => resolve({ confirmed: false, value: inputEl.value });
    okBtn.onclick = () => finish(true);
    cancelBtn.onclick = () => finish(false);
    $("#app-dialog-close").onclick = () => finish(false);
    dlg.addEventListener("close", onClose, { once: true });
    if (!dlg.open) dlg.showModal();
    if (input !== null) setTimeout(() => inputEl.focus(), 0);
  });
}

export function appAlert(message, title = "Notice") {
  return openAppDialog({ title, eyebrow: "NOTICE", message, confirmLabel: "OK", showCancel: false });
}

export function appConfirm(message, { title = "Confirm", danger = false } = {}) {
  return openAppDialog({ title, message, confirmLabel: "Confirm", danger }).then((r) => r.confirmed);
}

export function appPrompt(message, defaultValue = "", title = "Details needed") {
  return openAppDialog({ title, eyebrow: "INPUT NEEDED", message, confirmLabel: "Submit", input: defaultValue });
}

export async function waitForAppRestart(statusEl, message, statusType = "restart-status", progress = null) {
  for (let attempt = 0; attempt < 30; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 2000));
    try {
      await api("/api/status");
      if (progress) {
        progress.finish("Service is back — reloading…");
        await new Promise((resolve) => setTimeout(resolve, 600));
      }
      location.reload();
      return;
    } catch (_err) {
      statusEl.textContent = `${message} Waiting for the service to restart… (${(attempt + 1) * 2}s)`;
      progress?.restartWait(attempt + 1);
    }
  }
  statusEl.textContent = `${message} The service did not respond within 60 seconds. Check the service logs.`;
  statusEl.className = `${statusType} error`;
  progress?.fail("The service did not respond in time. Check the service logs.");
}

export function createUpdateProgress(barEl, labelEl) {
  if (!barEl) return null;
  let value = 0;
  let target = 0;
  let text = "";
  let timer = null;
  let creepTimer = null;
  let failed = false;
  function paint() {
    const pct = Math.max(3, Math.min(100, Math.round(value)));
    barEl.style.width = `${pct}%`;
    const track = barEl.parentElement;
    if (track) track.setAttribute("aria-valuenow", String(pct));
    if (labelEl) labelEl.textContent = text;
  }
  function loop() {
    if (value < target) {
      value = Math.min(target, value + Math.max(0.4, (target - value) * 0.07));
      paint();
      timer = setTimeout(loop, 90);
    } else {
      timer = null;
    }
  }
  function stage(pct, msg) {
    target = Math.max(target, pct);
    text = msg;
    if (!timer) loop();
  }
  function stopCreep() {
    if (creepTimer) {
      clearInterval(creepTimer);
      creepTimer = null;
    }
  }
  function stopLoop() {
    if (timer) {
      clearTimeout(timer);
      timer = null;
    }
  }
  return {
    begin(msg = "Preparing update…") {
      stopCreep();
      stopLoop();
      value = 0;
      target = 0;
      failed = false;
      text = msg;
      barEl.classList.remove("error", "done");
      paint();
      stage(12, msg);
      const labels = [
        "Fetching repository from GitHub…",
        "Copying updated files into the app…",
        "Finishing up…",
      ];
      let i = 0;
      creepTimer = setInterval(() => {
        if (target < 82) {
          target = Math.min(82, target + 5);
          text = labels[Math.min(i, labels.length - 1)];
          i += 1;
          if (!timer) loop();
        }
      }, 2600);
    },
    copyingDone(msg = "Update files installed. Restarting the service…") {
      stopCreep();
      stage(90, msg);
    },
    restartWait(attempt) {
      stage(Math.min(97, 90 + attempt), `Waiting for the service to restart… (${attempt * 2}s)`);
    },
    finish(msg = "Update complete.") {
      stopCreep();
      stopLoop();
      value = 100;
      target = 100;
      text = msg;
      barEl.classList.add("done");
      paint();
    },
    fail(msg = "Update failed.") {
      stopCreep();
      stopLoop();
      failed = true;
      barEl.classList.add("error");
      text = msg;
      paint();
    },
    get failed() {
      return failed;
    },
  };
}

export function formatTime(value) {
  if (!value) return "";
  const d = new Date(value);
  if (!Number.isFinite(d.getTime())) return String(value);
  return d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

export function elapsedSeconds(attempt) {
  if (!attempt?.started_at || !attempt?.finished_at) return null;
  const sec = (Date.parse(attempt.finished_at) - Date.parse(attempt.started_at)) / 1000;
  return Number.isFinite(sec) && sec >= 0 ? sec : null;
}

export function formatDuration(seconds) {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  if (seconds < 60) return `${Math.round(seconds)} sec`;
  const mins = Math.floor(seconds / 60);
  const rem = Math.round(seconds % 60);
  return rem ? `${mins} min ${rem} sec` : `${mins} min`;
}

export function slugify(value) {
  return (
    String(value || "")
      .toLowerCase()
      .normalize("NFKD")
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "")
      .slice(0, 40) || `project-${Date.now()}`
  );
}

export function statusClass(status) {
  return `state state-${String(status || "draft").toLowerCase().replace(/\s+/g, "-")}`;
}

export function periodBucket(period, date = new Date()) {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  if (period === "today") return `${y}-${m}-${d}`;
  if (period === "month") return `${y}-${m}`;
  if (period === "year") return String(y);
  return "all";
}

export function matchesPeriod(value, period, current = new Date()) {
  if (period === "all") return true;
  if (!value) return false;
  const d = new Date(value);
  if (!Number.isFinite(d.getTime())) return false;
  if (period === "today") return d.toDateString() === current.toDateString();
  if (period === "month") return d.getFullYear() === current.getFullYear() && d.getMonth() === current.getMonth();
  return d.getFullYear() === current.getFullYear();
}
