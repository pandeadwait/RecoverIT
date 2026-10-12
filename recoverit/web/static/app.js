// RecoverIT Dashboard & Remediation Presentation Logic

document.addEventListener('DOMContentLoaded', () => {
    const loadBtn = document.getElementById('load-btn');
    const incidentInput = document.getElementById('incident-id-input');

    if (loadBtn && incidentInput) {
        loadBtn.addEventListener('click', () => {
            const incidentId = incidentInput.value.trim();
            if (incidentId) {
                fetchInvestigation(incidentId);
            }
        });

        incidentInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                const incidentId = incidentInput.value.trim();
                if (incidentId) {
                    fetchInvestigation(incidentId);
                }
            }
        });
    }

    // Auto-load if incident_id query parameter is present in URL
    const urlParams = new URLSearchParams(window.location.search);
    const initialIncident = urlParams.get('incident_id');
    if (initialIncident) {
        if (incidentInput) incidentInput.value = initialIncident;
        fetchInvestigation(initialIncident);
    }
});

async function fetchInvestigation(incidentId) {
    try {
        const response = await fetch(`/api/investigations/${encodeURIComponent(incidentId)}`);
        if (!response.ok) {
            alert(`Investigation not found or error loading incident '${incidentId}' (HTTP ${response.status})`);
            return;
        }
        const data = await response.json();
        renderInvestigationResult(data);
    } catch (err) {
        console.error('Failed to load investigation:', err);
        alert(`Error communicating with RecoverIT API: ${err.message}`);
    }
}

function renderInvestigationResult(data) {
    renderOverview(data);
    renderHypotheses(data.ranked_hypotheses || []);
    renderRemediationSection(data.remediation_plan);
}

function renderOverview(data) {
    const section = document.getElementById('summary-section');
    if (!section) return;

    document.getElementById('overview-incident-id').textContent = data.incident_id || '—';
    document.getElementById('overview-service').textContent = data.service || '—';
    document.getElementById('overview-duration').textContent = `${(data.execution_time_seconds || 0).toFixed(2)}s`;
    document.getElementById('overview-provider').textContent = data.provider_used || '—';
    document.getElementById('overview-summary').textContent = data.summary || '—';

    const statusBadge = document.getElementById('investigation-status-badge');
    if (statusBadge) {
        const status = (data.status || 'UNKNOWN').toUpperCase();
        statusBadge.textContent = status;
        statusBadge.className = `badge ${status === 'COMPLETED' ? 'badge-completed' : 'badge-inconclusive'}`;
    }

    section.style.display = 'block';
}

function renderHypotheses(hypotheses) {
    const section = document.getElementById('hypotheses-section');
    const container = document.getElementById('hypotheses-list');
    if (!section || !container) return;

    container.innerHTML = '';
    if (!hypotheses || hypotheses.length === 0) {
        container.innerHTML = '<p class="text-muted">No root-cause hypotheses met confidence criteria.</p>';
    } else {
        hypotheses.forEach(h => {
            const card = document.createElement('div');
            card.className = 'hypothesis-card';
            card.innerHTML = `
                <div class="hypothesis-header">
                    <span class="hypothesis-statement">#${h.rank} ${escapeHtml(h.statement)}</span>
                    <span class="badge ${h.confidence_label === 'high' ? 'badge-completed' : 'badge-inconclusive'}">
                        ${(h.confidence_label || 'unknown').toUpperCase()} (${(h.evidence_score || 0).toFixed(1)}/100)
                    </span>
                </div>
                <div class="hypothesis-meta">
                    Category: <strong>${escapeHtml(h.root_cause_category)}</strong> · Component: <strong>${escapeHtml(h.affected_component)}</strong>
                </div>
            `;
            container.appendChild(card);
        });
    }

    section.style.display = 'block';
}

