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

(() => {
  const { loadAgentPrompts } = initPrompts();
  initProjectContext();
  const { openRepositoryView, openDocumentationFile } = initRepositoryViewer();
  const featureContext = {
    loadChat,
    renderNotifications,
    renderStatistics,
    renderTaskDetailContent,
    openTaskDetail,
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
        !["clarification_question", "project_overview", "design_review", "pm_escalation"].includes(task.task_type);
      providerRow.hidden = !showProviderRow;
      if (showProviderRow) {
        const canChange = !["In progress", "Done", "Canceled"].includes(task.status);
        if (providerSelect.options.length <= 2) populateProviderSelects();
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
              appAlert(err.message);
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

    // Design review section
    const dSec = $("#detail-design-section");
    if (task.task_type === "design_review") {
      dSec.hidden = false;
      const dBody = $("#detail-design-body");
      const rd = task.review_data || {};
      const dc = rd.design_change || {};
      const alts = rd.alternatives || [];
      const summaryHtml = `
        <div class="design-review-summary">
          ${rd.summary ? `<p><strong>Summary:</strong> ${escapeHtml(rd.summary)}</p>` : ""}
          ${dc.description ? `<p><strong>Change:</strong> ${escapeHtml(dc.description)}</p>` : ""}
          ${dc.reason ? `<p><strong>Reason:</strong> ${escapeHtml(dc.reason)}</p>` : ""}
          ${rd.recommendation ? `<p><strong>PM/PO recommendation:</strong> ${escapeHtml(rd.recommendation)}</p>` : ""}
          ${(rd.team_task_ids || []).length ? `<p><strong>Provisional team tasks:</strong> ${(rd.team_task_ids || []).map((id) => escapeHtml(id)).join(", ")}</p>` : ""}
        </div>`;
      if (task.status === "Needs approval") {
        const altOptions = alts
          .map((a, i) => `<option value="${i}">${escapeHtml(a.title || `Option ${i + 1}`)}${a.description ? ` — ${escapeHtml(a.description)}` : ""}</option>`)
          .join("");
        dBody.innerHTML = `
          ${summaryHtml}
          <div class="question-options">
            ${alts.length ? `<label>Or choose an alternative<select id="design-alt-select"><option value="">Select an alternative</option>${altOptions}</select></label>` : ""}
            <label>Your feedback (required to reject)<textarea id="design-free-text" rows="3" maxlength="5000" placeholder="Optional note, or explain the rejection"></textarea></label>
            <div class="design-review-actions">
              <button class="primary" type="button" id="design-approve">Approve design change</button>
              <button class="secondary" type="button" id="design-alternative" ${alts.length ? "" : "disabled"}>Use selected alternative</button>
              <button class="secondary" type="button" id="design-reject">Reject</button>
            </div>
          </div>`;
        const submit = async (decision, altIndex) => {
          const free_text = dBody.querySelector("#design-free-text")?.value || "";
          await api(`/api/tasks/${encodeURIComponent(task.id)}/design-review`, {
            method: "POST",
            body: JSON.stringify({ decision, free_text, alternative_index: altIndex ?? null }),
          });
          await refreshBoard();
        };
        dBody.querySelector("#design-approve")?.addEventListener("click", async (ev) => {
          ev.target.disabled = true;
          try { await submit("approve"); } catch (err) { appAlert(err.message); } finally { ev.target.disabled = false; }
        });
        dBody.querySelector("#design-alternative")?.addEventListener("click", async (ev) => {
          const idx = dBody.querySelector("#design-alt-select")?.value;
          if (idx === "" || idx == null) { appAlert("Select an alternative first."); return; }
          ev.target.disabled = true;
          try { await submit("alternative", Number(idx)); } catch (err) { appAlert(err.message); } finally { ev.target.disabled = false; }
        });
        dBody.querySelector("#design-reject")?.addEventListener("click", async (ev) => {
          ev.target.disabled = true;
          try { await submit("reject"); } catch (err) { appAlert(err.message); } finally { ev.target.disabled = false; }
        });
      } else {
        const decision = task.design_decision ? `Decision: ${escapeHtml(task.design_decision)}${task.design_alternative ? ` (${escapeHtml(task.design_alternative)})` : ""}.` : "";
        dBody.innerHTML = `${summaryHtml}<p class="detail-copy">${decision}${task.design_free_text ? ` Feedback: ${escapeHtml(task.design_free_text)}` : ""}</p>`;
      }
    } else {
      dSec.hidden = true;
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
    if (task.status === "Needs approval" && task.merge_approval_required && task.pull_request) {
      actionHtml += `<button class="primary" type="button" data-merge-approve="1">Approve merge</button>`;
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
      appAlert(err.message);
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
        !(
          await appConfirm(
            "Permanently delete this task, its agent replies, and its task activity log entries? The overall chat conversation will remain. This cannot be undone.",
            { title: "Delete task", danger: true }
          )
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
        appAlert(err.message);
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
        appAlert(err.message);
      } finally {
        button.disabled = false;
      }
      return;
    }

    if (e.target.closest("[data-merge-approve]")) {
      const button = e.target.closest("[data-merge-approve]");
      if (!(await appConfirm("Approve merging this pull request now? GitHub will re-check CI before merging.", { title: "Approve merge" }))) return;
      button.disabled = true;
      try {
        await api(`/api/tasks/${encodeURIComponent(task.id)}/approve-merge`, {
          method: "POST",
          body: JSON.stringify({}),
        });
        await refreshBoard();
      } catch (err) {
        appAlert(err.message);
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
        if (!(await appConfirm("Cancel this task? This cannot be undone.", { title: "Cancel task", danger: true }))) return;
      } else if (target === "Blocked") {
        const reasonRes = await appPrompt("Why is this task blocked? (Optional)", "", "Block task");
        if (!reasonRes.confirmed) return;
        const reason = reasonRes.value;
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
        appAlert(err.message);
      }
      return;
    }

    const ovBtn = e.target.closest("[data-overview-action]");
    if (ovBtn) {
      const approve = ovBtn.getAttribute("data-overview-action") === "approve";
      if (
        approve &&
        !(await appConfirm(
          "Approve this overview and replace Project brief, Technical overview, and Decision log? Current versions will be saved in history; Confirmed outcomes are preserved.",
          { title: "Approve overview" }
        ))
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
        appAlert(err.message);
      }
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
