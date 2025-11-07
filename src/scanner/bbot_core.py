# src/scanner/bbot_core.py
import asyncio
import logging
from typing import List, Optional

import httpx
from bbot.scanner import Scanner

from src.scanner.reporter import handle_finding
from src.scanner.throttler import ProgramThrottler

log = logging.getLogger("icarus.bbot")


async def run_scan(
    targets: List[str],
    program: str,
    config: dict,
    throttler: ProgramThrottler,
    client: Optional[httpx.AsyncClient] = None,
) -> Scanner:
    """
    Run a single BBOT scan for one program, with H1 safeguards.

    - Enables subdomain-enum preset (no standalone "subdomain" module).
    - Rotates User-Agents per request.
    - Adds X-Bug-Bounty header to all HTTP.
    - Throttles via program-specific limiter.
    - Filters payable/high-severity for ntfy/Markdown.

    Returns Scanner for await stop() in caller.
    """
    limiter = throttler.get(program)

    # --- Nuclei config (high/critical only for noise balance) ---
    nuclei_cfg = {
        "templates": [
            t.strip() for t in config["nuclei_templates"].split(",") if t.strip()
        ],
        "concurrency": config["nuclei_concurrency"],  # e.g., 25 for 8c
        "rate_limit": int(limiter.max_rate),  # H1-safe: <10/min
        "retries": 2,
        "bulk_size": 25,  # Batch for efficiency
        "severity": ["high", "critical"],  # Minimize noise
        "mode": "severe",
    }

    # --- Default rotating User-Agents (benign, H1-compliant) ---
    default_uas = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/121.0",
    ]
    rotating_uas = config.get("user_agents", default_uas)  # Tune in scanner.yaml

    # --- BBOT config (preset-driven for max parallelization) ---
    bbot_config = {
        "targets": targets,
        "flags": ["subdomain-enum"],  # Enables massdns, passive, dnsgen, etc.
        "modules": {
            "httpx": {
                "user_agents": rotating_uas,  # Rotate per request
            },
            "nuclei": nuclei_cfg,
        },
        "output_modules": ["json"],  # For parsing in handle_finding
        "scope_netloc": True,  # H1 scope respect
        "max_events": config["max_events_per_scan"],  # e.g., 100k to cap RAM
        "concurrency": config["bbot_concurrency"],  # e.g., 30 for 8c/24GB (low idle)
        "respect_http_schemes": True,
        "http_proxy": config.get("http_proxy"),
        "timeout": 15,  # Quick timeouts for 24/7
        "rate_limit": float(limiter.max_rate),  # Global RPS cap
        "rate_limit_scope": "global",
        "web": {
            "headers": {
                "X-Bug-Bounty": config.get(
                    "bug_bounty_header", "h1/icarus-_-"
                ),  # Tune in YAML
            },
        },
    }

    # --- Create & configure scanner ---
    scanner = Scanner(*targets, config=bbot_config)

    # Reuse shared httpx client (connection pooling, async efficiency)
    if client is not None:
        scanner.helpers.http_client = client

    # Wrap requests with per-program throttler (H1 ban safeguard)
    original_request = scanner.helpers.request

    async def throttled_request(*args, **kwargs):
        async with limiter:  # Applies to every outgoing HTTP
            return await original_request(*args, **kwargs)

    scanner.helpers.request = throttled_request

    log.info(
        f"[{program}] Starting BBOT scan: {len(targets)} targets @ {limiter.max_rate} RPS "
        f"(subdomain-enum preset, UA rotation enabled)"
    )

    # --- Run scan with timeout (testable: dry_run skips) ---
    try:
        if config.get("dry_run", False):
            log.info(f"[{program}] Dry-run: skipping scan")
            return scanner

        async with asyncio.timeout(
            config.get("scan_timeout_seconds", 1800)
        ):  # 30min max
            async for event in scanner.async_start():
                if event.type != "FINDING":
                    continue

                data = event.data
                severity = data.get("severity", "").lower()
                tags = data.get("tags", [])

                # Balance output: only payable/high for ntfy/Markdown
                if severity in ("high", "critical") and any(
                    tag in config.get("payable_tags", []) for tag in tags
                ):
                    await handle_finding(event, program, config)

    except asyncio.TimeoutError:
        log.warning(
            f"[{program}] Timeout after {config.get('scan_timeout_seconds', 1800)}s"
        )
    except asyncio.CancelledError:
        log.info(f"[{program}] Cancelled (24/7 graceful)")
        raise
    except Exception as exc:
        log.error(f"[{program}] Scan error: {type(exc).__name__}: {exc}", exc_info=True)
    finally:
        # Always stop cleanly (debug: logs completion)
        try:
            await scanner.stop()
            log.info(f"[{program}] Scan complete & stopped")
        except Exception as exc:
            log.error(f"[{program}] stop() error: {exc}")

    return scanner  # For caller await (already handled here)
