# src/main.py
import asyncio
import signal
import os
import sys
import httpx
import logging
import yaml
from pathlib import Path
from src.scanner.bbot_core import run_scan
from src.utils.cloud_cache import ensure_cloud_providers_cache
from src.scanner.loader import fetch_h1_targets
from src.scanner.throttler import ProgramThrottler

os.environ["BBOT_HOME"] = "/bbot"
os.environ["XDG_CONFIG_HOME"] = "/bbot/.config"
os.environ["XDG_CACHE_HOME"] = "/bbot/.cache"

# === SMART BASE DIRECTORY RESOLUTION ===
# 1. Docker: /app (mounted via volumes)
# 2. Local dev: ./config, ./logs, ./output exist → use project root
# 3. Fallback: ~/.icarus-bounty-scanner (never crashes)
PROJECT_ROOT = Path(__file__).parent.parent  # ~/icarus-bounty-scanner
LOCAL_MARKERS = ["config", "logs", "output"]

if Path("/app").exists():
    BASE_DIR = Path("/app")
    print("[INFO] Running in Docker → using /app")
elif all((PROJECT_ROOT / marker).exists() for marker in LOCAL_MARKERS):
    BASE_DIR = PROJECT_ROOT
    print(f"[INFO] Local dev mode → using project root: {BASE_DIR}")
else:
    BASE_DIR = Path.home() / ".icarus-bounty-scanner"
    BASE_DIR.mkdir(exist_ok=True)
    print(f"[INFO] First-time local run → using fallback: {BASE_DIR}")

# Export for other modules
os.environ["ICARUS_BASE"] = str(BASE_DIR)

# === Create required dirs ===
for subdir in ["logs", "output", "config"]:
    (BASE_DIR / subdir).mkdir(exist_ok=True)

# === Logging ===
logging.basicConfig(
    level=logging.DEBUG if os.getenv("DEBUG") else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout  # This is key!
)
log = logging.getLogger("icarus")
log.info(f"ICARUS BOUNTY SCANNER STARTED | BASE_DIR={BASE_DIR}")

# === Load config ===
config_path = BASE_DIR / "config" / "scanner.yaml"
if not config_path.exists():
    log.error(f"Config not found: {config_path}")
    log.error("Copy your scanner.yaml → icarus-bounty-scanner/config/scanner.yaml")
    sys.exit(1)

CONFIG = yaml.safe_load(config_path.read_text(encoding="utf-8"))
log.info("Config loaded successfully")

# === CLOUD PROVIDERS CACHE ===
try:
    cloud_path = ensure_cloud_providers_cache()
    # Inject into BBOT config globally
    CONFIG.setdefault("bbot", {})["cloud_providers_path"] = cloud_path
    log.info(f"BBOT cloudcheck using: {cloud_path}")
except Exception:
    log.warning("BBOT will download cloud_providers.json per worker (no cache)")

async def main_loop():
    throttler = ProgramThrottler(CONFIG)

    # httpx client with aggressive pooling + HTTP/2
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
        event_hooks={"response": [lambda r: r.raise_for_status()]},  # Auto-raise
    ) as client:
        while True:
            try:
                programs = await fetch_h1_targets(client, CONFIG["h1_json_url"], CONFIG)
                if not programs:
                    log.warning("No programs loaded. Sleeping 15min...")
                    await asyncio.sleep(900)
                    continue

                log.info("Starting scan batch for %d programs", len(programs))

                # Use Semaphore to limit concurrent *scans* (not just connections)
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
                    # Gather with return_exceptions to avoid one crash killing all
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    for res in results:
                        if isinstance(res, Exception):
                            log.error("Scan task failed: %s", res)

                log.info("Batch complete. Sleeping 15 minutes...")
                await asyncio.sleep(900)

            except Exception as e:
                log.exception("Critical error in main loop: %s", e)
                await asyncio.sleep(60)


def shutdown(loop: asyncio.AbstractEventLoop):
    log.info("Shutdown signal received. Cancelling all tasks...")
    for task in asyncio.all_tasks(loop):
        task.cancel()


def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: shutdown(loop))

    try:
        loop.run_until_complete(main_loop())
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("Graceful shutdown complete.")
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()


if __name__ == "__main__":
    main()
