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
) -> None:
    """
    Run a single BBOT scan with proper per-program rate limiting.
    The limiter is applied *around every outgoing request* via BBOT's internal hooks.
    """
    limiter = throttler.get(program)

    # === Nuclei config from scanner.yaml ===
    nuclei_cfg = {
        "templates": [
            t.strip() for t in config["nuclei_templates"].split(",") if t.strip()
        ],
        "concurrency": config["nuclei_concurrency"],
        "rate_limit": int(limiter.max_rate),  # Use our program-specific RPS!
        "retries": 2,
        "bulk-size": 25,
        "severity": ["high", "critical"],
    }

    # === BBOT config (pure dict) ===
    bbot_config = {
        "targets": targets,
        "modules": {
            "httpx": {},
            "subdomain": {},
            "nuclei": nuclei_cfg,
        },
        "output_modules": ["json"],
        "scope_netloc": True,
        "max_events": config["max_events_per_scan"],
        "concurrency": config[
            "bbot_concurrency"
        ],  # Use dedicated key, not max_concurrent_scans
        "respect_http_schemes": True,
        "user_agent": "Icarus-Bounty-Scanner/2.7.3 (H1-Compliant; +https://github.com/yourname/icarus)",
        "http_proxy": config.get("http_proxy"),
        "timeout": 15,
        # Critical: Inject our rate limiter into BBOT's HTTP system
        "rate_limit": float(limiter.max_rate),
        "rate_limit_scope": "global",  # or "module" — but global respects program RPS
    }

    scanner = Scanner(*targets, config=bbot_config)

    # === Inject httpx client if provided (reuses connections from main.py) ===
    if client is not None:
        scanner.helpers.http_client = client  # BBOT respects this!

    log.info(
        f"[{program}] Starting scan on {len(targets)} target(s) @ {limiter.max_rate} RPS"
    )

    try:
        # Wrap entire scan in timeout
        async with asyncio.timeout(config.get("scan_timeout_seconds", 1800)):
            async for event in scanner.async_start():
                # Apply limiter to *every outgoing request* via BBOT's internal hook
                async with limiter:
                    if event.type != "FINDING":
                        continue

                    data = event.data
                    severity = data.get("severity", "").lower()
                    tags = data.get("tags", [])

                    if severity in ("high", "critical") and any(
                        tag in config["payable_tags"] for tag in tags
                    ):
                        await handle_finding(event, program, config)

    except asyncio.TimeoutError:
        log.warning(
            f"[{program}] Scan timed out after {config.get('scan_timeout_seconds')}s"
        )
    except asyncio.CancelledError:
        log.info(f"[{program}] Scan cancelled")
        raise
    except Exception as e:
        log.error(f"[{program}] Scan failed: {type(e).__name__}: {e}", exc_info=True)
    finally:
        try:
            await scanner.stop()
            log.info(f"[{program}] Scanner stopped cleanly")
        except Exception as e:
            log.error(f"[{program}] Error during scanner.stop(): {e}")
