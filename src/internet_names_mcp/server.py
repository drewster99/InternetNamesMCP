"""
Internet Names MCP Server

An MCP server for checking availability of:
- Domain names (via NameSilo API or RDAP)
- Social media handles (see social_checks.py for how each platform is checked)
- Subreddit names (via Reddit's JSON endpoints in a headless browser session)
"""

import asyncio
import json
import logging
import math
import os
import re
from dataclasses import dataclass, replace
from enum import Enum
from typing import Literal

import httpx
import idna
from mcp.server.mcpserver import MCPServer

from . import __version__
from .config import get_namesilo_key
from .rdap_client import (
    DomainStatus,
    check_domains_async,
)
from .social_checks import (
    AvailabilityStatus,
    HandleResult,
    Platform,
    SocialChecker,
)


class _RedactKeyQueryParameterFilter(logging.Filter):
    """Masks `key=` query parameter values in log messages."""

    _KEY_QUERY_PARAMETER_PATTERN = re.compile(r"([?&]key=)[^&\s\"']+")

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = self._KEY_QUERY_PARAMETER_PATTERN.sub(r"\1[REDACTED]", message)
        if redacted != message:
            record.msg = redacted
            record.args = None
        return True


# NameSilo only accepts the API key as a URL query parameter, so httpx's request log lines
# would carry it to stderr, which MCP clients save to log files.
logging.getLogger("httpx").addFilter(_RedactKeyQueryParameterFilter())
# httpcore logs raw request and response headers (which can echo the URL) through child
# loggers that the httpx filter does not see, and only ever at DEBUG.
logging.getLogger("httpcore").setLevel(logging.WARNING)
# Set INTERNET_NAMES_DEBUG=1 to see httpx request logging
if not os.environ.get("INTERNET_NAMES_DEBUG"):
    logging.getLogger("httpx").setLevel(logging.WARNING)

# Initialize the MCP server
mcp = MCPServer("internet-names", version=__version__)

logger = logging.getLogger(__name__)

# =============================================================================
# Constants
# =============================================================================

NAMESILO_API_URL = "https://www.namesilo.com/api/checkRegisterAvailability"
# NameSilo's checkRegisterAvailability reference: "up to 200 can be processed" per request.
NAMESILO_MAX_DOMAINS_PER_REQUEST = 200
NAMESILO_SUCCESS_CODE = 300
NAMESILO_REQUEST_TIMEOUT_SECONDS = 30
DEFAULT_TLDS = ["com", "io", "ai", "co", "app", "dev", "net", "org"]

SUPPORTED_PLATFORMS = [platform.value for platform in Platform]

# All supported socials (includes subreddit which is checked separately)
ALL_SOCIALS = SUPPORTED_PLATFORMS + ["subreddit"]

# No supported platform is known to allow a longer handle, and Bluesky places the handle in a
# hostname label, which DNS limits to 63 characters.
MAX_USERNAME_LENGTH = 63


# =============================================================================
# Domain Name Validation
# =============================================================================

class InvalidDomainNameError(ValueError):
    """A domain name, label or TLD that breaks DNS or IDNA rules."""


def _to_ascii_domain(text: str) -> str:
    """
    Convert a domain name to the lowercase ASCII (A-label) form that registries expect.

    Raises:
        InvalidDomainNameError: if the name has empty or over-long labels, characters other
            than letters, digits and hyphens, misplaced hyphens, or exceeds 253 characters.
    """
    try:
        # The idna package rather than Python's built-in codec, which implements the
        # obsolete IDNA 2003 rules and lets through characters such as "_" and "/".
        ascii_name = idna.encode(text, uts46=True).decode("ascii")
    except idna.IDNAError as e:
        raise InvalidDomainNameError(str(e)) from e
    # A trailing dot only marks a name as fully qualified; registries never include it.
    return ascii_name.removesuffix(".")


