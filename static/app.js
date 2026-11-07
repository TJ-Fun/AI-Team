(() => {
  const $ = (selector) => document.querySelector(selector);
  const escapeHtml = (value) =>
    String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");

  const state = {
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
    routingProviders: ["Gemini", "GroqCloud", "OpenRouter"],
    repoMode: "files",
    repoCurrentPath: "",
    pollBusy: false,
    llmBusy: false,
  };

  async function api(url, options = {}) {
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

  async function waitForAppRestart(statusEl, message, statusType = "restart-status") {
    for (let attempt = 0; attempt < 30; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 2000));
      try {
        await api("/api/status");
        location.reload();
        return;
      } catch (_err) {
        statusEl.textContent = `${message} Waiting for the service to restart… (${(attempt + 1) * 2}s)`;
      }
    }
    statusEl.textContent = `${message} The service did not respond within 60 seconds. Check the service logs.`;
    statusEl.className = `${statusType} error`;
  }

  function formatTime(value) {
    if (!value) return "";
    const d = new Date(value);
    if (!Number.isFinite(d.getTime())) return String(value);
    return d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  }

  function elapsedSeconds(attempt) {
    if (!attempt?.started_at || !attempt?.finished_at) return null;
    const sec = (Date.parse(attempt.finished_at) - Date.parse(attempt.started_at)) / 1000;
    return Number.isFinite(sec) && sec >= 0 ? sec : null;
  }

  function formatDuration(seconds) {
    if (seconds == null || !Number.isFinite(seconds)) return "—";
    if (seconds < 60) return `${Math.round(seconds)} sec`;
    const mins = Math.floor(seconds / 60);
    const rem = Math.round(seconds % 60);
    return rem ? `${mins} min ${rem} sec` : `${mins} min`;
  }

  function slugify(value) {
    return (
      String(value || "")
        .toLowerCase()
        .normalize("NFKD")
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-|-$/g, "")
        .slice(0, 40) || `project-${Date.now()}`
    );
  }

  function statusClass(status) {
    return `state state-${String(status || "draft").toLowerCase().replace(/\s+/g, "-")}`;
  }

  function periodBucket(period, date = new Date()) {
    const y = date.getFullYear();
    const m = String(date.getMonth() + 1).padStart(2, "0");
    const d = String(date.getDate()).padStart(2, "0");
    if (period === "today") return `${y}-${m}-${d}`;
    if (period === "month") return `${y}-${m}`;
    if (period === "year") return String(y);
    return "all";
  }

  function matchesPeriod(value, period, current = new Date()) {
    if (period === "all") return true;
    if (!value) return false;
    const d = new Date(value);
    if (!Number.isFinite(d.getTime())) return false;
    if (period === "today") return d.toDateString() === current.toDateString();
    if (period === "month") return d.getFullYear() === current.getFullYear() && d.getMonth() === current.getMonth();
    return d.getFullYear() === current.getFullYear();
  }

  // --- Header & Status ---
  function chatProviderOptions(selected) {
    const providers = state.routingProviders.length ? state.routingProviders : ["Gemini", "GroqCloud", "OpenRouter"];
    const known = state.connections.length > 0;
    return providers
      .map(
        (opt) =>
          `<option value="${escapeHtml(opt)}"${opt === selected ? " selected" : ""}>${escapeHtml(opt)}${
            known && !isProviderConnected(opt) ? " · not connected" : ""
          }</option>`
      )
      .join("");
  }

  async function loadConfig() {
    const dot = $("#model-dot") || $(".model-chip .status-dot");
    const nameEl = $("#model-name");
    const menu = $("#chat-provider-menu");
    try {
      const cfg = await api("/api/config");
      const provider = cfg.chat_provider || "Gemini";
      state.chatProvider = provider;
      if (Array.isArray(cfg.providers) && cfg.providers.length) {
        state.routingProviders = cfg.providers;
      }
      nameEl.textContent = `Chat · ${provider}`;
      if (menu) menu.innerHTML = chatProviderOptions(provider);
      dot?.classList.toggle("offline", cfg.chat_configured === false);
    } catch {
      nameEl.textContent = "AI chat";
      dot?.classList.add("offline");
    }
  }

  async function refreshStatus() {
    try {
      const data = await api("/api/status");
      const cpu = data.cpu_percent;
      $("#cpu-value").textContent = cpu == null ? "—" : `${cpu}%`;
      const bar = $("#cpu-bar");
      if (bar) bar.style.width = cpu == null ? "0%" : `${Math.max(0, Math.min(100, cpu))}%`;
      const track = $(".cpu-track");
      if (track) track.setAttribute("aria-valuenow", String(cpu ?? 0));

      const working = Boolean(data.chat_working || data.gemini_working || data.openrouter_working);
      state.llmBusy = Boolean(data.chat_working);
      const llmState = $("#llm-state");
      llmState.textContent = data.openrouter_working
        ? "Hosted AI working"
        : data.gemini_working
          ? "Gemini working"
          : data.chat_working
            ? "Chat working"
            : "Idle";
      llmState.classList.toggle("working", working);
      $("#clear-chat").disabled = state.llmBusy;

      $("#agent-status").textContent =
        `Gemini ${data.gemini_configured ? "connected" : "not connected"} · ` +
        `GroqCloud ${data.groq_configured ? "connected" : "not connected"} · ` +
        `OpenRouter ${data.openrouter_configured ? "connected" : "not connected"} · ` +
        `GitHub ${data.github_configured ? "connected" : "not connected"}`;

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

  // --- Notifications ---
  const notificationsDialog = $("#notifications-dialog");

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
    const btn = e.target.closest("[data-dismiss-notification]");
    if (!btn) return;
    const id = btn.getAttribute("data-dismiss-notification");
    try {
      await api(`/api/notifications/${encodeURIComponent(id)}`, { method: "DELETE" });
      await refreshBoard();
    } catch (err) {
      alert(err.message);
    }
  });

  async function runAppUpdateFromNotification(btn) {
    btn.disabled = true;
    try {
      const cfgRes = await api("/api/settings/config");
      const cfg = cfgRes.config || {};
      if (!cfg.app_repo || !cfg.app_clone_dir || !cfg.app_working_dir) {
        alert("App update is not configured. Set the repository and directories in Settings → About first.");
        return;
      }
      if (!confirm(`Update the app to the latest version and restart?`)) return;
      const res = await api("/api/settings/update", {
        method: "POST",
        body: JSON.stringify({
          app_repo: cfg.app_repo,
          app_clone_dir: cfg.app_clone_dir,
          app_working_dir: cfg.app_working_dir,
        }),
      });
      const statusShim = { textContent: "", className: "" };
      await waitForAppRestart(statusShim, res.message || "Update complete.", "update-status");
    } catch (err) {
      alert(`Update failed: ${err.message}`);
    } finally {
      btn.disabled = false;
    }
  }

  // --- Projects & Active Project / Sub-project Selectors ---
  function renderActiveSubprojectSelect() {
    const wrap = $("#active-subproject-wrap");
    const select = $("#active-subproject");
    if (!wrap || !select) return;

    const activeProj = state.projects.find((p) => p.id === state.activeProjectId);
    const modules = activeProj?.modules || [];
    if (!modules.length) {
      if (state.activeModuleId) {
        state.activeModuleId = "";
        localStorage.setItem("ai_team_active_subproject", "");
      }
      wrap.hidden = true;
      select.innerHTML = `<option value="">Whole project</option>`;
      return;
    }

    if (state.activeModuleId && !modules.some((m) => m.id === state.activeModuleId)) {
      state.activeModuleId = "";
      localStorage.setItem("ai_team_active_subproject", "");
    }

    select.innerHTML =
      `<option value=""${state.activeModuleId === "" ? " selected" : ""}>Whole project</option>` +
      modules
        .map(
          (m) =>
            `<option value="${escapeHtml(m.id)}"${m.id === state.activeModuleId ? " selected" : ""}>${escapeHtml(
              m.name
            )}</option>`
        )
        .join("");
    wrap.hidden = false;
  }

  function renderActiveProjectSelect() {
    const select = $("#active-project");
    if (!select) return;
    if (!state.projects.some((p) => p.id === state.activeProjectId)) {
      state.activeProjectId = state.projects[0]?.id || "default";
      localStorage.setItem("ai_team_active_project", state.activeProjectId);
      state.activeModuleId = "";
      localStorage.setItem("ai_team_active_subproject", "");
    }
    select.innerHTML = state.projects
      .map(
        (p) =>
          `<option value="${escapeHtml(p.id)}"${p.id === state.activeProjectId ? " selected" : ""}>${escapeHtml(p.name)}</option>`
      )
      .join("");
    renderActiveSubprojectSelect();
  }

  $("#active-project")?.addEventListener("change", (e) => {
    state.activeProjectId = e.target.value;
    localStorage.setItem("ai_team_active_project", state.activeProjectId);
    state.activeModuleId = "";
    localStorage.setItem("ai_team_active_subproject", "");
    renderActiveSubprojectSelect();
    renderTasks();
    void loadChat();
    if ($("#repository-view-dialog")?.open) {
      void openRepositoryView(state.repoMode);
    }
  });

  $("#active-subproject")?.addEventListener("change", (e) => {
    state.activeModuleId = e.target.value;
    localStorage.setItem("ai_team_active_subproject", state.activeModuleId);
    renderTasks();
  });

  // --- Task Board ---
  function getAskedProvider(task) {
    if (task.provider) return task.provider;
    if (task.assigned_role === "OpenRouter free agent") return "OpenRouter";
    if (task.assigned_role === "Gemini") return "Gemini";
    return "Automatic";
  }

  function renderTasks() {
    const allTasks = state.tasks || [];
    const activeProj = state.projects.find((p) => p.id === state.activeProjectId);
    const modules = activeProj?.modules || [];
    const effectiveModuleId = modules.some((m) => m.id === state.activeModuleId) ? state.activeModuleId : "";

    const projectTasks = allTasks.filter((t) => (t.project_id || "default") === state.activeProjectId);
    const visibleTasks = effectiveModuleId
      ? projectTasks.filter((t) => (t.module_id || "") === effectiveModuleId)
      : projectTasks;

    const overviewExists = projectTasks.some((t) => t.task_type === "project_overview");
    const plannedWork = visibleTasks.filter((t) => t.task_type === "work" && t.status !== "Canceled");
    const completedWork = plannedWork.filter((t) => t.status === "Done").length;
    const pct = plannedWork.length ? Math.round((completedWork * 100) / plannedWork.length) : 0;

    const progWrap = $("#project-progress");
    if (progWrap) {
      progWrap.hidden = !overviewExists;
      if (overviewExists) {
        $("#progress-label").textContent = `${completedWork} of ${plannedWork.length} tasks · ${pct}%`;
        $("#progress-bar").value = pct;
      }
    }

    $("#task-summary").textContent = `${visibleTasks.length} ${visibleTasks.length === 1 ? "task" : "tasks"}`;
    const listEl = $("#task-list");
    const emptyEl = $("#empty-state");

    if (!visibleTasks.length) {
      listEl.innerHTML = "";
      if (emptyEl) {
        const emptyTitle = emptyEl.querySelector("h3");
        const emptyCopy = emptyEl.querySelector("p");
        if (emptyTitle) {
          emptyTitle.textContent = effectiveModuleId ? "No tasks in this sub-project yet" : "No tasks yet";
        }
        if (emptyCopy) {
          emptyCopy.textContent = effectiveModuleId
            ? "Create a task for this sub-project or switch back to Whole project."
            : "Create a task to start keeping the project plan in one place.";
        }
        emptyEl.hidden = false;
      }
      return;
    }
    if (emptyEl) emptyEl.hidden = true;

    listEl.innerHTML = visibleTasks
      .map((task) => {
        const proj = state.projects.find((p) => p.id === (task.project_id || "default"));
        const mod = proj?.modules?.find((m) => m.id === task.module_id);
        const attempts = task.attempts || [];
        const successful = [...attempts].reverse().find((a) => a.state === "succeeded");
        const dur = successful ? elapsedSeconds(successful) : null;
        const asked = getAskedProvider(task);
        const sentTo = [...new Set(attempts.map((a) => a.provider).filter(Boolean))];
        const route = sentTo.length
          ? `${asked}${asked === "Automatic" ? ` (sent to ${sentTo.join(", ")})` : ""}`
          : asked;
        const checksCount = (task.acceptance_checks || []).length;
        const pr = task.pull_request;

        return `
        <article class="task-card" data-task-id="${escapeHtml(task.id)}">
          <div class="task-topline">
            <span class="${escapeHtml(statusClass(task.status))}">${escapeHtml(task.status)}</span>
            <span class="task-id">${escapeHtml(task.id)}</span>
            <time>${escapeHtml(formatTime(task.created_at))}</time>
          </div>
          <h3>${escapeHtml(task.title)}</h3>
          ${
            proj
              ? `<div class="task-scope">${escapeHtml(proj.name)}${mod ? ` / ${escapeHtml(mod.name)}` : ""}</div>`
              : ""
          }
          <p class="task-request">${escapeHtml(task.request)}</p>
          <div class="task-ai-info">
            <span>Requested: ${escapeHtml(route)}</span>
            <span>${
              successful
                ? `Answered by: ${escapeHtml(successful.provider)} · ${escapeHtml(formatDuration(dur))}`
                : attempts.length
                  ? "No answer yet"
                  : "Not sent yet"
            }</span>
            ${
              pr?.url && String(pr.url).startsWith("https://github.com/")
                ? `<span><a href="${escapeHtml(pr.url)}" target="_blank" rel="noopener noreferrer">PR #${escapeHtml(pr.number)} · ${escapeHtml(pr.state)}</a></span>`
                : ""
            }
          </div>
          ${
            task.status === "Ready" && task.dispatch_note
              ? `<p class="queue-note">${escapeHtml(task.dispatch_note)}</p>`
              : ""
          }
          ${
            task.status === "Waiting for capacity"
              ? `<p class="queue-note">${escapeHtml(task.waiting_reason || "Waiting for the provider retry time.")}${
                  task.retry_after ? ` · retry after ${escapeHtml(formatTime(task.retry_after))}` : ""
                }</p>`
              : ""
          }
          ${
            task.status === "Blocked" && task.block_reason
              ? `<p class="queue-note blocked-note">${escapeHtml(task.block_reason)}</p>`
              : ""
          }
          <div class="task-footer">
            <span class="role-icon">${escapeHtml(task.assigned_role)} · ${escapeHtml(asked)}</span>
            <span>${checksCount} acceptance ${checksCount === 1 ? "check" : "checks"}</span>
            <div class="task-card-actions">
              ${
                task.status === "Draft"
                  ? `<button class="primary card-ready" type="button" data-mark-ready="${escapeHtml(task.id)}">Mark ready</button>`
                  : ""
              }
              ${
                task.status === "Waiting for capacity"
                  ? `<button class="primary card-ready" type="button" data-retry-task="${escapeHtml(task.id)}">Retry</button>`
                  : ""
              }
              <button class="danger card-delete" type="button" data-delete-task="${escapeHtml(task.id)}">Delete</button>
            </div>
            <button class="text-button" type="button" data-open-detail="${escapeHtml(task.id)}">Details</button>
          </div>
        </article>`;
      })
      .join("");
  }

  $("#task-list")?.addEventListener("click", async (e) => {
    const readyBtn = e.target.closest("[data-mark-ready], [data-retry-task]");
    if (readyBtn) {
      const id = readyBtn.getAttribute("data-mark-ready") || readyBtn.getAttribute("data-retry-task");
      readyBtn.disabled = true;
      try {
        await api(`/api/tasks/${encodeURIComponent(id)}/status`, {
          method: "POST",
          body: JSON.stringify({ status: "Ready" }),
        });
        await refreshBoard();
      } catch (err) {
        alert(err.message);
      } finally {
        readyBtn.disabled = false;
      }
      return;
    }

    const delBtn = e.target.closest("[data-delete-task]");
    if (delBtn) {
      const id = delBtn.getAttribute("data-delete-task");
      if (
        !confirm(
          "Permanently delete this task, its agent replies, and its task activity log entries? The overall chat conversation will remain. This cannot be undone."
        )
      ) {
        return;
      }
      delBtn.disabled = true;
      try {
        await api(`/api/tasks/${encodeURIComponent(id)}`, { method: "DELETE" });
        await refreshBoard();
      } catch (err) {
        alert(err.message);
      } finally {
        delBtn.disabled = false;
      }
      return;
    }

    const detailBtn = e.target.closest("[data-open-detail]");
    if (detailBtn) {
      const id = detailBtn.getAttribute("data-open-detail");
      void openTaskDetail(id);
    }
  });

  async function refreshBoard() {
    if (state.pollBusy) return;
    state.pollBusy = true;
    try {
      const [tasksRes, usageRes, noticesRes, projectsRes, qualityRes] = await Promise.all([
        api("/api/tasks"),
        api("/api/llm-usage"),
        api("/api/notifications"),
        api("/api/projects"),
        api("/api/provider-quality"),
      ]);
      state.projects = projectsRes.projects || [];
      state.tasks = tasksRes.tasks || [];
      state.statResets = usageRes.resets || [];
      state.notifications = noticesRes.notifications || [];
      state.qualityRows = qualityRes.quality || [];

      renderActiveProjectSelect();
      renderNotifications();
      renderTasks();
      if ($("#statistics-dialog")?.open) {
        renderStatistics();
      }
      if (state.detailTaskId && $("#task-detail-dialog")?.open) {
        const updated = state.tasks.find((t) => t.id === state.detailTaskId);
        const snapshot = updated ? JSON.stringify(updated) : "";
        if (updated && snapshot !== state.detailRenderedSnapshot) {
          const dialog = $("#task-detail-dialog");
          const dialogContent = dialog?.querySelector(".detail-dialog");
          const scrollTop = dialogContent?.scrollTop || 0;
          const hadFocusInsideDialog = Boolean(dialog?.contains(document.activeElement));
          const focusedId = document.activeElement?.id || "";
          const selectionStart = document.activeElement?.selectionStart;
          const selectionEnd = document.activeElement?.selectionEnd;
          state.detailRenderedSnapshot = snapshot;
          void renderTaskDetailContent(updated, false).then(() => {
            if (
              state.detailTaskId !== updated.id ||
              state.detailRenderedSnapshot !== snapshot
            ) {
              return;
            }
            if (
              hadFocusInsideDialog &&
              focusedId &&
              (document.activeElement === document.body || document.activeElement === dialog)
            ) {
              const focusTarget = document.getElementById(focusedId);
              focusTarget?.focus({ preventScroll: true });
              if (
                focusTarget &&
                typeof selectionStart === "number" &&
                typeof selectionEnd === "number" &&
                typeof focusTarget.setSelectionRange === "function"
              ) {
                focusTarget.setSelectionRange(selectionStart, selectionEnd);
              }
            }
            if (dialogContent) dialogContent.scrollTop = scrollTop;
          });
        }
      }
      const stamp = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
      $("#board-updated").textContent = `Updated ${stamp} · refreshes every 5 seconds`;
    } catch (err) {
      $("#board-updated").textContent = `Board refresh failed (${err.message}); retrying…`;
    } finally {
      state.pollBusy = false;
    }
  }

  // --- New / Edit Task Dialog ---
  const taskDialog = $("#task-dialog");
  const taskForm = $("#task-form");

  function populateTaskProjectOptions(selectedProjectId, selectedModuleId = "") {
    const projSelect = taskForm.elements.project_id;
    projSelect.innerHTML = state.projects
      .map(
        (p) =>
          `<option value="${escapeHtml(p.id)}"${p.id === selectedProjectId ? " selected" : ""}>${escapeHtml(p.name)}</option>`
      )
      .join("");
    populateTaskModuleOptions(selectedProjectId, selectedModuleId);
  }

  function populateTaskModuleOptions(projectId, selectedModuleId = "") {
    const modSelect = taskForm.elements.module_id;
    const proj = state.projects.find((p) => p.id === projectId);
    const modules = proj?.modules || [];
    modSelect.innerHTML =
      `<option value="">Whole project</option>` +
      modules
        .map(
          (m) =>
            `<option value="${escapeHtml(m.id)}"${m.id === selectedModuleId ? " selected" : ""}>${escapeHtml(m.name)}${
              m.path ? ` · ${escapeHtml(m.path)}` : ""
            }</option>`
        )
        .join("");
  }

  taskForm?.elements.project_id?.addEventListener("change", (e) => {
    populateTaskModuleOptions(e.target.value, "");
  });

  function openTaskDialog(task = null) {
    state.editingTaskId = task ? task.id : null;
    $("#task-dialog-title").textContent = task ? "Edit task" : "New task";
    $("#save-task-submit").textContent = task ? "Save changes" : "Save draft";
    $("#form-error").textContent = "";

    const projId = task?.project_id || state.activeProjectId || state.projects[0]?.id || "default";
    const projObj = state.projects.find((p) => p.id === projId);
    const defaultModId = task
      ? task.module_id || ""
      : state.activeModuleId && projObj?.modules?.some((m) => m.id === state.activeModuleId)
        ? state.activeModuleId
        : "";
    populateTaskProjectOptions(projId, defaultModId);

    taskForm.elements.title.value = task?.title || "";
    taskForm.elements.request.value = task?.request || "";
    taskForm.elements.role.value = task?.assigned_role || "PM/PO";
    taskForm.elements.provider.value = task ? getAskedProvider(task) : "Automatic";
    taskForm.elements.checks.value = (task?.acceptance_checks || []).join("\n");
    taskForm.elements.produces_deliverable.checked = Boolean(task?.produces_deliverable);

    if (!taskDialog.open) taskDialog.showModal();
  }

  $("#new-task")?.addEventListener("click", () => openTaskDialog(null));
  $("#empty-new-task")?.addEventListener("click", () => openTaskDialog(null));
  $("#close-dialog")?.addEventListener("click", () => taskDialog.close());
  $("#cancel-task")?.addEventListener("click", () => taskDialog.close());

  taskForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    $("#form-error").textContent = "";
    const submitBtn = $("#save-task-submit");
    submitBtn.disabled = true;
    try {
      const payload = {
        title: taskForm.elements.title.value,
        request: taskForm.elements.request.value,
        role: taskForm.elements.role.value,
        provider: taskForm.elements.provider.value,
        project_id: taskForm.elements.project_id.value,
        module_id: taskForm.elements.module_id.value,
        acceptance_checks: taskForm.elements.checks.value.split("\n"),
        produces_deliverable: taskForm.elements.produces_deliverable.checked,
      };
      if (state.editingTaskId) {
        await api(`/api/tasks/${encodeURIComponent(state.editingTaskId)}`, {
          method: "PUT",
          body: JSON.stringify(payload),
        });
      } else {
        await api("/api/tasks", {
          method: "POST",
          body: JSON.stringify(payload),
        });
      }
      taskDialog.close();
      await refreshBoard();
    } catch (err) {
      $("#form-error").textContent = err.message;
    } finally {
      submitBtn.disabled = false;
    }
  });

  // --- Task Detail Dialog ---
  const detailDialog = $("#task-detail-dialog");
  $("#close-detail")?.addEventListener("click", () => {
    state.detailTaskId = null;
    detailDialog.close();
  });

  async function openTaskDetail(taskId) {
    const task = state.tasks.find((t) => t.id === taskId);
    if (!task) return;
    state.detailTaskId = taskId;
    state.detailRenderedSnapshot = JSON.stringify(task);
    await renderTaskDetailContent(task, true);
    if (!detailDialog.open) detailDialog.showModal();
  }

  async function renderTaskDetailContent(task, resetInputs = false) {
    const renderSnapshot = JSON.stringify(task);
    const asked = getAskedProvider(task);
    $("#detail-eyebrow").textContent = `${task.id} · ${task.assigned_role} · ${asked}`;
    $("#detail-title").textContent = task.title;
    const statusEl = $("#detail-status");
    statusEl.className = statusClass(task.status);
    statusEl.textContent = task.status;
    $("#detail-deliverable").hidden = !task.produces_deliverable;

    const waitingNoteEl = $("#detail-waiting-note");
    if (waitingNoteEl) {
      if (task.status === "Waiting for capacity") {
        waitingNoteEl.hidden = false;
        waitingNoteEl.textContent =
          (task.waiting_reason || "Waiting for the provider retry time.") +
          (task.retry_after ? ` · scheduled retry after ${formatTime(task.retry_after)}` : "");
      } else {
        waitingNoteEl.hidden = true;
        waitingNoteEl.textContent = "";
      }
    }

    const providerRow = $("#detail-provider-row");
    const providerSelect = $("#detail-provider-select");
    const providerFeedback = $("#detail-provider-feedback");
    if (providerRow && providerSelect) {
      const showProviderRow =
        task.task_type !== "clarification_question" && task.task_type !== "project_overview";
      providerRow.hidden = !showProviderRow;
      if (showProviderRow) {
        const canChange = !["In progress", "Done", "Canceled"].includes(task.status);
        providerSelect.value = asked;
        providerSelect.disabled = !canChange;
        if (resetInputs && providerFeedback) {
          providerFeedback.textContent = canChange ? "" : `Locked while ${String(task.status).toLowerCase()}`;
        } else if (!canChange && providerFeedback) {
          providerFeedback.textContent = `Locked while ${String(task.status).toLowerCase()}`;
        }
      }
    }

    $("#detail-request").textContent = task.request || "";

    const pmSec = $("#detail-pm-section");
    if (task.planning_summary) {
      pmSec.hidden = false;
      $("#detail-pm-summary").textContent = task.planning_summary;
    } else {
      pmSec.hidden = true;
    }

    // Clarification question section
    const qSec = $("#detail-question-section");
    if (task.task_type === "clarification_question") {
      qSec.hidden = false;
      const qData = task.question_data || {};
      $("#detail-question-why").textContent = qData.why || "This information helps PM/PO plan the work.";
      const qBody = $("#detail-question-body");
      if (task.status === "Needs answer") {
        if (resetInputs || !qBody.querySelector("#question-option-select")) {
          const opts = (qData.options || [])
            .map(
              (o) =>
                `<option value="${escapeHtml(o.label)}">${escapeHtml(o.label)}${
                  o.description ? ` — ${escapeHtml(o.description)}` : ""
                }</option>`
            )
            .join("");
          qBody.innerHTML = `
            <div class="question-options">
              <label>Choose an option
                <select id="question-option-select">
                  <option value="">Select an option</option>
                  ${opts}
                </select>
              </label>
              ${
                qData.allow_free_text !== false
                  ? `<label>Or add your own answer<textarea id="question-free-text" rows="3" maxlength="5000" placeholder="Optional extra detail, or your own answer"></textarea></label>`
                  : ""
              }
              <button class="primary" type="button" id="submit-question-answer">Submit answer</button>
            </div>`;
          qBody.querySelector("#submit-question-answer")?.addEventListener("click", async (ev) => {
            ev.target.disabled = true;
            try {
              const selected_option = qBody.querySelector("#question-option-select")?.value || "";
              const free_text = qBody.querySelector("#question-free-text")?.value || "";
              const res = await api(`/api/tasks/${encodeURIComponent(task.id)}/answer`, {
                method: "POST",
                body: JSON.stringify({ selected_option, free_text }),
              });
              await refreshBoard();
              if (res.followup_task) await loadChat();
            } catch (err) {
              alert(err.message);
            } finally {
              ev.target.disabled = false;
            }
          });
        }
      } else {
        qBody.innerHTML = `<p class="detail-copy">${escapeHtml(task.answer || "")}</p>`;
      }
    } else {
      qSec.hidden = true;
    }

    // Project overview section
    const ovSec = $("#detail-overview-section");
    if (task.task_type === "project_overview") {
      ovSec.hidden = false;
      const fieldsWrap = $("#detail-overview-fields");
      if (resetInputs || !fieldsWrap.querySelector("textarea")) {
        const docs = task.overview_documents || {};
        const editable = task.status === "Needs approval";
        const pairs = [
          ["project_brief", "Project brief"],
          ["technical_overview", "Technical overview"],
          ["decisions", "Decision log"],
        ];
        fieldsWrap.innerHTML = pairs
          .map(
            ([key, label]) => `
            <label class="overview-field">${escapeHtml(label)}
              <textarea data-overview-doc="${escapeHtml(key)}" rows="7" maxlength="20000" ${
                editable ? "" : "readonly"
              }>${escapeHtml(docs[key] || "")}</textarea>
            </label>`
          )
          .join("");
      }
    } else {
      ovSec.hidden = true;
    }

    // Acceptance checks
    const checks = task.acceptance_checks || [];
    $("#detail-checks").innerHTML = checks.length
      ? `<ul>${checks.map((c) => `<li>${escapeHtml(c)}</li>`).join("")}</ul>`
      : `<p class="muted">No acceptance checks added.</p>`;

    // Validation
    if (task.validation) {
      const vChecks = (task.validation.checks || [])
        .map(
          (item) =>
            `<div class="validation-check">${item.passed === true ? "✓" : item.passed === false ? "✕" : "·"} ${escapeHtml(
              item.check
            )} — ${escapeHtml(item.message)}</div>`
        )
        .join("");
      $("#detail-validation").innerHTML = `<div class="validation-result"><strong>${escapeHtml(
        String(task.validation.status || "").replace(/_/g, " ")
      )}</strong>${vChecks}</div>`;
    } else {
      $("#detail-validation").innerHTML = `<p class="muted">No validation recorded yet.</p>`;
    }

    // PR & GitHub error
    const prSec = $("#detail-pr-section");
    if (task.pull_request?.url && String(task.pull_request.url).startsWith("https://github.com/")) {
      prSec.hidden = false;
      $("#detail-pr-copy").innerHTML = `<a href="${escapeHtml(
        task.pull_request.url
      )}" target="_blank" rel="noopener noreferrer">Open pull request #${escapeHtml(
        task.pull_request.number
      )} (${escapeHtml(task.pull_request.state)})</a>`;
    } else {
      prSec.hidden = true;
    }

    const ghErr = $("#detail-github-error");
    const repositoryErrors = [
      task.github_error ? `GitHub: ${task.github_error}` : "",
      task.ci_status === "failed" && task.ci_error ? `CI: ${task.ci_error}` : "",
      task.ci_status === "pending" ? "CI: GitHub Actions checks are still running; merge is waiting for them." : "",
      task.local_sync_error ? `Local checkout: ${task.local_sync_error}` : "",
    ].filter(Boolean);
    if (repositoryErrors.length) {
      ghErr.hidden = false;
      ghErr.textContent = repositoryErrors.join(" · ");
    } else {
      ghErr.hidden = true;
    }

    // Replies
    const repliesEl = $("#detail-replies");
    if (resetInputs) {
      repliesEl.textContent = "Loading saved replies…";
    }
    try {
      const res = await api(`/api/tasks/${encodeURIComponent(task.id)}/replies`);
      if (state.detailTaskId !== task.id || state.detailRenderedSnapshot !== renderSnapshot) return;
      const replies = res.replies || [];
      repliesEl.innerHTML = replies.length
        ? replies
            .map(
              (r) => `
            <article class="reply-card">
              <div class="reply-heading">${escapeHtml(r.provider)} · attempt ${escapeHtml(r.attempt)} · ${escapeHtml(
                r.state
              )} · ${escapeHtml(formatTime(r.at))}</div>
              <pre class="reply-text">${escapeHtml(r.text || r.error || "No response text was saved.")}</pre>
            </article>`
            )
            .join("")
        : `<span class="muted">No agent replies yet.</span>`;
    } catch (err) {
      if (state.detailTaskId === task.id && state.detailRenderedSnapshot === renderSnapshot) {
        repliesEl.textContent = `Could not load replies: ${err.message}`;
      }
    }

    // Activity
    const activity = [...(task.activity || [])].reverse();
    $("#detail-activity").innerHTML = activity.length
      ? activity
          .map(
            (a) => `
          <div class="activity-item">
            <span class="activity-dot"></span>
            <div>
              <div class="activity-title">${escapeHtml(a.message || a.type)}</div>
              <time>${escapeHtml(formatTime(a.at))}</time>
            </div>
          </div>`
          )
          .join("")
      : `<p class="muted">No activity yet.</p>`;

    // Actions
    const actions = [];
    if (["Draft", "Blocked", "Rework needed", "Waiting for capacity"].includes(task.status)) {
      actions.push(["edit", "Edit task", "secondary"]);
    }
    if (task.status === "Draft") actions.push(["Ready", "Mark Ready", "primary"]);
    if (["Draft", "Ready", "Waiting for capacity"].includes(task.status)) {
      actions.push(["Blocked", "Block", "secondary"]);
    }
    if (["Draft", "Ready", "Blocked", "Waiting for capacity"].includes(task.status)) {
      actions.push(["Canceled", "Cancel task", "secondary"]);
    }
    if (task.status === "Blocked") {
      actions.unshift(["Draft", "Return to Draft", "secondary"]);
      actions.splice(1, 0, ["Ready", "Mark Ready", "primary"]);
    }
    if (task.status === "Rework needed") {
      actions.unshift(["Ready", "Return to agent queue", "primary"]);
    }
    if (task.status === "Waiting for capacity") {
      actions.unshift(["Ready", "Retry", "primary"]);
    }

    let actionHtml = "";
    actionHtml += actions
      .map(
        ([key, label, cls]) =>
          `<button class="${cls}" type="button" data-detail-status-action="${escapeHtml(key)}">${escapeHtml(label)}</button>`
      )
      .join("");
    if (task.local_sync_error && task.pull_request?.state === "merged") {
      actionHtml += `<button class="secondary" type="button" data-local-sync-retry="1">Retry local checkout sync</button>`;
    }
    if (task.task_type === "project_overview" && task.status === "Needs approval") {
      actionHtml += `
        <button class="secondary" type="button" data-overview-action="save">Save edits</button>
        <button class="primary" type="button" data-overview-action="approve">Approve and update Project context</button>`;
    }
    actionHtml += `
      <button class="secondary" type="button" data-detail-close="1">Close</button>
      <button class="danger" type="button" data-detail-delete="1">Delete task</button>`;
    $("#detail-actions").innerHTML = actionHtml;
  }

  $("#detail-provider-select")?.addEventListener("change", async (e) => {
    const task = state.tasks.find((t) => t.id === state.detailTaskId);
    if (!task) return;
    const select = e.target;
    const nextProvider = select.value;
    const feedback = $("#detail-provider-feedback");
    select.disabled = true;
    if (feedback) feedback.textContent = "Saving…";
    try {
      await api(`/api/tasks/${encodeURIComponent(task.id)}/provider`, {
        method: "POST",
        body: JSON.stringify({ provider: nextProvider }),
      });
      await refreshBoard();
      if (feedback) feedback.textContent = `Updated to ${nextProvider}`;
    } catch (err) {
      if (feedback) feedback.textContent = "";
      select.value = getAskedProvider(task);
      alert(err.message);
    } finally {
      const latest = state.tasks.find((t) => t.id === task.id) || task;
      select.disabled = ["In progress", "Done", "Canceled"].includes(latest.status);
    }
  });

  $("#detail-actions")?.addEventListener("click", async (e) => {
    const task = state.tasks.find((t) => t.id === state.detailTaskId);
    if (!task) return;

    if (e.target.closest("[data-detail-close]")) {
      state.detailTaskId = null;
      detailDialog.close();
      return;
    }

    if (e.target.closest("[data-detail-delete]")) {
      if (
        !confirm(
          "Permanently delete this task, its agent replies, and its task activity log entries? The overall chat conversation will remain. This cannot be undone."
        )
      ) {
        return;
      }
      try {
        await api(`/api/tasks/${encodeURIComponent(task.id)}`, { method: "DELETE" });
        state.detailTaskId = null;
        detailDialog.close();
        await refreshBoard();
      } catch (err) {
        alert(err.message);
      }
      return;
    }

    if (e.target.closest("[data-local-sync-retry]")) {
      const button = e.target.closest("[data-local-sync-retry]");
      button.disabled = true;
      try {
        await api(`/api/tasks/${encodeURIComponent(task.id)}/sync-local`, {
          method: "POST",
          body: JSON.stringify({}),
        });
        await refreshBoard();
      } catch (err) {
        alert(err.message);
      } finally {
        button.disabled = false;
      }
      return;
    }

    const statusBtn = e.target.closest("[data-detail-status-action]");
    if (statusBtn) {
      const target = statusBtn.getAttribute("data-detail-status-action");
      if (target === "edit") {
        detailDialog.close();
        openTaskDialog(task);
        return;
      }
      let note = "";
      if (target === "Canceled") {
        if (!confirm("Cancel this task? This cannot be undone.")) return;
      } else if (target === "Blocked") {
        const reason = prompt("Why is this task blocked? (Optional)", "");
        if (reason === null) return;
        note = reason;
      }
      try {
        await api(`/api/tasks/${encodeURIComponent(task.id)}/status`, {
          method: "POST",
          body: JSON.stringify({ status: target, note }),
        });
        detailDialog.close();
        await refreshBoard();
      } catch (err) {
        alert(err.message);
      }
      return;
    }

    const ovBtn = e.target.closest("[data-overview-action]");
    if (ovBtn) {
      const approve = ovBtn.getAttribute("data-overview-action") === "approve";
      if (
        approve &&
        !confirm(
          "Approve this overview and replace Project brief, Technical overview, and Decision log? Current versions will be saved in history; Confirmed outcomes are preserved."
        )
      ) {
        return;
      }
      const documents = {};
      document.querySelectorAll("[data-overview-doc]").forEach((el) => {
        documents[el.getAttribute("data-overview-doc")] = el.value;
      });
      try {
        await api(
          approve
            ? `/api/tasks/${encodeURIComponent(task.id)}/approve-overview`
            : `/api/tasks/${encodeURIComponent(task.id)}/overview`,
          {
            method: approve ? "POST" : "PUT",
            body: JSON.stringify({ documents }),
          }
        );
        await refreshBoard();
      } catch (err) {
        alert(err.message);
      }
    }
  });

  // --- Agent Prompts ---
  const promptForm = $("#prompt-form");
  const promptSelect = promptForm?.elements.prompt_id;
  const promptTextarea = promptForm?.elements.content;
  const promptStatusBadge = $("#prompt-status-badge");
  const promptFeedback = $("#prompt-feedback");
  const resetPromptBtn = $("#reset-prompt");

  function setPromptFeedback(message = "") {
    if (!promptFeedback) return;
    if (message) {
      promptFeedback.textContent = message;
      promptFeedback.hidden = false;
    } else {
      promptFeedback.textContent = "";
      promptFeedback.hidden = true;
    }
  }

  function renderPromptOptions(selectedId) {
    if (!promptSelect) return;
    promptSelect.innerHTML = state.prompts
      .map(
        (p) =>
          `<option value="${escapeHtml(p.id)}"${p.id === selectedId ? " selected" : ""}>${escapeHtml(p.title)}${
            p.is_custom ? " (custom)" : ""
          }</option>`
      )
      .join("");
  }

  function updatePromptStatusUI() {
    const selected = state.prompts.find((p) => p.id === promptSelect?.value);
    if (!selected) return;
    const currentText = (promptTextarea?.value || "").trim();
    const savedText = (selected.content || "").trim();
    const defaultText = (selected.default_content || "").trim();
    const hasUnsaved = currentText !== savedText;
    const differsFromDefault = Boolean(defaultText && currentText !== defaultText);
    const isCustom = Boolean(selected.is_custom);

    if (promptStatusBadge) {
      promptStatusBadge.className = `state ${isCustom ? "state-ready" : "state-done"}`;
      promptStatusBadge.textContent =
        (isCustom ? "Custom prompt active" : "Default prompt active") + (hasUnsaved ? " · unsaved edits" : "");
    }
    if (resetPromptBtn) {
      resetPromptBtn.disabled = !isCustom && !differsFromDefault;
    }
  }

  async function loadAgentPrompts() {
    $("#prompt-error").textContent = "";
    setPromptFeedback("");
    const res = await api("/api/prompts");
    state.prompts = res.prompts || [];
    const initial = state.prompts.find((p) => p.id === promptSelect?.value) ||
      state.prompts.find((p) => p.id === "pm_po") ||
      state.prompts[0];
    if (initial) {
      renderPromptOptions(initial.id);
      promptTextarea.value = initial.content || "";
      updatePromptStatusUI();
    }
  }

  promptSelect?.addEventListener("change", () => {
    $("#prompt-error").textContent = "";
    setPromptFeedback("");
    const found = state.prompts.find((p) => p.id === promptSelect.value);
    promptTextarea.value = found?.content || "";
    updatePromptStatusUI();
  });

  promptTextarea?.addEventListener("input", () => {
    setPromptFeedback("");
    updatePromptStatusUI();
  });

  promptForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    $("#prompt-error").textContent = "";
    setPromptFeedback("");
    const id = promptSelect.value;
    try {
      const updated = await api(`/api/prompts/${encodeURIComponent(id)}`, {
        method: "PUT",
        body: JSON.stringify({ content: promptTextarea.value }),
      });
      state.prompts = state.prompts.map((p) =>
        p.id === id
          ? {
              ...p,
              content: updated.content,
              default_content: updated.default_content ?? p.default_content,
              is_custom: Boolean(updated.is_custom),
            }
          : p
      );
      promptTextarea.value = updated.content;
      renderPromptOptions(id);
      updatePromptStatusUI();
      setPromptFeedback(
        updated.is_custom
          ? `Saved custom prompt for ${updated.title || id}. Future agent runs will use this prompt.`
          : `Saved prompt for ${updated.title || id} (matches built-in default).`
      );
    } catch (err) {
      $("#prompt-error").textContent = err.message;
    }
  });

  resetPromptBtn?.addEventListener("click", async () => {
    const id = promptSelect?.value;
    const selected = state.prompts.find((p) => p.id === id);
    if (!selected) return;
    if (
      !confirm(
        `Reset "${selected.title}" back to its built-in default prompt? Your saved custom prompt for this role will be replaced with the default template.`
      )
    ) {
      return;
    }
    $("#prompt-error").textContent = "";
    setPromptFeedback("");
    resetPromptBtn.disabled = true;
    try {
      const reset = await api(`/api/prompts/${encodeURIComponent(id)}/reset`, {
        method: "POST",
        body: "{}",
      });
      state.prompts = state.prompts.map((p) =>
        p.id === id
          ? {
              ...p,
              content: reset.content,
              default_content: reset.default_content ?? p.default_content,
              is_custom: false,
            }
          : p
      );
      promptTextarea.value = reset.content;
      renderPromptOptions(id);
      updatePromptStatusUI();
      setPromptFeedback(`Restored default prompt for ${selected.title}.`);
    } catch (err) {
      $("#prompt-error").textContent = err.message;
      updatePromptStatusUI();
    }
  });

  // --- Project Context Dialog ---
  const contextDialog = $("#context-dialog");
  const contextForm = $("#context-form");
  const contextSelect = contextForm?.elements.document_id;
  const contextTextarea = contextForm?.elements.content;
  const contextVersionSelect = $("#context-version");

  function projectContextEndpoint(suffix = "") {
    return `/api/projects/${encodeURIComponent(state.contextProjectId)}/context-documents${suffix}`;
  }

  async function loadContextHistory(docId) {
    $("#context-error").textContent = "";
    try {
      const res = await api(projectContextEndpoint(`/${encodeURIComponent(docId)}/history`));
      const history = res.history || [];
      if (!history.length) {
        contextVersionSelect.innerHTML = `<option value="">No saved versions</option>`;
        $("#restore-context-version").disabled = true;
        $("#delete-context-version").disabled = true;
        return;
      }
      contextVersionSelect.innerHTML = history
        .map(
          (h) =>
            `<option value="${escapeHtml(h.id)}">${escapeHtml(formatTime(h.created_at))} · ${escapeHtml(
              h.preview || "Saved version"
            )}</option>`
        )
        .join("");
      $("#restore-context-version").disabled = false;
      $("#delete-context-version").disabled = false;
    } catch (err) {
      $("#context-error").textContent = `Could not load saved versions: ${err.message}`;
      contextVersionSelect.innerHTML = `<option value="">No saved versions</option>`;
      $("#restore-context-version").disabled = true;
      $("#delete-context-version").disabled = true;
    }
  }

  $("#open-context")?.addEventListener("click", async () => {
    $("#context-error").textContent = "";
    try {
      state.contextProjectId = state.activeProjectId;
      const project = state.projects.find((p) => p.id === state.contextProjectId);
      $("#context-project-name").textContent = project?.name || "Project";
      const res = await api(projectContextEndpoint());
      state.contextDocs = res.documents || [];
      const first = state.contextDocs.find((d) => d.id === "project_brief") || state.contextDocs[0];
      contextSelect.innerHTML = state.contextDocs
        .map((d) => `<option value="${escapeHtml(d.id)}">${escapeHtml(d.title)}</option>`)
        .join("");
      if (first) {
        contextSelect.value = first.id;
        contextTextarea.value = first.content || "";
        await loadContextHistory(first.id);
      }
      if (!contextDialog.open) contextDialog.showModal();
    } catch (err) {
      alert(`Could not load project context: ${err.message}`);
    }
  });

  contextSelect?.addEventListener("change", async () => {
    const doc = state.contextDocs.find((d) => d.id === contextSelect.value);
    contextTextarea.value = doc?.content || "";
    await loadContextHistory(contextSelect.value);
  });

  $("#close-context")?.addEventListener("click", () => contextDialog.close());
  $("#cancel-context")?.addEventListener("click", () => contextDialog.close());

  contextForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    $("#context-error").textContent = "";
    const docId = contextSelect.value;
    try {
      const res = await api(projectContextEndpoint(`/${encodeURIComponent(docId)}`), {
        method: "PUT",
        body: JSON.stringify({ content: contextTextarea.value }),
      });
      state.contextDocs = state.contextDocs.map((d) => (d.id === docId ? { ...d, content: res.content } : d));
      await loadContextHistory(docId);
    } catch (err) {
      $("#context-error").textContent = err.message;
    }
  });

  $("#restore-context-version")?.addEventListener("click", async () => {
    const versionId = contextVersionSelect.value;
    const docId = contextSelect.value;
    if (!versionId) return;
    if (!confirm("Restore this saved version? The current document will first be saved as a new history version.")) {
      return;
    }
    try {
      const res = await api(projectContextEndpoint(`/${encodeURIComponent(docId)}/restore`), {
        method: "POST",
        body: JSON.stringify({ snapshot_id: versionId }),
      });
      contextTextarea.value = res.content;
      state.contextDocs = state.contextDocs.map((d) => (d.id === docId ? { ...d, content: res.content } : d));
      await loadContextHistory(docId);
    } catch (err) {
      alert(err.message);
    }
  });

  $("#delete-context-version")?.addEventListener("click", async () => {
    const versionId = contextVersionSelect.value;
    const docId = contextSelect.value;
    if (!versionId) return;
    if (!confirm("Permanently delete this saved version? The current Project context will not change.")) {
      return;
    }
    try {
      await api(
        projectContextEndpoint(`/${encodeURIComponent(docId)}/history/${encodeURIComponent(versionId)}`),
        { method: "DELETE" }
      );
      await loadContextHistory(docId);
    } catch (err) {
      alert(err.message);
    }
  });

  // --- Local Documentation and Project Files ---
  const repoDialog = $("#repository-view-dialog");

  function localFilesEndpoint() {
    if (state.repoMode === "documentation") return "/api/documentation-files";
    return `/api/projects/${encodeURIComponent(state.activeProjectId)}/local-files`;
  }

  async function loadRepositoryFolder(folderPath = "") {
    $("#repository-view-error").textContent = "";
    $("#repository-file-content").innerHTML = `<span class="muted">Choose a file to view it.</span>`;
    const activeProj = state.projects.find((p) => p.id === state.activeProjectId);
    if (state.repoMode === "files" && !activeProj?.local_path) {
      $("#repository-view-meta").textContent = activeProj ? `${activeProj.name} · No local project folder` : "";
      $("#repository-file-list").innerHTML = `<p class="muted">No local project folder is configured.</p>`;
      $("#repository-view-error").textContent =
        "Set the project's local file path in Projects & modules. Clone the repository there first if needed.";
      return;
    }

    try {
      const res = await api(`${localFilesEndpoint()}?path=${encodeURIComponent(folderPath)}`);
      state.repoCurrentPath = res.path || "";
      const folderLabel = state.repoMode === "documentation" ? "Local documentation" : `${activeProj.name} · Local files`;
      $("#repository-view-meta").textContent = `${folderLabel} · ${res.path || "/"}`;

      const entries = res.entries || [];
      const folders = entries.filter((item) => item.type === "dir");
      const files = entries.filter((item) => item.type === "file");
      if (res.truncated) {
        $("#repository-view-error").textContent = "Only the first 1,000 entries are shown in this folder.";
      }

      const parentPath = state.repoCurrentPath.includes("/")
        ? state.repoCurrentPath.split("/").slice(0, -1).join("/")
        : "";

      let html = `<button type="button" class="repository-entry repository-parent" data-repository-path="${escapeHtml(
        parentPath
      )}">↑ ${state.repoCurrentPath ? "Parent folder" : "Local folder root"}</button>`;

      html += folders
        .map(
          (dir) =>
            `<button type="button" class="repository-entry" data-repository-path="${escapeHtml(
              dir.path
            )}">📁 ${escapeHtml(dir.name)}/</button>`
        )
        .join("");

      html += files
        .map(
          (file) =>
            `<button type="button" class="repository-entry" data-repository-file="${escapeHtml(
              file.path
            )}">${escapeHtml(file.name)} <span>${Number(file.size || 0).toLocaleString()} B</span></button>`
        )
        .join("");

      if (!folders.length && !files.length) {
        html += `<p class="muted">No matching files in this folder.</p>`;
      }
      $("#repository-file-list").innerHTML = html;
    } catch (err) {
      $("#repository-file-list").innerHTML = "";
      $("#repository-view-error").textContent = err.message;
    }
  }

  async function openRepositoryView(mode) {
    state.repoMode = mode;
    repoDialog.classList.remove("document-file-view");
    $("#repository-view-eyebrow").textContent =
      mode === "documentation" ? "LOCAL DOCUMENTATION FOLDER" : "LOCAL PROJECT FOLDER";
    $("#repository-view-title").textContent =
      mode === "documentation" ? "Project documentation" : "Project files";
    if (!repoDialog.open) repoDialog.showModal();
    await loadRepositoryFolder("");
  }

  async function openDocumentationFile(filePath, title) {
    state.repoMode = "documentation";
    repoDialog.classList.add("document-file-view");
    $("#repository-view-eyebrow").textContent = "LOCAL APP DOCUMENTATION";
    $("#repository-view-title").textContent = title;
    $("#repository-view-meta").textContent = filePath;
    $("#repository-file-list").innerHTML = "";
    $("#repository-file-content").textContent = "Loading…";
    $("#repository-view-error").textContent = "";
    if (!repoDialog.open) repoDialog.showModal();
    try {
      const res = await api(`/api/documentation-files?file=${encodeURIComponent(filePath)}`);
      $("#repository-view-meta").textContent = `Local documentation · ${res.path}`;
      $("#repository-file-content").textContent = res.content;
    } catch (err) {
      $("#repository-file-content").textContent = "";
      $("#repository-view-error").textContent = err.message;
    }
  }

  $("#open-documentation")?.addEventListener("click", () => void openRepositoryView("documentation"));
  $("#open-files")?.addEventListener("click", () => void openRepositoryView("files"));
  $("#close-repository-view")?.addEventListener("click", () => repoDialog.close());

  $("#repository-file-list")?.addEventListener("click", async (e) => {
    const folderBtn = e.target.closest("[data-repository-path]");
    if (folderBtn) {
      const nextPath = folderBtn.getAttribute("data-repository-path") || "";
      await loadRepositoryFolder(nextPath);
      return;
    }
    const fileBtn = e.target.closest("[data-repository-file]");
    if (fileBtn) {
      const filePath = fileBtn.getAttribute("data-repository-file");
      $("#repository-view-error").textContent = "";
      try {
        const res = await api(`${localFilesEndpoint()}?file=${encodeURIComponent(filePath)}`);
        const activeProj = state.projects.find((p) => p.id === state.activeProjectId);
        const folderLabel =
          state.repoMode === "documentation" ? "Local documentation" : `${activeProj?.name || "Project"} · Local files`;
        $("#repository-view-meta").textContent = `${folderLabel} · ${res.path}`;
        $("#repository-file-content").textContent = res.content;
      } catch (err) {
        $("#repository-view-error").textContent = err.message;
      }
    }
  });

  // --- Connections Dialog ---
  const connectionsForm = $("#connections-form");
  const routingForm = $("#routing-form");

  function isProviderConnected(providerName) {
    return state.connections.some((c) => {
      if (!c.configured) return false;
      if ((providerName === "Gemini") && c.id === "gemini") return true;
      if (providerName === "GroqCloud" && c.id === "groq") return true;
      if (providerName === "OpenRouter" && c.id === "openrouter") return true;
      return false;
    });
  }

  function setConnectionsSuccess(message = "") {
    const el = $("#connections-success");
    if (!el) return;
    if (message) {
      el.textContent = message;
      el.hidden = false;
    } else {
      el.textContent = "";
      el.hidden = true;
    }
  }

  function renderConnectionsAndRouting() {
    $("#connection-list").innerHTML = state.connections
      .map(
        (c) => `
        <div class="route-role">
          <div class="progress-heading">
            <strong>${escapeHtml(c.name)}</strong>
            <span class="state ${c.configured ? "state-done" : "state-draft"}">${
              c.configured ? `Connected · ${escapeHtml(c.source || "Saved")}` : "Not connected"
            }</span>
          </div>
          <div class="connection-controls">
            ${
              state.encryptionAvailable
                ? `<input type="password" class="connection-key" data-conn-input="${escapeHtml(
                    c.id
                  )}" placeholder="Paste new API key / token to replace" autocomplete="off">
                  <button type="button" class="secondary" data-save-conn="${escapeHtml(c.id)}">Save</button>`
                : ""
            }
            ${
              c.id === "gemini"
                ? `<button type="button" class="secondary" data-test-conn="gemini" ${
                    c.configured ? "" : "disabled"
                  }>Test</button>`
                : `<button type="button" class="secondary" data-test-conn="${escapeHtml(c.id)}" ${
                    c.configured ? "" : "disabled"
                  }>Test</button>`
            }
            ${
              state.encryptionAvailable
                ? `<button type="button" class="danger" data-clear-conn="${escapeHtml(c.id)}" ${
                    c.configured ? "" : "disabled"
                  }>Clear</button>`
                : ""
            }
          </div>
        </div>`
      )
      .join("");

    $("#routing-roles").innerHTML = state.routingRoles
      .map((role) => {
        const order = state.routing[role] || state.routingProviders;
        const selects = order
          .map(
            (prov, idx) => `
            <label>${idx + 1}
              <select data-route-role="${escapeHtml(role)}" data-route-idx="${idx}">
                ${state.routingProviders
                  .map(
                    (opt) =>
                      `<option value="${escapeHtml(opt)}"${opt === prov ? " selected" : ""}>${escapeHtml(opt)}${
                        !isProviderConnected(opt) ? " · not connected" : ""
                      }</option>`
                  )
                  .join("")}
              </select>
            </label>`
          )
          .join("");
        return `<section class="route-role"><strong>${escapeHtml(role)}</strong><div class="route-order">${selects}</div></section>`;
      })
      .join("");
  }

  function renderChatProviderSelect() {
    const options = chatProviderOptions(state.chatProvider);
    const sel = $("#chat-provider-select");
    if (sel) sel.innerHTML = options;
    const menu = $("#chat-provider-menu");
    if (menu) menu.innerHTML = options;
  }

  $("#chat-provider-select")?.addEventListener("change", (e) => {
    state.chatProvider = e.target.value;
    const menu = $("#chat-provider-menu");
    if (menu) menu.innerHTML = chatProviderOptions(state.chatProvider);
  });

  $("#chat-provider-menu")?.addEventListener("change", async (e) => {
    const provider = e.target.value;
    state.chatProvider = provider;
    const sel = $("#chat-provider-select");
    if (sel) sel.innerHTML = chatProviderOptions(provider);
    try {
      await api("/api/routing", {
        method: "POST",
        body: JSON.stringify({ chat_provider: provider }),
      });
    } catch (err) {
      alert(`Could not save chat provider: ${err.message}`);
    }
  });

  async function reloadSettingsData() {
    const [connRes, routeRes] = await Promise.all([api("/api/connections"), api("/api/routing")]);
    state.connections = connRes.connections || [];
    state.encryptionAvailable = connRes.encryption_available !== false;
    state.routingRoles = routeRes.roles || [];
    state.routingProviders = routeRes.providers || ["Gemini", "GroqCloud", "OpenRouter"];
    state.routing = routeRes.routing || {};
    state.chatProvider = routeRes.chat_provider || "Gemini";
    renderChatProviderSelect();
    renderConnectionsAndRouting();
  }

  $("#connection-list")?.addEventListener("click", async (e) => {
    const saveBtn = e.target.closest("[data-save-conn]");
    if (saveBtn) {
      const id = saveBtn.getAttribute("data-save-conn");
      const input = document.querySelector(`[data-conn-input="${id}"]`);
      const secret = input?.value?.trim() || "";
      if (!secret) {
        setConnectionsSuccess("");
        $("#connections-error").textContent = "Paste a key or token before clicking Save.";
        return;
      }
      $("#connections-error").textContent = "";
      setConnectionsSuccess("");
      try {
        await api(`/api/connections/${encodeURIComponent(id)}`, {
          method: "PUT",
          body: JSON.stringify({ secret }),
        });
        if (input) input.value = "";
        await reloadSettingsData();
        await refreshStatus();
        setConnectionsSuccess("Saved encrypted credential.");
      } catch (err) {
        $("#connections-error").textContent = err.message;
      }
      return;
    }

    const testBtn = e.target.closest("[data-test-conn]");
    if (testBtn) {
      const id = testBtn.getAttribute("data-test-conn");
      const origLabel = testBtn.textContent;
      testBtn.disabled = true;
      testBtn.textContent = "Testing…";
      $("#connections-error").textContent = "";
      setConnectionsSuccess("");
      try {
        const res = await api(`/api/connections/${encodeURIComponent(id)}/test`, {
          method: "POST",
          body: "{}",
        });
        const msg = res.message || (res.login ? `GitHub connected as ${res.login}.` : "Connection verified.");
        setConnectionsSuccess(msg);
        alert(msg);
      } catch (err) {
        $("#connections-error").textContent = err.message;
      } finally {
        testBtn.disabled = false;
        testBtn.textContent = origLabel || "Test";
      }
      return;
    }

    const clearBtn = e.target.closest("[data-clear-conn]");
    if (clearBtn) {
      const id = clearBtn.getAttribute("data-clear-conn");
      if (!confirm("Remove the saved credential from this VM?")) return;
      $("#connections-error").textContent = "";
      setConnectionsSuccess("");
      try {
        await api(`/api/connections/${encodeURIComponent(id)}`, { method: "DELETE" });
        await reloadSettingsData();
        await refreshStatus();
        setConnectionsSuccess("Removed saved credential.");
      } catch (err) {
        $("#connections-error").textContent = err.message;
      }
    }
  });

  $("#routing-roles")?.addEventListener("change", (e) => {
    const sel = e.target.closest("[data-route-role]");
    if (!sel) return;
    const role = sel.getAttribute("data-route-role");
    const idx = Number(sel.getAttribute("data-route-idx"));
    const nextProv = sel.value;
    const current = [...(state.routing[role] || state.routingProviders)];
    const existingIdx = current.indexOf(nextProv);
    if (existingIdx >= 0 && existingIdx !== idx) {
      const tmp = current[idx];
      current[idx] = nextProv;
      current[existingIdx] = tmp;
    } else {
      current[idx] = nextProv;
    }
    state.routing[role] = current;
    renderConnectionsAndRouting();
  });

  $("#test-github")?.addEventListener("click", async () => {
    $("#connections-error").textContent = "";
    setConnectionsSuccess("");
    try {
      const res = await api("/api/connections/github/test", { method: "POST", body: "{}" });
      const msg = res.message || `GitHub connected as ${res.login}.`;
      setConnectionsSuccess(msg);
      alert(msg);
    } catch (err) {
      $("#connections-error").textContent = err.message;
    }
  });

  connectionsForm?.addEventListener("submit", (e) => e.preventDefault());

  routingForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    $("#routing-error").textContent = "";
    try {
      await api("/api/routing", {
        method: "POST",
        body: JSON.stringify({ routing: state.routing, chat_provider: state.chatProvider }),
      });
      $("#settings-dialog").close();
      await refreshBoard();
    } catch (err) {
      $("#routing-error").textContent = err.message;
    }
  });

  // --- Projects & Sub-projects Dialog ---
  const projectsForm = $("#projects-form");
  let projectDrafts = [];

  async function fetchProjectHistory(projectId) {
    if (!projectId) return [];
    try {
      const res = await api(`/api/projects/${encodeURIComponent(projectId)}/context-history`);
      return res.history || [];
    } catch {
      return [];
    }
  }

  function renderProjectEditors() {
    $("#project-editors").innerHTML = projectDrafts
      .map(
        (d, idx) => `
        <section class="project-editor" data-proj-idx="${idx}">
          <div class="two-columns">
            <label>Project name
              <input data-proj-field="name" value="${escapeHtml(d.name)}" required maxlength="100">
            </label>
            <label>GitHub repository URL
              <input list="github-repo-options" data-proj-field="repository" value="${escapeHtml(
                d.repository
              )}" placeholder="https://github.com/owner/repository">
            </label>
          </div>
          <label>Local file path <span class="muted">Absolute path to the clone. On the systemd service, use a folder under TEAM_DATA_DIR (default: /var/lib/personal-ai-team/projects).</span>
            <input data-proj-field="local_path" value="${escapeHtml(d.local_path || "")}" placeholder="C:/Users/you/projects/repo">
          </label>
          ${d.repository && d.local_path ? `
          <div class="git-controls">
            <button type="button" class="secondary" data-proj-pull="${idx}">↓ Pull from GitHub</button>
            <button type="button" class="secondary" data-proj-push="${idx}">↑ Push to GitHub</button>
            <button type="button" class="secondary" data-proj-clone="${idx}">Clone repository</button>
            <span class="git-status" data-proj-status="${idx}">Checking status…</span>
          </div>
          ` : ''}
          <label>Project background notes
            <textarea rows="4" maxlength="20000" data-proj-field="context" placeholder="Goals, product shape, stack, shared conventions">${escapeHtml(
              d.context
            )}</textarea>
          </label>
          <div class="project-history-controls">
            <select data-proj-field="selectedVersion" ${d.history.length ? "" : "disabled"}>
              ${
                d.history.length
                  ? d.history
                      .map(
                        (h) =>
                          `<option value="${escapeHtml(h.id)}"${
                            h.id === d.selectedVersion ? " selected" : ""
                          }>${escapeHtml(formatTime(h.created_at))} · ${escapeHtml(h.preview)}</option>`
                      )
                      .join("")
                  : `<option value="">No saved versions</option>`
              }
            </select>
            <button type="button" class="secondary" data-proj-restore="${idx}" ${
              d.history.length ? "" : "disabled"
            }>Restore</button>
            <button type="button" class="danger" data-proj-del-ver="${idx}" ${
              d.history.length ? "" : "disabled"
            }>Delete version</button>
          </div>
          <label>Sub-projects <span class="muted">One per line: name | repository folder | focused background notes</span>
            <textarea rows="4" data-proj-field="modulesText" placeholder="Payments | backend/payments | Owns billing and subscription logic">${escapeHtml(
              d.modulesText
            )}</textarea>
          </label>
          <button type="button" class="danger" data-proj-remove="${idx}">Remove project</button>
        </section>`
      )
      .join("");
    
    // Load git status for projects with local paths
    projectDrafts.forEach(async (d, idx) => {
      if (d.repository && d.local_path && d.id) {
        try {
          const res = await api(`/api/git/${encodeURIComponent(d.id)}/status`);
          const statusEl = document.querySelector(`[data-proj-status="${idx}"]`);
          if (statusEl) {
            if (!res.available) {
              statusEl.textContent = res.message || "Not cloned yet";
              statusEl.className = "git-status";
              return;
            }
            const s = res.status;
            let statusText = s.branch;
            if (s.modified) statusText += ` · ${s.modified} modified`;
            if (s.ahead) statusText += " · ahead";
            if (s.behind) statusText += " · behind";
            if (s.clean) statusText = "Clean";
            statusEl.textContent = statusText;
            statusEl.className = `git-status git-status-${s.clean ? 'clean' : 'dirty'}`;
          }
        } catch {
          const statusEl = document.querySelector(`[data-proj-status="${idx}"]`);
          if (statusEl) statusEl.textContent = "Status unavailable";
        }
      }
    });
  }

  $("#project-editors")?.addEventListener("input", (e) => {
    const sec = e.target.closest("[data-proj-idx]");
    const field = e.target.getAttribute("data-proj-field");
    if (!sec || !field) return;
    const idx = Number(sec.getAttribute("data-proj-idx"));
    if (projectDrafts[idx]) projectDrafts[idx][field] = e.target.value;
  });

  $("#project-editors")?.addEventListener("change", (e) => {
    const sec = e.target.closest("[data-proj-idx]");
    const field = e.target.getAttribute("data-proj-field");
    if (!sec || !field) return;
    const idx = Number(sec.getAttribute("data-proj-idx"));
    if (projectDrafts[idx]) projectDrafts[idx][field] = e.target.value;
  });

  $("#project-editors")?.addEventListener("click", async (e) => {
    const rmBtn = e.target.closest("[data-proj-remove]");
    if (rmBtn) {
      if (projectDrafts.length < 2) {
        alert("Keep at least one project.");
        return;
      }
      projectDrafts.splice(Number(rmBtn.getAttribute("data-proj-remove")), 1);
      renderProjectEditors();
      return;
    }

    const pullBtn = e.target.closest("[data-proj-pull]");
    if (pullBtn) {
      const d = projectDrafts[Number(pullBtn.getAttribute("data-proj-pull"))];
      if (!d?.id) return;
      const statusEl = document.querySelector(`[data-proj-status="${pullBtn.getAttribute('data-proj-pull')}"]`);
      if (statusEl) statusEl.textContent = "Pulling…";
      try {
        const res = await api(`/api/git/${encodeURIComponent(d.id)}/pull`, { method: "POST" });
        alert(`Pull successful:\n${res.output}`);
        renderProjectEditors();
      } catch (err) {
        alert(`Pull failed: ${err.message}`);
        if (statusEl) statusEl.textContent = "Pull failed";
      }
      return;
    }

    const pushBtn = e.target.closest("[data-proj-push]");
    if (pushBtn) {
      const d = projectDrafts[Number(pushBtn.getAttribute("data-proj-push"))];
      if (!d?.id) return;
      const statusEl = document.querySelector(`[data-proj-status="${pushBtn.getAttribute('data-proj-push')}"]`);
      if (statusEl) statusEl.textContent = "Pushing…";
      try {
        const res = await api(`/api/git/${encodeURIComponent(d.id)}/push`, { method: "POST" });
        alert(`Push successful:\n${res.output}`);
        renderProjectEditors();
      } catch (err) {
        alert(`Push failed: ${err.message}`);
        if (statusEl) statusEl.textContent = "Push failed";
      }
      return;
    }

    const cloneBtn = e.target.closest("[data-proj-clone]");
    if (cloneBtn) {
      const d = projectDrafts[Number(cloneBtn.getAttribute("data-proj-clone"))];
      if (!d?.id) return;
      if (!confirm(`Clone repository to ${d.local_path}? This will create a new git clone.`)) return;
      const statusEl = document.querySelector(`[data-proj-status="${cloneBtn.getAttribute('data-proj-clone')}"]`);
      if (statusEl) statusEl.textContent = "Cloning…";
      try {
        const endpoint = `/api/git/${encodeURIComponent(d.id)}/clone`;
        let job = await api(endpoint, { method: "POST" });
        while (job.status === "running") {
          const progress = String(job.progress || "Preparing clone").trim();
          if (statusEl) statusEl.textContent = `Cloning… ${progress.slice(-100)}`;
          await new Promise((resolve) => setTimeout(resolve, 1500));
          job = await api(`${endpoint}/${encodeURIComponent(job.job_id)}`);
        }
        if (job.status !== "succeeded") {
          throw new Error(job.error || "Git clone ended without a result. Check the server logs for the clone job.");
        }
        alert(`Clone successful:\n${job.result?.output || ""}`);
        renderProjectEditors();
      } catch (err) {
        alert(`Clone failed: ${err.message}`);
        if (statusEl) statusEl.textContent = "Clone failed";
      }
      return;
    }

    const resBtn = e.target.closest("[data-proj-restore]");
    if (resBtn) {
      const d = projectDrafts[Number(resBtn.getAttribute("data-proj-restore"))];
      if (!d?.id || !d.selectedVersion) return;
      if (!confirm("Restore this saved background? The current notes will be saved as another version.")) return;
      try {
        const res = await api(`/api/projects/${encodeURIComponent(d.id)}/restore-context`, {
          method: "POST",
          body: JSON.stringify({ version_id: d.selectedVersion }),
        });
        d.context = res.context;
        d.history = await fetchProjectHistory(d.id);
        d.selectedVersion = d.history[0]?.id || "";
        renderProjectEditors();
      } catch (err) {
        alert(err.message);
      }
      return;
    }

    const delVerBtn = e.target.closest("[data-proj-del-ver]");
    if (delVerBtn) {
      const d = projectDrafts[Number(delVerBtn.getAttribute("data-proj-del-ver"))];
      if (!d?.id || !d.selectedVersion) return;
      if (!confirm("Permanently delete this saved project background version?")) return;
      try {
        await api(
          `/api/projects/${encodeURIComponent(d.id)}/context-history/${encodeURIComponent(d.selectedVersion)}`,
          { method: "DELETE" }
        );
        d.history = await fetchProjectHistory(d.id);
        d.selectedVersion = d.history[0]?.id || "";
        renderProjectEditors();
      } catch (err) {
        alert(err.message);
      }
    }
  });

  async function loadProjectsIntoSettings() {
    $("#projects-error").textContent = "";
    const res = await api("/api/projects");
    const list = res.projects || [];
    api("/api/connections/github/repositories", { method: "POST", body: "{}" })
      .then((r) => {
        $("#github-repo-options").innerHTML = (r.repositories || [])
          .map(
            (repo) =>
              `<option value="${escapeHtml(repo.clone_url)}">${escapeHtml(repo.full_name)}${
                repo.private ? " · private" : ""
              }</option>`
          )
          .join("");
      })
      .catch(() => {});

    projectDrafts = await Promise.all(
      list.map(async (p) => {
        const hist = await fetchProjectHistory(p.id);
        return {
          id: p.id || "",
          name: p.name || "",
          repository: p.repository || "",
          local_path: p.local_path || "",
          context: p.context || "",
          modulesText: (p.modules || [])
            .map((m) => `${m.name} | ${m.path || ""} | ${m.context || ""}`)
            .join("\n"),
          history: hist,
          selectedVersion: hist[0]?.id || "",
        };
      })
    );
    renderProjectEditors();
  }

  $("#add-project")?.addEventListener("click", () => {
    projectDrafts.push({
      id: "",
      name: "",
      repository: "",
      context: "",
      modulesText: "",
      history: [],
      selectedVersion: "",
    });
    renderProjectEditors();
  });

  projectsForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    $("#projects-error").textContent = "";
    try {
      const payload = projectDrafts.map((d) => {
        const name = d.name.trim();
        const modules = d.modulesText
          .split("\n")
          .map((l) => l.trim())
          .filter(Boolean)
          .map((line) => {
            const parts = line.split("|").map((p) => p.trim());
            return {
              id: slugify(parts[0]),
              name: parts[0],
              path: parts[1] || "",
              context: parts.slice(2).join(" | "),
            };
          });
        return {
          id: d.id || slugify(name),
          name,
          repository: d.repository.trim(),
          local_path: d.local_path.trim(),
          context: d.context.trim(),
          modules,
        };
      });
      await api("/api/projects", {
        method: "PUT",
        body: JSON.stringify({ projects: payload }),
      });
      $("#settings-dialog").close();
      await refreshBoard();
    } catch (err) {
      $("#projects-error").textContent = err.message;
    }
  });

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
    if (!confirm(`Reset the displayed statistic for ${provider} · ${model}? Past usage history will be kept.`)) {
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
      alert(err.message);
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

  chatInput?.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      chatForm.requestSubmit();
    }
  });

  $("#clear-chat")?.addEventListener("click", async () => {
    if (!confirm("Clear the saved PM/PO conversation? This keeps all tasks and agent reply files.")) {
      return;
    }
    try {
      await api(`/api/chat?project_id=${encodeURIComponent(state.activeProjectId)}`, { method: "DELETE" });
      await loadChat();
    } catch (err) {
      alert(err.message);
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
      alert("Configuration saved successfully.");
    } catch (err) {
      alert(`Failed to save configuration: ${err.message}`);
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
    if (!confirm("Clear all application logs? This cannot be undone.")) return;
    try {
      await api("/api/settings/logs/clear", { method: "POST" });
      $("#log-output").innerHTML = `<span class="muted">Logs cleared.</span>`;
    } catch (err) {
      alert(`Failed to clear logs: ${err.message}`);
    }
  });

  // Restart app
  $("#restart-app")?.addEventListener("click", async () => {
    if (!confirm("Restart the application? This will briefly interrupt the service.")) return;
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
      alert("Please fill in the app repository URL and local checkout directory.");
      return;
    }
    if (!confirm(`Update ${appRepo} in ${appCloneDir}, copy newer files into ${appWorkingDir}, and restart the app?`)) return;
    const statusEl = $("#update-status");
    const updateButton = $("#update-app");
    updateButton.disabled = true;
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
      statusEl.textContent = `${res.message} Restarting the service…`;
      statusEl.className = "update-status success";
      await waitForAppRestart(statusEl, res.message, "update-status");
    } catch (err) {
      statusEl.textContent = `Update failed: ${err.message}`;
      statusEl.className = "update-status error";
    } finally {
      updateButton.disabled = false;
    }
  });

  // --- Boot ---
  void loadConfig();
  void refreshBoard();
  void loadChat();
  void refreshStatus();
  setInterval(() => void refreshBoard(), 5000);
  setInterval(() => void refreshStatus(), 2000);
})();
