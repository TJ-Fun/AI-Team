import { $, state, api } from "./core.js";
import {
  isProviderConnected,
  chatProviderOptions,
  populateProviderSelects,
  setConnectionsSuccess,
} from "./provider-shared.js";
import { initChatProviderSelect } from "./provider-chat-select.js";
import { initProviderRouting } from "./provider-routing.js";
import { initProviderConnections } from "./provider-connections.js";

export function initProviders({ refreshStatus, refreshBoard } = {}) {
  const { renderChatModelSelect, renderChatProviderSelect } = initChatProviderSelect();
  const { ensureRouteModelCache, renderRoutingRoles } = initProviderRouting({ reloadSettingsData, refreshBoard });
  const { renderConnectionsAndRouting, refreshProviderModels } = initProviderConnections({
    ensureRouteModelCache,
    renderRoutingRoles,
    reloadSettingsData,
    refreshStatus,
    refreshBoard,
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

  return {
    chatProviderOptions,
    isProviderConnected,
    populateProviderSelects,
    setConnectionsSuccess,
    renderConnectionsAndRouting,
    refreshProviderModels,
    renderChatModelSelect,
    renderChatProviderSelect,
    reloadSettingsData,
  };
}