def _to_unicode_label(text: str) -> str:
    """
    Normalize a single label (a name without any TLD) to its lowercase Unicode (U-label) form.

    Raises:
        InvalidDomainNameError: if the text is not a valid label or contains a dot.
    """
    ascii_label = _to_ascii_domain(text)
    if "." in ascii_label:
        raise InvalidDomainNameError("Name must be a single label without dots")
    try:
        return idna.decode(ascii_label)
    except idna.IDNAError as e:
        raise InvalidDomainNameError(str(e)) from e


def _normalize_tlds(tlds: list[str]) -> list[str]:
    """
    Return the TLDs in lowercase ASCII form, without leading dots or duplicates, in their original order.

    Raises:
        InvalidDomainNameError: naming every TLD that cannot be used.
    """
    normalized: list[str] = []
    problems: list[str] = []
    for tld in tlds:
        # Callers often write a TLD the way it appears in a domain, with its leading dot.
        candidate = tld.strip().removeprefix(".")
        try:
            normalized.append(_to_ascii_domain(candidate))
        except InvalidDomainNameError as e:
            problems.append(f"{tld!r} ({e})")
    if problems:
        raise InvalidDomainNameError(
            f"Invalid TLDs: {'; '.join(problems)}. "
            'Pass each TLD as a separate array element, e.g. ["com", "io"]'
        )
    return list(dict.fromkeys(normalized))


def _describe_invalid_domains(errors: list[dict]) -> str:
    """One line naming each invalid domain or name and why it was rejected."""
    return "; ".join(f"{entry['domain']!r}: {entry['error']}" for entry in errors)


# =============================================================================
# Domain Checking (NameSilo + RDAP fallback)
# =============================================================================

@dataclass
class DomainAvailabilityResult:
    """Result of a domain availability check, normalized across lookup methods (NameSilo or RDAP)."""
    domain: str
    available: bool
    price: float | None = None
    error: str | None = None


async def _check_domains_rdap_async(
    domains: list[str],
    max_retries: int = 3,
) -> list[DomainAvailabilityResult]:
    """
    Check domain availability via RDAP protocol using async parallel execution.

    Returns DomainAvailabilityResult objects with proper status categorization.
    Errors (timeout, rate_limit) are NOT marked as unavailable.
    """
    rdap_results = await check_domains_async(domains, max_retries=max_retries)

    # NameSilo and RDAP lookups feed the same response builders, so RDAP results are
    # normalized into the shared DomainAvailabilityResult shape.
    results = []
    for r in rdap_results:
        if r.status == DomainStatus.AVAILABLE:
            results.append(DomainAvailabilityResult(domain=r.domain, available=True))
        elif r.status == DomainStatus.UNAVAILABLE:
            results.append(DomainAvailabilityResult(domain=r.domain, available=False))
        elif r.status == DomainStatus.UNSUPPORTED:
            results.append(DomainAvailabilityResult(
                domain=r.domain,
                available=False,
                error=r.error_message,
            ))
        else:  # ERROR status - keep error info for response
            results.append(DomainAvailabilityResult(
                domain=r.domain,
                available=False,
                error=r.error_message,
            ))

    return results


class _NameSiloReplySection(Enum):
    """The reply sections in which NameSilo reports a checked domain."""
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"


def _namesilo_domain_key(domain: str) -> str:
    """Comparison key used to match requested domains to the names in a NameSilo reply."""
    # DNS names are case-insensitive and a trailing dot names the same domain, so the reply
    # may spell a domain differently from the request.
    return domain.strip().rstrip(".").lower()


def _namesilo_section_entries(section: object) -> list[object] | None:
    """
    Flatten one reply section into its domain entries.

    Returns None when the section has a shape NameSilo is not known to produce.
    """
    # NameSilo's JSON mirrors its XML, so a section holding several domains is a list while a
    # section holding one is {"domain": ...} (the entry nested under "domain", or the section
    # being the entry itself). {"domain": [...]} is accepted too because that is the other
    # common XML-to-JSON rendering. An absent or empty element means no domains.
    if section is None or section == "" or section == {}:
        return []
    if isinstance(section, list):
        return section
    if isinstance(section, dict):
        inner = section.get("domain")
        if isinstance(inner, list):
            return inner
        if isinstance(inner, dict):
            return [inner]
        if isinstance(inner, str):
            return [section]
    return None


