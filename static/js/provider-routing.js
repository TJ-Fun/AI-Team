import { $, escapeHtml, state, api, appAlert } from "./core.js";
import { isProviderConnected } from "./provider-shared.js";

export function initProviderRouting({ reloadSettingsData, refreshBoard } = {}) {
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

  const routingForm = $("#routing-form");
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

  return { ensureRouteModelCache, renderRoutingRoles };
}
