import { $, escapeHtml, state, api } from "./core.js";
import { getAskedProvider } from "./task-utils.js";

export function initTaskDialog({ refreshBoard } = {}) {
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
  return { openTaskDialog };
}
