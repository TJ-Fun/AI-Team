const $ = (selector) => document.querySelector(selector);
const taskList = $("#task-list");
const emptyState = $("#empty-state");
const messages = $("#messages");
let tasksById = new Map();
let taskSignature = "";
let selectedTaskId = null;
let taskPollInFlight = false;
let projectCache = [];
let githubRepoOptions = [];

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[ch]));
}

function formatTime(value) {
  try { return new Date(value).toLocaleString([], {dateStyle:"medium", timeStyle:"short"}); }
  catch { return value || ""; }
}

function elapsedSeconds(attempt) {
  if (!attempt?.started_at || !attempt?.finished_at) return null;
  const elapsed = (Date.parse(attempt.finished_at) - Date.parse(attempt.started_at)) / 1000;
  return Number.isFinite(elapsed) && elapsed >= 0 ? elapsed : null;
}

function formatDuration(seconds) {
  if (seconds == null) return "—";
  if (seconds < 60) return `${Math.round(seconds)} sec`;
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.round(seconds % 60);
  return remainder ? `${minutes} min ${remainder} sec` : `${minutes} min`;
}

function periodBucket(period, date = new Date()) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return period === "today" ? `${year}-${month}-${day}` : period === "month" ? `${year}-${month}` : period === "year" ? String(year) : "all";
}

function matchesPeriod(value, period, current = new Date()) {
  if (period === "all") return true;
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return false;
  if (period === "today") return date.toDateString() === current.toDateString();
  if (period === "month") return date.getFullYear() === current.getFullYear() && date.getMonth() === current.getMonth();
  return date.getFullYear() === current.getFullYear();
}

function providerMetrics(tasks, localCalls, localModel, resets, period) {
  const records = [];
  for (const task of tasks) {
    for (const attempt of task.attempts || []) {
      if (attempt.provider) records.push({...attempt, requested_at:attempt.started_at || attempt.finished_at});
    }
  }
  for (const call of localCalls || []) records.push({...call, provider:"Ollama", requested_at:call.started_at});
  const bucket = periodBucket(period);
  const applicableResets = (resets || []).filter(reset => reset.period === period && reset.bucket === bucket);
  const cutoff = new Map(applicableResets.map(reset => [`${reset.provider}\u0000${reset.model}`, reset.reset_at]));
  const metrics = new Map();
  const keyFor = (provider, model) => `${provider}\u0000${model}`;
  const ensureMetric = (provider, model) => {
    const key = keyFor(provider, model);
    if (!metrics.has(key)) metrics.set(key, {provider, model, requests:0, replies:0, seconds:0, timedReplies:0});
    return metrics.get(key);
  };
  for (const record of records) if (record.provider) ensureMetric(record.provider, record.model || "Model not recorded");
  for (const reset of applicableResets) ensureMetric(reset.provider, reset.model);
  ensureMetric("Ollama", localModel || "orchestrator");
  for (const record of records) {
    if (!record.provider || !matchesPeriod(record.requested_at, period)) continue;
    const model = record.model || "Model not recorded";
    const resetAt = cutoff.get(keyFor(record.provider, model));
    if (resetAt && Date.parse(record.requested_at) <= Date.parse(resetAt)) continue;
    const metric = ensureMetric(record.provider, model);
    metric.requests++;
    if (record.state === "succeeded") {
      metric.replies++;
      const duration = typeof record.duration_seconds === "number" ? record.duration_seconds : elapsedSeconds(record);
      if (duration != null) { metric.seconds += duration; metric.timedReplies++; }
    }
  }
  const rows = [...metrics.values()].sort((a,b) => a.provider.localeCompare(b.provider) || a.model.localeCompare(b.model));
  const total = rows.reduce((sum, item) => ({requests:sum.requests+item.requests, replies:sum.replies+item.replies, seconds:sum.seconds+item.seconds, timedReplies:sum.timedReplies+item.timedReplies}), {requests:0,replies:0,seconds:0,timedReplies:0});
  return {rows, total};
}

