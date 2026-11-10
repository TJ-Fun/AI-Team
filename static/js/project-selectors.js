import { $, escapeHtml, state } from "./core.js";

export function initProjectSelectors(context = {}) {
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
    context.renderTasks();
    void context.loadChat();
    if ($("#repository-view-dialog")?.open) {
      void context.openRepositoryView(state.repoMode);
    }
  });

  $("#active-subproject")?.addEventListener("change", (e) => {
    state.activeModuleId = e.target.value;
    localStorage.setItem("ai_team_active_subproject", state.activeModuleId);
    context.renderTasks();
  });
  return { renderActiveProjectSelect, renderActiveSubprojectSelect };
}
