import { $, escapeHtml, state, api, appAlert, appConfirm } from "./core.js";

export function initChat({ refreshBoard } = {}) {
  // --- PM/PO Chat ---
  const messagesEl = $("#messages");
  const chatForm = $("#chat-form");
  const chatInput = $("#chat-input");

  function renderChatMessages(messages, pendingLabel = "") {
    if (!messages.length && !pendingLabel) {
      messagesEl.innerHTML = `
        <div class="welcome-message">
          <div class="welcome-icon">✳</div>
          <h3>Talk through the work</h3>
          <p>Ask for help clarifying a request, planning acceptance checks, or understanding the board.</p>
        </div>`;
      return;
    }
    let html = messages
      .map(
        (m) => `
        <div class="message ${m.role === "user" ? "user-message" : "assistant-message"}">
          <div class="message-role">${m.role === "user" ? "You" : `PM/PO${m.provider ? ` · ${escapeHtml(m.provider)}` : ""}`}</div>
          <div class="message-content">${escapeHtml(m.content)}</div>
        </div>`
      )
      .join("");
    if (pendingLabel) {
      html += `<div class="message assistant-message pending-message">${escapeHtml(pendingLabel)}</div>`;
    }
    messagesEl.innerHTML = html;
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  let chatMessages = [];
  async function loadChat() {
    try {
      const res = await api(`/api/chat?project_id=${encodeURIComponent(state.activeProjectId)}`);
      chatMessages = res.messages || [];
      renderChatMessages(chatMessages);
    } catch (err) {
      messagesEl.innerHTML = `<div class="inline-error">Could not load chat history: ${escapeHtml(err.message)}</div>`;
    }
  }

  chatForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = chatInput.value.trim();
    if (!text) return;
    chatInput.value = "";
    $("#send-button").disabled = true;
    chatMessages = [...chatMessages, { role: "user", content: text }];
    renderChatMessages(chatMessages, "PM/PO is thinking…");
    try {
      const res = await api("/api/chat", {
        method: "POST",
        body: JSON.stringify({ message: text, project_id: state.activeProjectId }),
      });
      chatMessages = res.messages || chatMessages;
      renderChatMessages(chatMessages);
    } catch (err) {
      chatMessages = [...chatMessages, { role: "assistant", content: err.message }];
      renderChatMessages(chatMessages);
    } finally {
      $("#send-button").disabled = false;
      chatInput.focus();
    }
  });

  $("#plan-request")?.addEventListener("click", async () => {
    const text = chatInput.value.trim();
    if (!text) {
      appAlert("Type a request to plan first.", "Nothing to plan");
      chatInput.focus();
      return;
    }
    const planBtn = $("#plan-request");
    planBtn.disabled = true;
    $("#send-button").disabled = true;
    try {
      await api("/api/pm-requests", {
        method: "POST",
        body: JSON.stringify({ request: text, project_id: state.activeProjectId }),
      });
      chatInput.value = "";
      await Promise.all([loadChat(), refreshBoard()]);
    } catch (err) {
      appAlert(err.message);
    } finally {
      planBtn.disabled = false;
      $("#send-button").disabled = false;
      chatInput.focus();
    }
  });

  chatInput?.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      chatForm.requestSubmit();
    }
  });

  $("#clear-chat")?.addEventListener("click", async () => {
    if (!(await appConfirm("Clear the saved PM/PO conversation? This keeps all tasks and agent reply files.", { title: "Clear conversation" }))) {
      return;
    }
    try {
      await api(`/api/chat?project_id=${encodeURIComponent(state.activeProjectId)}`, { method: "DELETE" });
      await loadChat();
    } catch (err) {
      appAlert(err.message);
    }
  });
  return { loadChat };
}
