# src/utils/cloud_cache.py

import httpx
import logging
from pathlib import Path

log = logging.getLogger("icarus.cache")

CLOUD_JSON_URL = "https://raw.githubusercontent.com/blacklanternsecurity/cloudcheck/master/cloud_providers.json"
CACHE_DIR = Path("/app/cache")
CACHE_FILE = CACHE_DIR / "cloud_providers.json"

def ensure_cloud_providers_cache() -> str:
    """
    Download and cache cloud_providers.json if missing or stale.
    Returns path to cached file.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if CACHE_FILE.exists():
        age_hours = (Path(CACHE_FILE).stat().st_mtime - CACHE_FILE.stat().st_mtime) / 3600
        if age_hours < 24:
            log.info("Using fresh cached cloud_providers.json")
            return str(CACHE_FILE)
        else:
            log.info("Cached cloud_providers.json is stale (>24h), refreshing...")

    log.info("Downloading cloud_providers.json...")
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(CLOUD_JSON_URL)
            resp.raise_for_status()
            CACHE_FILE.write_text(resp.text)
        log.info("cloud_providers.json cached successfully")
    except Exception as e:
        log.warning(f"Failed to download cloud_providers.json: {e}")
        if CACHE_FILE.exists():
            log.info("Falling back to stale cache")
        else:
            log.error("No cache available and download failed")
            raise

    return str(CACHE_FILE)