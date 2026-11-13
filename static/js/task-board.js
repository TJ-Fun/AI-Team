import { $, escapeHtml, state, api, appAlert, appConfirm, elapsedSeconds, formatDuration, formatTime, statusClass } from "./core.js";
import { getAskedProvider } from "./task-utils.js";

export function initTaskBoard(context = {}) {
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
            ${task.task_type === "env_setup" ? `<span class="state state-done">ENV</span>` : ""}
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

  for (const [btnId, kind] of [
    ["update-docs", "docs"],
    ["update-roadmap", "roadmap"],
  ]) {
    $(`#${btnId}`)?.addEventListener("click", async () => {
      const btn = $(`#${btnId}`);
      btn.disabled = true;
      try {
        await api("/api/doc-updates", {
          method: "POST",
          body: JSON.stringify({ kind, project_id: state.activeProjectId }),
        });
        await refreshBoard();
      } catch (err) {
        appAlert(err.message);
      } finally {
        btn.disabled = false;
      }
    });
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
        appAlert(err.message);
      } finally {
        readyBtn.disabled = false;
      }
      return;
    }

    const delBtn = e.target.closest("[data-delete-task]");
    if (delBtn) {
      const id = delBtn.getAttribute("data-delete-task");
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
      delBtn.disabled = true;
      try {
        await api(`/api/tasks/${encodeURIComponent(id)}`, { method: "DELETE" });
        await refreshBoard();
      } catch (err) {
        appAlert(err.message);
      } finally {
        delBtn.disabled = false;
      }
      return;
    }

    const detailBtn = e.target.closest("[data-open-detail]");
    if (detailBtn) {
      const id = detailBtn.getAttribute("data-open-detail");
      void context.openTaskDetail(id);
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

      context.renderActiveProjectSelect();
      context.renderNotifications();
      renderTasks();
      if ($("#statistics-dialog")?.open) {
        context.renderStatistics();
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
          void context.renderTaskDetailContent(updated, false).then(() => {
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
  return { refreshBoard, renderTasks };
}
