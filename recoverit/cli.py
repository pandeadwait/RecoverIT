"""Rich terminal command-line interface for RecoverIT.

Provides interactive triage, scenario benchmarks, live repository scanning,
and post-mortem incident report generation.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
import sys

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.text import Text

from collectors.fixtures import list_available_scenarios, load_scenario_json
from recoverit.runner import InvestigationResult, InvestigationRunner

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

console = Console(force_terminal=True, legacy_windows=False)

BANNER = """
[bold cyan]==============================================================[/bold cyan]
[bold cyan]  RecoverIT  -  Autonomous CI/CD Incident Triager & Self-Healer [/bold cyan]
[dim]  v0.1.0 • Multi-Source AI Root-Cause Investigation Platform [/dim]
[bold cyan]==============================================================[/bold cyan]
"""


def render_banner() -> None:
    console.print(BANNER)


def list_command(args: argparse.Namespace) -> None:
    """List all available pre-packaged scenario families."""
    render_banner()
    table = Table(title="Available Incident Alert Triggers", header_style="bold magenta")
    table.add_column("Scenario ID", style="cyan", no_wrap=True)
    table.add_column("Target Service", style="green")
    table.add_column("Incoming Alert Symptom", style="white")
    table.add_column("Trigger Severity", style="red")

    scenario_symptoms = {
        "bad_db_config": ("[P1] HTTP 500 Error Spike (>35% errors)", "CRITICAL"),
        "memory_exhaustion": ("[P1] Worker Pod CrashLoopBackOff (Exit Code 137)", "CRITICAL"),
        "dependency_incompatibility": ("[P2] Service Startup Failure after build", "HIGH"),
        "real_db_outage": ("[P1] Database Connection Pool Exhaustion", "CRITICAL"),
        "coincidental_deployment": ("[P1] Checkout Failure Rate Surge", "CRITICAL"),
    }

    for name in list_available_scenarios():
        data = load_scenario_json(name)
        symptom, severity = scenario_symptoms.get(name, (data.get("title", ""), "CRITICAL"))
        table.add_row(name, data.get("service", "unknown"), symptom, severity)

    console.print(table)
    console.print("\n[dim]Investigate any alert using:[/dim] [bold cyan]python -m recoverit.cli run --scenario <id>[/bold cyan]\n")


def display_results(res: InvestigationResult, report_path: str | None = None) -> None:
    """Pretty-print investigation outcome using Rich components."""
    # Summary panel
    status_style = "bold green" if res.status == "completed" else "bold yellow"
    summary_text = Text()
    summary_text.append(f"Incident ID: ", style="bold")
    summary_text.append(f"{res.incident_id}\n", style="cyan")
    summary_text.append(f"Target Service: ", style="bold")
    summary_text.append(f"{res.service}\n", style="white")
    summary_text.append(f"Status: ", style="bold")
    summary_text.append(f"{res.status.upper()}\n", style=status_style)
    summary_text.append(f"Reasoning Provider: ", style="bold")
    summary_text.append(f"{res.provider_used}\n", style="magenta")
    summary_text.append(f"Analysis Duration: ", style="bold")
    summary_text.append(f"{res.execution_time_seconds:.2f} seconds\n", style="white")
    summary_text.append(f"Alert Summary: ", style="bold")
    summary_text.append(f"{res.summary}", style="italic")

    console.print(Panel(summary_text, title="[bold]Incident Investigation Overview[/bold]", border_style="cyan"))

    # Reconstructed Timeline Table
    if res.timeline_events:
        t_table = Table(title="Reconstructed Incident Chronology", header_style="bold blue")
        t_table.add_column("Time (UTC)", style="cyan", no_wrap=True)
        t_table.add_column("Category", style="yellow")
        t_table.add_column("Observation / Event", style="white")
        t_table.add_column("Service", style="green")

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
                    title="[bold red]Associated Code / Config Diff[/bold red]",
                    border_style="red",
                )
            )

    # Ranked Hypotheses
    console.print("\n[bold]🎯 Ranked Root-Cause Hypotheses[/bold]")
    if not res.ranked_hypotheses:
        console.print("[yellow]No root-cause hypotheses met confidence criteria.[/yellow]")
    else:
        for h in res.ranked_hypotheses:
            rank = h["rank"]
            conf = h["confidence_label"].upper()
            score = h["evidence_score"]
            cat = h["root_cause_category"].replace("_", " ").title()
            comp = h["affected_component"]
            stmt = h["statement"]

            conf_style = "green" if conf == "HIGH" else ("yellow" if conf == "MEDIUM" else "red")
            card_lines = [
                f"[bold white]{stmt}[/bold white]",
                f"[dim]Category:[/dim] [yellow]{cat}[/yellow] | [dim]Component:[/dim] [cyan]{comp}[/cyan] | [dim]Score:[/dim] [bold]{score:.1f}/100[/bold] ([{conf_style}]{conf}[/{conf_style}])",
            ]

            citations = h.get("supporting_evidence", [])
            if citations:
                card_lines.append("\n[bold green]Corroborating Citations:[/bold green]")
                for c in citations:
                    card_lines.append(f"  - [cyan]{c['evidence_id']}[/cyan]: {c.get('reason', '')}")

            contradictions = h.get("contradicting_evidence", [])
            if contradictions:
                card_lines.append("\n[bold red]Contradicting Evidence:[/bold red]")
                for c in contradictions:
                    card_lines.append(f"  - [cyan]{c['evidence_id']}[/cyan]: {c.get('reason', '')}")

            border_color = "green" if rank == 1 else "blue"
            console.print(Panel("\n".join(card_lines), title=f"[bold]Rank #{rank} — {conf} Confidence[/bold]", border_style=border_color))

    # Save Markdown report if requested
    if report_path:
        out_file = Path(report_path)
        out_file.write_text(res.to_markdown_report(), encoding="utf-8")
        console.print(f"\n[bold green][OK] Post-mortem report saved to:[/bold green] {out_file.resolve()}")


async def run_scenario_flow(
    scenario: str,
    mode: str,
    provider: str,
    model: str | None,
    report: str | None,
) -> None:
    runner = InvestigationRunner()
    render_banner()

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task(f"Initializing investigation for scenario: [bold]{scenario}[/bold]...", total=None)
        await asyncio.sleep(0.3)

        progress.update(task, description="Assessing missing information gaps across 6 observability sources...")
        await asyncio.sleep(0.3)

        progress.update(task, description="Planning queries & executing collection...")
        res = await runner.run_scenario(
            scenario_name=scenario,
            mode=mode,
            provider=provider,
            llm_model=model,
        )

        progress.update(task, description="Building normalized timeline & ranking root-cause hypotheses...")
        await asyncio.sleep(0.2)

    console.print("\n[bold green][OK] Investigation complete![/bold green]\n")
    display_results(res, report_path=report)


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
        console.print(f"[bold red]Error:[/bold red] Repository path '{repo}' does not exist.")
        sys.exit(1)

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task(f"Connecting to Git repository at: [bold]{repo_path}[/bold]...", total=None)
        await asyncio.sleep(0.3)

        if logs:
            progress.update(task, description=f"Scanning application logs from [bold]{logs}[/bold]...")
            await asyncio.sleep(0.3)

        progress.update(task, description="Reconstructing commit diffs and evaluating incident timeline...")
        res = await runner.run_target(
            repo_path=repo_path,
            log_path=logs,
            service_name=service,
            summary=summary,
            mode=mode,
            provider=provider,
            llm_model=model,
        )

        progress.update(task, description="Evaluating root-cause hypotheses and citations...")
        await asyncio.sleep(0.2)

    console.print("\n[bold green]✓ Repository triage complete![/bold green]\n")
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
        default="bad_db_config",
        help="Scenario ID (default: bad_db_config)",
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
        asyncio.run(run_scenario_flow(args.scenario, args.mode, args.provider, args.model, args.report))
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
