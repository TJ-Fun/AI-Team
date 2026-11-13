import { $, escapeHtml, api, appAlert, appConfirm, appPrompt, formatTime, slugify } from "./core.js";
import { renderProjectEditors as renderProjectEditorsModule } from "./project-editor.js";

export function initProjectSettings({ refreshBoard } = {}) {
  // --- Projects & Sub-projects Dialog ---
  const projectsForm = $("#projects-form");
  const PROJECTS_STORAGE_KEY = "ai_team_settings_project_id";
  const SERVICE_FIELDS = [
    "svc_web_host", "svc_api_runtime", "svc_database", "svc_auth", "svc_media", "svc_bot_protection",
    "svc_preview_url", "svc_staging_url", "svc_production_url", "svc_health_path",
  ];
  function servicesToDraft(services = {}) {
    const out = {};
    for (const f of SERVICE_FIELDS) out[f] = services[f.slice(4)] || "";
    return out;
  }
  function draftToServices(d) {
    const out = {};
    for (const f of SERVICE_FIELDS) out[f.slice(4)] = d[f] || "";
    return out;
  }
  let projectDrafts = [];
  let selectedProjectIndex = 0;

  async function fetchProjectHistory(projectId) {
    if (!projectId) return [];
    try {
      const res = await api(`/api/projects/${encodeURIComponent(projectId)}/context-history`);
      return res.history || [];
    } catch {
      return [];
    }
  }

  function renderProjectEditors() {
    renderProjectEditorsModule(projectDrafts, selectedProjectIndex, PROJECTS_STORAGE_KEY);
  }


  $("#project-selector")?.addEventListener("change", (e) => {
    selectedProjectIndex = Number(e.target.value) || 0;
    renderProjectEditors();
  });

  $("#project-editors")?.addEventListener("input", (e) => {
    const sec = e.target.closest("[data-proj-idx]");
    const field = e.target.getAttribute("data-proj-field");
    if (!sec || !field) return;
    const idx = Number(sec.getAttribute("data-proj-idx"));
    if (projectDrafts[idx]) projectDrafts[idx][field] = e.target.value;
  });

  $("#project-editors")?.addEventListener("change", (e) => {
    const sec = e.target.closest("[data-proj-idx]");
    const field = e.target.getAttribute("data-proj-field");
    if (!sec || !field) return;
    const idx = Number(sec.getAttribute("data-proj-idx"));
    if (projectDrafts[idx]) projectDrafts[idx][field] = e.target.value;
  });

  $("#project-editors")?.addEventListener("click", async (e) => {
    const rmBtn = e.target.closest("[data-proj-remove]");
    if (rmBtn) {
      if (projectDrafts.length < 2) {
        appAlert("Keep at least one project.");
        return;
      }
      projectDrafts.splice(Number(rmBtn.getAttribute("data-proj-remove")), 1);
      selectedProjectIndex = Math.min(selectedProjectIndex, projectDrafts.length - 1);
      renderProjectEditors();
      return;
    }

    const pullBtn = e.target.closest("[data-proj-pull]");
    if (pullBtn) {
      const d = projectDrafts[Number(pullBtn.getAttribute("data-proj-pull"))];
      if (!d?.id) return;
      const statusEl = document.querySelector(`[data-proj-status="${pullBtn.getAttribute('data-proj-pull')}"]`);
      if (statusEl) statusEl.textContent = "Pulling…";
      try {
        const res = await api(`/api/git/${encodeURIComponent(d.id)}/pull`, { method: "POST" });
        appAlert(`Pull successful:\n${res.output}`, "Git pull");
        renderProjectEditors();
      } catch (err) {
        appAlert(`Pull failed: ${err.message}`, "Git pull failed");
        if (statusEl) statusEl.textContent = "Pull failed";
      }
      return;
    }

    const pushBtn = e.target.closest("[data-proj-push]");
    if (pushBtn) {
      const d = projectDrafts[Number(pushBtn.getAttribute("data-proj-push"))];
      if (!d?.id) return;
      const statusEl = document.querySelector(`[data-proj-status="${pushBtn.getAttribute('data-proj-push')}"]`);
      if (statusEl) statusEl.textContent = "Pushing…";
      try {
        const res = await api(`/api/git/${encodeURIComponent(d.id)}/push`, { method: "POST" });
        appAlert(`Push successful:\n${res.output}`, "Git push");
        renderProjectEditors();
      } catch (err) {
        appAlert(`Push failed: ${err.message}`, "Git push failed");
        if (statusEl) statusEl.textContent = "Push failed";
      }
      return;
    }

    const cloneBtn = e.target.closest("[data-proj-clone]");
    if (cloneBtn) {
      const d = projectDrafts[Number(cloneBtn.getAttribute("data-proj-clone"))];
      if (!d?.id) return;
      if (!(await appConfirm(`Clone repository to ${d.local_path}? This will create a new git clone.`, { title: "Clone repository" }))) return;
      const statusEl = document.querySelector(`[data-proj-status="${cloneBtn.getAttribute('data-proj-clone')}"]`);
      if (statusEl) statusEl.textContent = "Cloning…";
      try {
        const endpoint = `/api/git/${encodeURIComponent(d.id)}/clone`;
        let job = await api(endpoint, { method: "POST" });
        while (job.status === "running") {
          const progress = String(job.progress || "Preparing clone").trim();
          if (statusEl) statusEl.textContent = `Cloning… ${progress.slice(-100)}`;
          await new Promise((resolve) => setTimeout(resolve, 1500));
          job = await api(`${endpoint}/${encodeURIComponent(job.job_id)}`);
        }
        if (job.status !== "succeeded") {
          throw new Error(job.error || "Git clone ended without a result. Check the server logs for the clone job.");
        }
        appAlert(`Clone successful:\n${job.result?.output || ""}`, "Git clone");
        renderProjectEditors();
      } catch (err) {
        appAlert(`Clone failed: ${err.message}`, "Git clone failed");
        if (statusEl) statusEl.textContent = "Clone failed";
      }
      return;
    }

    const clearTasksBtn = e.target.closest("[data-proj-clear-tasks]");
    if (clearTasksBtn) {
      const d = projectDrafts[Number(clearTasksBtn.getAttribute("data-proj-clear-tasks"))];
      if (!d?.id) return;
      const ok = await appConfirm(`Delete all tasks for "${d.name || d.id}"? This removes task replies and activity entries. Chat history and project context stay.`, { title: "Clear project tasks", danger: true });
      if (!ok) return;
      try {
        const res = await api(`/api/projects/${encodeURIComponent(d.id)}/clear-tasks`, { method: "POST", body: JSON.stringify({ confirm: true }) });
        appAlert(`Cleared ${res.deleted_tasks} task(s) and ${res.deleted_replies} reply folder(s) for ${d.name || d.id}.`, "Tasks cleared");
        if (refreshBoard) await refreshBoard();
      } catch (err) {
        appAlert(err.message, "Clear tasks failed");
      }
      return;
    }

    const wipeGithubBtn = e.target.closest("[data-proj-wipe-github]");
    if (wipeGithubBtn) {
      const d = projectDrafts[Number(wipeGithubBtn.getAttribute("data-proj-wipe-github"))];
      if (!d?.id) return;
      const repoMatch = String(d.repository || "").trim().match(/github\.com[/:]([^/]+)\/([^/]+?)(?:\.git)?$/i);
      const repoName = repoMatch ? `${repoMatch[1]}/${repoMatch[2]}` : String(d.repository || "").trim();
      if (!repoName) {
        appAlert("Project has no GitHub repository configured.", "GitHub wipe");
        return;
      }
      const answer = await appPrompt(`This empties the GitHub repository contents and deletes the local clone for ${d.name || d.id}. Type ${repoName} to confirm.`, repoName, "Wipe GitHub contents");
      if (!answer.confirmed) return;
      if (String(answer.value || "").trim().casefold() !== repoName.casefold()) {
        appAlert("Repository name did not match; wipe cancelled.", "Wipe cancelled");
        return;
      }
      try {
        const res = await api(`/api/projects/${encodeURIComponent(d.id)}/wipe-github`, { method: "POST", body: JSON.stringify({ confirm_text: answer.value }) });
        const localNote = res.local_clone && res.local_clone.deleted ? " Local clone deleted." : "";
        appAlert(`GitHub contents wiped for ${res.repository}: ${res.deleted_branches} branch(es), ${res.deleted_tags} tag(s), ${res.deleted_releases} release(s).${localNote}`, "GitHub wiped");
        renderProjectEditors();
        if (refreshBoard) await refreshBoard();
      } catch (err) {
        appAlert(err.message, "GitHub wipe failed");
      }
      return;
    }

    const resBtn = e.target.closest("[data-proj-restore]");
    if (resBtn) {
      const d = projectDrafts[Number(resBtn.getAttribute("data-proj-restore"))];
      if (!d?.id || !d.selectedVersion) return;
      if (!(await appConfirm("Restore this saved background? The current notes will be saved as another version.", { title: "Restore background" }))) return;
      try {
        const res = await api(`/api/projects/${encodeURIComponent(d.id)}/restore-context`, {
          method: "POST",
          body: JSON.stringify({ version_id: d.selectedVersion }),
        });
        d.context = res.context;
        d.history = await fetchProjectHistory(d.id);
        d.selectedVersion = d.history[0]?.id || "";
        renderProjectEditors();
      } catch (err) {
        appAlert(err.message);
      }
      return;
    }

    const delVerBtn = e.target.closest("[data-proj-del-ver]");
    if (delVerBtn) {
      const d = projectDrafts[Number(delVerBtn.getAttribute("data-proj-del-ver"))];
      if (!d?.id || !d.selectedVersion) return;
      if (!(await appConfirm("Permanently delete this saved project background version?", { title: "Delete version", danger: true }))) return;
      try {
        await api(
          `/api/projects/${encodeURIComponent(d.id)}/context-history/${encodeURIComponent(d.selectedVersion)}`,
          { method: "DELETE" }
        );
        d.history = await fetchProjectHistory(d.id);
        d.selectedVersion = d.history[0]?.id || "";
        renderProjectEditors();
      } catch (err) {
        appAlert(err.message);
      }
    }
  });

  async function loadProjectsIntoSettings() {
    $("#projects-error").textContent = "";
    const res = await api("/api/projects");
    const list = res.projects || [];
    api("/api/connections/github/repositories", { method: "POST", body: "{}" })
      .then((r) => {
        $("#github-repo-options").innerHTML = (r.repositories || [])
          .map(
            (repo) =>
              `<option value="${escapeHtml(repo.clone_url)}">${escapeHtml(repo.full_name)}${
                repo.private ? " · private" : ""
              }</option>`
          )
          .join("");
      })
      .catch(() => {});

    projectDrafts = await Promise.all(
      list.map(async (p) => {
        const hist = await fetchProjectHistory(p.id);
        return {
          id: p.id || "",
          name: p.name || "",
          repository: p.repository || "",
          local_path: p.local_path || "",
          context: p.context || "",
          modulesText: (p.modules || [])
            .map((m) => `${m.name} | ${m.path || ""} | ${m.context || ""}`)
            .join("\n"),
          history: hist,
          selectedVersion: hist[0]?.id || "",
          ...servicesToDraft(p.services),
        };
      })
    );
    let savedId = "";
    try { savedId = localStorage.getItem(PROJECTS_STORAGE_KEY) || ""; } catch {}
    selectedProjectIndex = Math.max(0, projectDrafts.findIndex((d) => d.id && d.id === savedId));
    renderProjectEditors();
  }

  $("#add-project")?.addEventListener("click", () => {
    projectDrafts.push({
      id: "",
      name: "",
      repository: "",
      context: "",
      modulesText: "",
      history: [],
      selectedVersion: "",
      ...servicesToDraft({}),
    });
    selectedProjectIndex = projectDrafts.length - 1;
    renderProjectEditors();
  });

  projectsForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    $("#projects-error").textContent = "";
    try {
      const payload = projectDrafts.map((d) => {
        const name = d.name.trim();
        const modules = d.modulesText
          .split("\n")
          .map((l) => l.trim())
          .filter(Boolean)
          .map((line) => {
            const parts = line.split("|").map((p) => p.trim());
            return {
              id: slugify(parts[0]),
              name: parts[0],
              path: parts[1] || "",
              context: parts.slice(2).join(" | "),
            };
          });
        return {
          id: d.id || slugify(name),
          name,
          repository: d.repository.trim(),
          local_path: d.local_path.trim(),
          context: d.context.trim(),
          modules,
          services: draftToServices(d),
        };
      });
      await api("/api/projects", {
        method: "PUT",
        body: JSON.stringify({ projects: payload }),
      });
      $("#settings-dialog").close();
      await refreshBoard();
    } catch (err) {
      $("#projects-error").textContent = err.message;
    }
  });
  return { loadProjectsIntoSettings };
}