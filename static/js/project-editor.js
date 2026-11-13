import { $, escapeHtml, api, formatTime } from "./core.js";

function servicesHtml(d) {
  const sel = (field, label, opts) => `<label>${label}<select data-proj-field="${field}">${opts
    .map((o) => `<option value="${escapeHtml(o)}" ${(d[field] || "") === o ? "selected" : ""}>${escapeHtml(o || "Not set")}</option>`)
    .join("")}</select></label>`;
  const txt = (field, label, ph) => `<label>${label}<input data-proj-field="${field}" value="${escapeHtml(d[field] || "")}" placeholder="${escapeHtml(ph)}" maxlength="500"></label>`;
  return `<div class="project-services"><h4>Services &amp; hosting</h4>
    <div class="two-columns">${sel("svc_web_host", "Web host", ["", "vercel", "netlify", "github-pages", "oci", "other"])}${sel("svc_api_runtime", "API runtime", ["", "oci", "vercel", "netlify", "other"])}</div>
    <div class="two-columns">${sel("svc_database", "Database", ["", "supabase", "firebase", "oci-postgres", "other"])}${sel("svc_auth", "Auth", ["", "supabase", "firebase", "clerk", "other"])}</div>
    <div class="two-columns">${sel("svc_media", "Media", ["", "cloudinary", "imagekit", "other"])}${sel("svc_bot_protection", "Bot protection", ["", "cloudflare-turnstile", "other"])}</div>
    <div class="two-columns">${txt("svc_preview_url", "Preview URL", "https://preview.example.com")}${txt("svc_staging_url", "Staging URL", "https://staging.example.com")}</div>
    <div class="two-columns">${txt("svc_production_url", "Production URL", "https://app.example.com")}${txt("svc_health_path", "Health path", "/health")}</div>
  </div>`;
}

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
          ${servicesHtml(d)}
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
