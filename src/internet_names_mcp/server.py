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
import os
from dataclasses import dataclass
from typing import Literal

import httpx
from mcp.server.mcpserver import MCPServer

from .config import get_namesilo_key

# Suppress httpx request logging by default (shows API keys in URLs)
# Set INTERNET_NAMES_DEBUG=1 to enable verbose HTTP logging
if not os.environ.get("INTERNET_NAMES_DEBUG"):
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
from .rdap_bootstrap import get_rdap_server
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

# Server version
VERSION = "0.1.9"

# Initialize the MCP server
mcp = MCPServer("internet-names", version=VERSION)

# =============================================================================
# Constants
# =============================================================================

NAMESILO_API_URL = "https://www.namesilo.com/api/checkRegisterAvailability"
DEFAULT_TLDS = ["com", "io", "ai", "co", "app", "dev", "net", "org"]

SUPPORTED_PLATFORMS = [platform.value for platform in Platform]

# All supported socials (includes subreddit which is checked separately)
ALL_SOCIALS = SUPPORTED_PLATFORMS + ["subreddit"]


# =============================================================================
# Domain Checking (NameSilo + RDAP fallback)
# =============================================================================

@dataclass
class DomainResult:
    """Result of a domain availability check."""
    domain: str
    available: bool
    price: float | None = None
    error: str | None = None


async def _check_domains_rdap_async(
    domains: list[str],
    max_retries: int = 3,
) -> list[DomainResult]:
    """
    Check domain availability via RDAP protocol using async parallel execution.

    Returns DomainResult objects with proper status categorization.
    Errors (timeout, rate_limit) are NOT marked as unavailable.
    """
    rdap_results = await check_domains_async(domains, max_retries=max_retries)

    # Convert rdap_client.DomainResult to local DomainResult for backward compatibility
    results = []
    for r in rdap_results:
        if r.status == DomainStatus.AVAILABLE:
            results.append(DomainResult(domain=r.domain, available=True))
        elif r.status == DomainStatus.UNAVAILABLE:
            results.append(DomainResult(domain=r.domain, available=False))
        elif r.status == DomainStatus.UNSUPPORTED:
            results.append(DomainResult(
                domain=r.domain,
                available=False,
                error=r.error_message,
            ))
        else:  # ERROR status - keep error info for response
            results.append(DomainResult(
                domain=r.domain,
                available=False,
                error=r.error_message,
            ))

    return results


def _check_domains_rdap(
    domains: list[str],
    delay: float = 1.0,  # Deprecated, ignored
    max_retries: int = 3,
) -> list[DomainResult]:
    """
    Synchronous wrapper for RDAP domain checking.

    Note: The 'delay' parameter is deprecated and ignored.
    Rate limiting is now handled per-host automatically.
    """
    return asyncio.run(_check_domains_rdap_async(domains, max_retries=max_retries))


