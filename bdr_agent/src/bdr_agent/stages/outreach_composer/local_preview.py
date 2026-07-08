"""Local preview helpers for Outreach Composer before/after review.

This module intentionally avoids BigQuery, Slack, HubSpot, and GCS calls. It
generates compact Oz-style prompts for representative cases and compares saved
before/after email bodies or packets using local validation and lightweight
quality checks.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import sys
from typing import Any

from bdr_agent.stages.outreach_composer.validation import normalize_lead_brief_packet


GENERIC_BRIDGE_PATTERNS = (
    "related problem",
    "related area",
    "similar layer",
    "infrastructure side",
    "run, review, and control",
)

CONCRETE_BRIDGE_TERMS = (
    "cloud agent",
    "cloud agents",
    "multi-harness",
    "persistent memory",
    "background orchestration",
    "multi-repo",
    "Claude",
    "Codex",
    "Slack",
    "Linear",
    "GitHub",
    "cron",
    "webhook",
    "webhooks",
    "API",
    "SDK",
    "CI",
    "transcript",
    "transcripts",
    "steer",
    "steering",
)

SOURCE_OPENER_CUES = (
    "saw",
    "noticed",
    "read",
    "published",
    "launch",
    "launched",
    "announcement",
    "engineering post",
    "blog",
)


@dataclass(frozen=True)
class PreviewCase:
    name: str
    lead_id: str
    label: str
    lead_name: str
    company: str
    role: str
    source_signal: str
    expected_frame: str
    prompt_fields: dict[str, str]
    before_body: str | None = None
    notes: str | None = None


PREVIEW_CASES: dict[str, PreviewCase] = {
    "example_senior_engineer": PreviewCase(
        name="example_senior_engineer",
        lead_id="lead_456",
        label="Example / senior engineer regression target",
        lead_name="Jordan Lee",
        company="Example",
        role="Senior Software Engineer",
        source_signal=(
            "Public AI-agent/product launches and agent governance language; "
            "use the specific source from the company research row when available."
        ),
        expected_frame="capability_gap",
        prompt_fields={
            "LEAD_ID": "lead_456",
            "BDR_AGENT_STAGE": "lead_brief",
            "BDR_AGENT_TRIGGER": "stage_completion",
            "SOURCE_SYSTEM": "agent_orchestrator_stage_completion",
            "SOURCE_STAGE": "company_research",
            "PREVIOUS_RUN_ID": "<previous_company_research_run_id>",
            "PREVIOUS_OUTPUT_ID": "<latest_completed_company_research_output_id>",
            "COMPANY_RESEARCH_OUTPUT_ID": "<latest_completed_company_research_output_id>",
        },
        before_body=(
            "[Your product] helps engineering teams with the infrastructure side "
            "of agent work, making it easier to run, review, and control agent workflows "
            "as they become real team processes."
        ),
        notes=(
            "Rank-1 should cite the specific public source if available, avoid "
            "generic bridge phrases, and make the product bridge concrete with one mechanism or surface."
        ),
    ),
    "fixture_vp_engineering": PreviewCase(
        name="fixture_vp_engineering",
        lead_id="lead_123",
        label="Local fixture / VP Engineering",
        lead_name="Ada Lovelace",
        company="Example",
        role="VP Engineering",
        source_signal="Fixture engineering blog post about AI agents for developer productivity.",
        expected_frame="capability_gap",
        prompt_fields={
            "LEAD_ID": "lead_123",
            "BDR_AGENT_STAGE": "lead_brief",
            "BDR_AGENT_TRIGGER": "stage_completion",
            "SOURCE_SYSTEM": "local_preview_fixture",
            "SOURCE_STAGE": "company_research",
            "PREVIOUS_RUN_ID": "company_run_123",
            "PREVIOUS_OUTPUT_ID": "company_output_123",
            "COMPANY_RESEARCH_OUTPUT_ID": "company_output_123",
        },
        notes=(
            "Use with tests/fixtures/company_research_output.json "
            "for no-network packet validation."
        ),
    ),
    "platform_orchestration_representative": PreviewCase(
        name="platform_orchestration_representative",
        lead_id="<platform_lead_id>",
        label="Representative platform / DevOps lead",
        lead_name="<lead_name>",
        company="<company>",
        role="Platform, DevOps, or infrastructure buyer",
        source_signal="Public signal around platform automation, CI/CD, internal tools, or developer workflows.",
        expected_frame="workflow_orchestration",
        prompt_fields={
            "LEAD_ID": "<platform_lead_id>",
            "BDR_AGENT_STAGE": "lead_brief",
            "BDR_AGENT_TRIGGER": "stage_completion",
            "SOURCE_SYSTEM": "agent_orchestrator_stage_completion",
            "SOURCE_STAGE": "company_research",
            "PREVIOUS_RUN_ID": "<previous_company_research_run_id>",
            "PREVIOUS_OUTPUT_ID": "<company_research_output_id>",
            "COMPANY_RESEARCH_OUTPUT_ID": "<company_research_output_id>",
        },
        notes=(
            "Use a real completed company_research output for a platform persona. "
            "The after draft should choose one surface such as GitHub, Slack, CI, cron, or an API."
        ),
    ),
    "memory_evals_representative": PreviewCase(
        name="memory_evals_representative",
        lead_id="<memory_evals_lead_id>",
        label="Representative quality / evals lead",
        lead_name="<lead_name>",
        company="<company>",
        role="AI quality, evals, reliability, or engineering leadership buyer",
        source_signal="Public signal around AI quality, reliability, evals, regression checks, or recurring workflows.",
        expected_frame="governance",
        prompt_fields={
            "LEAD_ID": "<memory_evals_lead_id>",
            "BDR_AGENT_STAGE": "lead_brief",
            "BDR_AGENT_TRIGGER": "stage_completion",
            "SOURCE_SYSTEM": "agent_orchestrator_stage_completion",
            "SOURCE_STAGE": "company_research",
            "PREVIOUS_RUN_ID": "<previous_company_research_run_id>",
            "PREVIOUS_OUTPUT_ID": "<company_research_output_id>",
            "COMPANY_RESEARCH_OUTPUT_ID": "<company_research_output_id>",
        },
        notes=(
            "Use a real completed company_research output where quality or repeatability is the source signal. "
            "The after draft should avoid product-tour language."
        ),
    ),
}


def render_compact_prompt(case: PreviewCase) -> str:
    lines = [f"{key}={value}" for key, value in case.prompt_fields.items()]
    lines.extend(
        [
            "",
            "# Reviewer-only preview context, not Oz prompt payload:",
            f"# Case: {case.label}",
            f"# Lead: {case.lead_name} | {case.role} | {case.company}",
            f"# Strong source signal to verify in BigQuery: {case.source_signal}",
            f"# Expected frame to compare against: {case.expected_frame}",
        ]
    )
    if case.notes:
        lines.append(f"# Notes: {case.notes}")
    return "\n".join(lines).rstrip() + "\n"


def write_prompts(output_dir: Path, cases: list[PreviewCase]) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for case in cases:
        path = output_dir / f"{case.name}.prompt.txt"
        path.write_text(render_compact_prompt(case))
        written.append(path)
    index_path = output_dir / "README.md"
    index_path.write_text(render_prompt_index(cases))
    written.append(index_path)
    return written


def render_prompt_index(cases: list[PreviewCase]) -> str:
    lines = [
        "# Outreach Composer local preview prompts",
        "Use these files as compact prompts for before/after Oz runs. They intentionally contain IDs and stable trigger fields, not full research JSON.",
        "",
        "Recommended comparison flow:",
        "1. Run the current main-branch skill and save the rank-1 body or full packet as the before output.",
        "2. Run this branch's `bdr-outreach-composer` skill with the same prompt and save the after packet.",
        "3. Compare locally with `python -m bdr_agent.stages.outreach_composer.local_preview --case <case> --before-text-file <before.txt> --after-packet-json-file <after.json>`.",
        "",
        "Cases:",
    ]
    for case in cases:
        lines.append(f"- `{case.name}`: {case.label}; expected frame `{case.expected_frame}`.")
    return "\n".join(lines) + "\n"


def load_body_from_packet(path: Path) -> str:
    packet = normalize_lead_brief_packet(json.loads(path.read_text()))
    return next(draft["body"] for draft in packet["email_body_drafts"] if draft["rank"] == 1)


def load_body_from_text_or_packet(*, text_path: Path | None, packet_path: Path | None) -> str | None:
    if text_path and packet_path:
        raise ValueError("Pass either a text file or a packet JSON file, not both")
    if text_path:
        return text_path.read_text().strip()
    if packet_path:
        return load_body_from_packet(packet_path)
    return None


def analyze_body(body: str) -> dict[str, Any]:
    first_sentence = _first_sentence(body)
    body_lower = body.lower()
    concrete_terms = [
        term
        for term in CONCRETE_BRIDGE_TERMS
        if re.search(rf"\b{re.escape(term.lower())}\b", body_lower)
    ]
    generic_phrases = [
        phrase
        for phrase in GENERIC_BRIDGE_PATTERNS
        if phrase in body_lower
    ]
    return {
        "word_count": _word_count(body),
        "question_marks": body.count("?"),
        "paragraph_count": len([p for p in re.split(r"\n\s*\n", body.strip()) if p.strip()]),
        "first_sentence": first_sentence,
        "source_specific_opener": any(cue in first_sentence.lower() for cue in SOURCE_OPENER_CUES),
        "concrete_bridge_terms": concrete_terms,
        "generic_bridge_phrases": generic_phrases,
    }


def compare_bodies(*, case: PreviewCase, before_body: str | None, after_body: str | None) -> dict[str, Any]:
    return {
        "case": asdict(case),
        "before": analyze_body(before_body) if before_body else None,
        "after": analyze_body(after_body) if after_body else None,
    }


def render_comparison_markdown(comparison: dict[str, Any]) -> str:
    case = comparison["case"]
    lines = [
        f"# Outreach Composer preview: {case['label']}",
        f"- Lead: {case['lead_name']} | {case['role']} | {case['company']}",
        f"- Expected frame: `{case['expected_frame']}`",
        f"- Source signal to verify: {case['source_signal']}",
        "",
    ]
    for label in ("before", "after"):
        analysis = comparison[label]
        lines.append(f"## {label.title()} rank-1 body checks")
        if analysis is None:
            lines.append("No body supplied.")
            lines.append("")
            continue
        lines.extend(
            [
                f"- Word count: {analysis['word_count']}",
                f"- Paragraph count: {analysis['paragraph_count']}",
                f"- Question marks: {analysis['question_marks']}",
                f"- Source-specific opener: {analysis['source_specific_opener']}",
                f"- Concrete product bridge terms: {', '.join(analysis['concrete_bridge_terms']) or 'none'}",
                f"- Generic bridge phrases: {', '.join(analysis['generic_bridge_phrases']) or 'none'}",
                f"- First sentence: {analysis['first_sentence']}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def selected_cases(names: list[str] | None) -> list[PreviewCase]:
    if not names or names == ["all"]:
        return list(PREVIEW_CASES.values())
    unknown = [name for name in names if name not in PREVIEW_CASES]
    if unknown:
        raise ValueError(f"Unknown preview case(s): {', '.join(unknown)}")
    return [PREVIEW_CASES[name] for name in names]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate and compare local Outreach Composer previews.")
    parser.add_argument("--list-cases", action="store_true", help="List available preview cases and exit.")
    parser.add_argument(
        "--case",
        action="append",
        dest="cases",
        help="Preview case to use; pass multiple times or omit for all cases.",
    )
    parser.add_argument("--write-prompts", type=Path, help="Directory where compact prompt files should be written.")
    parser.add_argument("--before-text-file", type=Path)
    parser.add_argument("--before-packet-json-file", type=Path)
    parser.add_argument("--after-text-file", type=Path)
    parser.add_argument("--after-packet-json-file", type=Path)
    parser.add_argument("--json", action="store_true", help="Emit comparison as JSON instead of Markdown.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cases = selected_cases(args.cases)
    if args.list_cases:
        for case in cases:
            print(f"{case.name}\t{case.label}\t{case.expected_frame}")
        return 0
    if args.write_prompts:
        written = write_prompts(args.write_prompts, cases)
        for path in written:
            print(path)
        return 0

    if len(cases) != 1:
        raise SystemExit("Comparison mode requires exactly one --case")

    case = cases[0]
    before_body = load_body_from_text_or_packet(
        text_path=args.before_text_file,
        packet_path=args.before_packet_json_file,
    ) or case.before_body
    after_body = load_body_from_text_or_packet(
        text_path=args.after_text_file,
        packet_path=args.after_packet_json_file,
    )
    comparison = compare_bodies(case=case, before_body=before_body, after_body=after_body)
    if args.json:
        json.dump(comparison, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(render_comparison_markdown(comparison))
    return 0


def _first_sentence(body: str) -> str:
    normalized = re.sub(r"\s+", " ", body.strip())
    match = re.search(r"(.+?[.!?])(?:\s|$)", normalized)
    return match.group(1) if match else normalized


def _word_count(value: str) -> int:
    return len(re.findall(r"\b[\w']+\b", value))


if __name__ == "__main__":
    raise SystemExit(main())
