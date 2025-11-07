import jinja2, asyncio
from datetime import datetime
from pathlib import Path
import httpx  # <-- only import

env = jinja2.Environment(loader=jinja2.FileSystemLoader("templates"))
template = env.get_template("report.md.j2")


async def handle_finding(event, program: str, cfg: dict):
    data = event.data
    severity = data.get("severity", "").lower()
    if severity not in ("high", "critical"):
        return

    md = template.render(
        program=program,
        url=data["url"],
        severity=severity.title(),
        description=data["description"],
        poc=f"/poc/{event.id}.png" if cfg["screenshot"] else None,
        timestamp=datetime.utcnow().isoformat() + "Z",
    )
    out_file = Path(f"output/reports/{event.id}.md")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(md)

    # ntfy alert via raw httpx
    payload = {
        "topic": cfg["ntfy_topic"],
        "title": f"[{severity.upper()}] {program}",
        "message": f"**{data['url']}**\n{data['description'][:140]}...",
        "tags": "moneybag,bug",
        "markdown": True,
        "attach": f"https://your-ntfy-host.com/poc/{event.id}.png"
        if cfg["screenshot"]
        else None,
        "filename": f"{event.id}.png" if cfg["screenshot"] else None,
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            await client.post(
                cfg["ntfy_server"].rstrip("/") + "/" + cfg["ntfy_topic"], json=payload
            )
        except Exception as e:
            print(f"[ntfy] Alert failed: {e}")
