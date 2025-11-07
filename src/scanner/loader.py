# src/scanner/loader.py
import os
import httpx
import json
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Any

log = logging.getLogger(__name__)

# Auto-detect base dir (same logic as main.py)
BASE_DIR = Path(os.getenv("ICARUS_BASE", "/app"))
SCAN_DB = BASE_DIR / "output" / "scan_history.json"

log.info(f"Scan history DB: {SCAN_DB}")

async def fetch_h1_targets(
    client: httpx.AsyncClient,
    url: str,
    config: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    if config.get("manual_run", False):
        log.info("MANUAL RUN: Using manual_targets from config")
        programs = {}
        for p in config.get("manual_targets", []):
            name = p["program"]
            programs[name] = {
                "assets": p["assets"],
                "rps": p.get("rps", config["default_rps"]),
            }
        return programs

    # === LIVE H1 MODE ===
    try:
        response = await client.get(url)
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPError as e:
        log.error("Failed to fetch H1 programs: %s", e)
        return {}

    # Load history
    history = {}
    if SCAN_DB.exists():
        try:
            history = json.loads(SCAN_DB.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Corrupted or unreadable scan_history.json, resetting: %s", e)

    now = datetime.utcnow()
    today = now.date().isoformat()
    daily_limit = config.get("daily_scan_limit_per_program", 3)
    cooldown_hours = config.get("min_hours_between_scans", 4)

    programs = {}
    updated = False

    for p in data.get("programs", []):
        name = p["name"]
        assets = [a["asset"] for a in p.get("assets", []) if a.get("eligible", False)]
        if not assets:
            continue

        entry = history.get(name, {})
        scans_today = entry.get(today, 0)
        last_scan_str = entry.get("last_scan")
        last_scan = (
            datetime.fromisoformat(last_scan_str.replace("Z", "+00:00"))
            if last_scan_str
            else None
        )

        if scans_today >= daily_limit:
            log.debug(
                "Skipping %s: daily limit reached (%d/%d)",
                name,
                scans_today,
                daily_limit,
            )
            continue
        if last_scan and (now - last_scan) < timedelta(hours=cooldown_hours):
            log.debug("Skipping %s: cooldown active", name)
            continue

        rps = p.get("policy", {}).get("max_requests_per_second")
        if rps is None:
            rps = config["default_rps"]
        override = config["program_overrides"].get(name, {})
        rps = override.get("rps", rps)

        programs[name] = {"assets": assets, "rps": float(rps)}
        updated = True

        history.setdefault(name, {})
        history[name][today] = scans_today + 1
        history[name]["last_scan"] = now.isoformat(timespec="seconds") + "Z"

    # === Atomic write with proper try/finally ===
    if updated:
        try:
            SCAN_DB.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = SCAN_DB.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(history, f, indent=2)
            tmp_path.replace(SCAN_DB)  # Atomic replace
            log.info("Updated scan history: %d programs", len(programs))
        except Exception as e:
            log.error("Failed to save scan_history.json: %s", e)
    else:
        log.info("No new programs to scan. History unchanged.")

    return programs
