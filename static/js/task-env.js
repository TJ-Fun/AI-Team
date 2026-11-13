import { $, escapeHtml, state, api, appAlert, appPrompt } from "./core.js";

const DONE_STATES = new Set(["confirmed", "skipped"]);

function stepStatusLabel(status) {
  return { pending: "Pending", confirmed: "Confirmed", problem: "Problem", skipped: "Skipped" }[status] || status;
}

function canEditTask(task) {
  return !["Done", "Canceled"].includes(task.status);
}

function stepEditable(step) {
  return !DONE_STATES.has(step.status);
}

export function renderEnvSection(task) {
  const sec = $("#detail-env-section");
  const body = $("#detail-env-body");
  if (!sec || !body) return;
  if (task.task_type !== "env_setup") {
    sec.hidden = true;
    body.innerHTML = "";
    return;
  }
  sec.hidden = false;
  const steps = task.env_steps || [];
  const envState = task.env_state || {};
  const doneCount = steps.filter((s) => DONE_STATES.has(s.status)).length;
  const editable = canEditTask(task);
  const issue = task.env_issue;

  const issueHtml = issue
    ? `<div class="env-step env-issue">
        <div class="env-step-head"><strong>Blocked at ${escapeHtml(issue.step_id || "")}</strong><span class="env-step-status">${escapeHtml(issue.result || "problem")}</span></div>
        <p class="detail-copy"><strong>${escapeHtml(issue.step_title || "")}</strong></p>
        ${issue.note ? `<p class="detail-copy">Note: ${escapeHtml(issue.note)}</p>` : ""}
        ${issue.output ? `<pre class="env-output">${escapeHtml(issue.output)}</pre>` : ""}
        ${editable ? `<button class="primary" type="button" data-env-escalate="1">Ask PM/PO to troubleshoot</button>` : ""}
      </div>`
    : "";

  const startHtml = editable && ["Draft", "Ready"].includes(task.status)
    ? `<button class="primary" type="button" data-env-start="1">Start setup</button>`
    : "";

  const stepsHtml = steps
    .map((s, i) => {
      const isCurrent = i === (envState.current_step ?? 0);
      const showInput = editable && stepEditable(s);
      const outputBlock = s.output ? `<pre class="env-output">${escapeHtml(s.output)}</pre>` : "";
      const noteBlock = s.note ? `<p class="detail-copy">Note: ${escapeHtml(s.note)}</p>` : "";
      const inputBlock = showInput
        ? `<textarea data-env-output="${escapeHtml(s.id)}" rows="3" maxlength="20000" placeholder="Paste command output here (optional)"></textarea>
           <div class="env-step-actions">
             <button class="primary" type="button" data-env-step="${escapeHtml(s.id)}" data-env-result="confirmed">Confirm</button>
             <button class="secondary" type="button" data-env-step="${escapeHtml(s.id)}" data-env-result="skipped">Skip</button>
             <button class="secondary" type="button" data-env-step="${escapeHtml(s.id)}" data-env-result="problem">Report problem</button>
             <button class="secondary" type="button" data-env-step="${escapeHtml(s.id)}" data-env-result="change">Request change</button>
           </div>`
        : "";
      return `<div class="env-step${isCurrent ? " env-current" : ""}${s.status === "problem" ? " env-issue" : ""}">
        <div class="env-step-head">
          <strong>${i + 1}. ${escapeHtml(s.title)}</strong>
          <span class="env-step-status">${escapeHtml(stepStatusLabel(s.status))}</span>
        </div>
        <pre class="env-instructions">${escapeHtml(s.instructions)}</pre>
        ${s.expected ? `<p class="detail-copy"><strong>Expected:</strong> ${escapeHtml(s.expected)}</p>` : ""}
        ${outputBlock}
        ${noteBlock}
        ${inputBlock}
      </div>`;
    })
    .join("");

  body.innerHTML = `
    <p class="draft-note">${doneCount}/${steps.length} steps confirmed · status ${escapeHtml(task.status)}</p>
    ${startHtml}
    ${issueHtml}
    ${stepsHtml}`;
}

export function bindEnvSection({ refreshBoard } = {}) {
  $("#detail-env-body")?.addEventListener("click", async (e) => {
    const task = state.tasks.find((t) => t.id === state.detailTaskId);
    if (!task || task.task_type !== "env_setup") return;

    const startBtn = e.target.closest("[data-env-start]");
    if (startBtn) {
      startBtn.disabled = true;
      try {
        await api(`/api/tasks/${encodeURIComponent(task.id)}/env-start`, { method: "POST", body: JSON.stringify({}) });
        await refreshBoard();
      } catch (err) {
        appAlert(err.message, "Env start failed");
      } finally {
        startBtn.disabled = false;
      }
      return;
    }

    const escBtn = e.target.closest("[data-env-escalate]");
    if (escBtn) {
      const res = await appPrompt("Optional note for PM/PO", "", "Escalate env task");
      if (!res.confirmed) return;
      escBtn.disabled = true;
      try {
        const out = await api(`/api/tasks/${encodeURIComponent(task.id)}/env-escalate`, {
          method: "POST",
          body: JSON.stringify({ note: res.value || "" }),
        });
        appAlert(`Escalated to PM/PO as ${out.escalation_task_id}.`, "Env escalated");
        await refreshBoard();
      } catch (err) {
        appAlert(err.message, "Env escalation failed");
      } finally {
        escBtn.disabled = false;
      }
      return;
    }

    const stepBtn = e.target.closest("[data-env-step]");
    if (!stepBtn) return;
    const stepId = stepBtn.getAttribute("data-env-step");
    const result = stepBtn.getAttribute("data-env-result");
    const outputEl = document.querySelector(`[data-env-output="${CSS.escape(stepId)}"]`);
    const output = outputEl ? outputEl.value : "";
    let note = "";
    if (result === "problem" || result === "change") {
      const res = await appPrompt(
        result === "problem" ? "Describe the problem and what you tried" : "Describe the decision change you need",
        "",
        result === "problem" ? "Report env problem" : "Request env change"
      );
      if (!res.confirmed) return;
      note = res.value || "";
    } else if (result === "skipped") {
      const res = await appPrompt("Why is this step being skipped? (optional)", "", "Skip env step");
      if (!res.confirmed) return;
      note = res.value || "";
    }
    stepBtn.disabled = true;
    try {
      await api(`/api/tasks/${encodeURIComponent(task.id)}/env-feedback`, {
        method: "POST",
        body: JSON.stringify({ step_id: stepId, result, output, note }),
      });
      await refreshBoard();
    } catch (err) {
      appAlert(err.message, "Env feedback failed");
    } finally {
      stepBtn.disabled = false;
    }
  });
}
