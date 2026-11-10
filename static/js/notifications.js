import { $, escapeHtml, state, api, appAlert, appConfirm, createUpdateProgress, waitForAppRestart } from "./core.js";

export function initNotifications({ refreshBoard, openTaskDetail } = {}) {
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
  return { renderNotifications };
}