def _namesilo_entry_domain(entry: object) -> str | None:
    """Domain name of one reply entry, or None when the entry has an unknown shape."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict) and isinstance(entry.get("domain"), str):
        return entry["domain"]
    return None


def _namesilo_entry_result(
    section: _NameSiloReplySection,
    key: str,
    entry: object,
) -> DomainAvailabilityResult:
    """Convert one reply entry into the result for the domain identified by `key`."""
    match section:
        case _NameSiloReplySection.UNAVAILABLE:
            return DomainAvailabilityResult(domain=key, available=False)
        case _NameSiloReplySection.INVALID:
            return DomainAvailabilityResult(domain=key, available=False, error="Invalid domain name")
        case _NameSiloReplySection.AVAILABLE:
            raw_price = entry.get("price") if isinstance(entry, dict) else None
            if raw_price is None:
                return DomainAvailabilityResult(domain=key, available=True)
            price: float | None
            try:
                price = float(raw_price)
            except (TypeError, ValueError):
                price = None
            if price is None or not math.isfinite(price):
                # Reporting the domain as available with a guessed or dropped price would hide
                # a malformed reply, so surface it as an error that still says it was available.
                return DomainAvailabilityResult(
                    domain=key,
                    available=False,
                    error=f"NameSilo reported this domain available with an unparseable price: {raw_price!r}",
                )
            return DomainAvailabilityResult(domain=key, available=True, price=price)


# NameSilo rejects a request while another from the same API key is still processing (reply
# code 400), and MCP clients may run several tool calls at once.
_namesilo_request_lock = asyncio.Lock()


async def _check_namesilo_batch(
    client: httpx.AsyncClient,
    batch_keys: list[str],
    api_key: str,
) -> dict[str, DomainAvailabilityResult]:
    """
    Check one request's worth of domains via NameSilo.

    Returns a result for every key in `batch_keys`; domains NameSilo did not report on get an error.
    """
    def fail_all(message: str) -> dict[str, DomainAvailabilityResult]:
        return {key: DomainAvailabilityResult(domain=key, available=False, error=message) for key in batch_keys}

    params = {
        "version": "1",
        "type": "json",
        "key": api_key,
        "domains": ",".join(batch_keys),
    }

    # Error text avoids str(e) because httpx includes the request URL, which carries the API key.
    try:
        async with _namesilo_request_lock:
            response = await client.get(NAMESILO_API_URL, params=params)
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPStatusError as e:
        return fail_all(f"NameSilo HTTP error {e.response.status_code}")
    # InvalidURL (e.g. a query string over httpx's length limit) is not an HTTPError subclass.
    except (httpx.HTTPError, httpx.InvalidURL) as e:
        return fail_all(f"NameSilo request failed ({type(e).__name__})")
    except ValueError:
        return fail_all("NameSilo returned invalid JSON")

    reply = data.get("reply") if isinstance(data, dict) else None
    if not isinstance(reply, dict):
        return fail_all("NameSilo response has no reply object")

    raw_code = reply.get("code")
    # NameSilo's JSON mirrors its XML, so the code may arrive as a number or as a string.
    try:
        code = int(raw_code)
    except (TypeError, ValueError):
        return fail_all(f"NameSilo reply has an unrecognized code: {raw_code!r}")
    if code != NAMESILO_SUCCESS_CODE:
        detail = reply.get("detail") or "Unknown error"
        return fail_all(f"API Error {code}: {detail}")

    batch_key_set = set(batch_keys)
    results: dict[str, DomainAvailabilityResult] = {}
    conflicting_keys: set[str] = set()
    unmatched_entries: list[str] = []

    for section in _NameSiloReplySection:
        entries = _namesilo_section_entries(reply.get(section.value))
        if entries is None:
            return fail_all(f"NameSilo reply has an unrecognized '{section.value}' section")
        for entry in entries:
            name = _namesilo_entry_domain(entry)
            key = _namesilo_domain_key(name) if name is not None else None
            if key is None or key not in batch_key_set:
                unmatched_entries.append(repr(entry))
                continue
            result = _namesilo_entry_result(section, key, entry)
            if key in results and results[key] != result:
                conflicting_keys.add(key)
            results[key] = result

    if unmatched_entries:
        # The requested domains these entries were meant for are reported below as missing;
        # logging the raw entries makes the mismatch diagnosable.
        logger.warning("NameSilo reply entries matching no requested domain: %s", ", ".join(unmatched_entries))

    for key in conflicting_keys:
        results[key] = DomainAvailabilityResult(
            domain=key,
            available=False,
            error="NameSilo reported conflicting statuses for this domain",
        )
    for key in batch_keys:
        if key not in results:
            results[key] = DomainAvailabilityResult(
                domain=key,
                available=False,
                error="Domain missing from NameSilo response",
            )

    return results


async def _check_domains_internal(domains: list[str], api_key: str) -> list[DomainAvailabilityResult]:
    """
    Check domain availability via the NameSilo API.

    Returns exactly one result per entry of `domains`, in the same order, each carrying the
    domain as it was requested. Domains NameSilo does not report on get an explicit error.
    """
    requested_keys = [_namesilo_domain_key(domain) for domain in domains]
    results_by_key: dict[str, DomainAvailabilityResult] = {}
    sendable_keys: list[str] = []
    for key in dict.fromkeys(requested_keys):
        # A comma would split the entry into several domains in NameSilo's comma-delimited list.
        if not key or "," in key:
            results_by_key[key] = DomainAvailabilityResult(domain=key, available=False, error="Invalid domain name")
        else:
            sendable_keys.append(key)

    if sendable_keys:
        async with httpx.AsyncClient(timeout=NAMESILO_REQUEST_TIMEOUT_SECONDS) as client:
            for start in range(0, len(sendable_keys), NAMESILO_MAX_DOMAINS_PER_REQUEST):
                batch_keys = sendable_keys[start:start + NAMESILO_MAX_DOMAINS_PER_REQUEST]
                results_by_key.update(await _check_namesilo_batch(client, batch_keys, api_key))

    return [replace(results_by_key[key], domain=domain) for domain, key in zip(domains, requested_keys, strict=True)]


# =============================================================================
# Social Media Handle and Subreddit Checking
# =============================================================================

def _parse_platforms(platforms: list[str] | None) -> list[Platform]:
    """Resolve requested platform names (all platforms when None), dropping unsupported ones."""
    if platforms is None:
        return list(Platform)
    requested = dict.fromkeys(p.strip().lower() for p in platforms)
    return [Platform(p) for p in requested if p in SUPPORTED_PLATFORMS]


def _split_handle_results(
    platforms: list[Platform],
    results: dict[Platform, HandleResult],
) -> tuple[list[str], list[dict]]:
    """Split handle results into the response's available names and unavailable entries."""
    available_list: list[str] = []
    unavailable_list: list[dict] = []
    for platform in platforms:
        result = results[platform]
        if result.status == AvailabilityStatus.AVAILABLE:
            available_list.append(platform.value)
        elif result.status == AvailabilityStatus.ERROR:
            unavailable_list.append({"platform": platform.value, "error": result.error})
        else:
            entry = {"platform": platform.value}
            if result.url:
                entry["url"] = result.url
            if result.note:
                entry["note"] = result.note
            unavailable_list.append(entry)
    return available_list, unavailable_list


