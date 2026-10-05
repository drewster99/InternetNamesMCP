"""
RDAP Bootstrap Cache Module

Fetches and caches the IANA RDAP bootstrap file to enable direct
registry queries instead of using rdap.org as a proxy.

The bootstrap file maps TLDs to their authoritative RDAP servers.
"""

import asyncio
import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# IANA bootstrap URL
IANA_BOOTSTRAP_URL = "https://data.iana.org/rdap/dns.json"


def _get_cache_path() -> Path:
    """Get the cache file path in user's config directory."""
    if os.name == 'nt':  # Windows
        base = Path(os.environ.get('APPDATA', Path.home()))
    else:  # macOS, Linux
        base = Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache'))

    return base / 'internet-names-mcp' / 'rdap_bootstrap.json'


# Cache file location. Its directory is created lazily on first save so an
# unwritable cache location can't prevent the server from importing.
BOOTSTRAP_CACHE_PATH = _get_cache_path()

# Default cache expiry if no Cache-Control header (24 hours)
DEFAULT_CACHE_TTL = 86400

# Seconds to wait after a failed refresh before trying IANA again. Without it,
# an unreachable IANA would cost a blocking fetch on every domain lookup.
REFRESH_FAILURE_BACKOFF = 300

# The in-memory bootstrap is the single source of truth for lookups. It is only
# ever replaced (never mutated in place) while holding _cache_lock, so readers
# that take a reference without the lock always see a complete dict.
_cache: dict | None = None

# time.time() at which the bootstrap is next due to be loaded or refreshed.
# Zero means it has not been loaded in this process yet.
_next_refresh_attempt: float = 0.0

# A threading lock rather than an asyncio.Lock: refreshes run from sync callers
# and from asyncio.to_thread workers, under more than one event loop over the
# life of the process (e.g. asyncio.run per call in tests and scripts).
_cache_lock = threading.Lock()


class RDAPBootstrapUnavailableError(Exception):
    """No RDAP bootstrap is available: IANA could not be reached and there is no valid cached copy."""


def _is_string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _is_valid_cache(cache: object) -> bool:
    return (
        isinstance(cache, dict)
        and isinstance(cache.get("expires"), (int, float))
        and isinstance(cache.get("services"), dict)
        and len(cache["services"]) > 0
        and all(_is_string_list(urls) for urls in cache["services"].values())
    )


def _load_cache() -> dict | None:
    """Load cache from disk, returning None if not found or invalid."""
    try:
        with open(BOOTSTRAP_CACHE_PATH, "r") as f:
            cache = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        logger.warning("Ignoring unreadable RDAP bootstrap cache %s: %s", BOOTSTRAP_CACHE_PATH, e)
        return None

    if not _is_valid_cache(cache):
        logger.warning("Ignoring malformed RDAP bootstrap cache %s", BOOTSTRAP_CACHE_PATH)
        return None
    return cache


def _save_cache(cache: dict) -> bool:
    """Atomically save cache to disk. Returns True on success."""
    # Write to a temp file and rename over the target so a crash, or another
    # server process reading concurrently, never sees a truncated file.
    temp_path: str | None = None
    try:
        BOOTSTRAP_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(
            dir=BOOTSTRAP_CACHE_PATH.parent,
            prefix=f"{BOOTSTRAP_CACHE_PATH.name}.",
            suffix=".tmp",
        )
        with os.fdopen(fd, "w") as f:
            json.dump(cache, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, BOOTSTRAP_CACHE_PATH)
        return True
    except OSError as e:
        logger.warning("Could not save RDAP bootstrap cache %s: %s", BOOTSTRAP_CACHE_PATH, e)
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except OSError:
                pass  # A leftover temp file is harmless; the save failure is already logged.
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


