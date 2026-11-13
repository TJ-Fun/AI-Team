import {
  $, escapeHtml, state, api, appAlert, appConfirm, appPrompt, formatTime, statusClass,
} from "./core.js";
import { getAskedProvider } from "./task-utils.js";
import { renderTaskDetailActions, bindTaskDetailActions } from "./task-detail-actions.js";
import { renderEnvSection, bindEnvSection } from "./task-env.js";

export function initTaskDetail({ refreshBoard, loadChat, populateProviderSelects, openTaskDialog } = {}) {
  // --- Task Detail Dialog ---
  const detailDialog = $("#task-detail-dialog");
  $("#close-detail")?.addEventListener("click", () => {
    state.detailTaskId = null;
    detailDialog.close();
  });

  async function openTaskDetail(taskId) {
    const task = state.tasks.find((t) => t.id === taskId);
    if (!task) return;
    state.detailTaskId = taskId;
    state.detailRenderedSnapshot = JSON.stringify(task);
    await renderTaskDetailContent(task, true);
    if (!detailDialog.open) detailDialog.showModal();
  }

  async function renderTaskDetailContent(task, resetInputs = false) {
    const renderSnapshot = JSON.stringify(task);
    const asked = getAskedProvider(task);
    $("#detail-eyebrow").textContent = `${task.id} · ${task.assigned_role} · ${asked}`;
    $("#detail-title").textContent = task.title;
    const statusEl = $("#detail-status");
    statusEl.className = statusClass(task.status);
    statusEl.textContent = task.status;
    $("#detail-deliverable").hidden = !task.produces_deliverable;

    const waitingNoteEl = $("#detail-waiting-note");
    if (waitingNoteEl) {
      if (task.status === "Waiting for capacity") {
        waitingNoteEl.hidden = false;
        waitingNoteEl.textContent =
          (task.waiting_reason || "Waiting for the provider retry time.") +
          (task.retry_after ? ` · scheduled retry after ${formatTime(task.retry_after)}` : "");
      } else {
        waitingNoteEl.hidden = true;
        waitingNoteEl.textContent = "";
      }
    }

    const providerRow = $("#detail-provider-row");
    const providerSelect = $("#detail-provider-select");
    const providerFeedback = $("#detail-provider-feedback");
    if (providerRow && providerSelect) {
      const showProviderRow =
        !["clarification_question", "project_overview", "design_review", "pm_escalation", "doc_update", "roadmap_update"].includes(task.task_type);
      providerRow.hidden = !showProviderRow;
      if (showProviderRow) {
        const canChange = !["In progress", "Done", "Canceled"].includes(task.status);
        if (providerSelect.options.length <= 2) populateProviderSelects();
        providerSelect.value = asked;
        providerSelect.disabled = !canChange;
        if (resetInputs && providerFeedback) {
          providerFeedback.textContent = canChange ? "" : `Locked while ${String(task.status).toLowerCase()}`;
        } else if (!canChange && providerFeedback) {
          providerFeedback.textContent = `Locked while ${String(task.status).toLowerCase()}`;
        }
      }
    }

    $("#detail-request").textContent = task.request || "";

    const pmSec = $("#detail-pm-section");
    if (task.planning_summary) {
      pmSec.hidden = false;
      $("#detail-pm-summary").textContent = task.planning_summary;
    } else {
      pmSec.hidden = true;
    }

    // Clarification question section
    const qSec = $("#detail-question-section");
    if (task.task_type === "clarification_question") {
      qSec.hidden = false;
      const qData = task.question_data || {};
      $("#detail-question-why").textContent = qData.why || "This information helps PM/PO plan the work.";
      const qBody = $("#detail-question-body");
      if (task.status === "Needs answer") {
        if (resetInputs || !qBody.querySelector("#question-option-select")) {
          const opts = (qData.options || [])
            .map(
              (o) =>
                `<option value="${escapeHtml(o.label)}">${escapeHtml(o.label)}${
                  o.description ? ` — ${escapeHtml(o.description)}` : ""
                }</option>`
            )
            .join("");
          qBody.innerHTML = `
            <div class="question-options">
              <label>Choose an option
                <select id="question-option-select">
                  <option value="">Select an option</option>
                  ${opts}
                </select>
              </label>
              ${
                qData.allow_free_text !== false
                  ? `<label>Or add your own answer<textarea id="question-free-text" rows="3" maxlength="5000" placeholder="Optional extra detail, or your own answer"></textarea></label>`
                  : ""
              }
              <button class="primary" type="button" id="submit-question-answer">Submit answer</button>
            </div>`;
          qBody.querySelector("#submit-question-answer")?.addEventListener("click", async (ev) => {
            ev.target.disabled = true;
            try {
              const selected_option = qBody.querySelector("#question-option-select")?.value || "";
              const free_text = qBody.querySelector("#question-free-text")?.value || "";
              const res = await api(`/api/tasks/${encodeURIComponent(task.id)}/answer`, {
                method: "POST",
                body: JSON.stringify({ selected_option, free_text }),
              });
              await refreshBoard();
              if (res.followup_task) await loadChat();
            } catch (err) {
              appAlert(err.message);
            } finally {
              ev.target.disabled = false;
            }
          });
        }
      } else {
        qBody.innerHTML = `<p class="detail-copy">${escapeHtml(task.answer || "")}</p>`;
      }
    } else {
      qSec.hidden = true;
    }

    // Design review section
    const dSec = $("#detail-design-section");
    if (task.task_type === "design_review") {
      dSec.hidden = false;
      const dBody = $("#detail-design-body");
      const rd = task.review_data || {};
      const dc = rd.design_change || {};
      const alts = rd.alternatives || [];
      const summaryHtml = `
        <div class="design-review-summary">
          ${rd.summary ? `<p><strong>Summary:</strong> ${escapeHtml(rd.summary)}</p>` : ""}
          ${dc.description ? `<p><strong>Change:</strong> ${escapeHtml(dc.description)}</p>` : ""}
          ${dc.reason ? `<p><strong>Reason:</strong> ${escapeHtml(dc.reason)}</p>` : ""}
          ${rd.recommendation ? `<p><strong>PM/PO recommendation:</strong> ${escapeHtml(rd.recommendation)}</p>` : ""}
          ${(rd.team_task_ids || []).length ? `<p><strong>Provisional team tasks:</strong> ${(rd.team_task_ids || []).map((id) => escapeHtml(id)).join(", ")}</p>` : ""}
        </div>`;
      if (task.status === "Needs approval") {
        const altOptions = alts
          .map((a, i) => `<option value="${i}">${escapeHtml(a.title || `Option ${i + 1}`)}${a.description ? ` — ${escapeHtml(a.description)}` : ""}</option>`)
          .join("");
        dBody.innerHTML = `
          ${summaryHtml}
          <div class="question-options">
            ${alts.length ? `<label>Or choose an alternative<select id="design-alt-select"><option value="">Select an alternative</option>${altOptions}</select></label>` : ""}
            <label>Your feedback (required to reject)<textarea id="design-free-text" rows="3" maxlength="5000" placeholder="Optional note, or explain the rejection"></textarea></label>
            <div class="design-review-actions">
              <button class="primary" type="button" id="design-approve">Approve design change</button>
              <button class="secondary" type="button" id="design-alternative" ${alts.length ? "" : "disabled"}>Use selected alternative</button>
              <button class="secondary" type="button" id="design-reject">Reject</button>
            </div>
          </div>`;
        const submit = async (decision, altIndex) => {
          const free_text = dBody.querySelector("#design-free-text")?.value || "";
          await api(`/api/tasks/${encodeURIComponent(task.id)}/design-review`, {
            method: "POST",
            body: JSON.stringify({ decision, free_text, alternative_index: altIndex ?? null }),
          });
          await refreshBoard();
        };
        dBody.querySelector("#design-approve")?.addEventListener("click", async (ev) => {
          ev.target.disabled = true;
          try { await submit("approve"); } catch (err) { appAlert(err.message); } finally { ev.target.disabled = false; }
        });
        dBody.querySelector("#design-alternative")?.addEventListener("click", async (ev) => {
          const idx = dBody.querySelector("#design-alt-select")?.value;
          if (idx === "" || idx == null) { appAlert("Select an alternative first."); return; }
          ev.target.disabled = true;
          try { await submit("alternative", Number(idx)); } catch (err) { appAlert(err.message); } finally { ev.target.disabled = false; }
        });
        dBody.querySelector("#design-reject")?.addEventListener("click", async (ev) => {
          ev.target.disabled = true;
          try { await submit("reject"); } catch (err) { appAlert(err.message); } finally { ev.target.disabled = false; }
        });
      } else {
        const decision = task.design_decision ? `Decision: ${escapeHtml(task.design_decision)}${task.design_alternative ? ` (${escapeHtml(task.design_alternative)})` : ""}.` : "";
        dBody.innerHTML = `${summaryHtml}<p class="detail-copy">${decision}${task.design_free_text ? ` Feedback: ${escapeHtml(task.design_free_text)}` : ""}</p>`;
      }
    } else {
      dSec.hidden = true;
    }

    // Project overview section
    const ovSec = $("#detail-overview-section");
    if (task.task_type === "project_overview") {
      ovSec.hidden = false;
      const fieldsWrap = $("#detail-overview-fields");
      if (resetInputs || !fieldsWrap.querySelector("textarea")) {
        const docs = task.overview_documents || {};
        const editable = task.status === "Needs approval";
        const pairs = [
          ["project_brief", "Project brief"],
          ["technical_overview", "Technical overview"],
          ["decisions", "Decision log"],
        ];
        fieldsWrap.innerHTML = pairs
          .map(
            ([key, label]) => `
            <label class="overview-field">${escapeHtml(label)}
              <textarea data-overview-doc="${escapeHtml(key)}" rows="7" maxlength="20000" ${
                editable ? "" : "readonly"
              }>${escapeHtml(docs[key] || "")}</textarea>
            </label>`
          )
          .join("");
      }
    } else {
      ovSec.hidden = true;
    }

    // Project document / roadmap update section
    const updSec = $("#detail-update-section");
    const isUpdateTask = ["doc_update", "roadmap_update"].includes(task.task_type);
    if (isUpdateTask && task.update_payload) {
      updSec.hidden = false;
      const payload = task.update_payload;
      const entries =
        task.task_type === "doc_update"
          ? Object.entries(payload.documents || {})
          : [
              ["master_plan", payload.master_plan || ""],
              ...Object.entries(payload.phases || {}),
            ];
      const meta =
        task.task_type === "roadmap_update" && payload.active_phase != null
          ? `<p class="draft-note"><strong>Active phase:</strong> ${escapeHtml(String(payload.active_phase))}</p>`
          : "";
      $("#detail-update-body").innerHTML =
        meta +
        entries
          .map(
            ([slug, content]) => `
            <label class="overview-field">${escapeHtml(slug)}
              <textarea rows="8" readonly>${escapeHtml(content)}</textarea>
            </label>`
          )
          .join("");
    } else {
      updSec.hidden = true;
    }

    renderEnvSection(task);

    // Acceptance checks
    const checks = task.acceptance_checks || [];
    $("#detail-checks").innerHTML = checks.length
      ? `<ul>${checks.map((c) => `<li>${escapeHtml(c)}</li>`).join("")}</ul>`
      : `<p class="muted">No acceptance checks added.</p>`;

    // Validation
    if (task.validation) {
      const vChecks = (task.validation.checks || [])
        .map(
          (item) =>
            `<div class="validation-check">${item.passed === true ? "✓" : item.passed === false ? "✕" : "·"} ${escapeHtml(
              item.check
            )} — ${escapeHtml(item.message)}</div>`
        )
        .join("");
      $("#detail-validation").innerHTML = `<div class="validation-result"><strong>${escapeHtml(
        String(task.validation.status || "").replace(/_/g, " ")
      )}</strong>${vChecks}</div>`;
    } else {
      $("#detail-validation").innerHTML = `<p class="muted">No validation recorded yet.</p>`;
    }

    // PR & GitHub error
    const prSec = $("#detail-pr-section");
    if (task.pull_request?.url && String(task.pull_request.url).startsWith("https://github.com/")) {
      prSec.hidden = false;
      $("#detail-pr-copy").innerHTML = `<a href="${escapeHtml(
        task.pull_request.url
      )}" target="_blank" rel="noopener noreferrer">Open pull request #${escapeHtml(
        task.pull_request.number
      )} (${escapeHtml(task.pull_request.state)})</a>`;
    } else {
      prSec.hidden = true;
    }

    const ghErr = $("#detail-github-error");
    const repositoryErrors = [
      task.github_error ? `GitHub: ${task.github_error}` : "",
      task.ci_status === "failed" && task.ci_error ? `CI: ${task.ci_error}` : "",
      task.ci_status === "pending" ? "CI: GitHub Actions checks are still running; merge is waiting for them." : "",
      task.local_sync_error ? `Local checkout: ${task.local_sync_error}` : "",
    ].filter(Boolean);
    if (repositoryErrors.length) {
      ghErr.hidden = false;
      ghErr.textContent = repositoryErrors.join(" · ");
    } else {
      ghErr.hidden = true;
    }

    // Replies
    const repliesEl = $("#detail-replies");
    if (resetInputs) {
      repliesEl.textContent = "Loading saved replies…";
    }
    try {
      const res = await api(`/api/tasks/${encodeURIComponent(task.id)}/replies`);
      if (state.detailTaskId !== task.id || state.detailRenderedSnapshot !== renderSnapshot) return;
      const replies = res.replies || [];
      repliesEl.innerHTML = replies.length
        ? replies
            .map(
              (r) => `
            <article class="reply-card">
              <div class="reply-heading">${escapeHtml(r.provider)} · attempt ${escapeHtml(r.attempt)} · ${escapeHtml(
                r.state
              )} · ${escapeHtml(formatTime(r.at))}</div>
              <pre class="reply-text">${escapeHtml(r.text || r.error || "No response text was saved.")}</pre>
            </article>`
            )
            .join("")
        : `<span class="muted">No agent replies yet.</span>`;
    } catch (err) {
      if (state.detailTaskId === task.id && state.detailRenderedSnapshot === renderSnapshot) {
        repliesEl.textContent = `Could not load replies: ${err.message}`;
      }
    }

    // Activity
    const activity = [...(task.activity || [])].reverse();
    $("#detail-activity").innerHTML = activity.length
      ? activity
          .map(
            (a) => `
          <div class="activity-item">
            <span class="activity-dot"></span>
            <div>
              <div class="activity-title">${escapeHtml(a.message || a.type)}</div>
              <time>${escapeHtml(formatTime(a.at))}</time>
            </div>
          </div>`
          )
          .join("")
      : `<p class="muted">No activity yet.</p>`;

    renderTaskDetailActions(task);
  }

  bindTaskDetailActions({ detailDialog, refreshBoard, openTaskDialog });
  bindEnvSection({ refreshBoard });
  return { openTaskDetail, renderTaskDetailContent };
}
