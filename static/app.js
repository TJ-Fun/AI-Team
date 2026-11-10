import {
  $,
  escapeHtml,
  state,
  api,
  appAlert,
  appConfirm,
  appPrompt,
  waitForAppRestart,
  createUpdateProgress,
  formatTime,
  elapsedSeconds,
  formatDuration,
  slugify,
  statusClass,
  periodBucket,
  matchesPeriod,
} from "./js/core.js";
import { initPrompts } from "./js/prompts.js";
import { initProjectContext } from "./js/project-context.js";
import { initRepositoryViewer } from "./js/repository-viewer.js";
import { loadConfig, refreshStatus } from "./js/header.js";
import { initProviders } from "./js/providers.js";
import { initProjectSettings } from "./js/project-settings.js";
import { getAskedProvider } from "./js/task-utils.js";
import { initProjectSelectors } from "./js/project-selectors.js";
import { initTaskBoard } from "./js/task-board.js";
import { initTaskDialog } from "./js/task-dialog.js";
import { initTaskDetail } from "./js/task-detail.js";

(() => {
  const { loadAgentPrompts } = initPrompts();
  initProjectContext();
  const { openRepositoryView, openDocumentationFile } = initRepositoryViewer();
  const featureContext = {
    loadChat,
    renderNotifications,
    renderStatistics,
    openRepositoryView,
  };
  const projectSelectorsApi = initProjectSelectors(featureContext);
  featureContext.renderActiveProjectSelect = projectSelectorsApi.renderActiveProjectSelect;
  featureContext.renderActiveSubprojectSelect = projectSelectorsApi.renderActiveSubprojectSelect;
  const taskBoardApi = initTaskBoard(featureContext);
  const { refreshBoard, renderTasks } = taskBoardApi;
  featureContext.refreshBoard = refreshBoard;
  featureContext.renderTasks = renderTasks;
  const { openTaskDialog } = initTaskDialog({ refreshBoard });
  const providersApi = initProviders({ refreshStatus, refreshBoard });
  const { reloadSettingsData, setConnectionsSuccess, populateProviderSelects } = providersApi;
  const { loadProjectsIntoSettings } = initProjectSettings({ refreshBoard });
  const { openTaskDetail, renderTaskDetailContent } = initTaskDetail({
    refreshBoard,
    loadChat,
    populateProviderSelects,
    openTaskDialog,
  });
  featureContext.openTaskDetail = openTaskDetail;
  featureContext.renderTaskDetailContent = renderTaskDetailContent;

  // --- Notifications ---
  const notificationsDialog = $("#notifications-dialog");
  let appUpdateInProgress = false;

  function renderNotifications() {
    const countEl = $("#notification-count");
    const latestEl = $("#notification-latest-title");
    const modalCountEl = $("#notification-modal-count");
    const triggerEl = $("#open-notifications");
    const listEl = $("#notification-list");
    const items = state.notifications || [];
    const updates = items.filter((i) => i.kind === "app_update").length;
    const issues = items.length - updates;
    const parts = [];
    if (issues) parts.push(`${issues} ${issues === 1 ? "issue" : "issues"}`);
    if (updates) parts.push(`${updates} ${updates === 1 ? "update" : "updates"}`);
    const countLabel = parts.length ? parts.join(" · ") : "all clear";
    const latestTitle = items[0]?.title || "All clear";

    if (countEl) countEl.textContent = items.length ? String(items.length) : "All clear";
    if (latestEl) latestEl.textContent = latestTitle;
    if (modalCountEl) modalCountEl.textContent = `(${countLabel})`;
    if (triggerEl) {
      triggerEl.classList.toggle("has-issues", items.length > 0);
      triggerEl.title = items.length > 0 ? `${latestTitle} — click to view all` : "No active issues";
    }

    if (!listEl) return;
    if (appUpdateInProgress && listEl.querySelector(".update-progress")) return;
    if (!items.length) {
      listEl.innerHTML = `<p class="notification-empty">No current issues or provider suggestions.</p>`;
      return;
    }
    listEl.innerHTML = items
      .map(
        (item) => `
        <div class="notification notification-${escapeHtml(item.severity || "low")}">
          <div>
            <strong>${escapeHtml(item.title)}</strong>
            <p>${escapeHtml(item.message)}</p>
            ${item.action === "update" ? `<button type="button" class="notification-action" data-notification-action="update">Update now</button>` : ""}
            ${item.task_id ? `<button type="button" class="notification-action" data-open-task="${escapeHtml(item.task_id)}">Open task</button>` : ""}
          </div>
          <button type="button" class="notification-dismiss" data-dismiss-notification="${escapeHtml(item.id)}" aria-label="Dismiss">×</button>
        </div>`
      )
      .join("");
  }

  $("#open-notifications")?.addEventListener("click", () => {
    renderNotifications();
    if (notificationsDialog && !notificationsDialog.open) {
      notificationsDialog.showModal();
    }
  });
  $("#close-notifications")?.addEventListener("click", () => notificationsDialog?.close());
  $("#close-notifications-footer")?.addEventListener("click", () => notificationsDialog?.close());

  $("#notification-list")?.addEventListener("click", async (e) => {
    const actionBtn = e.target.closest("[data-notification-action]");
    if (actionBtn?.getAttribute("data-notification-action") === "update") {
      await runAppUpdateFromNotification(actionBtn);
      return;
    }
    const openBtn = e.target.closest("[data-open-task]");
    if (openBtn) {
      const taskId = openBtn.getAttribute("data-open-task");
      notificationsDialog?.close();
      void openTaskDetail(taskId);
      return;
    }
    const btn = e.target.closest("[data-dismiss-notification]");
    if (!btn) return;
    const id = btn.getAttribute("data-dismiss-notification");
    try {
      await api(`/api/notifications/${encodeURIComponent(id)}`, { method: "DELETE" });
      await refreshBoard();
    } catch (err) {
      appAlert(err.message);
    }
  });

  async function runAppUpdateFromNotification(btn) {
    btn.disabled = true;
    const item = btn.closest(".notification");
    let progressWrap = item ? item.querySelector(".update-progress") : null;
    if (item && !progressWrap) {
      progressWrap = document.createElement("div");
      progressWrap.className = "update-progress";
      progressWrap.innerHTML =
        '<div class="update-progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"><span class="update-progress-bar"></span></div>' +
        '<p class="update-progress-label"></p>';
      item.firstElementChild?.appendChild(progressWrap);
    }
    const progress = createUpdateProgress(
      progressWrap?.querySelector(".update-progress-bar"),
      progressWrap?.querySelector(".update-progress-label")
    );
    try {
      const cfgRes = await api("/api/settings/config");
      const cfg = cfgRes.config || {};
      if (!cfg.app_repo || !cfg.app_clone_dir || !cfg.app_working_dir) {
        appAlert("App update is not configured. Set the repository and directories in Settings → About first.", "Update unavailable");
        return;
      }
      if (!(await appConfirm("Update the app to the latest version and restart?", { title: "Update app" }))) return;
      appUpdateInProgress = true;
      progress?.begin("Preparing update…");
      const res = await api("/api/settings/update", {
        method: "POST",
        body: JSON.stringify({
          app_repo: cfg.app_repo,
          app_clone_dir: cfg.app_clone_dir,
          app_working_dir: cfg.app_working_dir,
        }),
      });
      progress?.copyingDone("Update files installed. Restarting the service…");
      const statusShim = { textContent: "", className: "" };
      await waitForAppRestart(statusShim, res.message || "Update complete.", "update-status", progress);
    } catch (err) {
      progress?.fail(`Update failed: ${err.message}`);
      appAlert(`Update failed: ${err.message}`, "Update failed");
    } finally {
      appUpdateInProgress = false;
      btn.disabled = false;
    }
  }

  // --- Statistics Dialog ---
  const statsDialog = $("#statistics-dialog");
  const statPeriodSelect = $("#stat-period");

  function renderStatistics() {
    const period = statPeriodSelect?.value || "today";
    const records = [];
    for (const task of state.tasks) {
      for (const att of task.attempts || []) {
        if (att.provider) {
          records.push({ ...att, requested_at: att.started_at || att.finished_at });
        }
      }
    }
    const bucket = periodBucket(period);
    const applicableResets = (state.statResets || []).filter((r) => r.period === period && r.bucket === bucket);
    const cutoff = new Map(applicableResets.map((r) => [`${r.provider}\0${r.model}`, r.reset_at]));
    const metrics = new Map();
    const ensure = (provider, model) => {
      const k = `${provider}\0${model}`;
      if (!metrics.has(k)) {
        metrics.set(k, { provider, model, requests: 0, replies: 0, seconds: 0, timedReplies: 0 });
      }
      return metrics.get(k);
    };

    for (const r of records) if (r.provider) ensure(r.provider, r.model || "Model not recorded");
    for (const r of applicableResets) ensure(r.provider, r.model);

    for (const r of records) {
      if (!r.provider || !matchesPeriod(r.requested_at, period)) continue;
      const model = r.model || "Model not recorded";
      const resetAt = cutoff.get(`${r.provider}\0${model}`);
      if (resetAt && r.requested_at && Date.parse(r.requested_at) <= Date.parse(resetAt)) continue;
      const m = ensure(r.provider, model);
      m.requests += 1;
      if (r.state === "succeeded") {
        m.replies += 1;
        const dur = typeof r.duration_seconds === "number" ? r.duration_seconds : elapsedSeconds(r);
        if (dur != null) {
          m.seconds += dur;
          m.timedReplies += 1;
        }
      }
    }

    const rows = [...metrics.values()].sort(
      (a, b) => a.provider.localeCompare(b.provider) || a.model.localeCompare(b.model)
    );
    const total = rows.reduce(
      (acc, item) => ({
        requests: acc.requests + item.requests,
        replies: acc.replies + item.replies,
        seconds: acc.seconds + item.seconds,
        timedReplies: acc.timedReplies + item.timedReplies,
      }),
      { requests: 0, replies: 0, seconds: 0, timedReplies: 0 }
    );
    $("#stats-total-requests").textContent = String(total.requests);
    $("#stats-success-rate").textContent = total.requests
      ? `${Math.round((total.replies / total.requests) * 100)}%`
      : "—";
    $("#stats-success-detail").textContent = total.requests
      ? `${total.replies} successful ${total.replies === 1 ? "reply" : "replies"} of ${total.requests} requests`
      : "No requests in this period";
    $("#stats-average-time").textContent = formatDuration(
      total.timedReplies ? total.seconds / total.timedReplies : null
    );
    const qRows = state.qualityRows || [];
    const scoredOutcomes = qRows.reduce(
      (acc, row) => {
        const measured = Number(row.measured) || 0;
        const rate = Number(row.qa_pass_rate);
        if (measured > 0 && Number.isFinite(rate)) {
          acc.measured += measured;
          acc.passed += (rate / 100) * measured;
        }
        return acc;
      },
      { measured: 0, passed: 0 }
    );
    $("#stats-qa-pass-rate").textContent = scoredOutcomes.measured
      ? `${Math.round((scoredOutcomes.passed / scoredOutcomes.measured) * 100)}%`
      : "—";
    $("#stats-qa-detail").textContent = scoredOutcomes.measured
      ? `${scoredOutcomes.measured} scored deliverable checks · all time`
      : "No scored deliverable checks yet";

    const cells = (m) => `
      <span>${m.requests} ${m.requests === 1 ? "request" : "requests"}</span>
      <span>${m.replies} ${m.replies === 1 ? "reply" : "replies"}</span>
      <span>avg ${escapeHtml(formatDuration(m.timedReplies ? m.seconds / m.timedReplies : null))}</span>`;

    $("#provider-statistics").hidden = false;
    $("#provider-stat-rows").innerHTML =
      `<div class="provider-stat-row provider-stat-total"><strong>Total</strong>${cells(total)}<span></span></div>` +
      rows
        .map(
          (m) => `
          <div class="provider-stat-row">
            <div class="provider-stat-model"><strong>${escapeHtml(m.provider)}</strong><small>${escapeHtml(
              m.model
            )}</small></div>
            ${cells(m)}
            <button type="button" class="stat-reset" data-reset-provider="${escapeHtml(
              m.provider
            )}" data-reset-model="${escapeHtml(m.model)}">Reset</button>
          </div>`
        )
        .join("");

    $("#provider-quality-rows").innerHTML = qRows.length
      ? `<div class="quality-head"><span>Role / provider</span><span>Requests / replies</span><span>QA pass</span><span>Errors</span><span>Avg time</span></div>` +
        qRows
          .map(
            (r) => `
          <div class="quality-row">
            <strong>${escapeHtml(r.role)}<small>${escapeHtml(r.provider)}</small></strong>
            <span>${r.requests} / ${r.replies}</span>
            <span>${r.qa_pass_rate == null ? `— (${r.measured} scored)` : `${r.qa_pass_rate}% (${r.measured})`}</span>
            <span>${r.quota_or_service_errors}</span>
            <span>${r.average_seconds == null ? "—" : escapeHtml(formatDuration(r.average_seconds))}</span>
          </div>`
          )
          .join("")
      : `<p class="notification-empty">No task outcomes recorded yet. Deliverable QA results will appear here.</p>`;
  }

  $("#open-statistics")?.addEventListener("click", () => {
    renderStatistics();
    if (!statsDialog.open) statsDialog.showModal();
  });
  $("#close-statistics")?.addEventListener("click", () => statsDialog.close());
  statPeriodSelect?.addEventListener("change", () => renderStatistics());

  $("#provider-stat-rows")?.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-reset-provider]");
    if (!btn) return;
    const provider = btn.getAttribute("data-reset-provider");
    const model = btn.getAttribute("data-reset-model");
    const period = statPeriodSelect.value || "today";
    if (!(await appConfirm(`Reset the displayed statistic for ${provider} · ${model}? Past usage history will be kept.`, { title: "Reset statistic", danger: true }))) {
      return;
    }
    btn.disabled = true;
    try {
      await api("/api/llm-usage/reset", {
        method: "POST",
        body: JSON.stringify({ provider, model, period, bucket: periodBucket(period) }),
      });
      await refreshBoard();
    } catch (err) {
      appAlert(err.message);
    } finally {
      btn.disabled = false;
    }
  });

  // --- PM/PO Chat ---
  const messagesEl = $("#messages");
  const chatForm = $("#chat-form");
  const chatInput = $("#chat-input");

  function renderChatMessages(messages, pendingLabel = "") {
    if (!messages.length && !pendingLabel) {
      messagesEl.innerHTML = `
        <div class="welcome-message">
          <div class="welcome-icon">✳</div>
          <h3>Talk through the work</h3>
          <p>Ask for help clarifying a request, planning acceptance checks, or understanding the board.</p>
        </div>`;
      return;
    }
    let html = messages
      .map(
        (m) => `
        <div class="message ${m.role === "user" ? "user-message" : "assistant-message"}">
          <div class="message-role">${m.role === "user" ? "You" : `PM/PO${m.provider ? ` · ${escapeHtml(m.provider)}` : ""}`}</div>
          <div class="message-content">${escapeHtml(m.content)}</div>
        </div>`
      )
      .join("");
    if (pendingLabel) {
      html += `<div class="message assistant-message pending-message">${escapeHtml(pendingLabel)}</div>`;
    }
    messagesEl.innerHTML = html;
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  let chatMessages = [];
  async function loadChat() {
    try {
      const res = await api(`/api/chat?project_id=${encodeURIComponent(state.activeProjectId)}`);
      chatMessages = res.messages || [];
      renderChatMessages(chatMessages);
    } catch (err) {
      messagesEl.innerHTML = `<div class="inline-error">Could not load chat history: ${escapeHtml(err.message)}</div>`;
    }
  }

  chatForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = chatInput.value.trim();
    if (!text) return;
    chatInput.value = "";
    $("#send-button").disabled = true;
    chatMessages = [...chatMessages, { role: "user", content: text }];
    renderChatMessages(chatMessages, "PM/PO is thinking…");
    try {
      const res = await api("/api/chat", {
        method: "POST",
        body: JSON.stringify({ message: text, project_id: state.activeProjectId }),
      });
      chatMessages = res.messages || chatMessages;
      renderChatMessages(chatMessages);
    } catch (err) {
      chatMessages = [...chatMessages, { role: "assistant", content: err.message }];
      renderChatMessages(chatMessages);
    } finally {
      $("#send-button").disabled = false;
      chatInput.focus();
    }
  });

  $("#plan-request")?.addEventListener("click", async () => {
    const text = chatInput.value.trim();
    if (!text) {
      appAlert("Type a request to plan first.", "Nothing to plan");
      chatInput.focus();
      return;
    }
    const planBtn = $("#plan-request");
    planBtn.disabled = true;
    $("#send-button").disabled = true;
    try {
      await api("/api/pm-requests", {
        method: "POST",
        body: JSON.stringify({ request: text, project_id: state.activeProjectId }),
      });
      chatInput.value = "";
      await Promise.all([loadChat(), refreshBoard()]);
    } catch (err) {
      appAlert(err.message);
    } finally {
      planBtn.disabled = false;
      $("#send-button").disabled = false;
      chatInput.focus();
    }
  });

  chatInput?.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      chatForm.requestSubmit();
    }
  });

  $("#clear-chat")?.addEventListener("click", async () => {
    if (!(await appConfirm("Clear the saved PM/PO conversation? This keeps all tasks and agent reply files.", { title: "Clear conversation" }))) {
      return;
    }
    try {
      await api(`/api/chat?project_id=${encodeURIComponent(state.activeProjectId)}`, { method: "DELETE" });
      await loadChat();
    } catch (err) {
      appAlert(err.message);
    }
  });

  // --- Settings Dialog ---
  const settingsDialog = $("#settings-dialog");
  let loadedSettingsTabs = new Set();

  async function switchSettingsTab(tabName, focusTab = false) {
    const validTabs = ["projects", "connections", "routing", "prompts", "logs", "about"];
    const tab = validTabs.includes(tabName) ? tabName : "about";
    document.querySelectorAll("[data-settings-tab]").forEach((button) => {
      const selected = button.dataset.settingsTab === tab;
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
      if (selected && focusTab) button.focus();
    });
    document.querySelectorAll(".settings-tab-panel").forEach((panel) => {
      panel.hidden = panel.id !== `settings-panel-${tab}`;
    });
    localStorage.setItem("ai_team_settings_tab", tab);
    $("#settings-error").textContent = "";
    if (tab === "projects" && !loadedSettingsTabs.has(tab)) {
      try {
        await loadProjectsIntoSettings();
        loadedSettingsTabs.add(tab);
      } catch (err) {
        $("#settings-error").textContent = `Could not load projects: ${err.message}`;
      }
    } else if (tab === "connections" && !loadedSettingsTabs.has(tab)) {
      $("#connections-error").textContent = "";
      setConnectionsSuccess("");
      try {
        await reloadSettingsData();
        loadedSettingsTabs.add(tab);
      } catch (err) {
        $("#settings-error").textContent = `Could not load connections: ${err.message}`;
      }
    } else if (tab === "routing" && !loadedSettingsTabs.has(tab)) {
      $("#routing-error").textContent = "";
      try {
        await reloadSettingsData();
        loadedSettingsTabs.add(tab);
      } catch (err) {
        $("#settings-error").textContent = `Could not load routing settings: ${err.message}`;
      }
    } else if (tab === "prompts" && !loadedSettingsTabs.has(tab)) {
      try {
        await loadAgentPrompts();
        loadedSettingsTabs.add(tab);
      } catch (err) {
        $("#prompt-error").textContent = `Could not load agent prompts: ${err.message}`;
      }
    } else if (tab === "about") {
      const about = $("#about-content");
      try {
        const res = await api("/api/documentation-files?file=ABOUT.md");
        about.textContent = res.content;
      } catch (err) {
        about.textContent = `Could not load app information: ${err.message}`;
      }
    }
  }

  document.querySelectorAll("[data-settings-tab]").forEach((button) => {
    button.addEventListener("click", () => void switchSettingsTab(button.dataset.settingsTab));
    button.addEventListener("keydown", (event) => {
      const tabs = [...document.querySelectorAll("[data-settings-tab]")];
      const index = tabs.indexOf(button);
      let nextIndex = index;
      if (event.key === "ArrowRight") nextIndex = (index + 1) % tabs.length;
      else if (event.key === "ArrowLeft") nextIndex = (index - 1 + tabs.length) % tabs.length;
      else if (event.key === "Home") nextIndex = 0;
      else if (event.key === "End") nextIndex = tabs.length - 1;
      else return;
      event.preventDefault();
      void switchSettingsTab(tabs[nextIndex].dataset.settingsTab, true);
    });
  });

  $("#open-settings")?.addEventListener("click", async () => {
    $("#settings-error").textContent = "";
    loadedSettingsTabs = new Set();
    try {
      const res = await api("/api/settings/config");
      const config = res.config || {};
      $("#app-repo-url").value = config.app_repo || "";
      $("#app-clone-dir").value = config.app_clone_dir || "";
      $("#app-working-dir").value = config.app_working_dir || "";
      $("#log-capture-level").value = config.log_level || "INFO";
    } catch (err) {
      $("#settings-error").textContent = `Could not load app configuration: ${err.message}`;
    }
    if (!settingsDialog.open) settingsDialog.showModal();
    await switchSettingsTab(localStorage.getItem("ai_team_settings_tab") || "app");
  });

  $("#close-settings")?.addEventListener("click", () => settingsDialog.close());
  $("#cancel-settings")?.addEventListener("click", () => settingsDialog.close());
  $("#open-version-log")?.addEventListener("click", () => {
    void openDocumentationFile("VERSIONS.md", "Version log");
  });
  $("#open-app-documentation")?.addEventListener("click", () => {
    void openDocumentationFile("DOCUMENTATION.md", "Documentation");
  });

  $("#log-capture-level")?.addEventListener("change", async (e) => {
    const level = e.target.value;
    const status = $("#log-settings-status");
    status.textContent = "Saving log level…";
    try {
      const res = await api("/api/settings/log-level", {
        method: "POST",
        body: JSON.stringify({ level }),
      });
      status.textContent = `Logs will store ${res.log_level} and above.`;
    } catch (err) {
      status.textContent = `Could not change log level: ${err.message}`;
    }
  });

  // Save app config
  $("#save-app-config")?.addEventListener("click", async () => {
    const appRepo = $("#app-repo-url")?.value?.trim();
    const appCloneDir = $("#app-clone-dir")?.value?.trim();
    const appWorkingDir = $("#app-working-dir")?.value?.trim();
    try {
      await api("/api/settings/config", {
        method: "POST",
        body: JSON.stringify({
          app_repo: appRepo,
          app_clone_dir: appCloneDir,
          app_working_dir: appWorkingDir,
        }),
      });
      appAlert("Configuration saved successfully.", "Settings saved");
    } catch (err) {
      appAlert(`Failed to save configuration: ${err.message}`, "Settings not saved");
    }
  });

  // Load logs
  $("#load-logs")?.addEventListener("click", async () => {
    const level = $("#log-level")?.value || "all";
    $("#log-output").textContent = "Loading logs…";
    try {
      const res = await api("/api/settings/logs", {
        method: "POST",
        body: JSON.stringify({ level, lines: 1000 }),
      });
      const logs = res.logs || [];
      if (logs.length === 0) {
        $("#log-output").innerHTML = `<span class="muted">No logs found.</span>`;
      } else {
        $("#log-output").innerHTML = logs
          .map((line) => {
            let className = "log-info";
            const level = line.match(/\|\s*(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s*\|/)?.[1];
            if (level === "CRITICAL") className = "log-critical";
            else if (level === "ERROR") className = "log-error";
            else if (level === "WARNING") className = "log-warning";
            else if (level === "DEBUG") className = "log-debug";
            return `<span class="${className}">${escapeHtml(line)}</span>`;
          })
          .join("\n");
      }
    } catch (err) {
      $("#log-output").innerHTML = `<span class="log-error">Failed to load logs: ${escapeHtml(err.message)}</span>`;
    }
  });

  // Clear logs
  $("#clear-logs")?.addEventListener("click", async () => {
    if (!(await appConfirm("Clear all application logs? This cannot be undone.", { title: "Clear logs", danger: true }))) return;
    try {
      await api("/api/settings/logs/clear", { method: "POST" });
      $("#log-output").innerHTML = `<span class="muted">Logs cleared.</span>`;
    } catch (err) {
      appAlert(`Failed to clear logs: ${err.message}`, "Clear logs failed");
    }
  });

  // Restart app
  $("#restart-app")?.addEventListener("click", async () => {
    if (!(await appConfirm("Restart the application? This will briefly interrupt the service.", { title: "Restart app" }))) return;
    const statusEl = $("#restart-status");
    statusEl.textContent = "Restarting…";
    statusEl.className = "restart-status info";
    try {
      await api("/api/settings/restart", { method: "POST" });
      statusEl.textContent = "Restart scheduled.";
      statusEl.className = "restart-status success";
      await waitForAppRestart(statusEl, "Restart scheduled.");
    } catch (err) {
      statusEl.textContent = `Restart failed: ${err.message}`;
      statusEl.className = "restart-status error";
    }
  });

  // Update app
  $("#update-app")?.addEventListener("click", async () => {
    const appRepo = $("#app-repo-url")?.value?.trim();
    const appCloneDir = $("#app-clone-dir")?.value?.trim();
    const appWorkingDir = $("#app-working-dir")?.value?.trim();
    if (!appRepo || !appCloneDir || !appWorkingDir) {
      appAlert("Please fill in the app repository URL and local checkout directory.", "Update not configured");
      return;
    }
    if (!(await appConfirm(`Update ${appRepo} in ${appCloneDir}, copy newer files into ${appWorkingDir}, and restart the app?`, { title: "Update app" }))) return;
    const statusEl = $("#update-status");
    const updateButton = $("#update-app");
    const progressWrap = $("#update-progress");
    const progress = createUpdateProgress($("#update-progress-bar"), $("#update-progress-label"));
    updateButton.disabled = true;
    if (progressWrap) progressWrap.hidden = false;
    progress?.begin("Preparing update…");
    statusEl.textContent = "Updating…";
    statusEl.className = "update-status info";
    try {
      const res = await api("/api/settings/update", {
        method: "POST",
        body: JSON.stringify({
          app_repo: appRepo,
          app_clone_dir: appCloneDir,
          app_working_dir: appWorkingDir,
        }),
      });
      progress?.copyingDone(`${res.message} Restarting the service…`);
      statusEl.textContent = `${res.message} Restarting the service…`;
      statusEl.className = "update-status success";
      await waitForAppRestart(statusEl, res.message, "update-status", progress);
    } catch (err) {
      progress?.fail(`Update failed: ${err.message}`);
      statusEl.textContent = `Update failed: ${err.message}`;
      statusEl.className = "update-status error";
    } finally {
      updateButton.disabled = false;
    }
  });

  // --- Boot ---
  void loadConfig(providersApi);
  void refreshBoard();
  void loadChat();
  void refreshStatus();
  setInterval(() => void refreshBoard(), 5000);
  setInterval(() => void refreshStatus(), 2000);
})();