def _parse_bootstrap_services(data: object) -> dict[str, list[str]]:
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

    Raises:
        ValueError: If `data` is not in the bootstrap format.
    """
    if not isinstance(data, dict) or not isinstance(data.get("services"), list):
        raise ValueError("no 'services' list")

    services: dict[str, list[str]] = {}
    for entry in data["services"]:
        if not (
            isinstance(entry, list)
            and len(entry) >= 2
            and _is_string_list(entry[0])
            and _is_string_list(entry[1])
        ):
            raise ValueError(f"malformed services entry {entry!r:.100}")
        tlds, urls = entry[0], entry[1]
        for tld in tlds:
            services[tld.lower()] = urls
    return services


def _expiry_time(response: httpx.Response) -> float:
    """Absolute expiry time for a response, from Cache-Control max-age or the default TTL."""
    max_age = _parse_max_age(response.headers.get("Cache-Control", ""))
    return time.time() + (max_age if max_age else DEFAULT_CACHE_TTL)


def _fetch_bootstrap(current: dict | None) -> tuple[dict, bool] | None:
    """
    Fetch the bootstrap from IANA, as a conditional GET when `current` exists.

    Returns:
        (cache, changed) on success, where `changed` is False for a 304, or
        None if IANA did not provide a usable bootstrap.
    """
    headers = {
        "Accept": "application/json",
        "User-Agent": "InternetNamesMCP/1.0 (RDAP Bootstrap)",
    }
    if current:
        if current.get("last_modified"):
            headers["If-Modified-Since"] = current["last_modified"]
        if current.get("etag"):
            headers["If-None-Match"] = current["etag"]

    try:
        response = httpx.get(IANA_BOOTSTRAP_URL, headers=headers, timeout=30)
    except httpx.HTTPError as e:
        logger.warning("RDAP bootstrap fetch failed: %s: %s", type(e).__name__, e)
        return None

    if response.status_code == 304 and current:
        return {**current, "expires": _expiry_time(response)}, False

    if response.status_code == 200:
        try:
            services = _parse_bootstrap_services(response.json())
        except ValueError as e:
            logger.warning("RDAP bootstrap response is not a valid bootstrap: %s", e)
            return None

        if not services:
            logger.warning("RDAP bootstrap response contains no services")
            return None

        return {
            "last_modified": response.headers.get("Last-Modified", ""),
            "etag": response.headers.get("ETag", ""),
            "expires": _expiry_time(response),
            "services": services,
        }, True

    logger.warning("RDAP bootstrap fetch returned HTTP %s", response.status_code)
    return None


def _refresh_locked(force: bool) -> bool:
    """
    Bring the in-memory bootstrap up to date. Caller must hold _cache_lock.

    Returns:
        True if new bootstrap contents were fetched from IANA.
    """
    global _cache, _next_refresh_attempt

    # Another server process may have refreshed the shared cache file already.
    disk_cache = _load_cache()
    if disk_cache is not None and (_cache is None or disk_cache["expires"] > _cache["expires"]):
        _cache = disk_cache

    if not force and _cache is not None and time.time() < _cache["expires"]:
        _next_refresh_attempt = _cache["expires"]
        return False

    fetched = _fetch_bootstrap(_cache)
    if fetched is None:
        _next_refresh_attempt = time.time() + REFRESH_FAILURE_BACKOFF
        if _cache is not None:
            # RDAP server assignments rarely change, so an expired bootstrap is
            # still far more useful than none; make its use visible, not silent.
            logger.warning(
                "Using expired RDAP bootstrap (expired %s); retrying IANA in %ss",
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(_cache["expires"])),
                REFRESH_FAILURE_BACKOFF,
            )
        return False

    _cache, changed = fetched
    _next_refresh_attempt = _cache["expires"]
    _save_cache(_cache)
    return changed


def refresh_bootstrap(force: bool = False) -> bool:
    """
    Fetch/update the RDAP bootstrap cache from IANA.

    Uses conditional GET (If-Modified-Since, If-None-Match) to avoid
    unnecessary downloads when the cache is still valid. Blocks on network
    I/O; async code should use refresh_bootstrap_if_due_async() instead.

    Args:
        force: If True, ignore cache expiry and always check for updates.

    Returns:
        True if cache was updated, False if unchanged or using stale cache.
    """
    with _cache_lock:
        return _refresh_locked(force)


def _is_refresh_due() -> bool:
    return time.time() >= _next_refresh_attempt


def _refresh_bootstrap_if_due() -> None:
    """Load or refresh the bootstrap if due, honoring the failure backoff."""
    with _cache_lock:
        # Re-check under the lock: another caller may have finished a refresh
        # while this one was waiting, and it must not trigger a second fetch.
        if _is_refresh_due():
            _refresh_locked(force=False)


async def refresh_bootstrap_if_due_async() -> None:
    """Load or refresh the bootstrap if due, without blocking the event loop."""
    if _is_refresh_due():
        await asyncio.to_thread(_refresh_bootstrap_if_due)


def _current_bootstrap() -> dict:
    """
    The in-memory bootstrap, loaded or refreshed first if due. May block on network I/O.

    Raises:
        RDAPBootstrapUnavailableError: If no bootstrap could be loaded.
    """
    if _is_refresh_due():
        _refresh_bootstrap_if_due()
    return _require_bootstrap(_cache)


def _require_bootstrap(bootstrap: dict | None) -> dict:
    # A missing bootstrap must not be reported as "TLD not supported": that
    # would tell the caller every domain is unsupported when nothing was checked.
    if bootstrap is None:
        raise RDAPBootstrapUnavailableError(
            f"RDAP bootstrap unavailable: could not fetch {IANA_BOOTSTRAP_URL} "
            "and no valid cached copy exists; retry later"
        )
    return bootstrap


def _lookup_rdap_server(bootstrap: dict, tld: str) -> str | None:
    urls = bootstrap["services"].get(tld.lower())
    if urls:
        return urls[0]  # Return first URL
    return None


def get_rdap_server(tld: str) -> str | None:
    """
    Get the RDAP server URL for a given TLD.

    Automatically refreshes the bootstrap cache if expired, which blocks on
    network I/O; async code should use get_rdap_server_async() instead.

    Args:
        tld: The top-level domain (without leading dot), e.g. "com", "io"

    Returns:
        The RDAP server URL (e.g. "https://rdap.verisign.com/com/v1/"),
        or None if the TLD is not in the bootstrap.

    Raises:
        RDAPBootstrapUnavailableError: If no bootstrap could be loaded.
    """
    return _lookup_rdap_server(_current_bootstrap(), tld)


async def get_rdap_server_async(tld: str) -> str | None:
    """
    Get the RDAP server URL for a given TLD without blocking the event loop.

    Args:
        tld: The top-level domain (without leading dot), e.g. "com", "io"

    Returns:
        The RDAP server URL, or None if the TLD is not in the bootstrap.

    Raises:
        RDAPBootstrapUnavailableError: If no bootstrap could be loaded.
    """
    await refresh_bootstrap_if_due_async()
    return _lookup_rdap_server(_require_bootstrap(_cache), tld)


def is_tld_supported(tld: str) -> bool:
    """
    Check if a TLD is supported by RDAP (has an entry in the bootstrap).

    Args:
        tld: The top-level domain (without leading dot)

    Returns:
        True if the TLD has RDAP support, False otherwise.

    Raises:
        RDAPBootstrapUnavailableError: If no bootstrap could be loaded.
    """
    return get_rdap_server(tld) is not None


def get_supported_tlds() -> list[str]:
    """
    Get list of all TLDs supported by RDAP.

    Returns:
        List of TLD strings, sorted alphabetically.

    Raises:
        RDAPBootstrapUnavailableError: If no bootstrap could be loaded.
    """
    return sorted(_current_bootstrap()["services"].keys())