async function api(url, options = {}) {
  const response = await fetch(url, {headers:{"Content-Type":"application/json"}, ...options});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

async function loadTasks() {
  if (taskPollInFlight) return;
  taskPollInFlight = true;
  try {
    const [{tasks}, usage, notices, projectData, qualityData] = await Promise.all([api("/api/tasks"), api("/api/llm-usage"), api("/api/notifications"), api("/api/projects"), api("/api/provider-quality")]);
    projectCache = projectData.projects || [];
    const activeProject = $("#active-project").value;
    $("#active-project").innerHTML = projectCache.map(project => `<option value="${escapeHtml(project.id)}">${escapeHtml(project.name)}</option>`).join("");
    $("#active-project").value = projectCache.some(project => project.id === activeProject) ? activeProject : projectCache[0]?.id || "default";
    renderNotifications(notices.notifications || []);
    $("#provider-quality-rows").innerHTML = qualityData.quality.length ? `<div class="quality-head"><span>Role / provider</span><span>Requests / replies</span><span>QA pass</span><span>Errors</span><span>Avg time</span></div>` + qualityData.quality.map(row => `<div class="quality-row"><strong>${escapeHtml(row.role)}<small>${escapeHtml(row.provider)}</small></strong><span>${row.requests} / ${row.replies}</span><span>${row.qa_pass_rate == null ? `— (${row.measured} scored)` : `${row.qa_pass_rate}% (${row.measured})`}</span><span>${row.quota_or_service_errors}</span><span>${row.average_seconds == null ? "—" : formatDuration(row.average_seconds)}</span></div>`).join("") : `<p class="notification-empty">No task outcomes recorded yet. Deliverable QA results will appear here.</p>`;
    tasksById = new Map(tasks.map(task => [task.id, task]));
    $("#task-summary").textContent = `${tasks.length} ${tasks.length === 1 ? "task" : "tasks"}`;
    const overviewExists = tasks.some(task => task.task_type === "project_overview");
    const plannedWork = tasks.filter(task => task.task_type === "work" && task.status !== "Canceled");
    const completedWork = plannedWork.filter(task => task.status === "Done").length;
    const progress = plannedWork.length ? Math.round(completedWork * 100 / plannedWork.length) : 0;
    $("#project-progress").hidden = !overviewExists;
    $("#progress-label").textContent = `${completedWork} of ${plannedWork.length} tasks · ${progress}%`;
    $("#progress-bar").value = progress;
    $("#progress-bar").setAttribute("aria-valuenow", String(progress));
    const period = $("#stat-period").value;
    const stats = providerMetrics(tasks, usage.local_calls, usage.local_model, usage.resets, period);
    const statLine = metric => `<span>${metric.requests} ${metric.requests === 1 ? "request" : "requests"}</span><span>${metric.replies} ${metric.replies === 1 ? "reply" : "replies"}</span><span>avg ${formatDuration(metric.timedReplies ? metric.seconds / metric.timedReplies : null)}</span>`;
    $("#provider-statistics").hidden = false;
    $("#provider-stat-rows").innerHTML = `<div class="provider-stat-row provider-stat-total"><strong>Total</strong>${statLine(stats.total)}<span></span></div>` + stats.rows.map(metric => `
      <div class="provider-stat-row"><div class="provider-stat-model"><strong>${escapeHtml(metric.provider)}</strong><small>${escapeHtml(metric.model)}</small></div>${statLine(metric)}<button type="button" class="stat-reset" data-stat-reset data-provider="${escapeHtml(metric.provider)}" data-model="${escapeHtml(metric.model)}">Reset</button></div>`).join("");
    $("#board-updated").textContent = `Updated ${new Date().toLocaleTimeString([], {hour:"2-digit", minute:"2-digit", second:"2-digit"})} · refreshes every 5 seconds`;
    const signature = JSON.stringify(tasks.map(task => [task.id, task.updated_at, task.status, task.latest_reply_file, task.project_id, task.module_id, task.pull_request?.url, task.github_error]));
    if (signature === taskSignature) return;
    taskSignature = signature;
    emptyState.hidden = tasks.length !== 0;
    taskList.innerHTML = tasks.map(task => `
      <article class="task-card">
        <div class="task-topline"><span class="state state-${task.status.toLowerCase().replaceAll(" ", "-")}">${escapeHtml(task.status)}</span><span class="task-id">${escapeHtml(task.id)}</span><time>${escapeHtml(formatTime(task.created_at))}</time></div>
        <h3>${escapeHtml(task.title)}</h3>
        ${(() => {const project = projectCache.find(item => item.id === (task.project_id || "default")); const module = project?.modules?.find(item => item.id === task.module_id); return project ? `<div class="task-scope">${escapeHtml(project.name)}${module ? ` / ${escapeHtml(module.name)}` : ""}</div>` : "";})()}
        <p class="task-request">${escapeHtml(task.request)}</p>
        ${(() => { const attempts = task.attempts || []; const successful = [...attempts].reverse().find(item => item.state === "succeeded"); const duration = successful ? elapsedSeconds(successful) : null; const asked = task.provider || (task.assigned_role === "OpenRouter free agent" ? "OpenRouter" : task.assigned_role === "Gemini" ? "Gemini" : "Automatic"); const sentTo = [...new Set(attempts.map(item => item.provider).filter(Boolean))]; const route = sentTo.length ? `${asked}${asked === "Automatic" ? ` (sent to ${sentTo.join(", ")})` : ""}` : asked; return `<div class="task-ai-info"><span>Requested: ${escapeHtml(route)}</span><span>${successful ? `Answered by: ${escapeHtml(successful.provider)} · ${formatDuration(duration)}` : attempts.length ? "No answer yet" : "Not sent yet"}</span>${task.pull_request?.url && task.pull_request.url.startsWith("https://github.com/") ? `<span><a href="${escapeHtml(task.pull_request.url)}" target="_blank" rel="noopener noreferrer">PR #${escapeHtml(task.pull_request.number)} · ${escapeHtml(task.pull_request.state)}</a></span>` : ""}</div>`; })()}
        ${task.status === "Ready" && task.dispatch_note ? `<p class="queue-note">${escapeHtml(task.dispatch_note)}</p>` : ""}
        ${task.status === "Waiting for capacity" ? `<p class="queue-note">${escapeHtml(task.waiting_reason || "Waiting for the provider retry time.")}${task.retry_after ? ` · retry after ${escapeHtml(formatTime(task.retry_after))}` : ""}</p>` : ""}
        ${task.status === "Blocked" && task.block_reason ? `<p class="queue-note blocked-note">${escapeHtml(task.block_reason)}</p>` : ""}
        <div class="task-footer"><span class="role-icon">${escapeHtml(task.assigned_role)} · ${escapeHtml(task.provider || (task.assigned_role === "OpenRouter free agent" ? "OpenRouter" : task.assigned_role === "Gemini" ? "Gemini" : "Automatic"))}</span><span>${(task.acceptance_checks || []).length} acceptance ${(task.acceptance_checks || []).length === 1 ? "check" : "checks"}</span><div class="task-card-actions">${task.status === "Draft" ? `<button class="primary card-ready" type="button" data-card-ready="${escapeHtml(task.id)}">Mark ready</button>` : ""}<button class="danger card-delete" type="button" data-card-delete="${escapeHtml(task.id)}">Delete</button></div><button class="text-button" data-open-task="${escapeHtml(task.id)}">Details</button></div>
      </article>`).join("");
    if (selectedTaskId && $("#task-detail-dialog").open && tasksById.has(selectedTaskId)) {
      renderTaskDetails(tasksById.get(selectedTaskId));
    }
  } catch (error) {
    taskList.innerHTML = `<div class="inline-error">Could not load tasks: ${escapeHtml(error.message)}</div>`;
    $("#board-updated").textContent = "Board refresh failed; retrying…";
  } finally { taskPollInFlight = false; }
}

function renderNotifications(items) {
  const box = $("#notification-list");
  $("#notification-count").textContent = items.length ? `${items.length} need attention` : "All clear";
  box.innerHTML = items.length ? items.map(item => `<div class="notification notification-${escapeHtml(item.severity)}"><div><strong>${escapeHtml(item.title)}</strong><p>${escapeHtml(item.message)}</p></div><button type="button" class="notification-dismiss" aria-label="Dismiss" data-dismiss-notification="${escapeHtml(item.id)}">×</button></div>`).join("") : `<p class="notification-empty">No current issues or provider suggestions.</p>`;
}

$("#notification-list").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-dismiss-notification]");
  if (!button) return;
  try { await api(`/api/notifications/${encodeURIComponent(button.dataset.dismissNotification)}`, {method:"DELETE"}); await loadTasks(); }
  catch (error) { alert(error.message); }
});

$("#stat-period").addEventListener("change", loadTasks);
const statisticsDialog = $("#statistics-dialog");
$("#open-statistics").addEventListener("click", () => statisticsDialog.showModal());
$("#close-statistics").addEventListener("click", () => statisticsDialog.close());
$("#provider-stat-rows").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-stat-reset]");
  if (!button) return;
  const period = $("#stat-period").value;
  const periodLabel = $("#stat-period").selectedOptions[0].textContent;
  const provider = button.dataset.provider;
  const model = button.dataset.model;
  if (!confirm(`Reset the displayed ${periodLabel} statistic for ${provider} · ${model}? Past usage history will be kept.`)) return;
  button.disabled = true;
  try {
    await api("/api/llm-usage/reset", {method:"POST", body:JSON.stringify({provider, model, period, bucket:periodBucket(period)})});
    await loadTasks();
  } catch (error) { alert(error.message); button.disabled = false; }
});

