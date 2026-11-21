import { $, escapeHtml, state, api, appAlert } from "./core.js";
import { chatProviderOptions } from "./provider-shared.js";

export function initChatProviderSelect() {
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

  return { renderChatModelSelect, renderChatProviderSelect };
}