def _normalize_subreddit_names(names: list[str]) -> list[str]:
    """Lowercase names, strip any r/ prefix, and drop empty names."""
    normalized = []
    for name in names:
        name = name.lower().strip()
        if name.startswith("r/"):
            name = name[2:]
        if name:
            normalized.append(name)
    return normalized


# =============================================================================
# MCP Tools
# =============================================================================

@mcp.tool()
def version() -> str:
    """
    Get the version of the Internet Names MCP server.

    Returns:
        Version string including server name and version number.
    """
    return f"Internet Names MCP Server version {__version__}"


@mcp.tool()
def get_supported_socials() -> str:
    """
    Get list of supported social media platforms.

    Returns:
        JSON with list of platform names that can be checked.
        Note: 'subreddit' is checked via check_subreddits(), not check_handles().
    """
    return json.dumps({
        "platforms": ALL_SOCIALS
    })


@mcp.tool()
async def check_domains(
    names: list[str],
    tlds: list[str] | None = None,
    method: Literal["auto", "rdap", "namesilo"] = "auto",
    only_report_available: bool = False
) -> str:
    """
    Check domain name availability and pricing.

    Args:
        names: List of domain names or base names to check.
               If a name contains a dot, it's treated as a full domain.
               Otherwise, it's combined with each TLD.
        tlds: Array of TLD strings to check. Each element is a single TLD without
              a leading dot. Example: ["com", "io", "ai"] — NOT "com\nio\nai" or "com,io,ai".
              Default: ["com", "io", "ai", "co", "app", "dev", "net", "org"]
        method: Lookup method - "auto" (default, uses namesilo if API key available, otherwise rdap),
                "rdap" (uses IANA bootstrap for direct registry queries),
                "namesilo" (requires API key, includes pricing)
        only_report_available: If true, only return available domains in response

    Returns:
        JSON with available domains, unavailable domains (unless only_report_available),
        errors (for invalid names and timeout/rate_limit issues), and summary.
        Domains are reported in lowercase ASCII form; internationalized names appear as
        xn-- A-labels.
    """
    if not names:
        return json.dumps({"error": "No domain names provided"})

    if tlds is None:
        tlds = DEFAULT_TLDS

    # Validate method
    method = method.lower()
    if method not in ("rdap", "namesilo", "auto"):
        return json.dumps({"error": f"Invalid method '{method}'. Use 'rdap', 'namesilo', or 'auto'"})

    try:
        tlds = _normalize_tlds(tlds)
    except InvalidDomainNameError as e:
        return json.dumps({"error": str(e)})

    # Expand names with TLDs, filtering out empty/whitespace names
    domains = []
    invalid_domain_errors = []
    for name in names:
        name = name.strip()
        if not name:
            continue
        try:
            ascii_name = _to_ascii_domain(name)
        except InvalidDomainNameError as e:
            invalid_domain_errors.append({"domain": name, "error": f"Invalid domain name: {e}"})
            continue
        # Dots are judged after IDNA mapping, which turns full-width and ideographic dots into ".".
        if "." in ascii_name:
            domains.append(ascii_name)
            continue
        for tld in tlds:
            candidate = f"{ascii_name}.{tld}"
            # Revalidated because a label and a TLD that are each valid can still form a name over 253 characters.
            try:
                domains.append(_to_ascii_domain(candidate))
            except InvalidDomainNameError as e:
                invalid_domain_errors.append({"domain": candidate, "error": f"Invalid domain name: {e}"})

    # Remove duplicates while preserving order
    domains = list(dict.fromkeys(domains))

    if not domains:
        if invalid_domain_errors:
            return json.dumps({
                "error": f"No valid domain names after expansion. {_describe_invalid_domains(invalid_domain_errors)}"
            })
        return json.dumps({"error": "No valid domain names after expansion"})

    # Select lookup method
    # The Keychain lookup runs a subprocess that can block on a Keychain dialog.
    api_key = await asyncio.to_thread(get_namesilo_key)
    if method == "namesilo":
        if not api_key:
            return json.dumps({"error": "NameSilo API key not configured"})
        results = await _check_domains_internal(domains, api_key)
    elif method == "rdap":
        results = await _check_domains_rdap_async(domains)
    else:  # auto
        if api_key:
            results = await _check_domains_internal(domains, api_key)
        else:
            results = await _check_domains_rdap_async(domains)

    # Build response with proper error categorization
    available_list = []
    unavailable_list = []
    errors_list = list(invalid_domain_errors)

    for r in results:
        if r.available:
            entry = {"domain": r.domain}
            if r.price is not None:
                entry["price"] = r.price
            available_list.append(entry)
        elif r.error:
            errors_list.append({
                "domain": r.domain,
                "error": r.error,
            })
        else:
            unavailable_list.append(r.domain)

    response = {
        "available": available_list,
    }

    if not only_report_available:
        response["unavailable"] = unavailable_list
        if errors_list:
            response["errors"] = errors_list

    # Build summary
    summary = {}
    if available_list:
        # Find cheapest
        with_price = [d for d in available_list if "price" in d]
        if with_price:
            cheapest = min(with_price, key=lambda x: x["price"])
            summary["cheapestAvailable"] = cheapest

        # Find shortest domain name
        shortest = min(available_list, key=lambda x: len(x["domain"]))
        summary["shortestAvailable"] = shortest

    if summary:
        response["summary"] = summary

    return json.dumps(response)


