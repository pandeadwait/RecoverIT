# Project I report outline

The report templates, drafts, and supporting media are in [Report/](../../Report/).

**Working title:** RecoverIT: Autonomous CI/CD Incident Investigation and Evidence-Based Remediation Recommendations

**Format authority:** `BCE497J PROJECT REPORT TEMPLATE.docx` and `BCSE497J_Project_I_Guidelines.pdf`. The report is an individual submission. The written template specifications govern A4 size, 3.81 cm left margin, 2.54 cm top/right/bottom margins, Times New Roman 12 pt body text, 1.5 line spacing, chapter headings at 16 pt bold, section headings at 14 pt uppercase, subsection headings at 12 pt uppercase, bottom-centred Arabic body-page numbers, and APA references. The template's logo and certificate language are retained. Target length: 60–100 pages.

## Preliminary pages

1. Cover page, retaining template logo and title-page arrangement. List all three team members on cover and title page, as the template directs. Programme: B.Tech in Computer Science and Engineering with specialization in Artificial Intelligence and Machine Learning.
2. Title page, same content and artwork.
3. Individual declaration, candidate identity blank pending confirmation.
4. Bonafide certificate, with guide and examiner signatures left blank; use the template certificate wording and logo.
5. Abstract, summarising the implemented investigation workflow, evidence-linked ranking, and recommendation-only remediation.
6. Acknowledgement, with only the individual candidate name blank.
7. Contents; list of figures; list of tables; list of acronyms.

## Chapter 1 Introduction

1.1 Background and motivation: operational incidents across CI/CD and runtime signals.
1.2 Problem statement: fragmented evidence, uncertainty, and time pressure.
1.3 Objectives and measurable outcomes.
1.4 Scope and boundaries: configured read-only sources, investigation, ranked hypotheses, advisory remediation. Production write actions and recovery verification are future work.
1.5 Project contributions and report organisation.

## Chapter 2 Literature Review

2.1 Review method and source selection from the supplied literature survey.
2.2 Agentic root-cause analysis and tool use.
2.3 Log analysis and evidence selection.
2.4 Microservice RCA, causal inference, and benchmark design.
2.5 Multi-agent and graph-assisted investigation.
2.6 CI/CD failure diagnosis and remediation research.
2.7 Comparative table of relevant work, limitations, and implications.
2.8 Research gap addressed by RecoverIT.

## Chapter 3 Methodology and System Design

3.1 Requirements, assumptions, threat and trust boundaries.
3.2 Architecture and data flow from alert to report.
3.3 Typed contracts and runtime configuration.
3.4 Read-only source discovery and collection.
3.5 Evidence validation, normalization, identity, redaction, and deduplication.
3.6 Timeline and context construction.
3.7 LangGraph investigation state, routing, budgets, and checkpoints.
3.8 Hypothesis generation, deterministic scoring, and abstention.
3.9 Remediation recommendation policy and safety validation.
3.10 Interface design: CLI, HTTP API, and dashboard.
3.11 Testing and evaluation plan.

## Chapter 4 Implementation and Results

4.1 Development environment and package structure.
4.2 Implemented modules mapped to source files.
4.3 End-to-end execution and output structure.
4.4 Contract, unit, and integration testing.
4.5 Current verification result: 506 passed, 40 subtests passed, one deprecation warning on 12 October 2026.
4.6 Analysis of supported outcomes and safety behaviour.
4.7 Limitations: no claim of production incident accuracy, time-to-resolution improvement, automatic infrastructure changes, or post-action recovery verification without evidence.

## Chapter 5 Conclusion and Future Work

5.1 Work completed and conclusions supported by repository/tests.
5.2 Remaining limitations.
5.3 Planned deployment-scale evaluation, calibration, controlled actions with approval, and post-action verification.

## References and appendices

- APA-style references for the literature actually cited.
- Appendix 1: API and CLI inputs/outputs.
- Appendix 2: data contracts and source capability map.
- Appendix 3: representative test and validation matrix.
- Appendix 4: individual contribution statement with candidate-specific details left blank until confirmed.

## Factual controls

- Distinguish the older architecture proposal and slide demonstrations from the present code.
- Treat `docs/` plans as design context, and source modules plus current tests as implementation evidence.
- Do not repeat unverified performance numbers from the literature as this project's own results.
- Do not imply write-capable remediation or closed-loop production healing exists in the current runtime.
