import { $, escapeHtml, state, api, appConfirm } from "./core.js";

export function initPrompts() {
  // --- Agent Prompts ---
  const promptForm = $("#prompt-form");
  const promptSelect = promptForm?.elements.prompt_id;
  const promptTextarea = promptForm?.elements.content;
  const promptStatusBadge = $("#prompt-status-badge");
  const promptFeedback = $("#prompt-feedback");
  const resetPromptBtn = $("#reset-prompt");

  function setPromptFeedback(message = "") {
    if (!promptFeedback) return;
    if (message) {
      promptFeedback.textContent = message;
      promptFeedback.hidden = false;
    } else {
      promptFeedback.textContent = "";
      promptFeedback.hidden = true;
    }
  }

  function renderPromptOptions(selectedId) {
    if (!promptSelect) return;
    promptSelect.innerHTML = state.prompts
      .map(
        (p) =>
          `<option value="${escapeHtml(p.id)}"${p.id === selectedId ? " selected" : ""}>${escapeHtml(p.title)}${
            p.is_custom ? " (custom)" : ""
          }</option>`
      )
      .join("");
  }

  function updatePromptStatusUI() {
    const selected = state.prompts.find((p) => p.id === promptSelect?.value);
    if (!selected) return;
    const currentText = (promptTextarea?.value || "").trim();
    const savedText = (selected.content || "").trim();
    const defaultText = (selected.default_content || "").trim();
    const hasUnsaved = currentText !== savedText;
    const differsFromDefault = Boolean(defaultText && currentText !== defaultText);
    const isCustom = Boolean(selected.is_custom);

    if (promptStatusBadge) {
      promptStatusBadge.className = `state ${isCustom ? "state-ready" : "state-done"}`;
      promptStatusBadge.textContent =
        (isCustom ? "Custom prompt active" : "Default prompt active") + (hasUnsaved ? " · unsaved edits" : "");
    }
    if (resetPromptBtn) {
      resetPromptBtn.disabled = !isCustom && !differsFromDefault;
    }
  }

  async function loadAgentPrompts() {
    $("#prompt-error").textContent = "";
    setPromptFeedback("");
    const res = await api("/api/prompts");
    state.prompts = res.prompts || [];
    const initial = state.prompts.find((p) => p.id === promptSelect?.value) ||
      state.prompts.find((p) => p.id === "pm_po") ||
      state.prompts[0];
    if (initial) {
      renderPromptOptions(initial.id);
      promptTextarea.value = initial.content || "";
      updatePromptStatusUI();
    }
  }

  promptSelect?.addEventListener("change", () => {
    $("#prompt-error").textContent = "";
    setPromptFeedback("");
    const found = state.prompts.find((p) => p.id === promptSelect.value);
    promptTextarea.value = found?.content || "";
    updatePromptStatusUI();
  });

  promptTextarea?.addEventListener("input", () => {
    setPromptFeedback("");
    updatePromptStatusUI();
  });

  promptForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    $("#prompt-error").textContent = "";
    setPromptFeedback("");
    const id = promptSelect.value;
    try {
      const updated = await api(`/api/prompts/${encodeURIComponent(id)}`, {
        method: "PUT",
        body: JSON.stringify({ content: promptTextarea.value }),
      });
      state.prompts = state.prompts.map((p) =>
        p.id === id
          ? {
              ...p,
              content: updated.content,
              default_content: updated.default_content ?? p.default_content,
              is_custom: Boolean(updated.is_custom),
            }
          : p
      );
      promptTextarea.value = updated.content;
      renderPromptOptions(id);
      updatePromptStatusUI();
      setPromptFeedback(
        updated.is_custom
          ? `Saved custom prompt for ${updated.title || id}. Future agent runs will use this prompt.`
          : `Saved prompt for ${updated.title || id} (matches built-in default).`
      );
    } catch (err) {
      $("#prompt-error").textContent = err.message;
    }
  });

  resetPromptBtn?.addEventListener("click", async () => {
    const id = promptSelect?.value;
    const selected = state.prompts.find((p) => p.id === id);
    if (!selected) return;
    if (
      !(await appConfirm(
        `Reset "${selected.title}" back to its built-in default prompt? Your saved custom prompt for this role will be replaced with the default template.`,
        { title: "Reset prompt" }
      ))
    ) {
      return;
    }
    $("#prompt-error").textContent = "";
    setPromptFeedback("");
    resetPromptBtn.disabled = true;
    try {
      const reset = await api(`/api/prompts/${encodeURIComponent(id)}/reset`, {
        method: "POST",
        body: "{}",
      });
      state.prompts = state.prompts.map((p) =>
        p.id === id
          ? {
              ...p,
              content: reset.content,
              default_content: reset.default_content ?? p.default_content,
              is_custom: false,
            }
          : p
      );
      promptTextarea.value = reset.content;
      renderPromptOptions(id);
      updatePromptStatusUI();
      setPromptFeedback(`Restored default prompt for ${selected.title}.`);
    } catch (err) {
      $("#prompt-error").textContent = err.message;
      updatePromptStatusUI();
    }
  });
  return { loadAgentPrompts };
}