function renderRemediationSection(plan) {
    const section = document.getElementById('remediation-section');
    if (!section) return;

    if (!plan) {
        section.style.display = 'none';
        return;
    }

    section.style.display = 'block';

    // 1. Safety Notice Banner
    const safetyText = document.getElementById('remediation-safety-text');
    if (safetyText) {
        safetyText.textContent = plan.safety_notice || 'All recommendations are advisory guidance. Human review required.';
    }

    // 2. Risk Badge
    const riskBadge = document.getElementById('remediation-risk-badge');
    const riskVal = (plan.risk || 'blocked').toLowerCase();
    if (riskBadge) {
        riskBadge.textContent = `RISK: ${riskVal.toUpperCase()}`;
        riskBadge.className = `badge risk-badge risk-${riskVal}`;
    }

    const recAvail = Boolean(plan.recommendation_available);
    const blockedBanner = document.getElementById('remediation-blocked-banner');
    const targetMeta = document.getElementById('remediation-target-metadata');
    const prereqsContainer = document.getElementById('remediation-prereqs-container');
    const stepsContainer = document.getElementById('remediation-steps-container');
    const escalationContainer = document.getElementById('remediation-escalation-container');
    const uncertaintyContainer = document.getElementById('remediation-uncertainty-container');

    if (recAvail) {
        // Hide blocked banner
        if (blockedBanner) blockedBanner.style.display = 'none';

        // Target Metadata
        if (targetMeta) {
            targetMeta.style.display = 'grid';
            document.getElementById('remediation-hypothesis-id').textContent = plan.hypothesis_id || '—';
            document.getElementById('remediation-category').textContent = (plan.root_cause_category || '—').replace('_', ' ');
            document.getElementById('remediation-confidence').textContent = (plan.confidence || '—').toUpperCase();
            document.getElementById('remediation-evidence-ids').textContent = (plan.evidence_ids && plan.evidence_ids.length > 0)
                ? plan.evidence_ids.join(', ')
                : 'None';
        }

        // Prerequisites
        if (prereqsContainer) {
            const list = document.getElementById('remediation-prereqs-list');
            if (plan.prerequisites && plan.prerequisites.length > 0) {
                list.innerHTML = plan.prerequisites.map(p => `<li>${escapeHtml(p)}</li>`).join('');
                prereqsContainer.style.display = 'block';
            } else {
                prereqsContainer.style.display = 'none';
            }
        }

        // Remediation Steps
        if (stepsContainer) {
            const stepsList = document.getElementById('remediation-steps-list');
            stepsList.innerHTML = '';
            const steps = plan.steps || [];
            if (steps.length > 0) {
                steps.forEach(step => {
                    const stepCard = document.createElement('div');
                    stepCard.className = 'step-card';

                    const instructionsHtml = (step.instructions || [])
                        .map(inst => `<li>${escapeHtml(inst)}</li>`)
                        .join('');

                    let verificationHtml = '';
                    if (step.verification && step.verification.length > 0) {
                        verificationHtml = `
                            <details class="collapsible verification">
                                <summary>🔍 Success Verification Checks (${step.verification.length})</summary>
                                <div class="collapsible-body">
                                    <ul>
                                        ${step.verification.map(v => `<li>${escapeHtml(v)}</li>`).join('')}
                                    </ul>
                                </div>
                            </details>
                        `;
                    }

                    let rollbackHtml = '';
                    if (step.rollback_guidance && step.rollback_guidance.length > 0) {
                        rollbackHtml = `
                            <details class="collapsible rollback">
                                <summary>↩ Safe Rollback Guidance (${step.rollback_guidance.length})</summary>
                                <div class="collapsible-body">
                                    <ul>
                                        ${step.rollback_guidance.map(rb => `<li>${escapeHtml(rb)}</li>`).join('')}
                                    </ul>
                                </div>
                            </details>
                        `;
                    }

                    stepCard.innerHTML = `
                        <div class="step-header">
                            <span class="step-title">Step ${step.step_number}: ${escapeHtml(step.title)}</span>
                            <span class="approval-badge">Requires Human Approval: Mandatory</span>
                        </div>
                        <div class="step-body">
                            <div class="step-purpose"><strong>Purpose:</strong> ${escapeHtml(step.purpose)}</div>
                            <ol class="step-instructions">
                                ${instructionsHtml}
                            </ol>
                            <div class="step-expected"><strong>Expected Result:</strong> ${escapeHtml(step.expected_result)}</div>
                            ${verificationHtml}
                            ${rollbackHtml}
                        </div>
                    `;
                    stepsList.appendChild(stepCard);
                });
                stepsContainer.style.display = 'block';
            } else {
                stepsContainer.style.display = 'none';
            }
        }

        // Hide blocked guidance sections if empty
        if (escalationContainer) escalationContainer.style.display = 'none';
        if (uncertaintyContainer) uncertaintyContainer.style.display = 'none';

    } else {
        // Recommendation is unavailable / blocked
        if (blockedBanner) blockedBanner.style.display = 'flex';
        if (targetMeta) targetMeta.style.display = 'none';
        if (prereqsContainer) prereqsContainer.style.display = 'none';
        if (stepsContainer) stepsContainer.style.display = 'none';

        // Escalation Guidance
        if (escalationContainer) {
            const escList = document.getElementById('remediation-escalation-list');
            if (plan.escalation_guidance && plan.escalation_guidance.length > 0) {
                escList.innerHTML = plan.escalation_guidance.map(e => `<li>${escapeHtml(e)}</li>`).join('');
                escalationContainer.style.display = 'block';
            } else {
                escalationContainer.style.display = 'none';
            }
        }

        // Unresolved Uncertainty
        if (uncertaintyContainer) {
            const uncList = document.getElementById('remediation-uncertainty-list');
            if (plan.unresolved_uncertainty && plan.unresolved_uncertainty.length > 0) {
                uncList.innerHTML = plan.unresolved_uncertainty.map(u => `<li>${escapeHtml(u)}</li>`).join('');
                uncertaintyContainer.style.display = 'block';
            } else {
                uncertaintyContainer.style.display = 'none';
            }
        }
    }
}

function escapeHtml(text) {
    if (!text) return '';
    const map = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;'
    };
    return String(text).replace(/[&<>"']/g, m => map[m]);
}
