import { $, escapeHtml, state, api, appAlert, appConfirm } from "./core.js";

export function initProviders({ refreshStatus, refreshBoard } = {}) {
  function chatProviderOptions(selected) {
    const providers = state.routingProviders.length ? state.routingProviders : [];
    const known = state.connections.length > 0;
    return ["Auto", ...providers]
      .map(
        (opt) =>
          `<option value="${escapeHtml(opt)}"${opt === selected ? " selected" : ""}>${escapeHtml(opt)}${
            opt !== "Auto" && known && !isProviderConnected(opt) ? " · not connected" : ""
          }</option>`
      )
      .join("");
  }

  // --- Connections Dialog ---
  const connectionsForm = $("#connections-form");
  const routingForm = $("#routing-form");

  function isProviderConnected(providerName) {
    return state.connections.some((c) => c.is_provider !== false && c.name === providerName && c.configured);
  }

  function providerKindLabel(kind) {
    return kind === "gemini" ? "Gemini API" : "OpenAI-compatible";
  }

  function populateProviderSelects() {
    const options = ["Automatic", "Auto", ...state.routingProviders]
      .map((opt) => `<option value="${escapeHtml(opt)}">${escapeHtml(opt)}</option>`)
      .join("");
    const taskSelect = document.querySelector('select[name="provider"]');
    if (taskSelect) {
      const current = taskSelect.value || "Automatic";
      taskSelect.innerHTML = options;
      taskSelect.value = [...taskSelect.options].some((o) => o.value === current) ? current : "Automatic";
    }
    const detailSelect = $("#detail-provider-select");
    if (detailSelect) {
      const current = detailSelect.value || "Automatic";
      detailSelect.innerHTML = options;
      detailSelect.value = [...detailSelect.options].some((o) => o.value === current) ? current : "Automatic";
    }
  }

  function setConnectionsSuccess(message = "") {
    const el = $("#connections-success");
    if (!el) return;
    if (message) {
      el.textContent = message;
      el.hidden = false;
    } else {
      el.textContent = "";
      el.hidden = true;
    }
  }

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

    const maxPreferred = state.maxPreferred || 4;
    $("#routing-roles").innerHTML =
      state.routingRoles
        .map((role) => {
          const preferred = state.routing[role] || [];
          const selects = Array.from({ length: maxPreferred }, (_, idx) => {
            const val = preferred[idx] || "Auto";
            return `
            <label>${idx + 1}
              <select data-route-role="${escapeHtml(role)}" data-route-idx="${idx}">
                <option value="Auto"${val === "Auto" ? " selected" : ""}>Auto</option>
                ${state.routingProviders
                  .map(
                    (opt) =>
                      `<option value="${escapeHtml(opt)}"${opt === val ? " selected" : ""}>${escapeHtml(opt)}${
                        !isProviderConnected(opt) ? " · not connected" : ""
                      }</option>`
                  )
                  .join("")}
              </select>
            </label>`;
          }).join("");
          return `<section class="route-role"><strong>${escapeHtml(role)}</strong><div class="route-order">${selects}</div></section>`;
        })
        .join("") +
      `<p class="draft-note">Up to ${maxPreferred} preferred providers per role. Auto expands to every provider ordered by audit score, breaking ties by the Connections order. After the preferred providers are exhausted, the same Auto order continues as fallback.</p>`;
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

  async function renderChatModelSelect() {
    const sel = $("#chat-model-select");
    if (!sel) return;
    const provider = state.chatProvider || "Auto";
    sel.disabled = true;
    if (provider === "Auto") {
      sel.innerHTML = `<option value="" selected>Auto model</option>`;
      sel.title = "Auto uses each provider's configured model.";
      return;
    }
    try {
      if (!state.connections.length) {
        const connRes = await api("/api/connections");
        state.connections = connRes.connections || [];
      }
      const rec = state.connections.find((c) => c.name === provider);
      if (!rec) {
        sel.innerHTML = `<option value="">Provider unavailable</option>`;
        return;
      }
      const res = await api(`/api/providers/${encodeURIComponent(rec.id)}/models`);
      const current = String(res.current || "auto");
      const override = state.chatModelOverride || "";
      const options = [
        `<option value=""${override ? "" : " selected"}>Provider default (${escapeHtml(
          current === "auto" ? "Auto" : current
        )})</option>`,
      ];
      for (const m of res.models || []) {
        options.push(`<option value="${escapeHtml(m)}"${m === override ? " selected" : ""}>${escapeHtml(m)}</option>`);
      }
      if (override && !(res.models || []).includes(override)) {
        options.push(`<option value="${escapeHtml(override)}" selected>${escapeHtml(override)}</option>`);
      }
      sel.innerHTML = options.join("");
      sel.disabled = false;
      sel.title = res.selectable
        ? "Model used by the PM/PO chat for this provider."
        : res.error || "Model list unavailable; the provider default is used.";
    } catch (err) {
      sel.innerHTML = `<option value="">Model unavailable</option>`;
      sel.title = err.message;
    }
  }

  function renderChatProviderSelect() {
    const options = chatProviderOptions(state.chatProvider);
    const sel = $("#chat-provider-select");
    if (sel) sel.innerHTML = options;
    const menu = $("#chat-provider-menu");
    if (menu) menu.innerHTML = options;
  }

  $("#chat-provider-select")?.addEventListener("change", (e) => {
    state.chatProvider = e.target.value;
    renderChatModelSelect();
    const menu = $("#chat-provider-menu");
    if (menu) menu.innerHTML = chatProviderOptions(state.chatProvider);
  });

  $("#chat-provider-menu")?.addEventListener("change", async (e) => {
    const provider = e.target.value;
    state.chatProvider = provider;
    renderChatModelSelect();
    const sel = $("#chat-provider-select");
    if (sel) sel.innerHTML = chatProviderOptions(provider);
    try {
      const res = await api("/api/routing", {
        method: "POST",
        body: JSON.stringify({ chat_provider: provider }),
      });
      state.chatModelOverride = res.chat_model_override || "";
      renderChatModelSelect();
    } catch (err) {
      appAlert(`Could not save chat provider: ${err.message}`, "Chat provider");
    }
  });

  $("#chat-model-select")?.addEventListener("change", async (e) => {
    const model = e.target.value;
    try {
      const res = await api("/api/routing", {
        method: "POST",
        body: JSON.stringify({ chat_provider: state.chatProvider, chat_model: model }),
      });
      state.chatProvider = res.chat_provider || state.chatProvider;
      state.chatModelOverride = res.chat_model_override || "";
    } catch (err) {
      appAlert(`Could not save chat model: ${err.message}`, "Chat model");
      renderChatModelSelect();
    }
  });

  async function reloadSettingsData() {
    const [connRes, routeRes] = await Promise.all([api("/api/connections"), api("/api/routing")]);
    state.connections = connRes.connections || [];
    state.providerPresets = connRes.presets || [];
    state.encryptionAvailable = connRes.encryption_available !== false;
    state.routingRoles = routeRes.roles || [];
    state.routingProviders = routeRes.providers || [];
    state.maxPreferred = routeRes.max_preferred || 4;
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

  $("#routing-roles")?.addEventListener("change", (e) => {
    const sel = e.target.closest("[data-route-role]");
    if (!sel) return;
    const role = sel.getAttribute("data-route-role");
    const idx = Number(sel.getAttribute("data-route-idx"));
    const nextProv = sel.value;
    const maxPreferred = state.maxPreferred || 4;
    const current = [...(state.routing[role] || [])];
    while (current.length < maxPreferred) current.push("Auto");
    const existingIdx =
      nextProv === "Auto" ? -1 : current.findIndex((p, i) => i !== idx && p && p === nextProv);
    if (existingIdx >= 0) {
      const tmp = current[idx];
      current[idx] = nextProv;
      current[existingIdx] = tmp;
    } else {
      current[idx] = nextProv;
    }
    state.routing[role] = current.slice(0, maxPreferred);
    renderConnectionsAndRouting();
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

  routingForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    $("#routing-error").textContent = "";
    try {
      await api("/api/routing", {
        method: "POST",
        body: JSON.stringify({
          routing: state.routing,
          chat_provider: state.chatProvider,
          autonomy: {
            plan_requires_approval: Boolean($("#autonomy-plan-approval")?.checked),
            escalation_runs_provisional: $("#autonomy-escalation-provisional")?.checked ?? true,
            merge_requires_approval: Boolean($("#autonomy-merge-approval")?.checked),
          },
        }),
      });
      $("#settings-dialog").close();
      await refreshBoard();
    } catch (err) {
      $("#routing-error").textContent = err.message;
    }
  });
  return { chatProviderOptions, isProviderConnected, populateProviderSelects, setConnectionsSuccess, renderConnectionsAndRouting, refreshProviderModels, renderChatModelSelect, renderChatProviderSelect, reloadSettingsData };
}
