import { $, escapeHtml, api, formatTime } from "./core.js";

export function renderProjectEditors(projectDrafts, selectedProjectIndex, storageKey) {
    const selector = $("#project-selector");
    if (selector) {
      selector.innerHTML = projectDrafts.map((d, idx) => `<option value="${idx}">${escapeHtml(d.name || `Project ${idx + 1}`)}</option>`).join("");
      selector.value = String(selectedProjectIndex);
      try { localStorage.setItem(storageKey, projectDrafts[selectedProjectIndex]?.id || ""); } catch {}
    }
    $("#project-editors").innerHTML = projectDrafts
      .map(
        (d, idx) => idx === selectedProjectIndex ? `
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
          ${d.id ? `
          <div class="git-controls project-danger-actions">
            <button type="button" class="danger" data-proj-clear-tasks="${idx}">Clear tasks</button>
            <button type="button" class="danger" data-proj-wipe-github="${idx}">Wipe GitHub contents</button>
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
        </section>` : ""
      )
      .join("");
    
    // Load git status for projects with local paths
    projectDrafts.forEach(async (d, idx) => {
      if (idx !== selectedProjectIndex) return;
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
