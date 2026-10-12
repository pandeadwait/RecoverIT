"""Rich terminal command-line interface for RecoverIT.

Provides live LangGraph investigation and post-mortem report generation.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys
from typing import Any

from rich.console import Console
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from contracts.errors.schemas import ProgressEvent
from contracts.incident.schemas import IncidentSeed
from contracts.investigation.schemas import InvestigationBudget
from recoverit.composition import (
    RuntimeConfigurationError,
    build_runtime,
    load_runtime_settings,
)
from recoverit.runner import (
    InvestigationResult,
    InvestigationRunner,
)

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

    def __call__(self, event: ProgressEvent | dict[str, object]) -> None:
        """Render both typed LangGraph events and legacy dictionary events."""

        if isinstance(event, ProgressEvent):
            event = event.model_dump(mode="json")
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


def display_results(
    res: InvestigationResult,
    report_path: str | None = None,
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

    # Suggested Remediation Plan
    if getattr(res, "remediation_plan", None):
        plan = res.remediation_plan
        console.print("\n[bold]🛠️ Suggested Remediation — Human Review Required[/bold]")

        safety_notice = plan.get("safety_notice", "Human review mandatory.")
        console.print(
            Panel(
                f"[bold color(167)]⚠ Safety Notice:[/bold color(167)] {safety_notice}",
                title="[bold]Human Operator Review Mandatory[/bold]",
                border_style="color(167)",
            )
        )

        risk_val = str(plan.get("risk", "blocked")).lower()
        risk_styles = {
            "low": "bold color(108)",
            "medium": "bold color(110)",
            "high": "bold color(179)",
            "blocked": "bold color(167)",
        }
        risk_style = risk_styles.get(risk_val, "bold color(167)")
        risk_badge = f"[{risk_style}]OPERATIONAL RISK: {risk_val.upper()}[/{risk_style}]"
        console.print(Padding(Text.from_markup(risk_badge), (0, 0, 1, 0)))

        rec_avail = plan.get("recommendation_available", False)
        if rec_avail:
            m_table = Table(box=None, show_header=False, padding=(0, 2))
            m_table.add_column("Key", style="dim")
            m_table.add_column("Value", style="white")

            hyp_id = plan.get("hypothesis_id")
            category = plan.get("root_cause_category")
            conf = plan.get("confidence")
            ev_ids = plan.get("evidence_ids", [])

            if hyp_id:
                m_table.add_row("Target Hypothesis", str(hyp_id))
            if category:
                m_table.add_row("Root Cause Category", str(category).replace("_", " ").title())
            if conf:
                m_table.add_row("Confidence", str(conf).upper())
            if ev_ids:
                m_table.add_row("Cited Evidence", ", ".join(str(e) for e in ev_ids))

            console.print(m_table)

            prereqs = plan.get("prerequisites", [])
            if prereqs:
                console.print("\n[bold color(110)]Prerequisites:[/bold color(110)]")
                for p in prereqs:
                    console.print(f"  • [white]{p}[/white]")

            steps = plan.get("steps", [])
            if steps:
                console.print("\n[bold color(108)]Remediation Steps:[/bold color(108)]")
                for s in steps:
                    s_num = s.get("step_number", 1)
                    s_title = s.get("title", "")
                    s_purpose = s.get("purpose", "")
                    s_inst = s.get("instructions", [])
                    s_exp = s.get("expected_result", "")
                    s_ver = s.get("verification", [])
                    s_rb = s.get("rollback_guidance", [])

                    step_lines = [
                        f"[bold color(109)]Purpose:[/bold color(109)] {s_purpose}",
                        f"[dim]Human Approval:[/dim] [bold color(108)]Mandatory[/bold color(108)]",
                        "",
                        "[bold color(110)]Instructions:[/bold color(110)]",
                    ]
                    for idx, inst in enumerate(s_inst, 1):
                        step_lines.append(f"  {idx}. [white]{inst}[/white]")
                    step_lines.append("")
                    step_lines.append(f"[bold color(108)]Expected Result:[/bold color(108)] {s_exp}")

                    if s_ver:
                        step_lines.append("")
                        step_lines.append("[bold color(139)]Verification:[/bold color(139)]")
                        for v in s_ver:
                            step_lines.append(f"  • [white]{v}[/white]")

                    if s_rb:
                        step_lines.append("")
                        step_lines.append("[bold color(179)]Rollback Guidance:[/bold color(179)]")
                        for rb in s_rb:
                            step_lines.append(f"  • [white]{rb}[/white]")

                    console.print(
                        Panel(
                            "\n".join(step_lines),
                            title=f"[bold]Step {s_num}: {s_title}[/bold]",
                            border_style="color(110)",
                            padding=(0, 1),
                        )
                    )
        else:
            console.print(
                Panel(
                    "[bold color(179)]No production change recommended. Operator escalation required.[/bold color(179)]",
                    border_style="color(179)",
                )
            )
            escalation = plan.get("escalation_guidance", [])
            if escalation:
                console.print("\n[bold color(179)]Escalation Guidance:[/bold color(179)]")
                for e in escalation:
                    console.print(f"  • [color(179)]{e}[/color(179)]")

            uncertainties = plan.get("unresolved_uncertainty", [])
            if uncertainties:
                console.print("\n[bold color(139)]Unresolved Uncertainty:[/bold color(139)]")
                for u in uncertainties:
                    console.print(f"  • [dim]{u}[/dim]")

    # Save Markdown report if requested
    if report_path:
        out_file = Path(report_path)
        out_file.write_text(res.to_markdown_report(), encoding="utf-8")
        console.print(f"\n[bold color(108)][OK] Post-mortem report saved to:[/bold color(108)] {out_file.resolve()}")

async def run_live_investigation_flow(
    runner: InvestigationRunner,
    incident: IncidentSeed,
    budget: InvestigationBudget | None = None,
    report: str | None = None,
) -> InvestigationResult:
    """Run one canonical incident through the graph-backed live runtime.

    Runtime composition remains outside the CLI so source credentials, clients,
    and benchmark fixtures can never be inferred from a command-line request.
    """

    render_banner()
    render_trace_header()
    trace = CLITraceRenderer()
    result = await runner.run(
        incident=incident,
        budget=budget,
        progress_callback=trace,
    )
    display_results(result, report_path=report)
    return result


def load_incident_seed(path: str | Path) -> IncidentSeed:
    """Load one generic, externally supplied incident request from JSON."""

    incident_path = Path(path)
    try:
        return IncidentSeed.model_validate_json(incident_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeConfigurationError(
            f"Could not read incident file '{incident_path}': {exc}"
        ) from exc
    except ValueError as exc:
        raise RuntimeConfigurationError(
            f"Incident file '{incident_path}' does not match IncidentSeed: {exc}"
        ) from exc


async def investigate_flow(
    config_path: str,
    incident_path: str,
    report: str | None,
) -> InvestigationResult:
    """Run the configured production runtime without scenario assumptions."""

    settings = load_runtime_settings(config_path)
    runtime = await build_runtime(settings)
    try:
        return await run_live_investigation_flow(
            InvestigationRunner(runtime),
            load_incident_seed(incident_path),
            report=report,
        )
    finally:
        await runtime.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="recoverit",
        description="RecoverIT — Autonomous CI/CD Incident Investigator",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    investigate_parser = subparsers.add_parser(
        "investigate",
        help="Run a configured live LangGraph investigation from an IncidentSeed JSON file",
    )
    investigate_parser.add_argument(
        "--config",
        required=True,
        help="Path to the validated live runtime JSON configuration",
    )
    investigate_parser.add_argument(
        "--incident",
        required=True,
        help="Path to a JSON file matching the IncidentSeed contract",
    )
    investigate_parser.add_argument(
        "--report",
        "-r",
        help="Optional file path to save the Markdown post-mortem report",
    )

    # ui / serve command
    ui_parser = subparsers.add_parser("ui", help="Launch the configured live LangGraph API")
    ui_parser.add_argument(
        "--config",
        required=True,
        help="Path to the validated live runtime JSON configuration",
    )
    ui_parser.add_argument("--port", type=int, default=8000, help="Port to listen on (default: 8000)")
    ui_parser.add_argument("--host", default="127.0.0.1", help="Host interface (default: 127.0.0.1)")

    args = parser.parse_args()

    try:
        if args.command == "investigate":
            asyncio.run(investigate_flow(args.config, args.incident, args.report))
        elif args.command == "ui":
            from recoverit.serve import start_server

            start_server(load_runtime_settings(args.config), host=args.host, port=args.port)
        else:
            parser.print_help()
    except RuntimeConfigurationError as exc:
        console.print(f"[bold color(167)]Configuration error:[/bold color(167)] {exc}")
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
