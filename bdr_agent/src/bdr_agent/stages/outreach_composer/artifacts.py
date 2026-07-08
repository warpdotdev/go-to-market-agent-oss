"""Artifact path, rendering, and GCS helpers for lead brief outputs."""

from __future__ import annotations

import html
import re
from urllib.parse import quote

from bdr_agent.stages.company_research.config import GCP_PROJECT_ID

from bdr_agent.stages.outreach_composer.config import (
    BRIEF_FILE_EXTENSION,
    DEFAULT_ARTIFACT_BASE_URI,
    STAGE,
    normalize_stage_contract,
)
from bdr_agent.common.artifacts import parse_gcs_uri, write_text_to_gcs

HTML_FILE_EXTENSION = "html"
AUTHENTICATED_GCS_HOST = "https://storage.cloud.google.com"


def build_lead_brief_gcs_uri(
    *,
    run_id: str,
    output_id: str,
    artifact_base_uri: str = DEFAULT_ARTIFACT_BASE_URI,
    stage: str = STAGE,
) -> str:
    base_uri = artifact_base_uri.rstrip("/")
    if not base_uri.startswith("gs://"):
        raise ValueError("artifact_base_uri must be a gs:// URI")
    return f"{base_uri}/{normalize_stage_contract(stage)}/{run_id}/{output_id}.{BRIEF_FILE_EXTENSION}"


def build_lead_brief_html_gcs_uri(
    *,
    run_id: str,
    output_id: str,
    artifact_base_uri: str = DEFAULT_ARTIFACT_BASE_URI,
    stage: str = STAGE,
) -> str:
    base_uri = artifact_base_uri.rstrip("/")
    if not base_uri.startswith("gs://"):
        raise ValueError("artifact_base_uri must be a gs:// URI")
    return f"{base_uri}/{normalize_stage_contract(stage)}/{run_id}/{output_id}.{HTML_FILE_EXTENSION}"


def build_authenticated_gcs_url(*, gcs_uri: str, authuser: str | None = "0") -> str:
    bucket_name, blob_name = parse_gcs_uri(gcs_uri)
    url = f"{AUTHENTICATED_GCS_HOST}/{bucket_name}/{quote(blob_name, safe='/')}"
    if authuser is not None:
        return f"{url}?authuser={quote(str(authuser), safe='@.')}"
    return url


def render_lead_brief_markdown_html(*, markdown: str) -> str:
    """Render the lead-brief Markdown subset to a safe standalone HTML page."""
    body = _render_markdown_body(markdown)
    title = _document_title(markdown)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{ color-scheme: light; }}
    body {{
      margin: 0;
      background: #f7f7f8;
      color: #24292f;
      font: 16px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    main {{
      max-width: 880px;
      margin: 40px auto;
      padding: 40px 48px;
      background: white;
      border: 1px solid #d8dee4;
      border-radius: 12px;
      box-shadow: 0 8px 30px rgba(27,31,36,0.08);
    }}
    h1 {{ margin-top: 0; font-size: 28px; line-height: 1.25; }}
    h2 {{ margin-top: 32px; border-bottom: 1px solid #d8dee4; padding-bottom: 8px; }}
    h3 {{ margin-top: 24px; }}
    ul {{ padding-left: 24px; }}
    li {{ margin: 6px 0; }}
    p {{ margin: 12px 0; }}
    a {{ color: #0969da; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    code {{ background: #f6f8fa; border-radius: 4px; padding: 0.15em 0.35em; }}
    .meta {{ color: #57606a; font-size: 14px; margin-bottom: 24px; }}
  </style>
</head>
<body>
  <main>
    <div class="meta">Rendered lead brief</div>
{body}
  </main>
</body>
</html>
"""


def write_rendered_lead_brief_html_to_gcs(
    *,
    gcs_uri: str,
    markdown: str,
    client: object | None = None,
) -> None:
    content = render_lead_brief_markdown_html(markdown=markdown)
    if client is not None and hasattr(client, "upload_text"):
        client.upload_text(gcs_uri, content)
        return

    bucket_name, blob_name = parse_gcs_uri(gcs_uri)
    if client is None:
        from google.cloud import storage

        client = storage.Client(project=GCP_PROJECT_ID)
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_string(content, content_type="text/html; charset=utf-8")


def _render_markdown_body(markdown: str) -> str:
    html_lines: list[str] = []
    paragraph_lines: list[str] = []
    in_list = False

    def flush_paragraph() -> None:
        if paragraph_lines:
            text = " ".join(line.strip() for line in paragraph_lines)
            html_lines.append(f"    <p>{_render_inline_markdown(text)}</p>")
            paragraph_lines.clear()

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            html_lines.append("    </ul>")
            in_list = False

    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        if not line:
            flush_paragraph()
            close_list()
            continue

        heading_match = re.match(r"^(#{1,6})\s+(.*)$", line)
        bullet_match = re.match(r"^[-*]\s+(.*)$", line)
        if heading_match:
            flush_paragraph()
            close_list()
            level = len(heading_match.group(1))
            html_lines.append(
                f"    <h{level}>{_render_inline_markdown(heading_match.group(2))}</h{level}>"
            )
            continue
        if bullet_match:
            flush_paragraph()
            if not in_list:
                html_lines.append("    <ul>")
                in_list = True
            html_lines.append(f"      <li>{_render_inline_markdown(bullet_match.group(1))}</li>")
            continue
        paragraph_lines.append(line)

    flush_paragraph()
    close_list()
    return "\n".join(html_lines)


def _render_inline_markdown(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(
        r"\[([^\]]+)\]\((https?://[^\s)]+)\)",
        r'<a href="\2" target="_blank" rel="noopener noreferrer">\1</a>',
        escaped,
    )
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    return escaped


def _document_title(markdown: str) -> str:
    for line in markdown.splitlines():
        match = re.match(r"^#\s+(.*)$", line.strip())
        if match:
            return match.group(1)
    return "Lead brief"


__all__ = [
    "build_authenticated_gcs_url",
    "build_lead_brief_gcs_uri",
    "build_lead_brief_html_gcs_uri",
    "render_lead_brief_markdown_html",
    "write_rendered_lead_brief_html_to_gcs",
    "write_text_to_gcs",
]
