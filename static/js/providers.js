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

  function defaultRouteProvider() {
    return state.routingProviders.find((p) => isProviderConnected(p)) || "Auto";
  }

  function defaultRouteRows() {
    return [0, 1, 2].map(() => ({
      provider: defaultRouteProvider(),
      model: "auto",
      timeout_seconds: 900,
      retries: 2,
      retry_delay_seconds: 300,
      stream: true,
    }));
  }

  function routeRowsFor(role) {
    if (!Array.isArray(state.routing[role]) || !state.routing[role].length) {
      state.routing[role] = defaultRouteRows();
    }
    return state.routing[role];
  }

  function ensureRouteModelCache(provider) {
    if (!provider || provider === "Auto") return;
    const cache = (state.routeModelCache ||= {});
    if (cache[provider]) return;
    const rec = state.connections.find((c) => c.is_provider !== false && c.name === provider);
    if (!rec) {
      cache[provider] = { status: "unavailable", models: [], current: "" };
      return;
    }
    cache[provider] = { status: "loading", models: [], current: "" };
    api(`/api/providers/${encodeURIComponent(rec.id)}/models`)
      .then((res) => {
        cache[provider] = {
          status: res.selectable && (res.models || []).length ? "ready" : "unavailable",
          models: res.models || [],
          current: String(res.current || ""),
        };
      })
      .catch(() => {
        cache[provider] = { status: "error", models: [], current: "" };
      })
      .finally(() => renderRoutingRoles());
  }

  function routeModelOptionsHtml(entry) {
    const cache = state.routeModelCache?.[entry.provider];
    const current = cache?.current && cache.current !== "auto" ? cache.current : "";
    const options = [
      `<option value="auto"${!entry.model || entry.model === "auto" ? " selected" : ""}>Auto${
        current ? ` · ${escapeHtml(current)}` : ""
      }</option>`,
    ];
    if (cache?.status === "ready") {
      for (const m of cache.models) {
        options.push(`<option value="${escapeHtml(m)}"${m === entry.model ? " selected" : ""}>${escapeHtml(m)}</option>`);
      }
    }
    if (entry.model && entry.model !== "auto" && !(cache?.models || []).includes(entry.model)) {
      options.push(`<option value="${escapeHtml(entry.model)}" selected>${escapeHtml(entry.model)}</option>`);
    }
    if (cache?.status === "loading") {
      options.push(`<option disabled>Loading models…</option>`);
    }
    return options.join("");
  }

  function renderRoutingRoles() {
    const maxRoutes = state.maxRoutes || 25;
    state.routingOpen ||= {};
    $("#routing-roles").innerHTML =
      state.routingRoles
        .map((role) => {
          const rows = routeRowsFor(role);
          const open = !!state.routingOpen[role];
          const autoIdx = rows.findIndex((e) => e.provider === "Auto");
          const body = rows
            .map((entry, idx) => {
              const inactive = autoIdx >= 0 && idx > autoIdx;
              const provOpts = ["Auto", ...state.routingProviders]
                .map(
                  (opt) =>
                    `<option value="${escapeHtml(opt)}"${opt === entry.provider ? " selected" : ""}>${escapeHtml(opt)}${
                      opt !== "Auto" && !isProviderConnected(opt) ? " · not connected" : ""
                    }</option>`
                )
                .join("");
              const modelDisabled = inactive || state.routeModelCache?.[entry.provider]?.status === "loading";
              const modelMissing = !!entry.model_unavailable;
              const timeoutMin = Math.round((entry.timeout_seconds ?? 900) / 60);
              const delayMin = Math.round((entry.retry_delay_seconds ?? 300) / 60);
              return `
            <div class="route-row${inactive ? " route-row--inactive" : ""}">
              <span class="route-rank">${idx + 1}</span>
              <select class="route-provider" data-route-role="${escapeHtml(role)}" data-route-idx="${idx}" ${
                inactive ? "disabled" : ""
              }>${provOpts}</select>
              <select class="route-model${modelMissing ? " route-model--missing" : ""}" data-route-role="${escapeHtml(role)}" data-route-idx="${idx}" ${
                modelDisabled ? "disabled" : ""
              } title="${modelMissing ? "This model is no longer listed by the provider." : ""}">${routeModelOptionsHtml(entry)}</select>
              <button type="button" class="secondary route-test" data-route-test="${escapeHtml(
                role
              )}" data-route-idx="${idx}" ${inactive ? "disabled" : ""} title="Send 'Hi' to this provider/model and show the reply">Test</button>
              <div class="route-settings">
                <label><span class="route-lbl">Timeout</span><input type="number" class="route-num" min="1" max="1440" step="1" value="${timeoutMin}" data-route-setting="timeout" data-route-role="${escapeHtml(role)}" data-route-idx="${idx}" ${inactive ? "disabled" : ""}><span class="route-unit">min</span></label>
                <label><span class="route-lbl">Retries</span><input type="number" class="route-num" min="0" max="10" step="1" value="${entry.retries ?? 2}" data-route-setting="retries" data-route-role="${escapeHtml(role)}" data-route-idx="${idx}" ${inactive ? "disabled" : ""}></label>
                <label><span class="route-lbl">Retry delay</span><input type="number" class="route-num" min="0" max="1440" step="1" value="${delayMin}" data-route-setting="delay" data-route-role="${escapeHtml(role)}" data-route-idx="${idx}" ${inactive ? "disabled" : ""}><span class="route-unit">min</span></label>
                <label class="route-stream-check"><input type="checkbox" data-route-setting="stream" data-route-role="${escapeHtml(role)}" data-route-idx="${idx}" ${entry.stream !== false ? "checked" : ""} ${inactive ? "disabled" : ""}><span class="route-lbl">Stream</span></label>
              </div>
              <span class="route-row-actions">
                <button type="button" class="secondary route-move" data-route-move="up" data-route-role="${escapeHtml(
                  role
                )}" data-route-idx="${idx}" ${idx === 0 || inactive ? "disabled" : ""} aria-label="Move route up">↑</button>
                <button type="button" class="secondary route-move" data-route-move="down" data-route-role="${escapeHtml(
                  role
                )}" data-route-idx="${idx}" ${idx === rows.length - 1 || inactive ? "disabled" : ""} aria-label="Move route down">↓</button>
                <button type="button" class="secondary route-remove" data-route-remove="${escapeHtml(
                  role
                )}" data-route-idx="${idx}" ${rows.length <= 1 || inactive ? "disabled" : ""} aria-label="Remove route">✕</button>
              </span>
            </div>`;
            })
            .join("");
          return `<section class="route-role route-card route-accordion">
            <button type="button" class="route-accordion-head" data-route-toggle="${escapeHtml(role)}" aria-expanded="${open}">
              <span class="route-accordion-chevron">${open ? "▾" : "▸"}</span>
              <strong>${escapeHtml(role)}</strong>
              <span class="route-accordion-count">${rows.length} route${rows.length === 1 ? "" : "s"}</span>
            </button>
            ${
              open
                ? `<div class="route-accordion-body"><div class="route-list">${body}</div><button type="button" class="secondary route-add" data-route-add="${escapeHtml(
                    role
                  )}" ${rows.length >= maxRoutes ? "disabled" : ""}>+ Add provider/model</button></div>`
                : ""
            }
          </section>`;
        })
        .join("") +
      `<p class="draft-note">Each role routes through an ordered list of provider/model pairs; model Auto uses the provider's configured default. Provider Auto expands to every provider ordered by audit score, and rows below an Auto provider are inactive. The same provider may appear multiple times with different models. Per route: Timeout is the per-attempt limit, Retries is how many times the same combo is retried after any failure, Retry delay is the wait between those retries, and Stream toggles SSE streaming (timer resets per chunk) vs a blocking call with a hard deadline. Unavailable connections are skipped automatically.</p>`;
    for (const role of state.routingRoles) {
      for (const entry of routeRowsFor(role)) ensureRouteModelCache(entry.provider);
    }
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

  $("#routing-roles")?.addEventListener("change", (e) => {
    const settingInput = e.target.closest("[data-route-setting]");
    if (settingInput) {
      const sRole = settingInput.getAttribute("data-route-role");
      const sIdx = Number(settingInput.getAttribute("data-route-idx"));
      const sRows = routeRowsFor(sRole);
      const sEntry = sRows[sIdx];
      if (!sEntry) return;
      const kind = settingInput.getAttribute("data-route-setting");
      if (kind === "timeout") {
        sEntry.timeout_seconds = Math.max(60, Math.min(86400, Math.round((Number(settingInput.value) || 15) * 60)));
      } else if (kind === "retries") {
        sEntry.retries = Math.max(0, Math.min(10, Math.round(Number(settingInput.value) || 0)));
      } else if (kind === "delay") {
        sEntry.retry_delay_seconds = Math.max(0, Math.min(86400, Math.round((Number(settingInput.value) || 0) * 60)));
      } else if (kind === "stream") {
        sEntry.stream = settingInput.checked;
      }
      renderRoutingRoles();
      return;
    }
    const provSel = e.target.closest("select.route-provider");
    const modelSel = e.target.closest("select.route-model");
    const sel = provSel || modelSel;
    if (!sel) return;
    const role = sel.getAttribute("data-route-role");
    const idx = Number(sel.getAttribute("data-route-idx"));
    const rows = routeRowsFor(role);
    const entry = rows[idx];
    if (!entry || Number.isNaN(idx)) return;
    if (provSel) {
      entry.provider = provSel.value;
      entry.model = "auto";
      delete entry.model_unavailable;
      ensureRouteModelCache(entry.provider);
    } else {
      entry.model = modelSel.value === "auto" ? "auto" : modelSel.value;
      delete entry.model_unavailable;
    }
    renderRoutingRoles();
  });

  $("#routing-roles")?.addEventListener("click", async (e) => {
    const toggleBtn = e.target.closest("[data-route-toggle]");
    if (toggleBtn) {
      const tRole = toggleBtn.getAttribute("data-route-toggle");
      state.routingOpen ||= {};
      state.routingOpen[tRole] = !state.routingOpen[tRole];
      renderRoutingRoles();
      return;
    }
    const addBtn = e.target.closest("[data-route-add]");
    if (addBtn) {
      const role = addBtn.getAttribute("data-route-add");
      const rows = routeRowsFor(role);
      if (rows.length >= (state.maxRoutes || 25)) return;
      const autoIdx = rows.findIndex((en) => en.provider === "Auto");
      const at = autoIdx >= 0 ? autoIdx : rows.length;
      rows.splice(at, 0, {
        provider: defaultRouteProvider(),
        model: "auto",
        timeout_seconds: 900,
        retries: 2,
        retry_delay_seconds: 300,
        stream: true,
      });
      renderRoutingRoles();
      return;
    }
    const moveBtn = e.target.closest("[data-route-move]");
    if (moveBtn) {
      const role = moveBtn.getAttribute("data-route-role");
      const idx = Number(moveBtn.getAttribute("data-route-idx"));
      const dir = moveBtn.getAttribute("data-route-move");
      const rows = routeRowsFor(role);
      const swap = dir === "up" ? idx - 1 : idx + 1;
      if (idx >= 0 && swap >= 0 && swap < rows.length) {
        [rows[idx], rows[swap]] = [rows[swap], rows[idx]];
        renderRoutingRoles();
      }
      return;
    }
    const testRouteBtn = e.target.closest("[data-route-test]");
    if (testRouteBtn) {
      const tRole = testRouteBtn.getAttribute("data-route-test");
      const tIdx = Number(testRouteBtn.getAttribute("data-route-idx"));
      const tEntry = routeRowsFor(tRole)[tIdx];
      if (!tEntry) return;
      const tRec = state.connections.find((c) => c.is_provider !== false && c.name === tEntry.provider);
      if (!tRec) {
        appAlert(`Provider "${tEntry.provider}" is not available.`, "Route test failed");
        return;
      }
      const tModel = tEntry.model && tEntry.model !== "auto" ? tEntry.model : "";
      const origLabel = testRouteBtn.textContent;
      testRouteBtn.disabled = true;
      testRouteBtn.textContent = "Testing…";
      try {
        const res = await api(`/api/providers/${encodeURIComponent(tRec.id)}/test-model`, {
          method: "POST",
          body: JSON.stringify({ model: tModel }),
        });
        const reply = String(res.reply || "").trim();
        appAlert(
          reply ? `“${reply.slice(0, 500)}${reply.length > 500 ? "…" : ""}”` : "The model returned an empty reply.",
          `Route test — ${res.provider} · ${res.model}`
        );
      } catch (err) {
        appAlert(err.message, "Route test failed");
      } finally {
        testRouteBtn.disabled = false;
        testRouteBtn.textContent = origLabel || "Test";
      }
      return;
    }
    const removeBtn = e.target.closest("[data-route-remove]");
    if (removeBtn) {
      const role = removeBtn.getAttribute("data-route-remove");
      const idx = Number(removeBtn.getAttribute("data-route-idx"));
      const rows = routeRowsFor(role);
      if (rows.length > 1) {
        rows.splice(idx, 1);
        renderRoutingRoles();
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
