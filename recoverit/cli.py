"""Rich terminal command-line interface for RecoverIT.

Provides interactive triage, scenario benchmarks, live repository scanning,
and post-mortem incident report generation.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

from rich.console import Console
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from collectors.fixtures import (
    list_available_scenarios,
    load_scenario_json,
    resolve_scenario_name,
)
from recoverit.runner import InvestigationResult, InvestigationRunner

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

console = Console(force_terminal=True, legacy_windows=False)

BANNER = """
[color(109)]──────────────────────────────────────────────────────────────[/color(109)]
[bold color(109)]  RecoverIT  ·  Autonomous CI/CD Incident Investigator[/bold color(109)]
[grey50]  v0.1.0  ·  Evidence-led, multi-source root-cause analysis[/grey50]
[color(109)]──────────────────────────────────────────────────────────────[/color(109)]
"""

TRACE_STYLES = {
    "status": ("STATUS", "color(109)"),
    "assessment": ("ASSESSMENT", "color(139)"),
    "reasoning": ("RATIONALE", "color(139)"),
    "action": ("TOOL CALL", "color(110)"),
    "observation": ("TOOL RESULT", "color(108)"),
    "decision": ("DECISION", "color(179)"),
    "warning": ("WARNING", "color(167)"),
    "trace_step": ("INVESTIGATION STEP", "color(110)"),
}


class CLITraceRenderer:
    """Render genuine orchestration events as a compact terminal activity trace."""

    def __init__(self, target_console: Console = console) -> None:
        self.console = target_console
        self.event_count = 0
        self.trace_steps: list[dict[str, Any]] = []

    def __call__(self, event: dict[str, object]) -> None:
        self.event_count += 1
        kind = str(event.get("kind", "status"))

        # Render structured 5-part investigation trace
        if kind == "trace_step":
            self.trace_steps.append(event)
            self._render_trace_step(event)
            return

        # Suppress raw collect-stage events to avoid clutter; trace_step renders the unified block
        if kind in ("action", "observation") and event.get("stage") == "collect":
            return

        label, style = TRACE_STYLES.get(kind, TRACE_STYLES["status"])
        title = str(event.get("title", "Investigation activity"))
        detail = str(event.get("detail", ""))
        output = event.get("output")
        metadata = event.get("metadata") or {}
        round_number = metadata.get("round") if isinstance(metadata, dict) else None
        round_label = f" · round {round_number}" if isinstance(round_number, int) and round_number > 0 else ""

        heading = Text()
        heading.append("● ", style=style)
        heading.append(title, style="bold grey82")
        heading.append(f"  {label}{round_label}", style=f"bold {style}")
        self.console.print(heading)

        if detail:
            self.console.print(Padding(Text(detail, style="grey62"), (0, 0, 0, 3)))

        if output:
            output_title = {
                "assessment": "assessment output",
                "action": "tool input",
                "observation": "received output",
                "decision": "decision output",
                "warning": "received output",
            }.get(kind, "agent context")
            self.console.print(
                Padding(
                    Panel(
                        Text(str(output), style="grey74"),
                        title=output_title,
                        title_align="left",
                        border_style=style,
                        padding=(0, 1),
                    ),
                    (0, 0, 1, 3),
                )
            )

    def _render_trace_step(self, event: dict[str, Any]) -> None:
        """Render a structured 5-part tool execution trace with rich visual styling."""
        metadata = event.get("metadata") or {}
        tool_result = event.get("tool_result") or {}
        round_num = metadata.get("round", 1)
        source_type = str(metadata.get("source_type", "source")).upper()
        adapter_type = str(tool_result.get("adapter_type") or metadata.get("adapter_type", "Recorded Replay"))
        adapter_name = str(tool_result.get("adapter_name") or metadata.get("adapter_name", "adapter"))
        latency_ms = float(tool_result.get("latency_ms") or metadata.get("latency_ms", 0.0))
        records_matched = int(tool_result.get("records_matched") or metadata.get("records_matched", 0))
        provider_name = str(metadata.get("provider_name", "Deterministic Preset"))

        rationale = str(event.get("rationale") or event.get("detail", ""))
        tool_call = str(event.get("tool_call") or "")
        interpretation = str(event.get("interpretation") or "")
        next_decision = str(event.get("next_decision") or "")

        # Adapter badge: Live Source is green, Recorded Replay is amber
        if adapter_type == "Live Source":
            adapter_badge = f"[bold color(108)]● Live Source ({adapter_name})[/bold color(108)]"
        else:
            adapter_badge = f"[bold color(179)]⟳ Recorded Replay ({adapter_name})[/bold color(179)]"

        # Step Header
        self.console.print(
            f"\n[bold color(110)]╭─── INVESTIGATION STEP · Round {round_num} · {source_type} ──────────────────────────────────────[/bold color(110)]"
        )
        self.console.print(
            f"[dim]│  Adapter:[/dim] {adapter_badge}  [dim]· Provider:[/dim] [color(139)]{provider_name}[/color(139)]  [dim]· Latency:[/dim] [white]{latency_ms:.2f}ms[/white]"
        )
        self.console.print("[dim]│[/dim]")

        # 1. RATIONALE
        self.console.print("  [bold color(109)]RATIONALE[/bold color(109)]")
        self.console.print(Padding(Text(rationale, style="grey82"), (0, 0, 1, 2)))

        # 2. TOOL CALL
        self.console.print("  [bold color(110)]TOOL CALL[/bold color(110)]")
        self.console.print(
            Padding(
                Panel(
                    Text(tool_call, style="bold color(110)"),
                    border_style="color(110)",
                    padding=(0, 1),
                ),
                (0, 0, 1, 2),
            )
        )

        # 3. TOOL RESULT
        self.console.print("  [bold color(108)]TOOL RESULT[/bold color(108)]")
        result_lines = [
            f"[bold]{records_matched} matching record(s)[/bold] [dim]({adapter_type})[/dim]"
        ]
        previews = tool_result.get("records_preview") or []
        if isinstance(previews, list) and previews:
            for p in previews:
                result_lines.append(f"  • [white]{p}[/white]")
        elif event.get("output"):
            result_lines.append(f"  • [white]{event.get('output')}[/white]")
        else:
            result_lines.append("  • [dim]No records returned matching search parameters.[/dim]")

        warnings = tool_result.get("warnings") or metadata.get("warnings") or []
        if isinstance(warnings, list) and warnings:
            for w in warnings:
                result_lines.append(f"  [bold color(167)]⚠ Warning: {w}[/bold color(167)]")

        self.console.print(
            Padding(
                Panel(
                    "\n".join(result_lines),
                    title=f"[color(108)]{source_type} Output Preview[/color(108)]",
                    title_align="left",
                    border_style="color(108)",
                    padding=(0, 1),
                ),
                (0, 0, 1, 2),
            )
        )

        # 4. INTERPRETATION
        if interpretation:
            self.console.print("  [bold color(139)]INTERPRETATION[/bold color(139)]")
            self.console.print(
                Padding(
                    Panel(
                        Text(interpretation, style="grey85 italic"),
                        title="[color(139)]Agent Synthesis[/color(139)]",
                        title_align="left",
                        border_style="color(139)",
                        padding=(0, 1),
                    ),
                    (0, 0, 1, 2),
                )
            )

        # 5. NEXT DECISION
        if next_decision:
            self.console.print("  [bold color(179)]NEXT DECISION[/bold color(179)]")
            self.console.print(
                Padding(
                    Panel(
                        Text(next_decision, style="bold color(179)"),
                        title="[color(179)]Actionable Next Step[/color(179)]",
                        title_align="left",
                        border_style="color(179)",
                        padding=(0, 1),
                    ),
                    (0, 0, 1, 2),
                )
            )

        self.console.print(
            f"[bold color(110)]╰─────────────────────────────────────────────────────────────────────────────[/bold color(110)]\n"
        )


def render_banner() -> None:
    console.print(BANNER)


def render_trace_header() -> None:
    console.print(
        Panel(
            "[grey70]Live events below come from the real investigation loop. "
            "Rationale is a concise explanation of structured decisions; source previews are "
            "sanitized observations returned by tools.[/grey70]",
            title="[bold color(109)]Agent investigation trace[/bold color(109)]",
            border_style="color(109)",
        )
    )


def list_command(args: argparse.Namespace) -> None:
    """List all available pre-packaged scenario families."""
    render_banner()
    table = Table(title="Available Incident Alert Triggers", header_style="bold color(139)")
    table.add_column("Scenario ID", style="bold color(109)", no_wrap=True)
    table.add_column("Alias", style="dim", no_wrap=True)
    table.add_column("Target Service", style="color(108)")
    table.add_column("Incoming Alert Symptom", style="white")
    table.add_column("Trigger Severity", style="color(167)")

    for name in list_available_scenarios(only_canonical=True):
        data = load_scenario_json(name)
        alias = resolve_scenario_name(name)
        symptom = data.get("title", "")
        severity = "HIGH" if "P2" in symptom else "CRITICAL"
        table.add_row(name, alias, data.get("service", "unknown"), symptom, severity)

    console.print(table)
    console.print("\n[dim]Investigate any alert using:[/dim] [bold color(109)]python -m recoverit.cli run --scenario <id>[/bold color(109)]\n")


def display_results(
    res: InvestigationResult,
    report_path: str | None = None,
    reveal_ground_truth: bool = False,
    scenario_name: str | None = None,
) -> None:
    """Pretty-print investigation outcome using Rich components."""
    # Summary panel
    status_style = "bold color(108)" if res.status == "completed" else "bold color(179)"
    summary_text = Text()
    summary_text.append(f"Incident ID: ", style="bold")
    summary_text.append(f"{res.incident_id}\n", style="color(109)")
    summary_text.append(f"Target Service: ", style="bold")
    summary_text.append(f"{res.service}\n", style="white")
    summary_text.append(f"Status: ", style="bold")
    summary_text.append(f"{res.status.upper()}\n", style=status_style)
    if res.stop_reason:
        summary_text.append("Stop Reason: ", style="bold")
        summary_text.append(f"{res.stop_reason}\n", style="color(179)")
    summary_text.append(f"Reasoning Provider: ", style="bold")
    summary_text.append(f"{res.provider_used}\n", style="color(139)")
    summary_text.append(f"Analysis Duration: ", style="bold")
    summary_text.append(f"{res.execution_time_seconds:.2f} seconds\n", style="white")
    summary_text.append(f"Alert Summary: ", style="bold")
    summary_text.append(f"{res.summary}", style="italic")

    console.print(Panel(summary_text, title="[bold]Incident Investigation Overview[/bold]", border_style="color(109)"))

    # Evidentiary Completion Requirements
    if getattr(res, "completion_criteria", None):
        c_table = Table(title="Evidentiary Completion Requirements", header_style="bold color(110)")
        c_table.add_column("Completion Requirement", style="white")
        c_table.add_column("Status", style="bold", justify="center")
        for crit, ok in res.completion_criteria.items():
            name = crit.replace("_", " ").title()
            mark = "[color(108)]PASS (Met)[/color(108)]" if ok else "[color(167)]FAIL (Unresolved)[/color(167)]"
            c_table.add_row(name, mark)
        console.print(c_table)

    if getattr(res, "unresolved_criteria", None):
        console.print("[bold color(179)]Unresolved Completion Requirements:[/bold color(179)]")
        for uc in res.unresolved_criteria:
            console.print(f"  • [color(179)]{uc}[/color(179)]")

    # Reconstructed Timeline Table
    if res.timeline_events:
        t_table = Table(title="Reconstructed Incident Chronology", header_style="bold color(110)")
        t_table.add_column("Time (UTC)", style="color(109)", no_wrap=True)
        t_table.add_column("Category", style="color(179)")
        t_table.add_column("Observation / Event", style="white")
        t_table.add_column("Service", style="color(108)")

        for ev in res.timeline_events:
            ts = ev.get("event_time") or "Time unknown"
            cat = str(ev.get("category", "")).replace("_", " ").title()
            t_table.add_row(ts, cat, ev.get("title", ""), ev.get("service", ""))

        console.print(t_table)

    # Diff Excerpts
    if res.diff_excerpts:
        for cid, diff in res.diff_excerpts.items():
            diff_lines = diff.strip().splitlines()[:25]
            preview = "\n".join(diff_lines)
            console.print(
                Panel(
                    f"[dim]Commit SHA: {cid}[/dim]\n\n{preview}",
                    title="[bold color(167)]Associated Code / Config Diff[/bold color(167)]",
                    border_style="color(167)",
                )
            )

    # Ranked Hypotheses
    console.print("\n[bold]🎯 Ranked Root-Cause Hypotheses[/bold]")
    if not res.ranked_hypotheses:
        console.print("[color(179)]No root-cause hypotheses met confidence criteria.[/color(179)]")
    else:
        for h in res.ranked_hypotheses:
            rank = h["rank"]
            conf = h["confidence_label"].upper()
            score = h["evidence_score"]
            cat = h["root_cause_category"].replace("_", " ").title()
            comp = h["affected_component"]
            stmt = h["statement"]

            conf_style = "color(108)" if conf == "HIGH" else ("color(179)" if conf == "MEDIUM" else "color(167)")
            card_lines = [
                f"[bold white]{stmt}[/bold white]",
                f"[dim]Category:[/dim] [color(179)]{cat}[/color(179)] | [dim]Component:[/dim] [color(109)]{comp}[/color(109)] | [dim]Score:[/dim] [bold]{score:.1f}/100[/bold] ([{conf_style}]{conf}[/{conf_style}])",
            ]

            bd = h.get("score_breakdown", {})
            if bd:
                symptom_conf = bd.get("symptom_score", 0.0)
                causal_conf = bd.get("causal_score", 0.0)
                card_lines.append(
                    f"[dim]Symptom Confidence:[/dim] [bold color(109)]{symptom_conf:.1f}%[/bold color(109)] | "
                    f"[dim]Causal Confidence:[/dim] [bold color(109)]{causal_conf:.1f}%[/bold color(109)]"
                )

                if bd.get("capped_reason"):
                    card_lines.append(f"\n[bold color(179)]⚠ {bd['capped_reason']}[/bold color(179)]")

            citations = h.get("supporting_evidence", [])
            if citations:
                card_lines.append("\n[bold color(108)]Corroborating Citations:[/bold color(108)]")
                for c in citations:
                    card_lines.append(f"  - [color(109)]{c['evidence_id']}[/color(109)]: {c.get('reason', '')}")

            contradictions = h.get("contradicting_evidence", [])
            if contradictions:
                card_lines.append("\n[bold color(167)]Contradicting Evidence:[/bold color(167)]")
                for c in contradictions:
                    card_lines.append(f"  - [color(109)]{c['evidence_id']}[/color(109)]: {c.get('reason', '')}")

            if bd:
                card_lines.append("\n[bold color(110)]Confidence Breakdown:[/bold color(110)]")
                items = [
                    ("Independent sources", "+", bd.get("independent_source_support", 0.0)),
                    ("Symptom coverage", "+", bd.get("symptom_coverage", 0.0)),
                    ("Temporal consistency", "+", bd.get("temporal_consistency", 0.0)),
                    ("Direct change evidence", "+", bd.get("change_consistency", 0.0)),
                    ("Specificity", "+", bd.get("specificity", 0.0)),
                    ("Prediction support", "+", bd.get("prediction_support", 0.0)),
                    ("Contradictions", "-" if bd.get("contradiction_penalty", 0.0) > 0 else " ", bd.get("contradiction_penalty", 0.0)),
                    ("Missing causal evidence", "-" if bd.get("missing_evidence_penalty", 0.0) > 0 else " ", bd.get("missing_evidence_penalty", 0.0)),
                ]
                for name, sign, val in items:
                    sign_style = "color(108)" if sign == "+" else ("color(167)" if sign == "-" else "dim")
                    card_lines.append(f"  • {name:<26} [{sign_style}]{sign}{val:5.2f}[/{sign_style}]")
                card_lines.append(f"  [dim]──────────────────────────────────────[/dim]")
                card_lines.append(f"  • {'Final evidence score':<26} [bold]{score:5.2f} / 100[/bold]")

            border_color = "color(108)" if rank == 1 else "color(110)"
            console.print(Panel("\n".join(card_lines), title=f"[bold]Rank #{rank} — {conf} Confidence[/bold]", border_style=border_color))

    # Save Markdown report if requested
    if report_path:
        out_file = Path(report_path)
        out_file.write_text(res.to_markdown_report(), encoding="utf-8")
        console.print(f"\n[bold color(108)][OK] Post-mortem report saved to:[/bold color(108)] {out_file.resolve()}")

    # Ground-Truth Benchmark Evaluation
    if reveal_ground_truth:
        from benchmarks.evaluator import BenchmarkEvaluator
        eval_res = BenchmarkEvaluator.evaluate(res, scenario_name=scenario_name)
        render_benchmark_evaluation(eval_res)


def render_benchmark_evaluation(eval_res: Any) -> None:
    """Render the hidden ground truth benchmark scorecard."""
    cat_badge = "[color(108)]PASS (Yes)[/color(108)]" if eval_res.category_match else "[color(167)]FAIL (Mismatch)[/color(167)]"
    rec_badge = "[color(108)]PASS (Yes)[/color(108)]" if eval_res.causal_record_cited else "[color(167)]FAIL (Not Cited)[/color(167)]"
    overall_badge = "[bold color(108)]SUCCESS (All Criteria Met)[/bold color(108)]" if eval_res.overall_success else "[bold color(167)]INCOMPLETE (Gap Detected)[/bold color(167)]"

    gt_text = Text()
    gt_text.append(f"Scenario ID:             ", style="bold")
    gt_text.append(f"{eval_res.scenario_id}\n", style="color(109)")
    gt_text.append(f"Expected Root Cause:     ", style="bold")
    gt_text.append(f"{eval_res.expected_root_cause}\n", style="white")
    gt_text.append(f"Leading Hypothesis:      ", style="bold")
    gt_text.append(f"{eval_res.leading_hypothesis_statement}\n", style="italic")
    gt_text.append(f"Expected Category:       ", style="bold")
    gt_text.append(f"{eval_res.expected_category}\n", style="color(179)")
    gt_text.append(f"Predicted Category:      ", style="bold")
    gt_text.append(f"{eval_res.predicted_category}  →  {cat_badge}\n", style="bold")
    gt_text.append(f"Expected Causal Record:  ", style="bold")
    gt_text.append(f"{eval_res.expected_causal_record}\n", style="color(179)")
    gt_text.append(f"Cited in Hypothesis:     ", style="bold")
    gt_text.append(f"{rec_badge}\n", style="bold")
    gt_text.append(f"Benchmark Outcome:       ", style="bold")
    gt_text.append(f"{overall_badge}\n", style="bold")

    console.print(
        Panel(
            gt_text,
            title="[bold color(139)]Benchmark Ground-Truth Evaluation[/bold color(139)]",
            border_style="color(139)",
        )
    )


async def run_scenario_flow(
    scenario: str,
    mode: str,
    provider: str,
    model: str | None,
    report: str | None,
    reveal_ground_truth: bool = False,
) -> None:
    runner = InvestigationRunner()
    render_banner()
    render_trace_header()
    trace = CLITraceRenderer()
    res = await runner.run_scenario(
        scenario_name=scenario,
        mode=mode,
        provider=provider,
        llm_model=model,
        progress_callback=trace,
    )

    if res.status == "completed":
        console.print("\n[bold color(108)]Investigation completed successfully.[/bold color(108)]\n")
    else:
        console.print(
            f"\n[bold color(179)]Investigation ended {res.status}: "
            f"{res.stop_reason or 'no stop reason provided'}.[/bold color(179)]\n"
        )
    display_results(
        res,
        report_path=report,
        reveal_ground_truth=reveal_ground_truth,
        scenario_name=scenario,
    )


async def run_benchmark_flow(
    scenarios: list[str],
    mode: str,
    provider: str,
    model: str | None,
    report_path: str | None,
) -> None:
    from benchmarks.evaluator import BenchmarkEvaluator
    render_banner()
    console.print(
        Panel(
            f"[bold color(139)]Evaluating {len(scenarios)} Benchmark Incident Scenarios[/bold color(139)]\n"
            f"[dim]Mode: {mode} · Provider: {provider} · Model: {model or 'default'}[/dim]",
            title="[bold]Benchmark Test Suite Runner[/bold]",
            border_style="color(139)",
        )
    )

    results = await BenchmarkEvaluator.run_suite(
        scenarios=scenarios,
        mode=mode,
        provider=provider,
        llm_model=model,
    )

    table = Table(title="Autonomous Incident Triage Benchmark Scorecard", header_style="bold color(139)")
    table.add_column("Scenario ID", style="bold color(109)", no_wrap=True)
    table.add_column("Target Service", style="color(108)")
    table.add_column("Category Match", justify="center")
    table.add_column("Expected Record", style="dim")
    table.add_column("Causal Citation", justify="center")
    table.add_column("Leading Score", justify="right")
    table.add_column("Benchmark Status", justify="center")

    total = len(results)
    cat_matches = sum(1 for r in results if r.category_match)
    cite_matches = sum(1 for r in results if r.causal_record_cited)
    overall_passes = sum(1 for r in results if r.overall_success)

    for r in results:
        cat_str = "[color(108)]PASS (Yes)[/color(108)]" if r.category_match else "[color(167)]FAIL (Mismatch)[/color(167)]"
        cite_str = "[color(108)]PASS (Yes)[/color(108)]" if r.causal_record_cited else "[color(167)]FAIL (Missing)[/color(167)]"
        status_str = "[bold color(108)]PASS[/bold color(108)]" if r.overall_success else "[bold color(167)]FAIL[/bold color(167)]"
        table.add_row(
            r.scenario_id,
            r.service,
            cat_str,
            r.expected_causal_record,
            cite_str,
            f"{r.leading_score:.1f}/100",
            status_str,
        )

    console.print(table)

    cat_pct = (cat_matches / total * 100.0) if total > 0 else 0.0
    cite_pct = (cite_matches / total * 100.0) if total > 0 else 0.0
    overall_pct = (overall_passes / total * 100.0) if total > 0 else 0.0

    summary_text = (
        f"Evaluated Scenarios: [bold]{total}[/bold] | "
        f"Category Accuracy: [bold color(108)]{cat_matches}/{total} ({cat_pct:.1f}%)[/bold color(108)] | "
        f"Causal Citation Rate: [bold color(108)]{cite_matches}/{total} ({cite_pct:.1f}%)[/bold color(108)] | "
        f"Overall Benchmark: [bold]{overall_passes}/{total} ({overall_pct:.1f}%)[/bold]"
    )
    console.print(
        Panel(
            summary_text,
            title="[bold]Summary Performance Metrics[/bold]",
            border_style="color(108)" if overall_passes == total else "color(179)",
        )
    )

    if report_path:
        out_file = Path(report_path)
        lines = [
            "# RecoverIT Benchmark Evaluation Report",
            "",
            f"- **Scenarios Evaluated:** {total}",
            f"- **Category Accuracy:** {cat_matches}/{total} ({cat_pct:.1f}%)",
            f"- **Causal Citation Rate:** {cite_matches}/{total} ({cite_pct:.1f}%)",
            f"- **Overall Benchmark Pass Rate:** {overall_passes}/{total} ({overall_pct:.1f}%)",
            "",
            "| Scenario ID | Service | Expected Category | Predicted Category | Category Match | Expected Record | Causal Citation | Status |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for r in results:
            lines.append(
                f"| `{r.scenario_id}` | `{r.service}` | `{r.expected_category}` | `{r.predicted_category}` | "
                f"{'PASS' if r.category_match else 'FAIL'} | `{r.expected_causal_record}` | "
                f"{'PASS' if r.causal_record_cited else 'FAIL'} | **{'PASS' if r.overall_success else 'FAIL'}** |"
            )
        out_file.write_text("\n".join(lines), encoding="utf-8")
        console.print(f"\n[bold color(108)][OK] Benchmark report saved to:[/bold color(108)] {out_file.resolve()}")


async def scan_repo_flow(
    repo: str,
    logs: str | None,
    service: str,
    mode: str,
    provider: str,
    model: str | None,
    summary: str,
    report: str | None,
) -> None:
    runner = InvestigationRunner()
    render_banner()

    repo_path = Path(repo).resolve()
    if not repo_path.exists():
        console.print(f"[bold color(167)]Error:[/bold color(167)] Repository path '{repo}' does not exist.")
        sys.exit(1)
    render_trace_header()
    trace = CLITraceRenderer()
    res = await runner.run_target(
        repo_path=repo_path,
        log_path=logs,
        service_name=service,
        summary=summary,
        mode=mode,
        provider=provider,
        llm_model=model,
        progress_callback=trace,
    )

    if res.status == "completed":
        console.print("\n[bold color(108)]Repository triage completed successfully.[/bold color(108)]\n")
    else:
        console.print(
            f"\n[bold color(179)]Repository triage ended {res.status}: "
            f"{res.stop_reason or 'no stop reason provided'}.[/bold color(179)]\n"
        )
    display_results(res, report_path=report)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="recoverit",
        description="RecoverIT — Autonomous CI/CD Incident Triager & Self-Healer",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # list command
    subparsers.add_parser("list", help="List available incident benchmark scenarios")

    # run command
    run_parser = subparsers.add_parser("run", help="Run incident investigation on a scenario")
    run_parser.add_argument(
        "--scenario",
        "-s",
        default="incident_001",
        help="Scenario ID (default: incident_001; e.g. incident_001..incident_005 or aliases like bad_db_config)",
    )
    run_parser.add_argument(
        "--mode",
        "-m",
        choices=["auto", "offline", "live"],
        default="auto",
        help="Reasoning mode (auto detects LLM API keys; offline uses presets)",
    )
    run_parser.add_argument(
        "--provider",
        choices=["auto", "gemini", "openai", "ollama", "offline"],
        default="auto",
        help="Reasoning provider (default: auto)",
    )
    run_parser.add_argument(
        "--model",
        help="LLM model name (e.g. gpt-4o-mini, gemini-1.5-flash, qwen2.5:3b)",
    )
    run_parser.add_argument(
        "--report",
        "-r",
        help="Optional file path to save Markdown post-mortem report",
    )
    run_parser.add_argument(
        "--reveal-ground-truth",
        action="store_true",
        help="Reveal and evaluate against hidden benchmark ground truth after investigation completes",
    )

    # benchmark command
    bench_parser = subparsers.add_parser("benchmark", help="Run automated ground-truth benchmark suite across scenarios")
    bench_parser.add_argument(
        "--scenarios",
        "-s",
        nargs="+",
        default=["incident_001", "incident_002", "incident_003", "incident_004", "incident_005"],
        help="List of scenarios to benchmark (default: all canonical scenarios)",
    )
    bench_parser.add_argument(
        "--mode",
        "-m",
        choices=["auto", "offline", "live"],
        default="offline",
        help="Reasoning mode (default: offline)",
    )
    bench_parser.add_argument(
        "--provider",
        choices=["auto", "gemini", "openai", "ollama", "offline"],
        default="offline",
        help="Reasoning provider (default: offline)",
    )
    bench_parser.add_argument(
        "--model",
        help="LLM model name",
    )
    bench_parser.add_argument(
        "--report",
        "-r",
        help="Optional file path to save Benchmark Markdown Report",
    )

    # scan command
    scan_parser = subparsers.add_parser("scan", help="Scan a local Git repository and log file")
    scan_parser.add_argument(
        "--repo",
        "-p",
        default=".",
        help="Local repository directory (default: current directory)",
    )
    scan_parser.add_argument(
        "--logs",
        "-l",
        help="Path to application log file (.log, .txt, or JSON)",
    )
    scan_parser.add_argument(
        "--service",
        default="target-service",
        help="Name of the service being investigated",
    )
    scan_parser.add_argument(
        "--mode",
        "-m",
        choices=["auto", "offline", "live"],
        default="auto",
        help="Reasoning mode",
    )
    scan_parser.add_argument(
        "--provider",
        choices=["auto", "gemini", "openai", "ollama", "offline"],
        default="auto",
        help="Reasoning provider (default: auto)",
    )
    scan_parser.add_argument(
        "--model",
        help="LLM model name (e.g. qwen2.5:3b, gpt-4o-mini, gemini-1.5-flash)",
    )
    scan_parser.add_argument(
        "--alert",
        "--summary",
        dest="summary",
        default="[P1 CRITICAL] Operational degradation detected",
        help="Incident symptom / alert title",
    )
    scan_parser.add_argument(
        "--report",
        "-r",
        help="Optional file path to save Markdown post-mortem report",
    )

    # ui / serve command
    ui_parser = subparsers.add_parser("ui", help="Launch the interactive Web Dashboard")
    ui_parser.add_argument("--port", type=int, default=8000, help="Port to listen on (default: 8000)")
    ui_parser.add_argument("--host", default="127.0.0.1", help="Host interface (default: 127.0.0.1)")

    args = parser.parse_args()

    if args.command == "list":
        list_command(args)
    elif args.command == "run":
        asyncio.run(
            run_scenario_flow(
                args.scenario,
                args.mode,
                args.provider,
                args.model,
                args.report,
                reveal_ground_truth=getattr(args, "reveal_ground_truth", False),
            )
        )
    elif args.command == "benchmark":
        asyncio.run(
            run_benchmark_flow(
                args.scenarios,
                args.mode,
                args.provider,
                args.model,
                args.report,
            )
        )
    elif args.command == "scan":
        asyncio.run(
            scan_repo_flow(
                args.repo,
                args.logs,
                args.service,
                args.mode,
                args.provider,
                args.model,
                args.summary,
                args.report,
            )
        )
    elif args.command == "ui":
        from recoverit.serve import start_server
        start_server(host=args.host, port=args.port)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