function renderTaskDetails(task) {
  const detail = $("#task-detail");
  const sameTask = selectedTaskId === task.id;
  const priorChoice = detail.querySelector("#question-option")?.value || "";
  const priorAnswerText = detail.querySelector("#question-free-text")?.value || "";
  const priorOverviewValues = sameTask ? Object.fromEntries([...detail.querySelectorAll("[data-overview-doc]")].map(field => [field.dataset.overviewDoc, field.value])) : {};
  const checks = (task.acceptance_checks || []).length
    ? `<ul>${task.acceptance_checks.map(check => `<li>${escapeHtml(check)}</li>`).join("")}</ul>`
    : `<p class="muted">No acceptance checks added.</p>`;
  const history = (task.activity || []).slice().reverse().map(item => `
    <div class="activity-item"><span class="activity-dot"></span><div><div class="activity-title">${escapeHtml(item.message || item.type)}</div><time>${escapeHtml(formatTime(item.at))}</time></div></div>`).join("");
  const validation = task.validation
    ? `<div class="validation-result"><strong>${escapeHtml(task.validation.status.replaceAll("_", " "))}</strong>${(task.validation.checks || []).map(item => `<div class="validation-check">${item.passed === true ? "✓" : item.passed === false ? "✕" : "·"} ${escapeHtml(item.check)} — ${escapeHtml(item.message)}</div>`).join("")}</div>`
    : `<p class="muted">No validation recorded yet.</p>`;
  const questionData = task.question_data || {};
  const questionPanel = task.task_type === "clarification_question"
    ? `<h3>Why PM/PO is asking</h3><p class="question-why">${escapeHtml(questionData.why || "This information helps PM/PO plan the work.")}</p>${task.status === "Needs answer"
      ? `<h3>Your answer</h3><div class="question-options"><label>Choose an option<select id="question-option"><option value="">Select an option</option>${(questionData.options || []).map(option => `<option value="${escapeHtml(option.label)}">${escapeHtml(option.label)}${option.description ? ` — ${escapeHtml(option.description)}` : ""}</option>`).join("")}</select></label>${questionData.allow_free_text !== false ? `<label>Or add your own answer<textarea id="question-free-text" rows="3" maxlength="5000" placeholder="Optional extra detail, or your own answer"></textarea></label>` : ""}<button class="primary" type="button" data-answer-question="${escapeHtml(task.id)}">Submit answer</button></div>`
      : `<h3>Your answer</h3><p class="detail-copy">${escapeHtml(task.answer || "")}</p>`}`
    : "";
  const overviewDocuments = task.overview_documents || {};
  const overviewFields = [
    ["project_brief", "Project brief"],
    ["technical_overview", "Technical overview"],
    ["decisions", "Decision log"],
  ];
  const overviewPanel = task.task_type === "project_overview"
    ? `<h3>Proposed Project context</h3><p class="draft-note">Edit any document before approval. Approval replaces these three context documents; confirmed outcomes are kept. Previous versions are saved for rollback.</p>${overviewFields.map(([key,label]) => `<label class="overview-field">${label}<textarea data-overview-doc="${key}" rows="7" maxlength="20000" ${task.status === "Needs approval" ? "" : "readonly"}>${escapeHtml(overviewDocuments[key] || "")}</textarea></label>`).join("")}`
    : "";
  const overviewActions = task.task_type === "project_overview" && task.status === "Needs approval"
    ? `<button class="secondary" type="button" data-save-overview="${escapeHtml(task.id)}">Save edits</button><button class="primary" type="button" data-approve-overview="${escapeHtml(task.id)}">Approve and update Project context</button>`
    : "";
  const actions = [];
  if (["Draft", "Blocked", "Rework needed"].includes(task.status)) actions.push(["edit", "Edit task", "secondary"]);
  if (task.status === "Draft") actions.push(["Ready", "Mark Ready", "primary"]);
  if (["Draft", "Ready"].includes(task.status)) actions.push(["Blocked", "Block", "secondary"]);
  if (["Draft", "Ready", "Blocked"].includes(task.status)) actions.push(["Canceled", "Cancel task", "secondary"]);
  if (task.status === "Blocked") actions.unshift(["Draft", "Return to Draft", "secondary"]);
  if (task.status === "Blocked") actions.splice(1, 0, ["Ready", "Mark Ready", "primary"]);
  if (task.status === "Rework needed") actions.unshift(["Ready", "Return to agent queue", "primary"]);
  detail.innerHTML = `
    <div class="dialog-heading"><div><p class="eyebrow">${escapeHtml(task.id)} · ${escapeHtml(task.assigned_role)} · ${escapeHtml(task.provider || (task.assigned_role === "OpenRouter free agent" ? "OpenRouter" : task.assigned_role === "Gemini" ? "Gemini" : "Automatic"))}</p><h2>${escapeHtml(task.title)}</h2></div><button class="icon-button" data-close-detail aria-label="Close">×</button></div>
    <div class="detail-status"><span class="state state-${task.status.toLowerCase().replaceAll(" ", "-")}">${escapeHtml(task.status)}</span>${task.produces_deliverable ? `<span class="deliverable-tag">Deliverable · validation required</span>` : ""}</div>
    <h3>Request</h3><p class="detail-copy">${escapeHtml(task.request)}</p>
    ${task.planning_summary ? `<h3>PM/PO summary</h3><p class="detail-copy">${escapeHtml(task.planning_summary)}</p>` : ""}
    ${questionPanel}
    ${overviewPanel}
    <h3>Acceptance checks</h3>${checks}
    <h3>Validation</h3>${validation}
    ${task.pull_request?.url && task.pull_request.url.startsWith("https://github.com/") ? `<h3>GitHub pull request</h3><p class="detail-copy"><a href="${escapeHtml(task.pull_request.url)}" target="_blank" rel="noopener noreferrer">Open pull request #${escapeHtml(task.pull_request.number)} (${escapeHtml(task.pull_request.state)})</a></p>` : ""}
    ${task.github_error ? `<p class="queue-note blocked-note">GitHub: ${escapeHtml(task.github_error)}</p>` : ""}
    <h3>Agent replies</h3><div class="agent-replies" id="agent-replies">Loading saved replies…</div>
    <h3>Activity</h3><div class="activity-list">${history || `<p class="muted">No activity yet.</p>`}</div>
    <div class="detail-actions">${actions.map(([status,label,kind]) => `<button class="${kind}" data-task-action="${status}" data-task-id="${escapeHtml(task.id)}">${label}</button>`).join("")}${overviewActions}<button class="secondary" type="button" data-close-detail>Close</button><button class="danger" data-delete-task="${escapeHtml(task.id)}">Delete task</button></div>`;
  if (task.task_type === "clarification_question" && task.status === "Needs answer") {
    detail.querySelector("#question-option").value = priorChoice;
    const freeText = detail.querySelector("#question-free-text");
    if (freeText) freeText.value = priorAnswerText;
  }
  if (task.task_type === "project_overview" && task.status === "Needs approval" && sameTask) {
    for (const field of detail.querySelectorAll("[data-overview-doc]")) {
      if (Object.hasOwn(priorOverviewValues, field.dataset.overviewDoc)) field.value = priorOverviewValues[field.dataset.overviewDoc];
    }
  }
  if (task.status === "Needs validation" && task.produces_deliverable) {
    detail.querySelector(".detail-actions").insertAdjacentHTML("afterbegin", `<button class="secondary" data-validation-action="rework" data-task-id="${escapeHtml(task.id)}">Request rework</button><button class="primary" data-validation-action="pass" data-task-id="${escapeHtml(task.id)}">Approve deliverable</button>`);
  }
  selectedTaskId = task.id;
  if (!$("#task-detail-dialog").open) $("#task-detail-dialog").showModal();
  loadTaskReplies(task.id);
}

async function loadTaskReplies(taskId) {
  const container = $("#agent-replies");
  try {
    const {replies} = await api(`/api/tasks/${encodeURIComponent(taskId)}/replies`);
    container.replaceChildren();
    if (!replies.length) { container.textContent = "No agent replies yet."; container.classList.add("muted"); return; }
    for (const reply of replies) {
      const card = document.createElement("article");
      card.className = "reply-card";
      const heading = document.createElement("div");
      heading.className = "reply-heading";
      heading.textContent = `${reply.provider} · attempt ${reply.attempt} · ${reply.state} · ${formatTime(reply.at)}`;
      const text = document.createElement("pre");
      text.className = "reply-text";
      text.textContent = reply.text || reply.error || "No response text was saved.";
      card.append(heading, text);
      container.append(card);
    }
  } catch (error) { container.textContent = `Could not load replies: ${error.message}`; }
}

taskList.addEventListener("click", async (event) => {
  const deleteButton = event.target.closest("[data-card-delete]");
  if (deleteButton) {
    if (!confirm("Permanently delete this task, its agent replies, and its task activity log entries? The overall chat conversation will remain. This cannot be undone.")) return;
    deleteButton.disabled = true;
    try {
      await api(`/api/tasks/${encodeURIComponent(deleteButton.dataset.cardDelete)}`, {method:"DELETE"});
      await loadTasks();
    } catch (error) { alert(error.message); deleteButton.disabled = false; }
    return;
  }
  const readyButton = event.target.closest("[data-card-ready]");
  if (readyButton) {
    readyButton.disabled = true;
    try {
      await api(`/api/tasks/${encodeURIComponent(readyButton.dataset.cardReady)}/status`, {method:"POST", body:JSON.stringify({status:"Ready"})});
      await loadTasks();
    } catch (error) { alert(error.message); readyButton.disabled = false; }
    return;
  }
  const button = event.target.closest("[data-open-task]");
  if (button && tasksById.has(button.dataset.openTask)) renderTaskDetails(tasksById.get(button.dataset.openTask));
});

$("#task-detail").addEventListener("click", async (event) => {
  if (event.target.closest("[data-close-detail]")) return $("#task-detail-dialog").close();
  const saveOverviewButton = event.target.closest("[data-save-overview]");
  const approveOverviewButton = event.target.closest("[data-approve-overview]");
  const overviewButton = saveOverviewButton || approveOverviewButton;
  if (overviewButton) {
    if (approveOverviewButton && !confirm("Approve this overview and replace Project brief, Technical overview, and Decision log? Current versions will be saved in history; Confirmed outcomes are preserved.")) return;
    const documents = Object.fromEntries([...$("#task-detail").querySelectorAll("[data-overview-doc]")].map(field => [field.dataset.overviewDoc, field.value]));
    overviewButton.disabled = true;
    try {
      const taskId = overviewButton.dataset.saveOverview || overviewButton.dataset.approveOverview;
      const url = approveOverviewButton ? `/api/tasks/${encodeURIComponent(taskId)}/approve-overview` : `/api/tasks/${encodeURIComponent(taskId)}/overview`;
      await api(url, {method:approveOverviewButton ? "POST" : "PUT", body:JSON.stringify({documents})});
      await loadTasks();
    } catch (error) { alert(error.message); overviewButton.disabled = false; }
    return;
  }
  const answerButton = event.target.closest("[data-answer-question]");
  if (answerButton) {
    answerButton.disabled = true;
    try {
      const {followup_task} = await api(`/api/tasks/${encodeURIComponent(answerButton.dataset.answerQuestion)}/answer`, {
        method:"POST", body:JSON.stringify({selected_option:$("#question-option").value, free_text:$("#question-free-text")?.value || ""})
      });
      await loadTasks();
      if (followup_task) await loadChat();
    } catch (error) { alert(error.message); answerButton.disabled = false; }
    return;
  }
  const deleteButton = event.target.closest("[data-delete-task]");
  if (deleteButton) {
    const confirmed = confirm("Permanently delete this task, its agent replies, and its task activity log entries? The overall chat conversation will remain. This cannot be undone.");
    if (!confirmed) return;
    deleteButton.disabled = true;
    try {
      await api(`/api/tasks/${encodeURIComponent(deleteButton.dataset.deleteTask)}`, {method:"DELETE"});
      $("#task-detail-dialog").close();
      await loadTasks();
    } catch (error) { alert(error.message); deleteButton.disabled = false; }
    return;
  }
  const validationButton = event.target.closest("[data-validation-action]");
  if (validationButton) {
    const decision = validationButton.dataset.validationAction;
    let notes = "";
    if (decision === "pass" && !confirm("Approve this deliverable and mark the task Done?")) return;
    if (decision === "rework") {
      const entered = prompt("What should the agent fix before you review it again?");
      if (entered === null) return;
      notes = entered.trim();
      if (!notes) return alert("Please describe what needs to change.");
    }
    validationButton.disabled = true;
    try {
      await api(`/api/tasks/${encodeURIComponent(validationButton.dataset.taskId)}/validation`, {method:"POST", body:JSON.stringify({decision, notes})});
      await loadTasks();
    } catch (error) { alert(error.message); validationButton.disabled = false; }
    return;
  }
  const button = event.target.closest("[data-task-action]");
  if (!button) return;
  const target = button.dataset.taskAction;
  if (target === "edit") {
    const task = tasksById.get(button.dataset.taskId);
    if (task) {
      $("#task-detail-dialog").close();
      openTaskDialog(task);
    }
    return;
  }
  let note = "";
  if (target === "Canceled" && !confirm("Cancel this task? This cannot be undone.")) return;
  if (target === "Blocked") {
    const entered = prompt("Why is this task blocked? (Optional)");
    if (entered === null) return;
    note = entered;
  }
  button.disabled = true;
  try {
    await api(`/api/tasks/${encodeURIComponent(button.dataset.taskId)}/status`, {method:"POST", body:JSON.stringify({status:target, note})});
    $("#task-detail-dialog").close();
    await loadTasks();
  } catch (error) {
    alert(error.message);
    button.disabled = false;
  }
});

function addMessage(message) {
  const bubble = document.createElement("div");
  bubble.className = `message ${message.role === "user" ? "user-message" : "assistant-message"}`;
  const who = document.createElement("div");
  who.className = "message-role";
  who.textContent = message.role === "user" ? "You" : "Orchestrator";
  const content = document.createElement("div");
  content.className = "message-content";
  content.textContent = message.content;
  bubble.append(who, content);
  messages.append(bubble);
}

$("#task-detail-dialog").addEventListener("close", () => { selectedTaskId = null; });

async function updateStatusMeter() {
  try {
    const data = await api("/api/status");
    const {cpu_percent, llm_working, gemini_working, gemini_configured, openrouter_working, openrouter_configured, watchdog} = data;
    const value = $("#cpu-value");
    const track = $(".cpu-track");
    const bar = $("#cpu-bar");
    value.textContent = cpu_percent === null ? "…" : `${cpu_percent}%`;
    bar.style.width = `${cpu_percent === null ? 0 : cpu_percent}%`;
    track.setAttribute("aria-valuenow", cpu_percent === null ? "0" : String(cpu_percent));
    $("#llm-state").textContent = openrouter_working ? "Hosted AI working" : gemini_working ? "Gemini working" : llm_working ? "Orchestrator working" : "Idle";
    $("#llm-state").classList.toggle("working", llm_working || gemini_working || openrouter_working);
    $("#clear-chat").disabled = llm_working;
    $("#agent-status").textContent = `Gemini / Antigravity ${gemini_configured ? "connected" : "not connected"} · GroqCloud ${data.groq_configured ? "connected" : "not connected"} · OpenRouter ${openrouter_configured ? "connected" : "not connected"} · GitHub ${data.github_configured ? "connected" : "not connected"}`;
    if (watchdog?.error) $("#watchdog-info").textContent = "Watchdog check had an error";
    else if (watchdog?.last_run) $("#watchdog-info").textContent = `Board checked ${formatTime(watchdog.last_run)} · every 2 hours`;
  } catch { $("#cpu-value").textContent = "—"; }
}

async function loadChat() {
  try {
    const {messages: history} = await api("/api/chat");
    messages.replaceChildren();
    if (history.length === 0) {
      const welcome = document.createElement("div");
      welcome.className = "welcome-message";
      welcome.innerHTML = `<div class="welcome-icon">✳</div><h3>Talk through the work</h3><p>Ask for help clarifying a request, planning acceptance checks, or understanding the board.</p>`;
      messages.append(welcome);
    } else history.forEach(addMessage);
    messages.scrollTop = messages.scrollHeight;
  } catch (error) {
    messages.innerHTML = `<div class="inline-error">Could not load chat history: ${escapeHtml(error.message)}</div>`;
  }
}

function openTaskDialog(task = null) {
  const form = $("#task-form");
  form.reset();
  form.dataset.editId = task?.id || "";
  $("#form-error").textContent = "";
  $("#task-dialog-title").textContent = task ? "Edit task" : "New task";
  form.querySelector('[type="submit"]').textContent = task ? "Save changes" : "Save draft";
  form.elements.project_id.innerHTML = projectCache.map(project => `<option value="${escapeHtml(project.id)}">${escapeHtml(project.name)}</option>`).join("");
  const defaultProjectId = task?.project_id || $("#active-project").value;
  form.elements.project_id.value = projectCache.some(project => project.id === defaultProjectId) ? defaultProjectId : projectCache[0]?.id || "default";
  updateModuleOptions(form.elements.project_id.value, task?.module_id || "");
  if (task) {
    form.elements.title.value = task.title;
    form.elements.request.value = task.request;
    form.elements.role.value = ["PM/PO", "Frontend developer", "Backend developer", "QA", "UI designer", "Documentation"].includes(task.assigned_role) ? task.assigned_role : "PM/PO";
    form.elements.provider.value = task.provider || (task.assigned_role === "Gemini" ? "Gemini" : task.assigned_role === "OpenRouter free agent" ? "OpenRouter" : "Automatic");
    form.elements.checks.value = (task.acceptance_checks || []).join("\n");
    form.elements.produces_deliverable.checked = Boolean(task.produces_deliverable);
  }
  $("#task-dialog").showModal();
}
function updateModuleOptions(projectId, selected = "") {
  const select = $("#task-form").elements.module_id;
  const project = projectCache.find(item => item.id === projectId);
  select.innerHTML = `<option value="">Whole project</option>` + (project?.modules || []).map(module => `<option value="${escapeHtml(module.id)}">${escapeHtml(module.name)}${module.path ? ` · ${escapeHtml(module.path)}` : ""}</option>`).join("");
  select.value = selected;
}
$("#task-form").elements.project_id.addEventListener("change", event => updateModuleOptions(event.target.value));
$("#new-task").addEventListener("click", openTaskDialog);
$("#empty-new-task").addEventListener("click", openTaskDialog);
$("#close-dialog").addEventListener("click", () => $("#task-dialog").close());
$("#cancel-task").addEventListener("click", () => $("#task-dialog").close());

$("#task-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const taskForm = event.currentTarget;
  const form = new FormData(taskForm);
  const button = taskForm.querySelector('[type="submit"]');
  button.disabled = true;
  $("#form-error").textContent = "";
  try {
    const editId = taskForm.dataset.editId;
    const payload = {
      title:form.get("title"), request:form.get("request"), role:form.get("role"), provider:form.get("provider"),
      acceptance_checks:String(form.get("checks") || "").split("\n"),
      project_id:form.get("project_id"), module_id:form.get("module_id"),
      produces_deliverable:form.get("produces_deliverable") === "on"
    };
    await api(editId ? `/api/tasks/${encodeURIComponent(editId)}` : "/api/tasks", {
      method:editId ? "PUT" : "POST", body:JSON.stringify(payload)
    });
    taskForm.reset();
    taskForm.dataset.editId = "";
    $("#task-dialog").close();
    await loadTasks();
  } catch (error) { $("#form-error").textContent = error.message; }
  finally { button.disabled = false; }
});

$("#chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = $("#chat-input");
  const button = $("#send-button");
  const text = input.value.trim();
  if (!text) return;
  addMessage({role:"user", content:text});
  input.value = "";
  button.disabled = true;
  const pending = document.createElement("div");
  pending.className = "message assistant-message pending-message";
  pending.textContent = "Orchestrator is thinking…";
  messages.append(pending);
  messages.scrollTop = messages.scrollHeight;
  try {
    const {reply} = await api("/api/chat", {method:"POST", body:JSON.stringify({message:text})});
    pending.remove();
    addMessage(reply);
  } catch (error) {
    pending.remove();
    addMessage({role:"assistant", content:`I couldn’t reach Ollama: ${error.message}`});
  } finally {
    button.disabled = false;
    input.focus();
    messages.scrollTop = messages.scrollHeight;
  }
});

$("#pm-button").addEventListener("click", async () => {
  const input = $("#chat-input");
  const button = $("#pm-button");
  const request = input.value.trim();
  if (!request) return alert("Describe the project request first.");
  button.disabled = true;
  const pending = document.createElement("div");
  pending.className = "message assistant-message pending-message";
  pending.textContent = "Sending the project request to PM/PO…";
  messages.append(pending);
  messages.scrollTop = messages.scrollHeight;
  try {
    await api("/api/pm-requests", {method:"POST", body:JSON.stringify({request, project_id:$("#active-project").value})});
    input.value = "";
    pending.remove();
    await Promise.all([loadChat(), loadTasks()]);
  } catch (error) {
    pending.remove();
    alert(`Could not send the request to PM/PO: ${error.message}`);
  } finally { button.disabled = false; input.focus(); }
});

$("#clear-chat").addEventListener("click", async () => {
  if (!confirm("Clear the saved Orchestrator conversation? This keeps all tasks and agent reply files.")) return;
  const button = $("#clear-chat");
  button.disabled = true;
  try {
    await api("/api/chat", {method:"DELETE"});
    await loadChat();
  } catch (error) {
    alert(error.message);
  } finally { button.disabled = false; }
});

const promptDialog = $("#prompt-dialog");
const promptForm = $("#prompt-form");
let promptCache = new Map();
$("#open-prompts").addEventListener("click", async () => {
  $("#prompt-error").textContent = "";
  try {
    const {prompts} = await api("/api/prompts");
    promptCache = new Map(prompts.map(prompt => [prompt.id, prompt]));
    promptForm.elements.prompt_id.innerHTML = prompts.map(prompt => `<option value="${escapeHtml(prompt.id)}">${escapeHtml(prompt.title)}</option>`).join("");
    promptForm.elements.prompt_id.value = "orchestrator";
    promptForm.elements.content.value = promptCache.get("orchestrator")?.content || "";
    promptDialog.showModal();
  } catch (error) { alert(`Could not load prompts: ${error.message}`); }
});
promptForm.elements.prompt_id.addEventListener("change", () => {
  promptForm.elements.content.value = promptCache.get(promptForm.elements.prompt_id.value)?.content || "";
});
$("#close-prompts").addEventListener("click", () => promptDialog.close());
$("#cancel-prompts").addEventListener("click", () => promptDialog.close());
promptForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const id = promptForm.elements.prompt_id.value;
  const button = promptForm.querySelector('[type="submit"]');
  button.disabled = true;
  $("#prompt-error").textContent = "";
  try {
    const {content, title} = await api(`/api/prompts/${encodeURIComponent(id)}`, {
      method:"PUT", body:JSON.stringify({content:promptForm.elements.content.value})
    });
    promptCache.set(id, {id, title, content});
    promptDialog.close();
  } catch (error) { $("#prompt-error").textContent = error.message; }
  finally { button.disabled = false; }
});

const contextDialog = $("#context-dialog");
const contextForm = $("#context-form");
let contextCache = new Map();
$("#open-context").addEventListener("click", async () => {
  $("#context-error").textContent = "";
  try {
    const {documents} = await api("/api/project-context");
    contextCache = new Map(documents.map(document => [document.id, document]));
    contextForm.elements.document_id.innerHTML = documents.map(document => `<option value="${escapeHtml(document.id)}">${escapeHtml(document.title)}</option>`).join("");
    contextForm.elements.document_id.value = "project_brief";
    contextForm.elements.content.value = contextCache.get("project_brief")?.content || "";
    await loadContextHistory("project_brief");
    contextDialog.showModal();
  } catch (error) { alert(`Could not load project context: ${error.message}`); }
});
contextForm.elements.document_id.addEventListener("change", async () => {
  const id = contextForm.elements.document_id.value;
  contextForm.elements.content.value = contextCache.get(id)?.content || "";
  await loadContextHistory(id);
});
$("#close-context").addEventListener("click", () => contextDialog.close());
$("#cancel-context").addEventListener("click", () => contextDialog.close());
contextForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const id = contextForm.elements.document_id.value;
  const button = contextForm.querySelector('[type="submit"]');
  button.disabled = true;
  $("#context-error").textContent = "";
  try {
    const {content, title} = await api(`/api/project-context/${encodeURIComponent(id)}`, {
      method:"PUT", body:JSON.stringify({content:contextForm.elements.content.value})
    });
    contextCache.set(id, {id, title, content});
    await loadContextHistory(id);
  } catch (error) { $("#context-error").textContent = error.message; }
  finally { button.disabled = false; }
});

async function loadContextHistory(id) {
  const select = $("#context-history");
  const restoreButton = $("#restore-context");
  const deleteButton = $("#delete-context-version");
  const previousSelection = select.value;
  const {history} = await api(`/api/project-context/${encodeURIComponent(id)}/history`);
  select.innerHTML = history.length
    ? history.map(item => `<option value="${escapeHtml(item.id)}">${escapeHtml(formatTime(item.created_at))} · ${escapeHtml(item.preview || "Saved version")}</option>`).join("")
    : `<option value="">No saved versions</option>`;
  if (history.some(item => item.id === previousSelection)) select.value = previousSelection;
  restoreButton.disabled = history.length === 0;
  deleteButton.disabled = history.length === 0;
}

$("#context-history").addEventListener("change", () => {
  const hasSelection = Boolean($("#context-history").value);
  $("#restore-context").disabled = !hasSelection;
  $("#delete-context-version").disabled = !hasSelection;
});

$("#restore-context").addEventListener("click", async () => {
  const id = contextForm.elements.document_id.value;
  const snapshotId = $("#context-history").value;
  if (!snapshotId) return;
  if (!confirm("Restore this saved version? The current document will first be saved as a new history version.")) return;
  const button = $("#restore-context");
  button.disabled = true;
  try {
    const restored = await api(`/api/project-context/${encodeURIComponent(id)}/restore`, {method:"POST", body:JSON.stringify({snapshot_id:snapshotId})});
    contextForm.elements.content.value = restored.content;
    contextCache.set(id, {id, title:restored.title, content:restored.content});
    await loadContextHistory(id);
  } catch (error) { alert(error.message); }
  finally { button.disabled = false; }
});

$("#delete-context-version").addEventListener("click", async () => {
  const id = contextForm.elements.document_id.value;
  const snapshotId = $("#context-history").value;
  if (!snapshotId) return;
  const selectedLabel = $("#context-history").selectedOptions[0]?.textContent || snapshotId;
  if (!confirm(`Permanently delete this saved version?\n\n${selectedLabel}\n\nThe current Project context will not change. This saved version cannot be restored after deletion.`)) return;
  const button = $("#delete-context-version");
  button.disabled = true;
  try {
    await api(`/api/project-context/${encodeURIComponent(id)}/history/${encodeURIComponent(snapshotId)}`, {method:"DELETE"});
    await loadContextHistory(id);
  } catch (error) { alert(error.message); }
  finally { button.disabled = !$("#context-history").value; }
});

$("#chat-input").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); $("#chat-form").requestSubmit(); }
});

api("/api/config").then(({model, available, model_found}) => {
  if (!available) { $("#model-name").textContent = "Ollama unavailable"; $(".status-dot").classList.add("offline"); }
  else if (!model_found) { $("#model-name").textContent = `Model not found · ${model}`; $(".status-dot").classList.add("offline"); }
  else { $("#model-name").textContent = `Ollama · ${model}`; }
}).catch(() => { $("#model-name").textContent = "Ollama unavailable"; $(".status-dot").classList.add("offline"); });
loadTasks();
loadChat();
updateStatusMeter();
setInterval(loadTasks, 5000);
setInterval(updateStatusMeter, 2000);

const settingsDialog = $("#settings-dialog");
const connectionLabels = {gemini:"Gemini API key", openrouter:"OpenRouter free API key", groq:"GroqCloud API key", github:"GitHub fine-grained token"};
$("#open-settings").addEventListener("click", async () => {
  $("#settings-error").textContent = "";
  try {
    const [{connections, encryption_available}, {roles, providers, routing}] = await Promise.all([api("/api/connections"), api("/api/routing")]);
    if (!encryption_available) throw new Error("Install python3-cryptography on the VM before adding saved credentials.");
    $("#connection-fields").innerHTML = connections.map(item => `<label>${escapeHtml(item.name)}<input type="password" class="connection-key" data-connection="${escapeHtml(item.id)}" autocomplete="new-password" placeholder="${item.configured ? `Connected · ${escapeHtml(item.source)} · paste to replace` : "Not connected · paste key"}"><small class="muted">${item.configured ? "Saved value is hidden." : "Stored encrypted on the VM."}</small></label>`).join("");
    $("#routing-fields").innerHTML = roles.map(role => `<section class="route-role"><strong>${escapeHtml(role)}</strong><div class="route-order">${routing[role].map((provider, index) => `<label>${index+1}<select data-route-role="${escapeHtml(role)}" data-route-rank="${index}">${providers.map(option => `<option value="${escapeHtml(option)}" ${option===provider?"selected":""}>${escapeHtml(option)}${!connections.some(c => c.configured && ((option==="Gemini"||option==="Antigravity")&&c.id==="gemini"||option==="OpenRouter"&&c.id==="openrouter"||option==="GroqCloud"&&c.id==="groq"))?" · not connected":""}</option>`).join("")}</select></label>`).join("")}</div></section>`).join("");
    settingsDialog.showModal();
  } catch (error) { $("#settings-error").textContent = error.message; settingsDialog.showModal(); }
});
$("#close-settings").addEventListener("click", () => settingsDialog.close());
$("#cancel-settings").addEventListener("click", () => settingsDialog.close());
$("#remove-github").addEventListener("click", async () => {
  if (!confirm("Remove the saved GitHub token from the VM? Projects and task records will remain.")) return;
  try { await api("/api/connections/github", {method:"DELETE"}); alert("Saved GitHub token removed."); settingsDialog.close(); }
  catch (error) { $("#settings-error").textContent = error.message; }
});
$("#test-github").addEventListener("click", async () => {
  try { const result = await api("/api/connections/github/test", {method:"POST", body:"{}"}); alert(`GitHub connected as ${result.login}.`); }
  catch (error) { $("#settings-error").textContent = error.message; }
});
$("#settings-form").addEventListener("submit", async event => {
  event.preventDefault();
  const button = event.currentTarget.querySelector('[type="submit"]'); button.disabled = true; $("#settings-error").textContent = "";
  try {
    for (const input of $("#connection-fields").querySelectorAll(".connection-key")) if (input.value.trim()) await api(`/api/connections/${encodeURIComponent(input.dataset.connection)}`, {method:"PUT", body:JSON.stringify({secret:input.value.trim()})});
    const routeMap = {};
    for (const role of $("#routing-fields").querySelectorAll(".route-role")) {
      const selects = [...role.querySelectorAll("select")].sort((a,b) => Number(a.dataset.routeRank)-Number(b.dataset.routeRank));
      routeMap[selects[0]?.dataset.routeRole] = selects.map(select => select.value);
    }
    await api("/api/routing", {method:"POST", body:JSON.stringify({routing:routeMap})});
    await loadTasks(); settingsDialog.close();
  } catch (error) { $("#settings-error").textContent = error.message; }
  finally { button.disabled = false; }
});

const projectsDialog = $("#projects-dialog");
function slugify(value) { return String(value).toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g,"-").replace(/^-|-$/g,"").slice(0,40) || `project-${Date.now()}`; }
function renderProjectEditor(projects) {
  $("#projects-fields").innerHTML = projects.map(project => `<section class="project-editor" data-project-card><div class="two-columns"><label>Project name<input data-project-name value="${escapeHtml(project.name||"")}" required maxlength="100"></label><label>GitHub repository URL<input data-project-repository list="github-repo-options" value="${escapeHtml(project.repository||"")}" placeholder="https://github.com/owner/repository"></label></div><label>Project background notes<textarea data-project-context rows="4" maxlength="20000" placeholder="Goals, product shape, stack, shared conventions">${escapeHtml(project.context||"")}</textarea></label><div class="project-history-controls"><select data-project-history disabled><option value="">No saved versions</option></select><button type="button" class="secondary" data-project-restore disabled>Restore</button><button type="button" class="danger" data-project-delete-version disabled>Delete version</button></div><label>Sub-projects <span class="muted">One per line: name | repository folder | focused background notes</span><textarea data-project-modules rows="4" placeholder="Payments | backend/payments | Owns billing and subscription logic">${escapeHtml((project.modules||[]).map(module=>`${module.name} | ${module.path} | ${module.context||""}`).join("\n"))}</textarea><input type="hidden" data-project-id value="${escapeHtml(project.id||"")}"></label><button type="button" class="danger" data-remove-project>Remove project</button></section>`).join("") + `<datalist id="github-repo-options">${githubRepoOptions.map(repo=>`<option value="${escapeHtml(repo.clone_url)}">${escapeHtml(repo.full_name)}${repo.private?" · private":""}</option>`).join("")}</datalist>`;
}
$("#open-projects").addEventListener("click", async () => {
  $("#projects-error").textContent = "";
  try { const result = await api("/api/projects"); projectCache = result.projects; githubRepoOptions = []; try { githubRepoOptions = (await api("/api/connections/github/repositories")).repositories || []; } catch {} renderProjectEditor(projectCache); for (const card of $("#projects-fields").querySelectorAll("[data-project-card]")) await loadProjectBackgroundHistory(card); projectsDialog.showModal(); }
  catch (error) { $("#projects-error").textContent = error.message; projectsDialog.showModal(); }
});
$("#close-projects").addEventListener("click", () => projectsDialog.close());
$("#cancel-projects").addEventListener("click", () => projectsDialog.close());
$("#add-project").addEventListener("click", () => { const items = [...$("#projects-fields").querySelectorAll("[data-project-card]")].map(readProjectCard); items.push({id:"",name:"",repository:"",modules:[]}); renderProjectEditor(items); });
$("#projects-fields").addEventListener("click", event => {
  const restoreButton = event.target.closest("[data-project-restore]");
  if (restoreButton) {
    const card = restoreButton.closest("[data-project-card]");
    const versionId = card.querySelector("[data-project-history]").value;
    if (!versionId || !confirm("Restore this saved background? The current notes will be saved as another version.")) return;
    api(`/api/projects/${encodeURIComponent(card.querySelector("[data-project-id]").value)}/restore-context`, {method:"POST", body:JSON.stringify({version_id:versionId})})
      .then(result => {card.querySelector("[data-project-context]").value=result.context;return loadProjectBackgroundHistory(card);})
      .catch(error => alert(error.message));
    return;
  }
  const deleteVersionButton = event.target.closest("[data-project-delete-version]");
  if (deleteVersionButton) {
    const card=deleteVersionButton.closest("[data-project-card]");const versionId=card.querySelector("[data-project-history]").value;
    if (!versionId || !confirm("Permanently delete this saved project background version?")) return;
    api(`/api/projects/${encodeURIComponent(card.querySelector("[data-project-id]").value)}/context-history/${encodeURIComponent(versionId)}`, {method:"DELETE"})
      .then(()=>loadProjectBackgroundHistory(card)).catch(error=>alert(error.message));
    return;
  }
  if (!event.target.closest("[data-remove-project]")) return;
  const cards = [...$("#projects-fields").querySelectorAll("[data-project-card]")];
  if (cards.length < 2) return alert("Keep at least one project.");
  event.target.closest("[data-project-card]").remove();
});
function readProjectCard(card) {
  const name = card.querySelector("[data-project-name]").value.trim();
  const lines = card.querySelector("[data-project-modules]").value.split("\n").map(line=>line.trim()).filter(Boolean);
  const modules = lines.map(line=>{const parts=line.split("|").map(part=>part.trim());return {id:slugify(parts[0]),name:parts[0],path:parts[1]||"",context:parts.slice(2).join(" | ")};});
  return {id:card.querySelector("[data-project-id]").value||slugify(name),name,repository:card.querySelector("[data-project-repository]").value.trim(),context:card.querySelector("[data-project-context]").value.trim(),modules};
}

const repositoryViewDialog = $("#repository-view-dialog");
let repositoryViewMode = "files";
let repositoryViewPath = "";
async function showRepositoryFolder(path = "") {
  const projectId = $("#active-project").value;
  const error = $("#repository-view-error");
  error.textContent = "";
  try {
    const result = await api(`/api/projects/${encodeURIComponent(projectId)}/github-files?path=${encodeURIComponent(path)}`);
    repositoryViewPath = result.path;
    $("#repository-view-meta").textContent = `${projectCache.find(item => item.id === projectId)?.repository || "GitHub repository"} · ${result.branch} · ${result.path || "/"}`;
    const folders = result.entries.filter(item => item.type === "dir");
    let files = result.entries.filter(item => item.type === "file");
    if (repositoryViewMode === "documentation") files = files.filter(item => /\.(md|txt)$/i.test(item.name));
    $("#repository-file-list").innerHTML = `<button class="repository-entry repository-parent" data-repository-path="${escapeHtml(result.path.includes("/") ? result.path.split("/").slice(0,-1).join("/") : "")}">↑ ${result.path ? "Parent folder" : "Repository root"}</button>` + folders.map(item => `<button class="repository-entry" data-repository-path="${escapeHtml(item.path)}">📁 ${escapeHtml(item.name)}/</button>`).join("") + files.map(item => `<button class="repository-entry" data-repository-file="${escapeHtml(item.path)}">${escapeHtml(item.name)} <span>${Number(item.size||0).toLocaleString()} B</span></button>`).join("") || `<p class="muted">No matching files in this folder.</p>`;
    $("#repository-file-content").textContent = "Choose a file to view it.";
  } catch (cause) {
    if (repositoryViewMode === "documentation" && path === "docs") return showRepositoryFolder("");
    error.textContent = cause.message; $("#repository-file-list").replaceChildren();
  }
}
async function openRepositoryView(mode) {
  repositoryViewMode = mode;
  $("#repository-view-title").textContent = mode === "documentation" ? "Project documentation" : "Project files";
  $("#repository-view-eyebrow").textContent = mode === "documentation" ? "VERSIONED PROJECT DOCUMENTS" : "GITHUB DEFAULT BRANCH";
  repositoryViewDialog.showModal();
  if (mode === "documentation") {
    try { await showRepositoryFolder("docs"); }
    catch { await showRepositoryFolder(""); }
  } else await showRepositoryFolder("");
}
$("#open-files").addEventListener("click", () => openRepositoryView("files"));
$("#open-documentation").addEventListener("click", () => openRepositoryView("documentation"));
$("#close-repository-view").addEventListener("click", () => repositoryViewDialog.close());
$("#repository-file-list").addEventListener("click", async event => {
  const folder = event.target.closest("[data-repository-path]");
  if (folder) return showRepositoryFolder(folder.dataset.repositoryPath);
  const file = event.target.closest("[data-repository-file]");
  if (!file) return;
  const projectId = $("#active-project").value;
  const error = $("#repository-view-error"); error.textContent = "";
  try {
    const result = await api(`/api/projects/${encodeURIComponent(projectId)}/github-files?file=${encodeURIComponent(file.dataset.repositoryFile)}`);
    $("#repository-view-meta").textContent = `${projectCache.find(item => item.id === projectId)?.repository || "GitHub repository"} · ${result.branch} · ${result.path}`;
    $("#repository-file-content").textContent = result.content;
  } catch (cause) { error.textContent = cause.message; }
});
$("#active-project").addEventListener("change", () => {
  if (repositoryViewDialog.open) showRepositoryFolder(repositoryViewMode === "documentation" ? "docs" : "");
});
async function loadProjectBackgroundHistory(card) {
  const projectId = card.querySelector("[data-project-id]").value;
  const select = card.querySelector("[data-project-history]");
  if (!projectId) return;
  try {
    const {history} = await api(`/api/projects/${encodeURIComponent(projectId)}/context-history`);
    select.innerHTML = history.length ? history.map(item => `<option value="${escapeHtml(item.id)}">${escapeHtml(formatTime(item.created_at))} · ${escapeHtml(item.preview)}</option>`).join("") : `<option value="">No saved versions</option>`;
    card.querySelector("[data-project-restore]").disabled = history.length === 0;
    card.querySelector("[data-project-delete-version]").disabled = history.length === 0;
  } catch {}
}
$("#projects-form").addEventListener("submit", async event => {
  event.preventDefault(); const button=event.currentTarget.querySelector('[type="submit"]');button.disabled=true;$("#projects-error").textContent="";
  try { const projects=[...$("#projects-fields").querySelectorAll("[data-project-card]")].map(readProjectCard);await api("/api/projects",{method:"PUT",body:JSON.stringify({projects})});projectCache=projects;projectsDialog.close();await loadTasks(); }
  catch(error){$("#projects-error").textContent=error.message;} finally{button.disabled=false;}
});
