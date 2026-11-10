import { $, escapeHtml, state, api } from "./core.js";

export function initRepositoryViewer() {
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
  return { openRepositoryView, openDocumentationFile };
}
