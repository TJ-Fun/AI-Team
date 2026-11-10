import { $, escapeHtml, state, api, appAlert, appConfirm, formatTime } from "./core.js";

export function initProjectContext() {
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
      appAlert(`Could not load project context: ${err.message}`);
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
    if (!(await appConfirm("Restore this saved version? The current document will first be saved as a new history version.", { title: "Restore version" }))) {
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
      appAlert(err.message);
    }
  });

  $("#delete-context-version")?.addEventListener("click", async () => {
    const versionId = contextVersionSelect.value;
    const docId = contextSelect.value;
    if (!versionId) return;
    if (!(await appConfirm("Permanently delete this saved version? The current Project context will not change.", { title: "Delete version", danger: true }))) {
      return;
    }
    try {
      await api(
        projectContextEndpoint(`/${encodeURIComponent(docId)}/history/${encodeURIComponent(versionId)}`),
        { method: "DELETE" }
      );
      await loadContextHistory(docId);
    } catch (err) {
      appAlert(err.message);
    }
  });
}
