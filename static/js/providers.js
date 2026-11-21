import { $, escapeHtml, state, api, appAlert, appConfirm } from "./core.js";
import {
  isProviderConnected,
  providerKindLabel,
  chatProviderOptions,
  populateProviderSelects,
  setConnectionsSuccess,
} from "./provider-shared.js";
import { initChatProviderSelect } from "./provider-chat-select.js";
import { initProviderRouting } from "./provider-routing.js";

export function initProviders({ refreshStatus, refreshBoard } = {}) {
  const { renderChatModelSelect, renderChatProviderSelect } = initChatProviderSelect();
  const { ensureRouteModelCache, renderRoutingRoles } = initProviderRouting({ reloadSettingsData, refreshBoard });

  // --- Connections Dialog ---
  const connectionsForm = $("#connections-form");

  function renderConnectionsAndRouting() {
    const providers = state.connections
      .filter((c) => c.is_provider !== false)
      .sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
    const github = state.connections.find((c) => c.id === "github");
    $("#connection-list").innerHTML =
      providers
        .map((p, idx) => {
          const first = idx === 0;
          const last = idx === providers.length - 1;
          return `
        <div class="route-role provider-row">
          <div class="provider-order">
            <button type="button" class="secondary provider-move" data-move-provider="up" data-provider-id="${escapeHtml(
              p.id
            )}" ${first ? "disabled" : ""} aria-label="Move ${escapeHtml(p.name)} up">↑</button>
            <button type="button" class="secondary provider-move" data-move-provider="down" data-provider-id="${escapeHtml(
              p.id
            )}" ${last ? "disabled" : ""} aria-label="Move ${escapeHtml(p.name)} down">↓</button>
          </div>
          <div class="provider-main">
            <div class="progress-heading">
              <strong>${escapeHtml(p.name)}</strong>
              <span class="provider-kind">${escapeHtml(providerKindLabel(p.kind))}</span>
              <span class="state ${p.configured ? "state-done" : "state-draft"}">${
                p.configured ? `Connected · ${escapeHtml(p.source || "Saved")}` : "Not connected"
              }</span>
            </div>
            <div class="provider-meta">${escapeHtml(p.model || "")}${p.base_url ? ` · ${escapeHtml(p.base_url)}` : ""}</div>
            <div class="provider-model-line">
              <label class="provider-model-label">Model
                <select class="provider-model" data-provider-model="${escapeHtml(p.id)}" disabled><option value="">…</option></select>
              </label>
              <button type="button" class="secondary provider-test-model" data-test-model="${escapeHtml(p.id)}" ${
                p.configured ? "" : "disabled"
              }>Test model</button>
              <button type="button" class="secondary provider-refresh-models" data-refresh-models="${escapeHtml(p.id)}" ${
                p.configured ? "" : "disabled"
              } title="Fetch the latest model list from this provider">Refresh models</button>
            </div>
            <div class="connection-controls">
              ${
                state.encryptionAvailable
                  ? `<input type="password" class="connection-key" data-conn-input="${escapeHtml(
                      p.id
                    )}" placeholder="Paste new API key / token to replace" autocomplete="off">
                    <button type="button" class="secondary" data-save-conn="${escapeHtml(p.id)}">Save</button>`
                  : ""
              }
              <button type="button" class="secondary" data-test-conn="${escapeHtml(p.id)}" ${
                p.configured ? "" : "disabled"
              }>Test</button>
              ${
                state.encryptionAvailable
                  ? `<button type="button" class="danger" data-clear-conn="${escapeHtml(p.id)}" ${
                      p.configured ? "" : "disabled"
                    }>Clear</button>`
                  : ""
              }
              <button type="button" class="danger" data-remove-provider="${escapeHtml(p.id)}">Remove</button>
            </div>
          </div>
        </div>`;
        })
        .join("") +
      (github
        ? `
        <div class="route-role">
          <div class="progress-heading">
            <strong>${escapeHtml(github.name)}</strong>
            <span class="state ${github.configured ? "state-done" : "state-draft"}">${
              github.configured ? `Connected · ${escapeHtml(github.source || "Saved")}` : "Not connected"
            }</span>
          </div>
          <div class="connection-controls">
            ${
              state.encryptionAvailable
                ? `<input type="password" class="connection-key" data-conn-input="github" placeholder="Paste new token to replace" autocomplete="off">
                  <button type="button" class="secondary" data-save-conn="github">Save</button>`
                : ""
            }
            <button type="button" class="secondary" data-test-conn="github" ${github.configured ? "" : "disabled"}>Test</button>
            ${
              state.encryptionAvailable
                ? `<button type="button" class="danger" data-clear-conn="github" ${github.configured ? "" : "disabled"}>Clear</button>`
                : ""
            }
          </div>
        </div>`
        : "");

    renderRoutingRoles();
    refreshProviderModels();
  }


  async function refreshProviderModels() {
    const selects = document.querySelectorAll("select.provider-model");
    for (const sel of selects) {
      const pid = sel.getAttribute("data-provider-model");
      const rec = state.connections.find((c) => c.id === pid);
      const compat = rec && rec.kind !== "gemini";
      sel.disabled = true;
      sel.innerHTML = `<option value="">…</option>`;
      sel.title = "";
      try {
        const res = await api(`/api/providers/${encodeURIComponent(pid)}/models`);
        const current = String(res.current || "auto");
        const models = res.models || [];
        if (!res.selectable || !models.length) {
          sel.innerHTML = `<option value="${escapeHtml(current)}" selected>${escapeHtml(
            current === "auto" ? "Auto" : current
          )}</option>`;
          sel.disabled = true;
          sel.title = res.error || "Model selection is not available for this provider.";
          continue;
        }
        const options = [];
        if (compat) {
          options.push(`<option value="auto"${current === "auto" ? " selected" : ""}>Auto</option>`);
        }
        for (const m of models) {
          options.push(`<option value="${escapeHtml(m)}"${m === current ? " selected" : ""}>${escapeHtml(m)}</option>`);
        }
        if (current !== "auto" && !models.includes(current)) {
          options.push(`<option value="${escapeHtml(current)}" selected>${escapeHtml(current)}</option>`);
        }
        sel.innerHTML = options.join("");
        sel.value = current === "auto" ? "auto" : current;
        sel.disabled = false;
      } catch (err) {
        sel.innerHTML = `<option value="">Model unavailable</option>`;
        sel.disabled = true;
        sel.title = err.message;
      }
    }
  }

  async function reloadSettingsData() {
    const [connRes, routeRes] = await Promise.all([api("/api/connections"), api("/api/routing")]);
    state.connections = connRes.connections || [];
    state.providerPresets = connRes.presets || [];
    state.encryptionAvailable = connRes.encryption_available !== false;
    state.routingRoles = routeRes.roles || [];
    state.routingProviders = routeRes.providers || [];
    state.maxRoutes = routeRes.max_routes || 25;
    state.routeModelCache = {};
    state.routing = routeRes.routing || {};
    state.chatProvider = routeRes.chat_provider || "Auto";
    state.autonomy = routeRes.autonomy || {};
    const autonomyDefaults = {
      plan_requires_approval: true,
      escalation_runs_provisional: true,
      merge_requires_approval: false,
    };
    const autonomyControls = {
      plan_requires_approval: "#autonomy-plan-approval",
      escalation_runs_provisional: "#autonomy-escalation-provisional",
      merge_requires_approval: "#autonomy-merge-approval",
    };
    for (const [key, selector] of Object.entries(autonomyControls)) {
      const el = $(selector);
      if (el) el.checked = Boolean(state.autonomy[key] ?? autonomyDefaults[key]);
    }
    renderChatProviderSelect();
    renderChatModelSelect();
    renderConnectionsAndRouting();
    populateProviderSelects();
  }

  $("#connection-list")?.addEventListener("click", async (e) => {
    const moveBtn = e.target.closest("[data-move-provider]");
    if (moveBtn) {
      const id = moveBtn.getAttribute("data-provider-id");
      const dir = moveBtn.getAttribute("data-move-provider");
      const ids = state.connections
        .filter((c) => c.is_provider !== false)
        .sort((a, b) => (a.order ?? 0) - (b.order ?? 0))
        .map((c) => c.id);
      const idx = ids.indexOf(id);
      const swap = dir === "up" ? idx - 1 : idx + 1;
      if (idx >= 0 && swap >= 0 && swap < ids.length) {
        [ids[idx], ids[swap]] = [ids[swap], ids[idx]];
        $("#connections-error").textContent = "";
        setConnectionsSuccess("");
        try {
          await api("/api/providers/order", { method: "POST", body: JSON.stringify({ order: ids }) });
          await reloadSettingsData();
          await refreshStatus();
          setConnectionsSuccess("Provider order saved.");
        } catch (err) {
          $("#connections-error").textContent = err.message;
        }
      }
      return;
    }

    const removeBtn = e.target.closest("[data-remove-provider]");
    if (removeBtn) {
      const id = removeBtn.getAttribute("data-remove-provider");
      const rec = state.connections.find((c) => c.id === id);
      if (
        !(await appConfirm(
          `Remove provider "${rec?.name || id}"? Built-in providers can be re-added from the preset list; custom providers are deleted completely.`,
          { title: "Remove provider", danger: true }
        ))
      )
        return;
      $("#connections-error").textContent = "";
      setConnectionsSuccess("");
      try {
        await api(`/api/providers/${encodeURIComponent(id)}`, { method: "DELETE" });
        await reloadSettingsData();
        await refreshStatus();
        await refreshBoard();
        setConnectionsSuccess("Provider removed.");
      } catch (err) {
        $("#connections-error").textContent = err.message;
      }
      return;
    }

    const saveBtn = e.target.closest("[data-save-conn]");
    if (saveBtn) {
      const id = saveBtn.getAttribute("data-save-conn");
      const input = document.querySelector(`[data-conn-input="${id}"]`);
      const secret = input?.value?.trim() || "";
      if (!secret) {
        setConnectionsSuccess("");
        $("#connections-error").textContent = "Paste a key or token before clicking Save.";
        return;
      }
      $("#connections-error").textContent = "";
      setConnectionsSuccess("");
      try {
        await api(`/api/connections/${encodeURIComponent(id)}`, {
          method: "PUT",
          body: JSON.stringify({ secret }),
        });
        if (input) input.value = "";
        await reloadSettingsData();
        await refreshStatus();
        setConnectionsSuccess("Saved encrypted credential.");
      } catch (err) {
        $("#connections-error").textContent = err.message;
      }
      return;
    }

    const testBtn = e.target.closest("[data-test-conn]");
    if (testBtn) {
      const id = testBtn.getAttribute("data-test-conn");
      const origLabel = testBtn.textContent;
      testBtn.disabled = true;
      testBtn.textContent = "Testing…";
      $("#connections-error").textContent = "";
      setConnectionsSuccess("");
      try {
        const res = await api(`/api/connections/${encodeURIComponent(id)}/test`, {
          method: "POST",
          body: "{}",
        });
        const msg = res.message || (res.login ? `GitHub connected as ${res.login}.` : "Connection verified.");
        setConnectionsSuccess(msg);
      } catch (err) {
        appAlert(err.message, "Connection test failed");
      } finally {
        testBtn.disabled = false;
        testBtn.textContent = origLabel || "Test";
      }
      return;
    }

    const refreshModelsBtn = e.target.closest("[data-refresh-models]");
    if (refreshModelsBtn) {
      const pid = refreshModelsBtn.getAttribute("data-refresh-models");
      const rec = state.connections.find((c) => c.id === pid);
      const origLabel = refreshModelsBtn.textContent;
      refreshModelsBtn.disabled = true;
      refreshModelsBtn.textContent = "Refreshing…";
      $("#connections-error").textContent = "";
      setConnectionsSuccess("");
      try {
        const res = await api(`/api/providers/${encodeURIComponent(pid)}/refresh-models`, { method: "POST", body: "{}" });
        const count = (res.models || []).length;
        if (res.selectable && count) {
          setConnectionsSuccess(`${res.provider}: ${count} models refreshed.`);
        } else {
          setConnectionsSuccess(`${res.provider}: refresh failed — ${res.error || "no models listed"}.`);
        }
        if (rec) {
          delete state.routeModelCache?.[rec.name];
          ensureRouteModelCache(rec.name);
        }
        await refreshProviderModels();
      } catch (err) {
        appAlert(err.message, "Model list refresh failed");
      } finally {
        refreshModelsBtn.disabled = false;
        refreshModelsBtn.textContent = origLabel || "Refresh models";
      }
      return;
    }

    const testModelBtn = e.target.closest("[data-test-model]");
    if (testModelBtn) {
      const pid = testModelBtn.getAttribute("data-test-model");
      const modelSel = document.querySelector(`select.provider-model[data-provider-model="${pid}"]`);
      const model = modelSel?.value || "";
      const origLabel = testModelBtn.textContent;
      testModelBtn.disabled = true;
      testModelBtn.textContent = "Testing…";
      $("#connections-error").textContent = "";
      setConnectionsSuccess("");
      try {
        const res = await api(`/api/providers/${encodeURIComponent(pid)}/test-model`, {
          method: "POST",
          body: JSON.stringify({ model }),
        });
        const reply = String(res.reply || "").trim();
        setConnectionsSuccess(`${res.provider} · ${res.model} replied.`);
        appAlert(
          reply ? `“${reply.slice(0, 500)}${reply.length > 500 ? "…" : ""}”` : "The model returned an empty reply.",
          `Model test — ${res.provider} · ${res.model}`
        );
      } catch (err) {
        appAlert(err.message, "Model test failed");
      } finally {
        testModelBtn.disabled = false;
        testModelBtn.textContent = origLabel || "Test model";
      }
      return;
    }

    const clearBtn = e.target.closest("[data-clear-conn]");
    if (clearBtn) {
      const id = clearBtn.getAttribute("data-clear-conn");
      if (!(await appConfirm("Remove the saved credential from this VM?", { title: "Clear credential", danger: true }))) return;
      $("#connections-error").textContent = "";
      setConnectionsSuccess("");
      try {
        await api(`/api/connections/${encodeURIComponent(id)}`, { method: "DELETE" });
        await reloadSettingsData();
        await refreshStatus();
        setConnectionsSuccess("Removed saved credential.");
      } catch (err) {
        $("#connections-error").textContent = err.message;
      }
    }
  });

  $("#connection-list")?.addEventListener("change", async (e) => {
    const sel = e.target.closest("select.provider-model");
    if (!sel) return;
    const pid = sel.getAttribute("data-provider-model");
    const model = sel.value;
    $("#connections-error").textContent = "";
    setConnectionsSuccess("");
    try {
      await api(`/api/providers/${encodeURIComponent(pid)}`, {
        method: "PUT",
        body: JSON.stringify({ model }),
      });
      const rec = state.connections.find((c) => c.id === pid);
      if (rec) rec.model = model;
      setConnectionsSuccess(`Model updated for ${rec?.name || pid}.`);
    } catch (err) {
      $("#connections-error").textContent = err.message;
      refreshProviderModels();
    }
  });

  $("#add-provider")?.addEventListener("click", async () => {
    const body = {
      name: $("#add-provider-name")?.value?.trim() || "",
      base_url: $("#add-provider-base-url")?.value?.trim() || "",
    };
    const key = $("#add-provider-key")?.value?.trim() || "";
    $("#connections-error").textContent = "";
    setConnectionsSuccess("");
    try {
      const res = await api("/api/providers", { method: "POST", body: JSON.stringify(body) });
      const pid = res?.provider?.id || "";
      if (key && pid) {
        await api(`/api/connections/${encodeURIComponent(pid)}`, {
          method: "PUT",
          body: JSON.stringify({ secret: key }),
        });
      }
      $("#add-provider-name").value = "";
      $("#add-provider-base-url").value = "";
      $("#add-provider-key").value = "";
      await reloadSettingsData();
      await refreshStatus();
      setConnectionsSuccess(key ? "Provider added and key saved. Pick a model below, then Test." : "Provider added. Save its API key below, pick a model, then Test.");
    } catch (err) {
      $("#connections-error").textContent = err.message;
    }
  });

  connectionsForm?.addEventListener("submit", (e) => e.preventDefault());

  return { chatProviderOptions, isProviderConnected, populateProviderSelects, setConnectionsSuccess, renderConnectionsAndRouting, refreshProviderModels, renderChatModelSelect, renderChatProviderSelect, reloadSettingsData };
}
