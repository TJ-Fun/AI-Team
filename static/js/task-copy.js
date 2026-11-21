import { state, api, formatTime } from "./core.js";
import { getAskedProvider } from "./task-utils.js";

function section(title, body) {
  if (body === undefined || body === null || body === "") return "";
  return `\n=== ${title} ===\n${body}\n`;
}

function listBlock(items) {
  if (!items || !items.length) return "";
  return items.map((item) => `- ${item}`).join("\n");
}

export function taskCopyText(task, replies = []) {
  const asked = getAskedProvider(task);
  const attempts = task.attempts || [];
  const successful = [...attempts].reverse().find((a) => a.state === "succeeded");
  const sentTo = [...new Set(attempts.map((a) => a.provider).filter(Boolean))];
  const route = sentTo.length
    ? `${asked}${asked === "Automatic" ? ` (sent to ${sentTo.join(", ")})` : ""}`
    : asked;

  const lines = [
    `Task: ${task.title}`,
    `ID: ${task.id}`,
    `Status: ${task.status}`,
    `Role: ${task.assigned_role}`,
    `Requested provider: ${route}`,
    `Type: ${task.task_type || "work"}`,
    task.release ? `Release: ${task.release}` : null,
    task.produces_deliverable ? "Deliverable: yes (QA review required)" : null,
    `Created: ${formatTime(task.created_at)}`,
    task.updated_at ? `Updated: ${formatTime(task.updated_at)}` : null,
    task.planning_summary ? `PM/PO summary: ${task.planning_summary}` : null,
    task.answer ? `Answer: ${task.answer}` : null,
    task.block_reason ? `Block reason: ${task.block_reason}` : null,
    task.waiting_reason ? `Waiting reason: ${task.waiting_reason}` : null,
    task.retry_after ? `Retry after: ${formatTime(task.retry_after)}` : null,
    task.dispatch_note ? `Dispatch note: ${task.dispatch_note}` : null,
    task.design_decision
      ? `Design decision: ${task.design_decision}${task.design_alternative ? ` (${task.design_alternative})` : ""}${task.design_free_text ? ` — ${task.design_free_text}` : ""}`
      : null,
    task.github_error ? `GitHub error: ${task.github_error}` : null,
    task.ci_status ? `CI status: ${task.ci_status}${task.ci_error ? ` — ${task.ci_error}` : ""}` : null,
    task.local_sync_error ? `Local sync error: ${task.local_sync_error}` : null,
    task.pull_request?.url
      ? `Pull request: #${task.pull_request.number} (${task.pull_request.state}) ${task.pull_request.url}`
      : null,
    successful
      ? `Answered by: ${successful.provider} · attempt ${successful.attempt}`
      : attempts.length
        ? "No successful answer yet"
        : "Not sent yet",
  ].filter((l) => l !== null && l !== undefined && l !== "");

  let out = lines.join("\n");

  out += section("Request", task.request || "");
  out += section("Acceptance checks", listBlock(task.acceptance_checks));

  if (task.question_data) {
    const q = task.question_data;
    out += section(
      "Clarification question",
      [
        q.question ? `Question: ${q.question}` : null,
        q.why ? `Why: ${q.why}` : null,
        listBlock((q.options || []).map((o) => `${o.label}${o.description ? ` — ${o.description}` : ""}`)),
      ]
        .filter(Boolean)
        .join("\n")
    );
  }

  if (task.review_data) {
    const rd = task.review_data;
    const dc = rd.design_change || {};
    out += section(
      "Design review",
      [
        rd.summary ? `Summary: ${rd.summary}` : null,
        dc.description ? `Change: ${dc.description}` : null,
        dc.reason ? `Reason: ${dc.reason}` : null,
        rd.recommendation ? `Recommendation: ${rd.recommendation}` : null,
        (rd.team_task_ids || []).length ? `Provisional team tasks: ${rd.team_task_ids.join(", ")}` : null,
        (rd.alternatives || []).length
          ? `Alternatives:\n${rd.alternatives.map((a, i) => `  ${i}. ${a.title || ""}${a.description ? ` — ${a.description}` : ""}`).join("\n")}`
          : null,
      ]
        .filter(Boolean)
        .join("\n")
    );
  }

  if (task.overview_documents && Object.keys(task.overview_documents).length) {
    out += section(
      "Proposed project context",
      Object.entries(task.overview_documents)
        .map(([key, content]) => `--- ${key} ---\n${content}`)
        .join("\n\n")
    );
  }

  if (task.update_payload) {
    const payload = task.update_payload;
    if (task.task_type === "board_delta") {
      const muts = (payload.mutations || [])
        .map((m) => `- ${m.op || ""}${m.task_id ? ` → ${m.task_id}` : ""}${m.title ? ` — ${m.title} [${m.role || ""}]` : ""}${m.reason ? ` (${m.reason})` : ""}`)
        .join("\n");
      out += section("Proposed board changes", `${payload.summary || ""}\n${muts}`.trim());
    } else if (task.task_type === "chain_plan" && task.chain_plan) {
      const plan = task.chain_plan;
      const subs = (plan.subtasks || [])
        .map((s, i) => `${i + 1}. ${s.title || ""} [${s.kind || ""}]\n   ${s.request || ""}`)
        .join("\n");
      out += section("Chain plan", `Stage: ${plan.stage || task.chain_stage || ""}\n${plan.summary || ""}\n${subs}`.trim());
    } else {
      const entries =
        task.task_type === "roadmap_update"
          ? [["roadmap", payload.roadmap || payload.master_plan || ""], ...Object.entries(payload.phases || {})]
          : Object.entries(payload.documents || payload).filter(([key]) => !["type", "summary"].includes(key));
      out += section(
        "Proposed document update",
        `${payload.summary || ""}\n${entries.map(([slug, content]) => `--- ${slug} ---\n${content}`).join("\n\n")}`.trim()
      );
    }
  }

  if (task.env_state || task.env_steps) {
    const env = task.env_state || {};
    const steps = (task.env_steps || env.steps || [])
      .map(
        (s) =>
          `- [${s.status || ""}] ${s.title || s.id || ""}\n  ${s.instructions || ""}${s.expected ? `\n  Expected: ${s.expected}` : ""}${s.troubleshooting ? `\n  Troubleshooting: ${s.troubleshooting}` : ""}${s.output ? `\n  Output: ${s.output}` : ""}${s.note ? `\n  Note: ${s.note}` : ""}`
      )
      .join("\n");
    const issue = env.issue
      ? `\nIssue at ${env.issue.step_id || ""}: ${env.issue.result || ""}${env.issue.note ? ` — ${env.issue.note}` : ""}`
      : "";
    const fallback = task.env_fallback ? `\nFallback: ${task.env_fallback}` : "";
    out += section("Environment setup", `${steps}${issue}${fallback}`.trim());
  }

  if (task.validation) {
    const v = task.validation;
    const checks = (v.checks || [])
      .map((c) => `- ${c.passed === true ? "PASS" : c.passed === false ? "FAIL" : "?"} ${c.check || ""} — ${c.message || ""}`)
      .join("\n");
    out += section("Validation", `${String(v.status || "").replace(/_/g, " ")}\n${checks}`.trim());
  }

  if (replies.length) {
    out += section(
      "Agent replies",
      replies
        .map((r) => {
          const head = `${r.provider} · attempt ${r.attempt} · ${r.state}${r.error_kind ? ` · ${r.error_kind}` : ""} · ${formatTime(r.at)}`;
          const body = r.text || r.error || "No response text was saved.";
          const raw = r.raw_text && r.raw_text !== r.text ? `\n--- raw reply ---\n${r.raw_text}` : "";
          return `${head}\n${body}${raw}`;
        })
        .join("\n\n")
    );
  }

  const activity = [...(task.activity || [])].reverse();
  if (activity.length) {
    out += section(
      "Activity",
      activity.map((a) => `- ${formatTime(a.at)} · ${a.message || a.type}`).join("\n")
    );
  }

  return out.trim() + "\n";
}

export async function copyTaskToClipboard(taskId, button = null) {
  const task = state.tasks.find((t) => t.id === taskId);
  if (!task) return false;
  let replies = [];
  try {
    const res = await api(`/api/tasks/${encodeURIComponent(task.id)}/replies`);
    replies = res.replies || [];
  } catch {
    replies = [];
  }
  const text = taskCopyText(task, replies);
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }
  if (button) {
    const original = button.textContent;
    button.textContent = "Copied!";
    setTimeout(() => {
      button.textContent = original;
    }, 1500);
  }
  return true;
}
