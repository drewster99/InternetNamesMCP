"""
Internet Names MCP Server

An MCP server for checking availability of:
- Domain names (via NameSilo API or RDAP)
- Social media handles (via Sherlock + Playwright for X/Twitter)
- Subreddit names (via Reddit API)
"""

import asyncio
import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass

import httpx
from mcp.server.fastmcp import FastMCP

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

# Server version
VERSION = "0.1.5"

# Initialize the MCP server
mcp = FastMCP("internet-names")
mcp._mcp_server.version = VERSION

# =============================================================================
# Constants
# =============================================================================

NAMESILO_API_URL = "https://www.namesilo.com/api/checkRegisterAvailability"
DEFAULT_TLDS = ["com", "io", "ai", "co", "app", "dev", "net", "org"]

# Platforms supported by Sherlock + Twitter (via Playwright)
SUPPORTED_PLATFORMS = [
    "instagram",
    "twitter",
    "reddit",
    "youtube",
    "tiktok",
    "twitch",
    "threads",
]

# All supported socials (includes subreddit which is checked separately)
ALL_SOCIALS = SUPPORTED_PLATFORMS + ["subreddit"]

# Mapping from our lowercase names to Sherlock's expected names
SHERLOCK_PLATFORM_MAP = {
    "instagram": "Instagram",
    "reddit": "Reddit",
    "youtube": "YouTube",
    "tiktok": "TikTok",
    "twitch": "Twitch",
    "threads": "threads",
}


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
# Social Media Handle Checking (Sherlock + Playwright for Twitter)
# =============================================================================

def _check_sherlock(username: str, platforms: list[str]) -> dict[str, dict]:
    """Check username via Sherlock (excludes Twitter which is handled separately)."""
    # Filter out twitter - we handle that with Playwright
    sherlock_platforms = [SHERLOCK_PLATFORM_MAP[p] for p in platforms if p in SHERLOCK_PLATFORM_MAP]

    if not sherlock_platforms:
        return {}

    cmd = [
        "sherlock", username,
        "--print-all", "--no-txt", "--timeout", "15"
    ]
    for p in sherlock_platforms:
        cmd.extend(["--site", p])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        output = result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return {p.lower(): {"available": None, "error": "Timeout"} for p in sherlock_platforms}
    except FileNotFoundError:
        return {p.lower(): {"available": None, "error": "sherlock not found"} for p in sherlock_platforms}

    results = {}

    for line in output.split("\n"):
        line = line.strip()
        if not line or line.startswith("[*]"):
            continue

        if line.startswith("[+]"):
            parts = line[4:].split(": ", 1)
            if len(parts) >= 1:
                platform = parts[0].lower()
                url = parts[1] if len(parts) > 1 else None
                results[platform] = {"available": False, "url": url}

        elif line.startswith("[-]"):
            parts = line[4:].split(": ", 1)
            if len(parts) == 2:
                platform = parts[0].lower()
                status = parts[1]
                if status == "Not Found!":
                    results[platform] = {"available": True}
                elif "Error" in status or "Illegal" in status:
                    results[platform] = {"available": None, "error": status}
                else:
                    results[platform] = {"available": True}

    return results


