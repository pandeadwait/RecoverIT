/* ==========================================================================
   RecoverIT Web Dashboard Client Logic
   ========================================================================== */

let scenariosData = [];
let currentResult = null;

document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  loadScenarios();
  setupEventListeners();
});

// Initialize Tab Switching
function initTabs() {
  const tabPreset = document.getElementById("tab-preset");
  const tabCustom = document.getElementById("tab-custom");
  const viewPreset = document.getElementById("view-preset");
  const viewCustom = document.getElementById("view-custom");

  tabPreset.addEventListener("click", () => {
    tabPreset.classList.add("active");
    tabCustom.classList.remove("active");
    viewPreset.classList.add("active");
    viewCustom.classList.remove("active");
  });

  tabCustom.addEventListener("click", () => {
    tabCustom.classList.add("active");
    tabPreset.classList.remove("active");
    viewCustom.classList.add("active");
    viewPreset.classList.remove("active");
  });
}

// Fetch available scenarios from the API
async function loadScenarios() {
  try {
    const res = await fetch("/api/scenarios");
    scenariosData = await res.json();
    const select = document.getElementById("scenario-select");
    select.innerHTML = "";

    scenariosData.forEach((scen) => {
      const opt = document.createElement("option");
      opt.value = scen.id;
      opt.textContent = `${scen.title} (${scen.service})`;
      select.appendChild(opt);
    });

    select.addEventListener("change", () => updateScenarioBanner(select.value));
    if (scenariosData.length > 0) {
      updateScenarioBanner(scenariosData[0].id);
    }
  } catch (err) {
    console.error("Failed loading scenarios:", err);
  }
}

// Update the scenario description banner
function updateScenarioBanner(scenarioId) {
  const scen = scenariosData.find((s) => s.id === scenarioId);
  if (!scen) return;

  document.getElementById("scen-category").textContent = scen.category;
  document.getElementById("scen-service").textContent = `Service: ${scen.service}`;
  document.getElementById("scen-severity").textContent = scen.severity;
  document.getElementById("scen-text").textContent = scen.description || scen.title;
}

// Setup button click events
function setupEventListeners() {
  document.getElementById("btn-run").addEventListener("click", () => {
    const scenarioId = document.getElementById("scenario-select").value;
    const mode = document.getElementById("mode-select").value;
    const provider = document.getElementById("provider-select").value;
    runInvestigation({ scenario: scenarioId, mode: mode, provider: provider });
  });

  document.getElementById("btn-run-custom").addEventListener("click", () => {
    const repo = document.getElementById("custom-repo").value.trim();
    const logs = document.getElementById("custom-logs").value.trim();
    const service = document.getElementById("custom-service").value.trim();
    const alert = document.getElementById("custom-alert").value.trim();
    const mode = document.getElementById("mode-select").value;
    const provider = document.getElementById("provider-select").value;

    runInvestigation({
      repo_path: repo || ".",
      log_path: logs || null,
      service_name: service || "payment-api",
      summary: alert || "[P1 CRITICAL] Operational degradation detected",
      mode: mode,
      provider: provider,
    });
  });

  document.getElementById("btn-download-report").addEventListener("click", () => {
    if (!currentResult || !currentResult.markdown_report) return;
    const blob = new Blob([currentResult.markdown_report], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `incident_report_${currentResult.incident_id}.md`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  });

  document.getElementById("btn-copy-summary").addEventListener("click", () => {
    if (!currentResult) return;
    const topHypo = currentResult.ranked_hypotheses[0];
    const summaryText = `[RecoverIT Incident Diagnosis]\nIncident: ${currentResult.incident_id} (${currentResult.service})\nStatus: ${currentResult.status.toUpperCase()}\nRoot Cause: ${topHypo ? topHypo.statement : "Unknown"}\nScore: ${topHypo ? topHypo.evidence_score : "N/A"}/100\nDuration: ${currentResult.execution_time_seconds.toFixed(2)}s`;
    
    navigator.clipboard.writeText(summaryText).then(() => {
      const btn = document.getElementById("btn-copy-summary");
      const origText = btn.textContent;
      btn.textContent = "✓ Copied to Clipboard!";
      setTimeout(() => { btn.textContent = origText; }, 2000);
    });
  });
}

// Execute Investigation and Animate Stepper
async function runInvestigation(payload) {
  const stepper = document.getElementById("pipeline-stepper");
  const resultsSection = document.getElementById("results-section");

  // Reset and show stepper
  stepper.classList.remove("hidden");
  resultsSection.classList.add("hidden");
  animateSteps();

  try {
    const res = await fetch("/api/investigate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Investigation failed");
    }

    currentResult = await res.json();
    completeAllSteps();

    setTimeout(() => {
      renderResults(currentResult);
      resultsSection.classList.remove("hidden");
      resultsSection.scrollIntoView({ behavior: "smooth" });
    }, 400);

  } catch (err) {
    alert(`Investigation failed: ${err.message}`);
    console.error(err);
  }
}

