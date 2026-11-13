import { $, escapeHtml, state, api, appAlert, appConfirm, appPrompt } from "./core.js";
import { getAskedProvider } from "./task-utils.js";

export function renderTaskDetailActions(task) {
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
    if (["doc_update", "roadmap_update"].includes(task.task_type) && task.status === "Needs approval") {
      actionHtml += `
        <button class="secondary" type="button" data-update-decision="reject">Reject</button>
        <button class="primary" type="button" data-update-decision="approve">Approve and apply</button>`;
    }
    if (task.status === "Needs approval" && task.merge_approval_required && task.pull_request) {
      actionHtml += `<button class="primary" type="button" data-merge-approve="1">Approve merge</button>`;
    }
    actionHtml += `
      <button class="secondary" type="button" data-detail-close="1">Close</button>
      <button class="danger" type="button" data-detail-delete="1">Delete task</button>`;
    $("#detail-actions").innerHTML = actionHtml;
}

export function bindTaskDetailActions({ detailDialog, refreshBoard, openTaskDialog } = {}) {
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

    const updBtn = e.target.closest("[data-update-decision]");
    if (updBtn) {
      const decision = updBtn.getAttribute("data-update-decision");
      if (
        decision === "approve" &&
        !(await appConfirm(
          "Approve this update and apply it to the project documents? Previous versions are saved in history.",
          { title: "Approve update" }
        ))
      ) {
        return;
      }
      updBtn.disabled = true;
      try {
        await api(`/api/tasks/${encodeURIComponent(task.id)}/doc-update-decision`, {
          method: "POST",
          body: JSON.stringify({ decision }),
        });
        await refreshBoard();
      } catch (err) {
        appAlert(err.message);
      } finally {
        updBtn.disabled = false;
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
}
