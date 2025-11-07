# src/main.py
import asyncio
import signal
import os
import sys
import httpx
import logging
import yaml
from pathlib import Path

# ----------------------------------------------------------------------
# Imports
# ----------------------------------------------------------------------
from src.scanner.bbot_core import run_scan
from src.utils.cloud_cache import ensure_cloud_providers_cache
from src.scanner.loader import fetch_h1_targets
from src.scanner.throttler import ProgramThrottler

# ----------------------------------------------------------------------
# BBOT environment
# ----------------------------------------------------------------------
os.environ["BBOT_HOME"] = "/bbot"
os.environ["XDG_CONFIG_HOME"] = "/bbot/.config"
os.environ["XDG_CACHE_HOME"] = "/bbot/.cache"

# ----------------------------------------------------------------------
# SMART BASE DIRECTORY RESOLUTION
# ----------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.parent
LOCAL_MARKERS = ["config", "logs", "output"]

if Path("/app").exists():
    BASE_DIR = Path("/app")
    print("[INFO] Docker → /app")
elif all((PROJECT_ROOT / m).exists() for m in LOCAL_MARKERS):
    BASE_DIR = PROJECT_ROOT
    print(f"[INFO] Local dev mode → using project root: {BASE_DIR}")
else:
    BASE_DIR = Path.home() / ".icarus-bounty-scanner"
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[INFO] First-time local run → using fallback: {BASE_DIR}")

os.environ["ICARUS_BASE"] = str(BASE_DIR)

# ----------------------------------------------------------------------
# Create required sub-directories
# ----------------------------------------------------------------------
for sub in ("logs", "output", "config"):
    (BASE_DIR / sub).mkdir(parents=True, exist_ok=True)

# ----------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------
logging.basicConfig(
    level=logging.DEBUG if os.getenv("DEBUG") else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("icarus")
log.info(f"ICARUS BOUNTY SCANNER STARTED | BASE_DIR={BASE_DIR}")

# ----------------------------------------------------------------------
# Load scanner.yaml
# ----------------------------------------------------------------------
config_path = BASE_DIR / "config" / "scanner.yaml"
if not config_path.exists():
    log.error(f"Config not found: {config_path}")
    log.error("Copy scanner.yaml → icarus-bounty-scanner/config/scanner.yaml")
    sys.exit(1)

CONFIG = yaml.safe_load(config_path.read_text(encoding="utf-8"))
log.info("Config loaded successfully")


# ----------------------------------------------------------------------
# CLOUD PROVIDERS CACHE (async, uses shared client)
# ----------------------------------------------------------------------
_cache_lock = asyncio.Lock()

async def _setup_cloud_cache(client: httpx.AsyncClient) -> None:
    async with _cache_lock:
        bbot_config = CONFIG.get("bbot", {})
        if bbot_config.get("cloud_providers_path"):
            return  # already configured

        try:
            cloud_path = await ensure_cloud_providers_cache(client)
            CONFIG.setdefault("bbot", {})["cloud_providers_path"] = cloud_path
            log.info(f"BBOT cloudcheck using: {cloud_path}")
        except Exception as exc:
            log.warning("BBOT will download cloud_providers.json per worker (no cache)")
            log.debug(f"Cache error: {exc}")


# ----------------------------------------------------------------------
# Main scanning loop – MASS PARALLEL (one scan per program)
# ----------------------------------------------------------------------
async def main_loop() -> None:
    throttler = ProgramThrottler(CONFIG)

    # Global httpx client (shared across loader, cache, scans)
    limits = httpx.Limits(
        max_keepalive_connections=100,
        max_connections=500,
        keepalive_expiry=30.0,
    )
    timeout = httpx.Timeout(30.0, connect=10.0, read=60.0)
    transport = httpx.AsyncHTTPTransport(retries=3)

    async with httpx.AsyncClient(
        limits=limits,
        timeout=timeout,
        transport=transport,
        http2=True,
        headers={"User-Agent": "Icarus-Bounty-Scanner/2.7 (H1-Compliant)"},
        event_hooks={"response": [lambda r: r.raise_for_status()]},
    ) as client:
        # ---- cache cloud providers first ----
        await _setup_cloud_cache(client)

        while True:
            try:
                programs = await fetch_h1_targets(client, CONFIG["h1_json_url"], CONFIG)
                if not programs:
                    log.warning("No programs loaded. Sleeping 15 min…")
                    await asyncio.sleep(900)
                    continue

                log.info("Starting scan batch for %d programs", len(programs))

                # ---- limit concurrent BBOT scans (prevents Ansible race) ----
                max_scans = CONFIG.get("max_concurrent_scans", 10)
                sem = asyncio.Semaphore(max_scans)

                async def bound_scan(*args):
                    async with sem:
                        return await run_scan(*args, client=client)

                tasks = [
                    bound_scan([asset], name, CONFIG, throttler)
                    for name, info in programs.items()
                    for asset in info["assets"]
                    if not CONFIG.get("dry_run", False)
                ]

                if tasks:
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    for res in results:
                        if isinstance(res, Exception):
                            log.error("Scan task failed: %s", res)

                log.info("Batch complete. Sleeping 15 minutes…")
                await asyncio.sleep(900)

            except Exception as exc:
                log.exception("Critical error in main loop: %s", exc)
                await asyncio.sleep(60)


# ----------------------------------------------------------------------
# Graceful shutdown
# ----------------------------------------------------------------------
def _shutdown(loop: asyncio.AbstractEventLoop) -> None:
    log.info("Shutdown signal received. Cancelling all tasks…")
    for task in asyncio.all_tasks(loop):
        task.cancel()


def main() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: _shutdown(loop))

    try:
        loop.run_until_complete(main_loop())
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("Graceful shutdown complete.")
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()


if __name__ == "__main__":
    main()
