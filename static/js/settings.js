import { $, escapeHtml, api, appAlert, appConfirm, waitForAppRestart, createUpdateProgress } from "./core.js";

export function initSettings({ loadProjectsIntoSettings, reloadSettingsData, setConnectionsSuccess, loadAgentPrompts, openDocumentationFile } = {}) {
  // --- Settings Dialog ---
  const settingsDialog = $("#settings-dialog");
  let loadedSettingsTabs = new Set();

  async function switchSettingsTab(tabName, focusTab = false) {
    const validTabs = ["projects", "connections", "routing", "prompts", "logs", "about"];
    const tab = validTabs.includes(tabName) ? tabName : "about";
    document.querySelectorAll("[data-settings-tab]").forEach((button) => {
      const selected = button.dataset.settingsTab === tab;
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
      if (selected && focusTab) button.focus();
    });
    document.querySelectorAll(".settings-tab-panel").forEach((panel) => {
      panel.hidden = panel.id !== `settings-panel-${tab}`;
    });
    localStorage.setItem("ai_team_settings_tab", tab);
    $("#settings-error").textContent = "";
    if (tab === "projects" && !loadedSettingsTabs.has(tab)) {
      try {
        await loadProjectsIntoSettings();
        loadedSettingsTabs.add(tab);
      } catch (err) {
        $("#settings-error").textContent = `Could not load projects: ${err.message}`;
      }
    } else if (tab === "connections" && !loadedSettingsTabs.has(tab)) {
      $("#connections-error").textContent = "";
      setConnectionsSuccess("");
      try {
        await reloadSettingsData();
        loadedSettingsTabs.add(tab);
      } catch (err) {
        $("#settings-error").textContent = `Could not load connections: ${err.message}`;
      }
    } else if (tab === "routing" && !loadedSettingsTabs.has(tab)) {
      $("#routing-error").textContent = "";
      try {
        await reloadSettingsData();
        loadedSettingsTabs.add(tab);
      } catch (err) {
        $("#settings-error").textContent = `Could not load routing settings: ${err.message}`;
      }
    } else if (tab === "prompts" && !loadedSettingsTabs.has(tab)) {
      try {
        await loadAgentPrompts();
        loadedSettingsTabs.add(tab);
      } catch (err) {
        $("#prompt-error").textContent = `Could not load agent prompts: ${err.message}`;
      }
    } else if (tab === "about") {
      const about = $("#about-content");
      try {
        const res = await api("/api/documentation-files?file=ABOUT.md");
        about.textContent = res.content;
      } catch (err) {
        about.textContent = `Could not load app information: ${err.message}`;
      }
    }
  }

  document.querySelectorAll("[data-settings-tab]").forEach((button) => {
    button.addEventListener("click", () => void switchSettingsTab(button.dataset.settingsTab));
    button.addEventListener("keydown", (event) => {
      const tabs = [...document.querySelectorAll("[data-settings-tab]")];
      const index = tabs.indexOf(button);
      let nextIndex = index;
      if (event.key === "ArrowRight") nextIndex = (index + 1) % tabs.length;
      else if (event.key === "ArrowLeft") nextIndex = (index - 1 + tabs.length) % tabs.length;
      else if (event.key === "Home") nextIndex = 0;
      else if (event.key === "End") nextIndex = tabs.length - 1;
      else return;
      event.preventDefault();
      void switchSettingsTab(tabs[nextIndex].dataset.settingsTab, true);
    });
  });

  $("#open-settings")?.addEventListener("click", async () => {
    $("#settings-error").textContent = "";
    loadedSettingsTabs = new Set();
    try {
      const res = await api("/api/settings/config");
      const config = res.config || {};
      $("#app-repo-url").value = config.app_repo || "";
      $("#app-clone-dir").value = config.app_clone_dir || "";
      $("#app-working-dir").value = config.app_working_dir || "";
      $("#log-capture-level").value = config.log_level || "INFO";
    } catch (err) {
      $("#settings-error").textContent = `Could not load app configuration: ${err.message}`;
    }
    if (!settingsDialog.open) settingsDialog.showModal();
    await switchSettingsTab(localStorage.getItem("ai_team_settings_tab") || "app");
  });

  $("#close-settings")?.addEventListener("click", () => settingsDialog.close());
  $("#cancel-settings")?.addEventListener("click", () => settingsDialog.close());
  $("#open-version-log")?.addEventListener("click", () => {
    void openDocumentationFile("VERSIONS.md", "Version log");
  });
  $("#open-app-documentation")?.addEventListener("click", () => {
    void openDocumentationFile("DOCUMENTATION.md", "Documentation");
  });

  $("#log-capture-level")?.addEventListener("change", async (e) => {
    const level = e.target.value;
    const status = $("#log-settings-status");
    status.textContent = "Saving log level…";
    try {
      const res = await api("/api/settings/log-level", {
        method: "POST",
        body: JSON.stringify({ level }),
      });
      status.textContent = `Logs will store ${res.log_level} and above.`;
    } catch (err) {
      status.textContent = `Could not change log level: ${err.message}`;
    }
  });

  // Save app config
  $("#save-app-config")?.addEventListener("click", async () => {
    const appRepo = $("#app-repo-url")?.value?.trim();
    const appCloneDir = $("#app-clone-dir")?.value?.trim();
    const appWorkingDir = $("#app-working-dir")?.value?.trim();
    try {
      await api("/api/settings/config", {
        method: "POST",
        body: JSON.stringify({
          app_repo: appRepo,
          app_clone_dir: appCloneDir,
          app_working_dir: appWorkingDir,
        }),
      });
      appAlert("Configuration saved successfully.", "Settings saved");
    } catch (err) {
      appAlert(`Failed to save configuration: ${err.message}`, "Settings not saved");
    }
  });

  // Load logs
  $("#load-logs")?.addEventListener("click", async () => {
    const level = $("#log-level")?.value || "all";
    $("#log-output").textContent = "Loading logs…";
    try {
      const res = await api("/api/settings/logs", {
        method: "POST",
        body: JSON.stringify({ level, lines: 1000 }),
      });
      const logs = res.logs || [];
      if (logs.length === 0) {
        $("#log-output").innerHTML = `<span class="muted">No logs found.</span>`;
      } else {
        $("#log-output").innerHTML = logs
          .map((line) => {
            let className = "log-info";
            const level = line.match(/\|\s*(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s*\|/)?.[1];
            if (level === "CRITICAL") className = "log-critical";
            else if (level === "ERROR") className = "log-error";
            else if (level === "WARNING") className = "log-warning";
            else if (level === "DEBUG") className = "log-debug";
            return `<span class="${className}">${escapeHtml(line)}</span>`;
          })
          .join("\n");
      }
    } catch (err) {
      $("#log-output").innerHTML = `<span class="log-error">Failed to load logs: ${escapeHtml(err.message)}</span>`;
    }
  });

  // Clear logs
  $("#clear-logs")?.addEventListener("click", async () => {
    if (!(await appConfirm("Clear all application logs? This cannot be undone.", { title: "Clear logs", danger: true }))) return;
    try {
      await api("/api/settings/logs/clear", { method: "POST" });
      $("#log-output").innerHTML = `<span class="muted">Logs cleared.</span>`;
    } catch (err) {
      appAlert(`Failed to clear logs: ${err.message}`, "Clear logs failed");
    }
  });

  // Restart app
  $("#restart-app")?.addEventListener("click", async () => {
    if (!(await appConfirm("Restart the application? This will briefly interrupt the service.", { title: "Restart app" }))) return;
    const statusEl = $("#restart-status");
    statusEl.textContent = "Restarting…";
    statusEl.className = "restart-status info";
    try {
      await api("/api/settings/restart", { method: "POST" });
      statusEl.textContent = "Restart scheduled.";
      statusEl.className = "restart-status success";
      await waitForAppRestart(statusEl, "Restart scheduled.");
    } catch (err) {
      statusEl.textContent = `Restart failed: ${err.message}`;
      statusEl.className = "restart-status error";
    }
  });

  // Update app
  $("#update-app")?.addEventListener("click", async () => {
    const appRepo = $("#app-repo-url")?.value?.trim();
    const appCloneDir = $("#app-clone-dir")?.value?.trim();
    const appWorkingDir = $("#app-working-dir")?.value?.trim();
    if (!appRepo || !appCloneDir || !appWorkingDir) {
      appAlert("Please fill in the app repository URL and local checkout directory.", "Update not configured");
      return;
    }
    if (!(await appConfirm(`Update ${appRepo} in ${appCloneDir}, copy newer files into ${appWorkingDir}, and restart the app?`, { title: "Update app" }))) return;
    const statusEl = $("#update-status");
    const updateButton = $("#update-app");
    const progressWrap = $("#update-progress");
    const progress = createUpdateProgress($("#update-progress-bar"), $("#update-progress-label"));
    updateButton.disabled = true;
    if (progressWrap) progressWrap.hidden = false;
    progress?.begin("Preparing update…");
    statusEl.textContent = "Updating…";
    statusEl.className = "update-status info";
    try {
      const res = await api("/api/settings/update", {
        method: "POST",
        body: JSON.stringify({
          app_repo: appRepo,
          app_clone_dir: appCloneDir,
          app_working_dir: appWorkingDir,
        }),
      });
      progress?.copyingDone(`${res.message} Restarting the service…`);
      statusEl.textContent = `${res.message} Restarting the service…`;
      statusEl.className = "update-status success";
      await waitForAppRestart(statusEl, res.message, "update-status", progress);
    } catch (err) {
      progress?.fail(`Update failed: ${err.message}`);
      statusEl.textContent = `Update failed: ${err.message}`;
      statusEl.className = "update-status error";
    } finally {
      updateButton.disabled = false;
    }
  });
}