def _check_twitter(username: str) -> dict:
    """Check Twitter/X username using Playwright."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"available": None, "error": "playwright not installed. Run: pip install playwright"}

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            )
            page = context.new_page()

            url = f"https://x.com/{username}"
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)

            empty_state = page.query_selector('[data-testid="empty_state_header_text"]')

            if empty_state:
                text = empty_state.inner_text().replace("'", "'").lower()
                if "doesn't exist" in text:
                    browser.close()
                    return {"available": True}
                elif "suspended" in text:
                    browser.close()
                    return {"available": False, "url": url, "note": "suspended"}

            user_name = page.query_selector('[data-testid="UserName"]')
            if user_name:
                browser.close()
                return {"available": False, "url": url}

            body_text = page.inner_text("body").replace("'", "'")
            browser.close()

            if "This account doesn't exist" in body_text:
                return {"available": True}
            elif f"@{username}" in body_text.lower():
                return {"available": False, "url": url}
            else:
                return {"available": None, "error": "Could not determine"}

    except Exception as e:
        error_str = str(e)
        if "Executable doesn't exist" in error_str or "browserType.launch" in error_str.lower():
            return {"available": None, "error": "Chromium not installed. Run: playwright install chromium"}
        return {"available": None, "error": error_str[:100]}


def _check_handles_internal(username: str, platforms: list[str]) -> dict[str, dict]:
    """Check username across multiple platforms."""
    results = {}

    # Check non-Twitter platforms via Sherlock
    sherlock_results = _check_sherlock(username, [p for p in platforms if p != "twitter"])
    results.update(sherlock_results)

    # Check Twitter via Playwright if requested
    if "twitter" in platforms:
        results["twitter"] = _check_twitter(username)

    # Fill in missing platforms
    for p in platforms:
        if p not in results:
            results[p] = {"available": None, "error": "No response"}

    return results


# =============================================================================
# Subreddit Checking (Reddit API)
# =============================================================================

def _check_subreddits_internal(names: list[str]) -> list[dict]:
    """Check subreddit availability via Reddit JSON API."""
    results = []
    headers = {"User-Agent": "SubredditChecker/1.0"}

    with httpx.Client(headers=headers, timeout=10) as client:
        for name in names:
            name = name.lower().strip()
            if name.startswith("r/"):
                name = name[2:]

            # Skip empty names
            if not name:
                continue

            url = f"https://www.reddit.com/r/{name}/about.json"

            try:
                response = client.get(url, follow_redirects=True)

                if response.status_code == 404:
                    results.append({"name": name, "available": True})
                elif response.status_code == 403:
                    results.append({"name": name, "available": False, "note": "private"})
                elif response.status_code == 200:
                    data = response.json()
                    sub_data = data.get("data", {})
                    if sub_data.get("display_name"):
                        subscribers = sub_data.get("subscribers", 0)
                        results.append({
                            "name": name,
                            "available": False,
                            "subscribers": subscribers
                        })
                    else:
                        results.append({"name": name, "available": True})
                else:
                    results.append({"name": name, "available": None, "error": f"HTTP {response.status_code}"})

            except Exception as e:
                results.append({"name": name, "available": None, "error": str(e)[:100]})

            time.sleep(0.5)  # Rate limiting

    return results


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
    method: str = "auto",
    onlyReportAvailable: bool = False
) -> str:
    """
    Check domain name availability and pricing.

    Args:
        names: List of domain names or base names to check.
               If a name contains a dot, it's treated as a full domain.
               Otherwise, it's combined with each TLD.
        tlds: List of TLDs to check (default: com, io, ai, co, app, dev, net, org)
        method: Lookup method - "auto" (default, uses namesilo if API key available, otherwise rdap),
                "rdap" (uses IANA bootstrap for direct registry queries),
                "namesilo" (requires API key, includes pricing)
        onlyReportAvailable: If true, only return available domains in response

    Returns:
        JSON with available domains, unavailable domains (unless onlyReportAvailable),
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
            # Errors are separate from unavailable when using RDAP
            if use_rdap:
                errors_list.append({
                    "domain": r.domain,
                    "error": r.error,
                })
            else:
                # NameSilo errors go to unavailable for backward compatibility
                unavailable_list.append(r.domain)
        else:
            unavailable_list.append(r.domain)

    response = {
        "available": available_list,
    }

    if not onlyReportAvailable:
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
def check_handles(
    username: str,
    platforms: list[str] | None = None,
    onlyReportAvailable: bool = False
) -> str:
    """
    Check social media handle/username availability across platforms.

    Includes X/Twitter checking (which takes ~4 seconds).

    Args:
        username: The username/handle to check
        platforms: List of platforms to check (default: all supported platforms)
                   Supported: instagram, twitter, reddit, youtube, tiktok, twitch, threads
        onlyReportAvailable: If true, only return available handles in response

    Returns:
        JSON with available platforms, unavailable platforms (unless onlyReportAvailable).
    """
    if not username or not username.strip():
        return json.dumps({"error": "No username provided"})

    username = username.strip()

    supported = SUPPORTED_PLATFORMS
    if platforms is None:
        platforms = supported.copy()
    else:
        # Normalize to lowercase
        platforms = [p.lower() for p in platforms]
        # Check if twitter was requested but not available
        if "twitter" in platforms and "twitter" not in supported:
            return json.dumps({
                "error": "Twitter checking unavailable. Chromium browser failed to install. Try manually: playwright install chromium"
            })
        # Filter to only supported platforms
        platforms = [p for p in platforms if p in supported]

    if not platforms:
        return json.dumps({"error": "No valid platforms specified"})

    results = _check_handles_internal(username, platforms)

    available_list = []
    unavailable_list = []

    for platform in platforms:
        info = results.get(platform, {"available": None, "error": "Unknown"})

        if info.get("error"):
            # Treat errors as unavailable with note
            unavailable_list.append({"platform": platform, "error": info["error"]})
        elif info.get("available"):
            available_list.append(platform)
        else:
            entry = {"platform": platform}
            if info.get("url"):
                entry["url"] = info["url"]
            if info.get("note"):
                entry["note"] = info["note"]
            unavailable_list.append(entry)

    response = {
        "available": available_list,
    }

    if not onlyReportAvailable:
        response["unavailable"] = unavailable_list

    return json.dumps(response)