def _check_domains_internal(domains: list[str], api_key: str) -> list[DomainResult]:
    """Internal function to check domain availability via NameSilo API."""
    params = {
        "version": "1",
        "type": "json",
        "key": api_key,
        "domains": ",".join(domains),
    }

    try:
        response = httpx.get(NAMESILO_API_URL, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPError as e:
        return [DomainResult(domain=d, available=False, error=str(e)) for d in domains]
    except ValueError as e:
        return [DomainResult(domain=d, available=False, error=f"Invalid JSON: {e}") for d in domains]

    reply = data.get("reply", {})
    code = reply.get("code")
    if code and int(code) != 300:
        detail = reply.get("detail", "Unknown error")
        return [DomainResult(domain=d, available=False, error=f"API Error {code}: {detail}") for d in domains]

    results = []

    # Process available domains
    # API returns different formats:
    # - Multiple: {"available": [{"domain": "foo.com", "price": 17.29}, ...]}
    # - Single: {"available": {"domain": {"domain": "foo.com", "price": 17.29}}}
    available = reply.get("available", {})
    if isinstance(available, dict):
        # Single domain case - nested under "domain" key
        inner = available.get("domain")
        if isinstance(inner, dict):
            available = [inner]
        elif isinstance(inner, list):
            available = inner
        else:
            available = []
    elif not isinstance(available, list):
        available = []

    for item in available:
        if isinstance(item, dict):
            domain = item.get("domain", "")
            price = item.get("price")
            results.append(DomainResult(
                domain=domain,
                available=True,
                price=float(price) if price else None
            ))

    # Process unavailable domains
    unavailable = reply.get("unavailable", {})
    if isinstance(unavailable, dict) and "domain" in unavailable:
        unavailable = [unavailable["domain"]]
    elif not isinstance(unavailable, list):
        unavailable = []

    for domain in unavailable:
        if isinstance(domain, str):
            results.append(DomainResult(domain=domain, available=False))
        elif isinstance(domain, dict):
            results.append(DomainResult(domain=domain.get("domain", ""), available=False))

    # Process invalid domains
    invalid = reply.get("invalid", {})
    if isinstance(invalid, dict) and "domain" in invalid:
        invalid = [invalid["domain"]]
    elif not isinstance(invalid, list):
        invalid = []

    for domain in invalid:
        if isinstance(domain, str):
            results.append(DomainResult(domain=domain, available=False, error="Invalid domain name"))
        elif isinstance(domain, dict):
            results.append(DomainResult(domain=domain.get("domain", ""), available=False, error="Invalid domain name"))

    return results


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
    return f"Internet Names MCP Server version {VERSION}"


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
        errors (for timeout/rate_limit issues), and summary.
    """
    if not names:
        return json.dumps({"error": "No domain names provided"})

    if tlds is None:
        tlds = DEFAULT_TLDS

    # Validate method
    method = method.lower()
    if method not in ("rdap", "namesilo", "auto"):
        return json.dumps({"error": f"Invalid method '{method}'. Use 'rdap', 'namesilo', or 'auto'"})

    # Expand names with TLDs, filtering out empty/whitespace names
    domains = []
    for name in names:
        name = name.strip()
        if not name:
            continue
        if "." in name:
            domains.append(name)
        else:
            for tld in tlds:
                domains.append(f"{name}.{tld}")

    # Remove duplicates while preserving order
    domains = list(dict.fromkeys(domains))

    if not domains:
        return json.dumps({"error": "No valid domain names after expansion"})

    # Select lookup method
    api_key = get_namesilo_key()
    use_rdap = False
    if method == "namesilo":
        if not api_key:
            return json.dumps({"error": "NameSilo API key not configured"})
        results = _check_domains_internal(domains, api_key)
    elif method == "rdap":
        use_rdap = True
        results = await _check_domains_rdap_async(domains)
    else:  # auto
        if api_key:
            results = _check_domains_internal(domains, api_key)
        else:
            use_rdap = True
            results = await _check_domains_rdap_async(domains)

    # Build response with proper error categorization
    available_list = []
    unavailable_list = []
    errors_list = []

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

    This tool may take 10–30 seconds. All platforms are checked in parallel;
    Instagram, Threads and Reddit need a headless browser, which takes a few
    seconds to start.

    Args:
        username: The username/handle to check
        platforms: List of platforms to check (default: all supported platforms)
                   Supported: instagram, twitter, reddit, youtube, tiktok, twitch, threads, bluesky
        only_report_available: If true, only return available handles in response

    Returns:
        JSON with available platforms, unavailable platforms (unless only_report_available).
    """
    if not username or not username.strip():
        return json.dumps({"error": "No username provided"})

    username = username.strip()

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
                   Supported: instagram, twitter, reddit, youtube, tiktok, twitch, threads, bluesky
        method: Domain lookup method - "auto" (default, uses namesilo if API key available,
                otherwise rdap), "rdap" (direct registry queries), "namesilo" (requires API key)
        require_all_tlds_available: If true, a name must be available in ALL specified TLDs
                                    to qualify for social handle checking
        only_report_available: If true, omit unavailable items from response
        also_include_hyphens: If true, also check hyphenated versions (e.g., "red-sweater")

    Returns:
        JSON with available domains, successful basenames, available/unavailable handles, and summary.
    """
    if tlds is None:
        tlds = ["com", "net", "org", "io", "ai"]

    if not tlds:
        return json.dumps({"error": "No TLDs specified"})

    # Validate method
    method = method.lower()
    if method not in ("rdap", "namesilo", "auto"):
        return json.dumps({"error": f"Invalid method '{method}'. Use 'rdap', 'namesilo', or 'auto'"})

    selected_platforms = _parse_platforms(platforms)
    if not selected_platforms:
        return json.dumps({"error": "No valid platforms specified"})

    # Generate name combinations from components
    generated_names = set()

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

            # Hyphenated versions (only for domains, not handles)
            if also_include_hyphens:
                hyphen_concat = "-".join(clean_components)
                generated_names.add(hyphen_concat)

                hyphen_reverse = "-".join(reversed(clean_components))
                generated_names.add(hyphen_reverse)

    generated_names = list(generated_names)

    if not generated_names:
        return json.dumps({"error": "No valid name components provided"})

    # Build all domain combinations
    all_domains = []
    for name in generated_names:
        for tld in tlds:
            all_domains.append(f"{name}.{tld}")

    # Select lookup method
    api_key = get_namesilo_key()
    if method == "namesilo":
        if not api_key:
            return json.dumps({"error": "NameSilo API key not configured"})
        domain_results = _check_domains_internal(all_domains, api_key)
    elif method == "rdap":
        domain_results = await _check_domains_rdap_async(all_domains)
    else:  # auto
        if api_key:
            domain_results = _check_domains_internal(all_domains, api_key)
        else:
            domain_results = await _check_domains_rdap_async(all_domains)

    # Group results by basename and collect errors
    basename_results: dict[str, list[DomainResult]] = {}
    domain_errors = []
    for r in domain_results:
        if r.error:
            domain_errors.append({"domain": r.domain, "error": r.error})
        # Extract basename from domain
        basename = r.domain.rsplit(".", 1)[0]
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
    available_handles: dict[str, list[str]] = {}
    unavailable_handles: dict[str, list[dict]] = {}

    async with SocialChecker() as checker:
        for basename in domain_successful_basenames:
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
    for basename in domain_successful_basenames:
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
