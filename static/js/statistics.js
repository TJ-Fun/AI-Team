import { $, escapeHtml, state, api, appAlert, appConfirm, periodBucket, matchesPeriod, elapsedSeconds, formatDuration } from "./core.js";

export function initStatistics({ refreshBoard } = {}) {
  // --- Statistics Dialog ---
  const statsDialog = $("#statistics-dialog");
  const statPeriodSelect = $("#stat-period");

  function renderStatistics() {
    const period = statPeriodSelect?.value || "today";
    const records = [];
    for (const task of state.tasks) {
      for (const att of task.attempts || []) {
        if (att.provider) {
          records.push({ ...att, requested_at: att.started_at || att.finished_at });
        }
      }
    }
    const bucket = periodBucket(period);
    const applicableResets = (state.statResets || []).filter((r) => r.period === period && r.bucket === bucket);
    const cutoff = new Map(applicableResets.map((r) => [`${r.provider}\0${r.model}`, r.reset_at]));
    const metrics = new Map();
    const ensure = (provider, model) => {
      const k = `${provider}\0${model}`;
      if (!metrics.has(k)) {
        metrics.set(k, { provider, model, requests: 0, replies: 0, seconds: 0, timedReplies: 0 });
      }
      return metrics.get(k);
    };

    for (const r of records) if (r.provider) ensure(r.provider, r.model || "Model not recorded");
    for (const r of applicableResets) ensure(r.provider, r.model);

    for (const r of records) {
      if (!r.provider || !matchesPeriod(r.requested_at, period)) continue;
      const model = r.model || "Model not recorded";
      const resetAt = cutoff.get(`${r.provider}\0${model}`);
      if (resetAt && r.requested_at && Date.parse(r.requested_at) <= Date.parse(resetAt)) continue;
      const m = ensure(r.provider, model);
      m.requests += 1;
      if (r.state === "succeeded") {
        m.replies += 1;
        const dur = typeof r.duration_seconds === "number" ? r.duration_seconds : elapsedSeconds(r);
        if (dur != null) {
          m.seconds += dur;
          m.timedReplies += 1;
        }
      }
    }

    const rows = [...metrics.values()].sort(
      (a, b) => a.provider.localeCompare(b.provider) || a.model.localeCompare(b.model)
    );
    const total = rows.reduce(
      (acc, item) => ({
        requests: acc.requests + item.requests,
        replies: acc.replies + item.replies,
        seconds: acc.seconds + item.seconds,
        timedReplies: acc.timedReplies + item.timedReplies,
      }),
      { requests: 0, replies: 0, seconds: 0, timedReplies: 0 }
    );
    $("#stats-total-requests").textContent = String(total.requests);
    $("#stats-success-rate").textContent = total.requests
      ? `${Math.round((total.replies / total.requests) * 100)}%`
      : "—";
    $("#stats-success-detail").textContent = total.requests
      ? `${total.replies} successful ${total.replies === 1 ? "reply" : "replies"} of ${total.requests} requests`
      : "No requests in this period";
    $("#stats-average-time").textContent = formatDuration(
      total.timedReplies ? total.seconds / total.timedReplies : null
    );
    const qRows = state.qualityRows || [];
    const scoredOutcomes = qRows.reduce(
      (acc, row) => {
        const measured = Number(row.measured) || 0;
        const rate = Number(row.qa_pass_rate);
        if (measured > 0 && Number.isFinite(rate)) {
          acc.measured += measured;
          acc.passed += (rate / 100) * measured;
        }
        return acc;
      },
      { measured: 0, passed: 0 }
    );
    $("#stats-qa-pass-rate").textContent = scoredOutcomes.measured
      ? `${Math.round((scoredOutcomes.passed / scoredOutcomes.measured) * 100)}%`
      : "—";
    $("#stats-qa-detail").textContent = scoredOutcomes.measured
      ? `${scoredOutcomes.measured} scored deliverable checks · all time`
      : "No scored deliverable checks yet";

    const cells = (m) => `
      <span>${m.requests} ${m.requests === 1 ? "request" : "requests"}</span>
      <span>${m.replies} ${m.replies === 1 ? "reply" : "replies"}</span>
      <span>avg ${escapeHtml(formatDuration(m.timedReplies ? m.seconds / m.timedReplies : null))}</span>`;

    $("#provider-statistics").hidden = false;
    $("#provider-stat-rows").innerHTML =
      `<div class="provider-stat-row provider-stat-total"><strong>Total</strong>${cells(total)}<span></span></div>` +
      rows
        .map(
          (m) => `
          <div class="provider-stat-row">
            <div class="provider-stat-model"><strong>${escapeHtml(m.provider)}</strong><small>${escapeHtml(
              m.model
            )}</small></div>
            ${cells(m)}
            <button type="button" class="stat-reset" data-reset-provider="${escapeHtml(
              m.provider
            )}" data-reset-model="${escapeHtml(m.model)}">Reset</button>
          </div>`
        )
        .join("");

    $("#provider-quality-rows").innerHTML = qRows.length
      ? `<div class="quality-head"><span>Role / provider</span><span>Requests / replies</span><span>QA pass</span><span>Errors</span><span>Avg time</span></div>` +
        qRows
          .map(
            (r) => `
          <div class="quality-row">
            <strong>${escapeHtml(r.role)}<small>${escapeHtml(r.provider)}</small></strong>
            <span>${r.requests} / ${r.replies}</span>
            <span>${r.qa_pass_rate == null ? `— (${r.measured} scored)` : `${r.qa_pass_rate}% (${r.measured})`}</span>
            <span>${r.quota_or_service_errors}</span>
            <span>${r.average_seconds == null ? "—" : escapeHtml(formatDuration(r.average_seconds))}</span>
          </div>`
          )
          .join("")
      : `<p class="notification-empty">No task outcomes recorded yet. Deliverable QA results will appear here.</p>`;
  }

  $("#open-statistics")?.addEventListener("click", () => {
    renderStatistics();
    if (!statsDialog.open) statsDialog.showModal();
  });
  $("#close-statistics")?.addEventListener("click", () => statsDialog.close());
  statPeriodSelect?.addEventListener("change", () => renderStatistics());

  $("#provider-stat-rows")?.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-reset-provider]");
    if (!btn) return;
    const provider = btn.getAttribute("data-reset-provider");
    const model = btn.getAttribute("data-reset-model");
    const period = statPeriodSelect.value || "today";
    if (!(await appConfirm(`Reset the displayed statistic for ${provider} · ${model}? Past usage history will be kept.`, { title: "Reset statistic", danger: true }))) {
      return;
    }
    btn.disabled = true;
    try {
      await api("/api/llm-usage/reset", {
        method: "POST",
        body: JSON.stringify({ provider, model, period, bucket: periodBucket(period) }),
      });
      await refreshBoard();
    } catch (err) {
      appAlert(err.message);
    } finally {
      btn.disabled = false;
    }
  });
  return { renderStatistics };
}