// Stepper animation
let stepTimer = null;
function animateSteps() {
  const steps = [1, 2, 3, 4, 5, 6];
  let currentStep = 1;

  steps.forEach(s => {
    const el = document.getElementById(`step-${s}`);
    el.className = "step-item";
  });

  document.getElementById("step-1").classList.add("active");

  if (stepTimer) clearInterval(stepTimer);
  stepTimer = setInterval(() => {
    if (currentStep < 5) {
      const prevEl = document.getElementById(`step-${currentStep}`);
      prevEl.classList.remove("active");
      prevEl.classList.add("completed");

      currentStep++;
      const nextEl = document.getElementById(`step-${currentStep}`);
      nextEl.classList.add("active");
    }
  }, 250);
}

function completeAllSteps() {
  if (stepTimer) clearInterval(stepTimer);
  for (let s = 1; s <= 6; s++) {
    const el = document.getElementById(`step-${s}`);
    el.className = "step-item completed";
  }
}

// Render Results onto the UI
function renderResults(res) {
  // Metrics strip
  document.getElementById("res-incident-id").textContent = res.incident_id;
  document.getElementById("res-service").textContent = res.service;
  document.getElementById("res-status").textContent = res.status.toUpperCase();
  document.getElementById("res-latency").textContent = `${res.execution_time_seconds.toFixed(2)}s`;
  document.getElementById("res-provider").textContent = res.provider_used;

  // Render Hypotheses
  const hypoContainer = document.getElementById("hypotheses-container");
  hypoContainer.innerHTML = "";

  if (!res.ranked_hypotheses || res.ranked_hypotheses.length === 0) {
    hypoContainer.innerHTML = `<div class="scenario-banner"><p>No root-cause hypotheses met confidence criteria.</p></div>`;
  } else {
    res.ranked_hypotheses.forEach(h => {
      const isTop = h.rank === 1;
      const card = document.createElement("div");
      card.className = `hypothesis-card ${isTop ? "rank-1" : "rank-other"}`;

      let citationsHtml = "";
      if (h.supporting_evidence && h.supporting_evidence.length > 0) {
        citationsHtml = `
          <div class="citations-block">
            <div class="citations-title">✓ Corroborating Evidence Citations</div>
            <ul class="citations-list">
              ${h.supporting_evidence.map(c => `
                <li class="citation-item">
                  <span class="citation-id">${c.evidence_id}</span>: ${c.reason || "Corroborating factor"}
                </li>
              `).join("")}
            </ul>
          </div>
        `;
      }

      card.innerHTML = `
        <div class="hypo-header">
          <span class="hypo-rank-badge">RANK #${h.rank}</span>
          <div class="hypo-score-gauge">
            <span style="color: ${isTop ? "var(--emerald)" : "var(--cyan)"}">${h.evidence_score.toFixed(1)}/100</span>
            <span class="badge ${isTop ? "badge-green" : "badge-blue"}">${h.confidence_label.toUpperCase()}</span>
          </div>
        </div>
        <div class="hypo-statement">${h.statement}</div>
        <div class="hypo-meta-row">
          <span><strong>Category:</strong> ${h.root_cause_category.replace("_", " ").toUpperCase()}</span>
          <span><strong>Component:</strong> <code>${h.affected_component}</code></span>
        </div>
        ${citationsHtml}
      `;

      hypoContainer.appendChild(card);
    });
  }

  // Render Timeline
  const timelineContainer = document.getElementById("timeline-container");
  timelineContainer.innerHTML = "";

  if (res.timeline_events && res.timeline_events.length > 0) {
    res.timeline_events.forEach(ev => {
      const tCard = document.createElement("div");
      tCard.className = "timeline-card";

      const timeStr = ev.event_time ? ev.event_time.split("T")[1]?.slice(0, 8) + " UTC" : "TIME UNKNOWN";
      tCard.innerHTML = `
        <div class="timeline-time">${timeStr}</div>
        <div class="timeline-body">
          <div class="timeline-category">${ev.category || "OBSERVATION"} • ${ev.service || res.service}</div>
          <div class="timeline-title">${ev.title}</div>
        </div>
      `;
      timelineContainer.appendChild(tCard);
    });
  } else {
    timelineContainer.innerHTML = `<p class="scenario-text">No timeline events recorded.</p>`;
  }

  // Render Diff Excerpts
  const diffPanel = document.getElementById("diff-panel");
  const diffCode = document.getElementById("diff-content").querySelector("code");
  const diffCommitId = document.getElementById("diff-commit-id");

  if (res.diff_excerpts && Object.keys(res.diff_excerpts).length > 0) {
    diffPanel.classList.remove("hidden");
    const firstCommit = Object.keys(res.diff_excerpts)[0];
    diffCommitId.textContent = firstCommit;

    const rawDiff = res.diff_excerpts[firstCommit];
    const highlightedLines = rawDiff.split("\n").map(line => {
      const escaped = line.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      if (line.startsWith("+")) {
        return `<span class="diff-add">${escaped}</span>`;
      } else if (line.startsWith("-")) {
        return `<span class="diff-del">${escaped}</span>`;
      }
      return escaped;
    }).join("\n");

    diffCode.innerHTML = highlightedLines;
  } else {
    diffPanel.classList.add("hidden");
  }
}