@mcp.tool()
async def check_handles(
    username: str,
    platforms: list[str] | None = None,
    only_report_available: bool = False
) -> str:
    """
    Check social media handle/username availability across platforms.

    All platforms are checked in parallel; a call usually takes 2–5 seconds.
    Instagram, Threads and Reddit use a headless browser, which is downloaded
    on first use (that first call can take a minute or more).

    Args:
        username: The username/handle to check (at most 63 characters)
        platforms: List of platforms to check (default: all supported platforms)
                   Supported: instagram, twitter, reddit, youtube, tiktok, twitch, threads, bluesky,
                   github, snapchat, pinterest, kick, substack
        only_report_available: If true, only return available handles in response

    Returns:
        JSON with available platforms, unavailable platforms (unless only_report_available).
    """
    if not username or not username.strip():
        return json.dumps({"error": "No username provided"})

    username = username.strip()
    if len(username) > MAX_USERNAME_LENGTH:
        return json.dumps({
            "error": f"Username is {len(username)} characters; the limit is {MAX_USERNAME_LENGTH}"
        })

    selected_platforms = _parse_platforms(platforms)
    if not selected_platforms:
        return json.dumps({"error": "No valid platforms specified"})

    async with SocialChecker() as checker:
        results = await checker.check_handles(username, selected_platforms)

    available_list, unavailable_list = _split_handle_results(selected_platforms, results)

    response = {
        "available": available_list,
    }

    if not only_report_available:
        response["unavailable"] = unavailable_list

    return json.dumps(response)