@mcp.tool()
def check_subreddits(
    names: list[str],
    onlyReportAvailable: bool = False
) -> str:
    """
    Check subreddit name availability on Reddit.

    Args:
        names: List of subreddit names to check (with or without r/ prefix)
        onlyReportAvailable: If true, only return available subreddits in response

    Returns:
        JSON with available subreddits, unavailable subreddits (unless onlyReportAvailable).
    """
    if not names:
        return json.dumps({"error": "No subreddit names provided"})

    results = _check_subreddits_internal(names)

    available_list = []
    unavailable_list = []

    for r in results:
        name = r["name"]
        if r.get("error"):
            unavailable_list.append({"name": name, "error": r["error"]})
        elif r.get("available"):
            available_list.append(name)
        else:
            entry = {"name": name}
            if r.get("subscribers"):
                entry["subscribers"] = r["subscribers"]
            if r.get("note"):
                entry["note"] = r["note"]
            unavailable_list.append(entry)

    response = {
        "available": available_list,
    }

    if not onlyReportAvailable:
        response["unavailable"] = unavailable_list

    return json.dumps(response)


@mcp.tool()
async def check_everything(
    components: list[str],
    tlds: list[str] | None = None,
    platforms: list[str] | None = None,
    method: str = "auto",
    requireAllTLDsAvailable: bool = False,
    onlyReportAvailable: bool = False,
    alsoIncludeHyphens: bool = False
) -> str:
    """
    Comprehensive check across domains and social media.

    Generates name combinations from components and checks domains first (fast),
    then checks social media handles for names that pass the domain check.

    Args:
        components: Name components to combine (e.g., ["red", "sweater"])
                    Generates: single components + concatenations in both orders
        tlds: TLDs to check (default: com, net, org, io, ai)
        platforms: Social platforms to check (default: all)
        method: Domain lookup method - "auto" (default), "rdap", or "namesilo"
        requireAllTLDsAvailable: If true, a name must be available in ALL TLDs to pass
        onlyReportAvailable: If true, omit unavailable items from response
        alsoIncludeHyphens: If true, also check hyphenated versions (e.g., "red-sweater")

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

    supported = SUPPORTED_PLATFORMS
    if platforms is None:
        platforms = supported.copy()
    else:
        platforms = [p.lower() for p in platforms]
        # Check if twitter was requested but not available
        if "twitter" in platforms and "twitter" not in supported:
            return json.dumps({
                "error": "Twitter checking unavailable. Chromium browser failed to install. Try manually: playwright install chromium"
            })
        platforms = [p for p in platforms if p in supported]

    if not platforms:
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
            if alsoIncludeHyphens:
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

    # Group results by basename
    basename_results: dict[str, list[DomainResult]] = {}
    for r in domain_results:
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

        if requireAllTLDsAvailable:
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

    for basename in domain_successful_basenames:
        handle_results = _check_handles_internal(basename, platforms)

        available_for_name = []
        unavailable_for_name = []

        for platform in platforms:
            info = handle_results.get(platform, {"available": None, "error": "Unknown"})

            if info.get("error"):
                unavailable_for_name.append({"platform": platform, "error": info["error"]})
            elif info.get("available"):
                available_for_name.append(platform)
            else:
                entry = {"platform": platform}
                if info.get("url"):
                    entry["url"] = info["url"]
                unavailable_for_name.append(entry)

        if available_for_name:
            available_handles[basename] = available_for_name
        if unavailable_for_name:
            unavailable_handles[basename] = unavailable_for_name

    # Build response
    response = {
        "availableDomains": available_domains,
        "domainSuccessfulBasenames": domain_successful_basenames,
        "availableHandles": available_handles,
    }

    if not onlyReportAvailable:
        response["unavailableHandles"] = unavailable_handles

    # Build summary
    summary = {}

    # Find fully available names (available on ALL checked platforms)
    fully_available = []
    for basename in domain_successful_basenames:
        if basename in available_handles:
            if len(available_handles[basename]) == len(platforms):
                fully_available.append(basename)

    if fully_available:
        summary["fullyAvailable"] = fully_available

    # Find cheapest domain
    if available_domains:
        with_price = [d for d in available_domains if "price" in d]
        if with_price:
            cheapest = min(with_price, key=lambda x: x["price"])
            summary["cheapestDomain"] = cheapest

    if summary:
        response["summary"] = summary

    return json.dumps(response)
