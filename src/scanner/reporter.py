# src/scanner/reporter.py
import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import httpx
import jinja2

log = logging.getLogger("icarus.reporter")

# ----------------------------------------------------------------------
# Jinja2 template setup (templates/report.md.j2 must exist)
# ----------------------------------------------------------------------
TEMPLATE_DIR = Path(__file__).parent.parent.parent / "templates"
env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=jinja2.select_autoescape(["html", "xml"]),
    trim_blocks=True,
    lstrip_blocks=True,
)
try:
    template = env.get_template("report.md.j2")
except jinja2.TemplateNotFound:
    log.error("report.md.j2 not found in templates/ directory")
    template = None


async def handle_finding(event: Any, program: str, cfg: Dict[str, Any]) -> None:
    """
    Process a BBOT FINDING event → H1-ready Markdown + ntfy alert.

    - Filters: high/critical severity + payable_tags match
    - Writes: /app/output/reports/{event.id}.md
    - Sends: ntfy to icarus_bounty_alerts (only if payable)
    """
    data = event.data
    severity = data.get("severity", "").lower()

    # ------------------------------------------------------------------
    # 1. Severity filter (high/critical only)
    # ------------------------------------------------------------------
    if severity not in ("high", "critical"):
        return

    # ------------------------------------------------------------------
    # 2. Payable filter (must contain at least one payable_tag)
    # ------------------------------------------------------------------
    tags = [t.lower() for t in data.get("tags", [])]
    payable_tags = [t.lower() for t in cfg.get("payable_tags", [])]
    if not any(tag in payable_tags for tag in tags):
        log.debug(f"[{program}] {event.id} not payable (tags: {tags})")
        return

    # ------------------------------------------------------------------
    # 3. Render Markdown report
    # ------------------------------------------------------------------
    if template is None:
        log.error("Cannot render report: template missing")
        return

    poc_path = None
    if cfg.get("screenshot", False):
        poc_path = f"/app/output/poc/{event.id}.png"
        # Ensure directory exists
        Path(poc_path).parent.mkdir(parents=True, exist_ok=True)

    md_content = template.render(
        program=program,
        url=data.get("url", "N/A"),
        severity=severity.title(),
        description=data.get("description", "No description"),
        poc=poc_path,
        timestamp=datetime.utcnow().isoformat() + "Z",
        event_id=event.id,
        tags=", ".join(data.get("tags", [])),
    )

    # Write report
    report_file = Path(f"/app/output/reports/{event.id}.md")
    report_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        report_file.write_text(md_content, encoding="utf-8")
        log.info(f"[{program}] Report written: {report_file}")
    except Exception as e:
        log.error(f"[{program}] Failed to write report: {e}")
        return

    # ------------------------------------------------------------------
    # 4. Send ntfy alert (only for payable vulns)
    # ------------------------------------------------------------------
    ntfy_server = cfg["ntfy_server"].rstrip("/")
    topic = cfg["ntfy_topic"]  # icarus_bounty_alerts

    payload = {
        "topic": topic,
        "title": f"[{severity.upper()}] {program}",
        "message": f"**{data.get('url', 'N/A')}**\n{data.get('description', '')[:140]}...",
        "tags": "moneybag,bug,skull" if severity == "critical" else "moneybag,bug",
        "priority": 5 if severity == "critical" else 4,
        "markdown": True,
    }

    # Attach screenshot if available
    if cfg.get("screenshot", False) and poc_path and Path(poc_path).exists():
        payload["attach"] = f"{ntfy_server}/poc/{event.id}.png"
        payload["filename"] = f"{event.id}.png"

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(f"{ntfy_server}/{topic}", json=payload)
            resp.raise_for_status()
            log.info(f"[{program}] ntfy alert sent: {event.id}")
        except Exception as e:
            log.error(f"[{program}] ntfy alert failed: {e}")
