"""Command line interface.

pipelinemd diagnose <url>      diagnose a failed pipeline or job
pipelinemd distill <file>      distil a saved trace, offline
pipelinemd rules               list the rule catalog
pipelinemd explain <rule-id>   show one rule in full
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import IO, Any

from . import __version__
from .config import detect_ci, resolve_anthropic_key, resolve_credentials, resolve_gitlab_url
from .diagnose import DEFAULT_MODEL
from .diagnose import available as llm_available
from .diagnose import diagnose as run_diagnosis
from .distill import distill
from .distill.extract import DEFAULT_MAX_LINES, DEFAULT_TAIL_LINES, DEFAULT_THRESHOLD
from .errors import DiagnosisError, GitLabError, PipelinemdError, UsageError
from .gitlab import GitLabClient, Target, parse_target, rebase, target_from_parts
from .models import JobRef, Report
from .render import make_style, render_markdown, render_terminal
from .render.json_out import report_to_dict
from .rules import ALL_RULES, get_rule, match_rules

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_GITLAB = 3
EXIT_NOTHING = 4

MAX_JOBS_DEFAULT = 1


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pipelinemd",
        description="GitLab CI/CD failure doctor: distil a failed job's log and explain it.",
    )
    parser.add_argument("--version", action="version", version=f"pipelinemd {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(sub: argparse.ArgumentParser) -> None:
        sub.add_argument(
            "-f",
            "--format",
            choices=("terminal", "markdown", "json"),
            default="terminal",
            help="Output format (default: terminal).",
        )
        sub.add_argument(
            "--color",
            choices=("auto", "always", "never"),
            default="auto",
            help="Colourise terminal output (default: auto).",
        )
        sub.add_argument(
            "-o", "--output", metavar="PATH", help="Write to a file instead of stdout."
        )
        sub.add_argument(
            "--evidence-limit",
            type=int,
            default=40,
            help="Evidence lines to display (default: 40).",
        )
        sub.add_argument("--all-rules", action="store_true", help="Show every rule that matched.")

    def add_distill_options(sub: argparse.ArgumentParser) -> None:
        sub.add_argument(
            "--max-evidence-lines",
            type=int,
            default=DEFAULT_MAX_LINES,
            help=f"Line budget for the excerpt (default: {DEFAULT_MAX_LINES}).",
        )
        sub.add_argument(
            "--tail-lines",
            type=int,
            default=DEFAULT_TAIL_LINES,
            help=f"Trailing lines always kept (default: {DEFAULT_TAIL_LINES}).",
        )
        sub.add_argument(
            "--threshold",
            type=float,
            default=DEFAULT_THRESHOLD,
            help=f"Score a line must reach to anchor a window (default: {DEFAULT_THRESHOLD}).",
        )

    # diagnose ------------------------------------------------------------
    diagnose_parser = subparsers.add_parser(
        "diagnose",
        help="Diagnose a failed pipeline or job.",
        description=(
            "Fetch a failed job's trace, distil it, match the rule catalog, and "
            "(when a key is available) ask Claude for the root cause.\n\n"
            "With no target, uses the surrounding pipeline's CI_PIPELINE_ID when "
            "running inside GitLab CI."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    diagnose_parser.add_argument(
        "target",
        nargs="?",
        help="A GitLab pipeline or job URL.",
    )
    diagnose_parser.add_argument("--project", help="Project path, e.g. group/project.")
    diagnose_parser.add_argument("--pipeline", type=int, help="Pipeline id.")
    diagnose_parser.add_argument("--job", type=int, help="Job id.")
    diagnose_parser.add_argument("--gitlab-url", help="GitLab instance URL.")
    diagnose_parser.add_argument(
        "--token", help="GitLab token (else $GITLAB_TOKEN, $CI_JOB_TOKEN)."
    )
    diagnose_parser.add_argument(
        "--from-file",
        metavar="PATH",
        help="Diagnose a saved trace instead of fetching one ('-' for stdin).",
    )
    diagnose_parser.add_argument(
        "--all-jobs",
        action="store_true",
        help="Diagnose every failed job in the pipeline, not just the first.",
    )
    diagnose_parser.add_argument(
        "--with-ci-config",
        action="store_true",
        help="Also fetch .gitlab-ci.yml and give it to the model.",
    )
    diagnose_parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip the Claude call; report rules only.",
    )
    diagnose_parser.add_argument("--api-key", help="Anthropic API key (else $ANTHROPIC_API_KEY).")
    diagnose_parser.add_argument(
        "--model", default=DEFAULT_MODEL, help=f"Model (default: {DEFAULT_MODEL})."
    )
    diagnose_parser.add_argument(
        "--effort",
        choices=("low", "medium", "high", "xhigh", "max"),
        default="high",
        help="Reasoning effort (default: high).",
    )
    diagnose_parser.add_argument(
        "--timeout", type=float, default=30.0, help="HTTP timeout in seconds."
    )
    add_distill_options(diagnose_parser)
    add_common(diagnose_parser)
    diagnose_parser.set_defaults(func=cmd_diagnose)

    # distill -------------------------------------------------------------
    distill_parser = subparsers.add_parser(
        "distill",
        help="Distil a saved trace offline and match rules against it.",
        description="Purely deterministic: no network, no model, no API key.",
    )
    distill_parser.add_argument("path", help="Trace file, or '-' for stdin.")
    add_distill_options(distill_parser)
    add_common(distill_parser)
    distill_parser.set_defaults(func=cmd_distill)

    # rules ---------------------------------------------------------------
    rules_parser = subparsers.add_parser("rules", help="List the rule catalog.")
    rules_parser.add_argument("--category", help="Only rules in this category.")
    rules_parser.add_argument("--search", help="Only rules whose id or title contains this text.")
    rules_parser.add_argument("--json", action="store_true", help="Emit JSON.")
    rules_parser.add_argument(
        "--color", choices=("auto", "always", "never"), default="auto", help="Colourise output."
    )
    rules_parser.set_defaults(func=cmd_rules)

    # explain -------------------------------------------------------------
    explain_parser = subparsers.add_parser("explain", help="Show one rule in full.")
    explain_parser.add_argument("rule_id", help="Rule id, e.g. npm.eresolve.")
    explain_parser.add_argument("--json", action="store_true", help="Emit JSON.")
    explain_parser.add_argument(
        "--color", choices=("auto", "always", "never"), default="auto", help="Colourise output."
    )
    explain_parser.set_defaults(func=cmd_explain)

    return parser


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(text: str, destination: str | None, stdout: IO[str]) -> None:
    if destination:
        Path(destination).write_text(text, encoding="utf-8")
    else:
        stdout.write(text)


def _read_trace(path: str, stdin: IO[str]) -> str:
    if path == "-":
        return stdin.read()
    source = Path(path)
    if not source.exists():
        raise UsageError(f"No such file: {path}")
    return source.read_text(encoding="utf-8", errors="replace")


def _render(
    reports: list[Report],
    args: argparse.Namespace,
    stdout: IO[str],
) -> None:
    rule_limit = 999 if args.all_rules else 5
    if args.format == "json":
        payload: Any = (
            [report_to_dict(report) for report in reports]
            if len(reports) != 1
            else report_to_dict(reports[0])
        )
        text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    elif args.format == "markdown":
        text = "\n---\n\n".join(
            render_markdown(report, rule_limit=rule_limit, evidence_limit=args.evidence_limit)
            for report in reports
        )
    else:
        style = make_style(args.color, stdout if not args.output else None)
        text = "\n".join(
            render_terminal(
                report,
                style,
                rule_limit=rule_limit,
                evidence_limit=args.evidence_limit,
            )
            for report in reports
        )
    _write(text, args.output, stdout)


def _resolve_target(args: argparse.Namespace) -> Target:
    """Work out what to fetch, from a URL, explicit flags, or the CI env."""
    if args.target:
        target = parse_target(args.target)
        if args.gitlab_url:
            target = rebase(target, args.gitlab_url)
        return target

    base_url = resolve_gitlab_url(args.gitlab_url)
    if args.project and (args.pipeline or args.job):
        return target_from_parts(base_url, args.project, pipeline=args.pipeline, job=args.job)

    context = detect_ci()
    if context is None:
        raise UsageError(
            "Nothing to diagnose. Pass a pipeline or job URL, or --project with "
            "--pipeline/--job, or run inside a GitLab CI job."
        )
    if args.pipeline or args.job:
        return target_from_parts(
            context.server_url, context.project_path, pipeline=args.pipeline, job=args.job
        )
    if context.pipeline_id is None:
        raise UsageError("Running in CI but CI_PIPELINE_ID is not set.")
    return target_from_parts(context.server_url, context.project_path, pipeline=context.pipeline_id)


def _maybe_diagnose(
    report: Report,
    args: argparse.Namespace,
    ci_config: str | None,
    stderr: IO[str],
) -> Report:
    """Attach an LLM diagnosis when possible; never let its absence be fatal."""
    if args.no_llm:
        return report
    if not llm_available():
        stderr.write(
            "note: skipping Claude diagnosis - the `anthropic` package is not installed "
            "(pip install 'pipelinemd[llm]'). Rules were still applied.\n"
        )
        return report

    api_key = resolve_anthropic_key(args.api_key)
    try:
        diagnosis = run_diagnosis(
            report.job,
            report.distilled,
            report.hits,
            api_key=api_key,
            model=args.model,
            effort=args.effort,
            ci_config=ci_config,
        )
    except DiagnosisError as exc:
        stderr.write(f"note: Claude diagnosis unavailable ({exc}). Rules were still applied.\n")
        return report
    return replace(report, diagnosis=diagnosis)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_distill(args: argparse.Namespace, stdout: IO[str], stderr: IO[str], stdin: IO[str]) -> int:
    raw = _read_trace(args.path, stdin)
    distilled = distill(
        raw,
        threshold=args.threshold,
        max_lines=args.max_evidence_lines,
        tail_lines=args.tail_lines,
    )
    name = "stdin" if args.path == "-" else Path(args.path).name
    report = Report(
        job=JobRef(name=name, source="file"),
        distilled=distilled,
        hits=match_rules(distilled),
    )
    _render([report], args, stdout)
    return EXIT_OK


def cmd_diagnose(args: argparse.Namespace, stdout: IO[str], stderr: IO[str], stdin: IO[str]) -> int:
    # Offline path: a trace already on disk.
    if args.from_file:
        raw = _read_trace(args.from_file, stdin)
        distilled = distill(
            raw,
            threshold=args.threshold,
            max_lines=args.max_evidence_lines,
            tail_lines=args.tail_lines,
        )
        name = "stdin" if args.from_file == "-" else Path(args.from_file).name
        report = Report(
            job=JobRef(name=name, source="file"),
            distilled=distilled,
            hits=match_rules(distilled),
        )
        report = _maybe_diagnose(report, args, None, stderr)
        _render([report], args, stdout)
        return EXIT_OK

    target = _resolve_target(args)
    credentials = resolve_credentials(args.token)
    if not credentials.present:
        stderr.write(
            "note: no GitLab token found. Public projects still work; private ones "
            "need $GITLAB_TOKEN or --token.\n"
        )
    client = GitLabClient(
        target.base_url,
        credentials.token,
        token_header=credentials.header,
        timeout=args.timeout,
    )

    jobs: list[dict[str, Any]]
    if target.is_job:
        jobs = [client.get_job(target.project, target.id)]
    else:
        jobs = client.failed_jobs(target.project, target.id)
        if not jobs:
            stderr.write(
                f"No failed jobs in pipeline {target.id} of {target.project}. Nothing to diagnose.\n"
            )
            return EXIT_NOTHING
        if not args.all_jobs and len(jobs) > 1:
            others = ", ".join(str(job.get("name")) for job in jobs[1:])
            stderr.write(
                f"note: {len(jobs)} jobs failed; diagnosing '{jobs[0].get('name')}'. "
                f"Use --all-jobs for the rest ({others}).\n"
            )
            jobs = jobs[:MAX_JOBS_DEFAULT]

    reports: list[Report] = []
    for job in jobs:
        job_ref = client.job_ref(job, target.project)
        raw = client.get_trace(target.project, int(job["id"]))
        distilled = distill(
            raw,
            threshold=args.threshold,
            max_lines=args.max_evidence_lines,
            tail_lines=args.tail_lines,
        )
        report = Report(job=job_ref, distilled=distilled, hits=match_rules(distilled))
        ci_config = (
            client.get_ci_config(target.project, job_ref.ref or "HEAD")
            if args.with_ci_config
            else None
        )
        reports.append(_maybe_diagnose(report, args, ci_config, stderr))

    _render(reports, args, stdout)
    return EXIT_OK


def cmd_rules(args: argparse.Namespace, stdout: IO[str], stderr: IO[str], stdin: IO[str]) -> int:
    selected = list(ALL_RULES)
    if args.category:
        wanted = args.category.strip().lower()
        selected = [rule for rule in selected if rule.category.value == wanted]
        if not selected:
            available = ", ".join(sorted({rule.category.value for rule in ALL_RULES}))
            raise UsageError(f"Unknown category {args.category!r}. Try one of: {available}")
    if args.search:
        needle = args.search.strip().lower()
        selected = [
            rule for rule in selected if needle in rule.id.lower() or needle in rule.title.lower()
        ]

    if args.json:
        payload = [
            {
                "id": rule.id,
                "title": rule.title,
                "category": rule.category.value,
                "confidence": rule.confidence.value,
                "patterns": list(rule.patterns),
                "explanation": rule.explanation,
                "fixes": list(rule.fixes),
                "docs": list(rule.docs),
            }
            for rule in selected
        ]
        stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        return EXIT_OK

    style = make_style(args.color, stdout)
    grouped: dict[str, list[Any]] = {}
    for rule in selected:
        grouped.setdefault(rule.category.value, []).append(rule)

    stdout.write(style.bold(f"{len(selected)} rules\n"))
    for category in sorted(grouped):
        stdout.write("\n" + style.heading(category) + "\n")
        width = max(len(rule.id) for rule in grouped[category])
        for rule in grouped[category]:
            stdout.write(
                f"  {style.bold(rule.id.ljust(width))}  {rule.title}"
                f"  {style.dim(rule.confidence.value)}\n"
            )
    stdout.write(style.dim("\nRun `pipelinemd explain <id>` for the full entry.\n"))
    return EXIT_OK


def cmd_explain(args: argparse.Namespace, stdout: IO[str], stderr: IO[str], stdin: IO[str]) -> int:
    rule = get_rule(args.rule_id)
    if rule is None:
        close = [r.id for r in ALL_RULES if args.rule_id.lower() in r.id.lower()][:5]
        hint = f" Did you mean: {', '.join(close)}?" if close else ""
        raise UsageError(f"No rule with id {args.rule_id!r}.{hint}")

    if args.json:
        stdout.write(
            json.dumps(
                {
                    "id": rule.id,
                    "title": rule.title,
                    "category": rule.category.value,
                    "confidence": rule.confidence.value,
                    "patterns": list(rule.patterns),
                    "excludes": list(rule.excludes),
                    "requires": list(rule.requires),
                    "exit_codes": list(rule.exit_codes),
                    "explanation": rule.explanation,
                    "fixes": list(rule.fixes),
                    "docs": list(rule.docs),
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n"
        )
        return EXIT_OK

    style = make_style(args.color, stdout)
    stdout.write(f"{style.bold(rule.id)}  {rule.title}\n")
    stdout.write(style.dim(f"{rule.category.value} · {rule.confidence.value} confidence\n\n"))
    stdout.write(f"{rule.explanation}\n\n")
    stdout.write(style.heading("Fixes") + "\n")
    for fix in rule.fixes:
        stdout.write(f"  → {fix}\n")
    stdout.write("\n" + style.heading("Matches when the log contains") + "\n")
    for pattern in rule.patterns:
        stdout.write(style.dim(f"  /{pattern}/\n"))
    if rule.excludes:
        stdout.write(style.heading("\nUnless it also contains") + "\n")
        for pattern in rule.excludes:
            stdout.write(style.dim(f"  /{pattern}/\n"))
    if rule.exit_codes:
        codes = ", ".join(str(code) for code in rule.exit_codes)
        stdout.write(style.dim(f"\nTypical exit codes: {codes}\n"))
    if rule.docs:
        stdout.write("\n")
        for doc in rule.docs:
            stdout.write(style.dim(f"docs: {doc}\n"))
    return EXIT_OK


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: IO[str] | None = None,
    stderr: IO[str] | None = None,
    stdin: IO[str] | None = None,
) -> int:
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    inp = stdin if stdin is not None else sys.stdin

    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        result = args.func(args, out, err, inp)
        return int(result)
    except UsageError as exc:
        err.write(f"error: {exc}\n")
        return EXIT_USAGE
    except GitLabError as exc:
        err.write(f"error: {exc}\n")
        return EXIT_GITLAB
    except PipelinemdError as exc:
        err.write(f"error: {exc}\n")
        return EXIT_USAGE
    except KeyboardInterrupt:  # pragma: no cover
        err.write("\ninterrupted\n")
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