@mcp.tool()
async def check_subreddits(
    names: list[str],
    only_report_available: bool = False
) -> str:
    """
    Check subreddit name availability on Reddit.

    Args:
        names: List of subreddit names to check (with or without r/ prefix)
        only_report_available: If true, only return available subreddits in response

    Returns:
        JSON with available subreddits, unavailable subreddits (unless only_report_available).
    """
    if not names:
        return json.dumps({"error": "No subreddit names provided"})

    async with SocialChecker() as checker:
        results = await checker.check_subreddits(_normalize_subreddit_names(names))

    available_list = []
    unavailable_list = []

    for r in results:
        if r.status == AvailabilityStatus.ERROR:
            unavailable_list.append({"name": r.name, "error": r.error})
        elif r.status == AvailabilityStatus.AVAILABLE:
            available_list.append(r.name)
        else:
            entry = {"name": r.name}
            if r.subscribers is not None:
                entry["subscribers"] = r.subscribers
            if r.note:
                entry["note"] = r.note
            unavailable_list.append(entry)

    response = {
        "available": available_list,
    }

    if not only_report_available:
        response["unavailable"] = unavailable_list

    return json.dumps(response)


@mcp.tool()
async def check_everything(
    components: list[str],
    tlds: list[str] | None = None,
    platforms: list[str] | None = None,
    method: Literal["auto", "rdap", "namesilo"] = "auto",
    require_all_tlds_available: bool = False,
    only_report_available: bool = False,
    also_include_hyphens: bool = False
) -> str:
    """
    Comprehensive check across domains and social media.

    This tool may be long-running. Domain checks are fast, but social handle
    checking (parallel requests per name, plus a headless browser for Instagram,
    Threads and Reddit) can take 30–90 seconds depending on the number of names
    and platforms checked.

    Generates name combinations from components and checks domains first (fast),
    then checks social media handles for names that pass the domain check.

    Args:
        components: Name components to combine (e.g., ["red", "sweater"])
                    Generates: single components + concatenations in both orders
        tlds: Array of TLD strings to check. Each element is a single TLD without
              a leading dot. Example: ["com", "io", "ai"] — NOT "com\nio\nai" or "com,io,ai".
              Default: ["com", "net", "org", "io", "ai"]
        platforms: Social platforms to check (default: all).
                   Supported: instagram, twitter, reddit, youtube, tiktok, twitch, threads, bluesky,
                   github, snapchat, pinterest, kick, substack
        method: Domain lookup method - "auto" (default, uses namesilo if API key available,
                otherwise rdap), "rdap" (direct registry queries), "namesilo" (requires API key)
        require_all_tlds_available: If true, a name must be available in ALL specified TLDs
                                    to qualify for social handle checking
        only_report_available: If true, omit unavailable items from response
        also_include_hyphens: If true, also check domains for hyphenated versions (e.g., "red-sweater").
                              Hyphenated versions are not checked for social handles, so they
                              never appear in available_handles, unavailable_handles or
                              fully_available. Hyphens typed into a component are kept and checked.

    Returns:
        JSON with available domains, successful basenames, available/unavailable handles, and summary.
        Names that are not valid domain labels are reported in domain_errors. Domains are
        reported in lowercase ASCII form (internationalized names as xn-- A-labels); basenames
        and handles use the normalized Unicode form of each name.
    """
    if tlds is None:
        tlds = ["com", "net", "org", "io", "ai"]

    if not tlds:
        return json.dumps({"error": "No TLDs specified"})

    try:
        # Deduplicated so require_all_tlds_available can compare against the TLD count.
        tlds = _normalize_tlds(tlds)
    except InvalidDomainNameError as e:
        return json.dumps({"error": str(e)})

    # Validate method
    method = method.lower()
    if method not in ("rdap", "namesilo", "auto"):
        return json.dumps({"error": f"Invalid method '{method}'. Use 'rdap', 'namesilo', or 'auto'"})

    selected_platforms = _parse_platforms(platforms)
    if not selected_platforms:
        return json.dumps({"error": "No valid platforms specified"})

    # Generate name combinations from components
    generated_names = set()
    hyphenated_variants = set()

    # Add single components (non-empty after stripping)
    for comp in components:
        comp = comp.lower().strip()
        if comp:
            generated_names.add(comp)

    # Add concatenations (both orders for 2+ components)
    if len(components) >= 2:
        # Clean components for joining
        clean_components = [c.lower().strip() for c in components if c.strip()]

        if clean_components:
            # All components concatenated in given order
            concat = "".join(clean_components)
            generated_names.add(concat)

            # Reverse order
            reverse_concat = "".join(reversed(clean_components))
            generated_names.add(reverse_concat)

            # Hyphenated versions are for domains only: many platforms, Instagram included,
            # reject hyphens in handles, and confirming each rejection spends Instagram's
            # small signup-check budget.
            if also_include_hyphens:
                hyphenated_variants.add("-".join(clean_components))
                hyphenated_variants.add("-".join(reversed(clean_components)))

    if not generated_names:
        return json.dumps({"error": "No valid name components provided"})

    # Names are compared, handle-checked and reported in normalized form, so that two
    # spellings of one domain label (such as full-width letters) are a single name.
    domain_errors = []
    normalized_by_name: dict[str, str] = {}
    for name in generated_names | hyphenated_variants:
        try:
            normalized_by_name[name] = _to_unicode_label(name)
        except InvalidDomainNameError as e:
            domain_errors.append({"domain": name, "error": f"Invalid domain name: {e}"})
    regular_names = {normalized_by_name[n] for n in generated_names if n in normalized_by_name}
    variant_names = {normalized_by_name[n] for n in hyphenated_variants if n in normalized_by_name}

    # Only variants that are not also regular names are domain-only. When a single component
    # is non-blank its hyphen join is that component, which must still get handle checks.
    # Regular names keep handle checks even when the caller typed a hyphen into a component.
    domain_only_names = variant_names - regular_names

    # Each domain maps back to its basename because a multi-label TLD such as "co.uk" means
    # the basename cannot be recovered by splitting the domain.
    basename_by_domain: dict[str, str] = {}
    for name in regular_names | variant_names:
        for tld in tlds:
            candidate = f"{name}.{tld}"
            # Revalidated because a label and a TLD that are each valid can still form a name over 253 characters.
            try:
                basename_by_domain[_to_ascii_domain(candidate)] = name
            except InvalidDomainNameError as e:
                domain_errors.append({"domain": candidate, "error": f"Invalid domain name: {e}"})

    if not basename_by_domain:
        return json.dumps({
            "error": f"No valid domain names could be generated. {_describe_invalid_domains(domain_errors)}"
        })

    all_domains = list(basename_by_domain)

    # Select lookup method
    # The Keychain lookup runs a subprocess that can block on a Keychain dialog.
    api_key = await asyncio.to_thread(get_namesilo_key)
    if method == "namesilo":
        if not api_key:
            return json.dumps({"error": "NameSilo API key not configured"})
        domain_results = await _check_domains_internal(all_domains, api_key)
    elif method == "rdap":
        domain_results = await _check_domains_rdap_async(all_domains)
    else:  # auto
        if api_key:
            domain_results = await _check_domains_internal(all_domains, api_key)
        else:
            domain_results = await _check_domains_rdap_async(all_domains)

    # Group results by basename and collect errors
    basename_results: dict[str, list[DomainAvailabilityResult]] = {}
    for r in domain_results:
        if r.error:
            domain_errors.append({"domain": r.domain, "error": r.error})
        basename = basename_by_domain[r.domain]
        if basename not in basename_results:
            basename_results[basename] = []
        basename_results[basename].append(r)

    # Determine which basenames pass the domain check
    domain_successful_basenames = []
    available_domains = []

    for basename, results in basename_results.items():
        available_for_basename = [r for r in results if r.available]

        if require_all_tlds_available:
            # Must have all TLDs available
            if len(available_for_basename) == len(tlds):
                domain_successful_basenames.append(basename)
                for r in available_for_basename:
                    entry = {"domain": r.domain}
                    if r.price is not None:
                        entry["price"] = r.price
                    available_domains.append(entry)
        else:
            # At least one TLD available
            if available_for_basename:
                domain_successful_basenames.append(basename)
                for r in available_for_basename:
                    entry = {"domain": r.domain}
                    if r.price is not None:
                        entry["price"] = r.price
                    available_domains.append(entry)

    # Check social handles for successful basenames
    handle_check_basenames = [b for b in domain_successful_basenames if b not in domain_only_names]
    available_handles: dict[str, list[str]] = {}
    unavailable_handles: dict[str, list[dict]] = {}

    async with SocialChecker() as checker:
        for basename in handle_check_basenames:
            handle_results = await checker.check_handles(basename, selected_platforms)
            available_for_name, unavailable_for_name = _split_handle_results(selected_platforms, handle_results)

            if available_for_name:
                available_handles[basename] = available_for_name
            if unavailable_for_name:
                unavailable_handles[basename] = unavailable_for_name

    # Build response
    response = {
        "available_domains": available_domains,
        "domain_successful_basenames": domain_successful_basenames,
        "available_handles": available_handles,
    }

    if not only_report_available:
        response["unavailable_handles"] = unavailable_handles
        if domain_errors:
            response["domain_errors"] = domain_errors

    # Build summary
    summary = {}

    # Find fully available names (available on ALL checked platforms)
    fully_available = []
    for basename in handle_check_basenames:
        if basename in available_handles:
            if len(available_handles[basename]) == len(selected_platforms):
                fully_available.append(basename)

    if fully_available:
        summary["fully_available"] = fully_available

    # Find cheapest domain
    if available_domains:
        with_price = [d for d in available_domains if "price" in d]
        if with_price:
            cheapest = min(with_price, key=lambda x: x["price"])
            summary["cheapest_domain"] = cheapest

    if summary:
        response["summary"] = summary

    return json.dumps(response)
