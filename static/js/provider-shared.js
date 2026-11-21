import { $, escapeHtml, state } from "./core.js";

export function isProviderConnected(providerName) {
  return state.connections.some((c) => c.is_provider !== false && c.name === providerName && c.configured);
}

export function providerKindLabel(kind) {
  return kind === "gemini" ? "Gemini API" : "OpenAI-compatible";
}

export function chatProviderOptions(selected) {
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

export function populateProviderSelects() {
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

export function setConnectionsSuccess(message = "") {
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
