"""
RDAP Bootstrap Cache Module

Fetches and caches the IANA RDAP bootstrap file to enable direct
registry queries instead of using rdap.org as a proxy.

The bootstrap file maps TLDs to their authoritative RDAP servers.
"""

import json
import os
import time
from pathlib import Path

import httpx

# IANA bootstrap URL
IANA_BOOTSTRAP_URL = "https://data.iana.org/rdap/dns.json"


def _get_cache_path() -> Path:
    """Get the cache file path in user's config directory."""
    if os.name == 'nt':  # Windows
        base = Path(os.environ.get('APPDATA', Path.home()))
    else:  # macOS, Linux
        base = Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache'))

    cache_dir = base / 'internet-names-mcp'
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / 'rdap_bootstrap.json'


# Cache file location
BOOTSTRAP_CACHE_PATH = _get_cache_path()

# Default cache expiry if no Cache-Control header (24 hours)
DEFAULT_CACHE_TTL = 86400


def _load_cache() -> dict | None:
    """Load cache from disk, returning None if not found or invalid."""
    try:
        if BOOTSTRAP_CACHE_PATH.exists():
            with open(BOOTSTRAP_CACHE_PATH, "r") as f:
                return json.load(f)
    except (json.JSONDecodeError, OSError):
        pass
    return None


def _save_cache(cache: dict) -> bool:
    """Save cache to disk. Returns True on success."""
    try:
        with open(BOOTSTRAP_CACHE_PATH, "w") as f:
            json.dump(cache, f, indent=2)
        return True
    except OSError:
        return False


def _parse_max_age(cache_control: str) -> int | None:
    """Parse max-age from Cache-Control header."""
    for directive in cache_control.split(","):
        directive = directive.strip().lower()
        if directive.startswith("max-age="):
            try:
                return int(directive[8:])
            except ValueError:
                pass
    return None


def _parse_bootstrap_services(data: dict) -> dict[str, list[str]]:
    """
    Parse IANA bootstrap format into TLD -> server URLs mapping.

    Bootstrap format:
    {
        "services": [
            [["com", "net"], ["https://rdap.verisign.com/com/v1/"]],
            [["org"], ["https://rdap.publicinterestregistry.org/rdap/"]],
            ...
        ]
    }
    """
    services = {}
    for entry in data.get("services", []):
        if len(entry) >= 2:
            tlds = entry[0]
            urls = entry[1]
            for tld in tlds:
                tld_lower = tld.lower()
                services[tld_lower] = urls
    return services


def refresh_bootstrap(force: bool = False) -> bool:
    """
    Fetch/update the RDAP bootstrap cache from IANA.

    Uses conditional GET (If-Modified-Since, If-None-Match) to avoid
    unnecessary downloads when the cache is still valid.

    Args:
        force: If True, ignore cache expiry and always check for updates.

    Returns:
        True if cache was updated, False if unchanged or using stale cache.
    """
    cache = _load_cache()

    # Check if refresh is needed
    if not force and cache:
        expires = cache.get("expires", 0)
        if time.time() < expires:
            return False  # Cache still valid

    # Build request headers for conditional GET
    headers = {
        "Accept": "application/json",
        "User-Agent": "InternetNamesMCP/1.0 (RDAP Bootstrap)",
    }
    if cache:
        if cache.get("last_modified"):
            headers["If-Modified-Since"] = cache["last_modified"]
        if cache.get("etag"):
            headers["If-None-Match"] = cache["etag"]

    try:
        response = httpx.get(IANA_BOOTSTRAP_URL, headers=headers, timeout=30)
    except httpx.HTTPError:
        # Network error - use stale cache if available
        return False

    if response.status_code == 304:
        # Not modified - extend expiry based on new Cache-Control
        if cache:
            cache_control = response.headers.get("Cache-Control", "")
            max_age = _parse_max_age(cache_control)
            if max_age:
                cache["expires"] = time.time() + max_age
            else:
                cache["expires"] = time.time() + DEFAULT_CACHE_TTL
            _save_cache(cache)
        return False

    if response.status_code == 200:
        # Parse and cache new bootstrap data
        try:
            data = response.json()
        except json.JSONDecodeError:
            return False

        services = _parse_bootstrap_services(data)
        if not services:
            return False  # Invalid data

        # Calculate expiry from Cache-Control
        cache_control = response.headers.get("Cache-Control", "")
        max_age = _parse_max_age(cache_control)
        expires = time.time() + (max_age if max_age else DEFAULT_CACHE_TTL)

        new_cache = {
            "last_modified": response.headers.get("Last-Modified", ""),
            "etag": response.headers.get("ETag", ""),
            "expires": expires,
            "services": services,
        }
        _save_cache(new_cache)
        return True

    # Other status codes (4xx, 5xx) - use stale cache
    return False


def get_rdap_server(tld: str) -> str | None:
    """
    Get the RDAP server URL for a given TLD.

    Automatically refreshes the bootstrap cache if expired.

    Args:
        tld: The top-level domain (without leading dot), e.g. "com", "io"

    Returns:
        The RDAP server URL (e.g. "https://rdap.verisign.com/com/v1/"),
        or None if the TLD is not in the bootstrap.
    """
    # Ensure cache is loaded/refreshed
    cache = _load_cache()

    if not cache or time.time() >= cache.get("expires", 0):
        refresh_bootstrap()
        cache = _load_cache()

    if not cache:
        return None

    services = cache.get("services", {})
    tld_lower = tld.lower()

    urls = services.get(tld_lower)
    if urls and len(urls) > 0:
        return urls[0]  # Return first URL

    return None


def is_tld_supported(tld: str) -> bool:
    """
    Check if a TLD is supported by RDAP (has an entry in the bootstrap).

    Args:
        tld: The top-level domain (without leading dot)

    Returns:
        True if the TLD has RDAP support, False otherwise.
    """
    return get_rdap_server(tld) is not None


def get_supported_tlds() -> list[str]:
    """
    Get list of all TLDs supported by RDAP.

    Returns:
        List of TLD strings, sorted alphabetically.
    """
    # Ensure cache is loaded/refreshed
    cache = _load_cache()

    if not cache or time.time() >= cache.get("expires", 0):
        refresh_bootstrap()
        cache = _load_cache()

    if not cache:
        return []

    return sorted(cache.get("services", {}).keys())
