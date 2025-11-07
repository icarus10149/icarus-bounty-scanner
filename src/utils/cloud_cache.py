# src/utils/cloud_cache.py
import httpx
import logging
import os
from pathlib import Path
from datetime import datetime
from typing import Optional

log = logging.getLogger("icarus.cache")

CLOUD_JSON_URL = "https://raw.githubusercontent.com/blacklanternsecurity/cloudcheck/master/cloud_providers.json"

# Use ICARUS_BASE from main.py (set via os.environ["ICARUS_BASE"])
BASE_DIR = Path(os.environ.get("ICARUS_BASE", "/app"))
CACHE_DIR = BASE_DIR / "cache"
CACHE_FILE = CACHE_DIR / "cloud_providers.json"


async def ensure_cloud_providers_cache(
    client: Optional[httpx.AsyncClient] = None,
) -> str:
    """
    Asynchronously ensure cloud_providers.json is cached and fresh.
    Uses shared httpx client if provided (from main.py).
    Returns absolute path to cached file.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    log.debug(f"Cloud cache dir: {CACHE_DIR}")

    # --- Check if cache is fresh (<24h old) ---
    if CACHE_FILE.exists():
        age_seconds = (
            datetime.now() - datetime.fromtimestamp(CACHE_FILE.stat().st_mtime)
        ).total_seconds()
        if age_seconds < 24 * 3600:
            log.info("Using fresh cached cloud_providers.json")
            return str(CACHE_FILE)
        else:
            log.info(
                f"Cached cloud_providers.json is stale ({age_seconds / 3600:.1f}h), refreshing..."
            )

    # --- Download fresh copy ---
    log.info("Downloading cloud_providers.json...")
    download_success = False

    if client is None:
        client = httpx.AsyncClient(timeout=30.0)

    try:
        resp = await client.get(CLOUD_JSON_URL)
        resp.raise_for_status()
        CACHE_FILE.write_text(resp.text, encoding="utf-8")
        log.info(f"cloud_providers.json cached → {CACHE_FILE}")
        download_success = True
    except Exception as e:
        log.warning(f"Failed to download cloud_providers.json: {e}")
    finally:
        if client and not hasattr(client, "is_closed"):  # Only close if we created it
            await client.aclose()

    # --- Fallback: use stale cache if download failed ---
    if not download_success and CACHE_FILE.exists():
        log.info("Falling back to stale cache")
        return str(CACHE_FILE)
    elif not download_success:
        log.error("No cache available and download failed")
        raise RuntimeError("cloud_providers.json unavailable")

    return str(CACHE_FILE)
