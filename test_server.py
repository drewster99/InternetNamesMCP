#!/usr/bin/env python3
"""
Test suite for Internet Names MCP Server

Usage:
    source .venv/bin/activate
    python test_server.py
"""

import sys

# Check Python version and dependencies early
if sys.version_info < (3, 10):
    print("Error: Python 3.10+ required")
    print()
    print("Activate the virtual environment:")
    print("    source .venv/bin/activate")
    print("    python test_server.py")
    sys.exit(1)

try:
    import httpx
    import mcp
except ImportError as e:
    print(f"Error: {e}")
    print()
    print("Activate the virtual environment first:")
    print("    source .venv/bin/activate")
    print("    python test_server.py")
    sys.exit(1)

import asyncio
import json
import random
import string
import time
from dataclasses import dataclass


def run_sync(coro):
    """Helper to run async coroutines synchronously for tests."""
    return asyncio.run(coro)


def generate_unique_name() -> str:
    """Generate a unique name unlikely to be taken."""
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
    return f"xyztest{suffix}"


# Add current directory to path for imports
sys.path.insert(0, ".")

from server import (
    get_supported_socials,
    check_domains,
    check_handles,
    check_subreddits,
    check_everything,
    SUPPORTED_PLATFORMS,
    ALL_SOCIALS,
    DEFAULT_TLDS,
    VERSION,
)

from rdap_bootstrap import (
    get_rdap_server,
    is_tld_supported,
    get_supported_tlds,
    refresh_bootstrap,
    BOOTSTRAP_CACHE_PATH,
)


@dataclass
class TestResult:
    """Result of a single test."""
    name: str
    passed: bool
    message: str = ""
    duration: float = 0.0


class TestRunner:
    """Runs tests and collects results."""

    def __init__(self):
        self.results: list[TestResult] = []
        self.current_section: str = ""

    def section(self, name: str):
        """Start a new test section."""
        self.current_section = name
        print(f"\n{'=' * 60}")
        print(f"  {name}")
        print(f"{'=' * 60}")

    def test(self, name: str, condition: bool, message: str = ""):
        """Record a test result."""
        result = TestResult(
            name=f"{self.current_section}: {name}",
            passed=condition,
            message=message
        )
        self.results.append(result)

        if condition:
            print(f"  ✓ {name}")
        else:
            print(f"  ✗ {name}")
            if message:
                print(f"    → {message}")

    def test_json(self, name: str, json_str: str, checks: dict):
        """Test JSON response against expected checks."""
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            self.test(name, False, f"Invalid JSON: {e}")
            return None

        all_passed = True
        messages = []

        for check_name, check_fn in checks.items():
            try:
                if not check_fn(data):
                    all_passed = False
                    messages.append(f"{check_name} failed")
            except Exception as e:
                all_passed = False
                messages.append(f"{check_name} raised {e}")

        self.test(name, all_passed, "; ".join(messages) if messages else "")
        return data

    def summary(self) -> bool:
        """Print summary and return True if all tests passed."""
        passed = sum(1 for r in self.results if r.passed)
        failed = sum(1 for r in self.results if not r.passed)
        total = len(self.results)

        print(f"\n{'=' * 60}")
        print(f"  SUMMARY: {passed}/{total} passed, {failed} failed")
        print(f"{'=' * 60}")

        if failed > 0:
            print("\nFailed tests:")
            for r in self.results:
                if not r.passed:
                    print(f"  ✗ {r.name}")
                    if r.message:
                        print(f"    → {r.message}")

        return failed == 0


def run_offline_tests(runner: TestRunner):
    """Run tests that don't require API calls."""

    # =========================================================================
    # RDAP Bootstrap
    # =========================================================================
    runner.section("RDAP Bootstrap")

    # Test bootstrap refresh
    refreshed = refresh_bootstrap()
    runner.test("bootstrap refresh runs without error", True)

    # Test cache file exists after refresh
    runner.test("cache file exists", BOOTSTRAP_CACHE_PATH.exists())

    # Test get_rdap_server for known TLDs
    com_server = get_rdap_server("com")
    runner.test("get_rdap_server returns URL for .com", com_server is not None)
    if com_server:
        runner.test(".com server URL is valid", com_server.startswith("https://"))

    net_server = get_rdap_server("net")
    runner.test("get_rdap_server returns URL for .net", net_server is not None)

    org_server = get_rdap_server("org")
    runner.test("get_rdap_server returns URL for .org", org_server is not None)

    # Test is_tld_supported
    runner.test("is_tld_supported for .com", is_tld_supported("com"))
    runner.test("is_tld_supported for .net", is_tld_supported("net"))
    runner.test("is_tld_supported for .dev", is_tld_supported("dev"))
    runner.test("is_tld_supported for .app", is_tld_supported("app"))
    runner.test("is_tld_supported for .xyz", is_tld_supported("xyz"))

    # Test case insensitivity
    runner.test("is_tld_supported case insensitive (COM)", is_tld_supported("COM"))

    # Test get_supported_tlds returns a list
    supported = get_supported_tlds()
    runner.test("get_supported_tlds returns list", isinstance(supported, list))
    runner.test("get_supported_tlds has entries", len(supported) > 10)
    runner.test("get_supported_tlds includes com", "com" in supported)

    # Test unknown TLD returns None
    unknown = get_rdap_server("notarealtld12345")
    runner.test("unknown TLD returns None", unknown is None)
    runner.test("unknown TLD not supported", not is_tld_supported("notarealtld12345"))

    # =========================================================================
    # get_supported_socials
    # =========================================================================
    runner.section("get_supported_socials")

    result = get_supported_socials()
    data = runner.test_json("returns valid JSON", result, {
        "has platforms": lambda d: "platforms" in d,
        "platforms is list": lambda d: isinstance(d["platforms"], list),
    })

    if data:
        platforms = data["platforms"]
        runner.test("includes instagram", "instagram" in platforms)
        runner.test("includes twitter", "twitter" in platforms)
        runner.test("includes reddit", "reddit" in platforms)
        runner.test("includes youtube", "youtube" in platforms)
        runner.test("includes tiktok", "tiktok" in platforms)
        runner.test("includes twitch", "twitch" in platforms)
        runner.test("includes threads", "threads" in platforms)
        runner.test("includes subreddit", "subreddit" in platforms)
        runner.test("matches ALL_SOCIALS constant", platforms == ALL_SOCIALS)

    # =========================================================================
    # check_domains - edge cases
    # =========================================================================
    runner.section("check_domains - edge cases")

    # Empty list
    result = run_sync(check_domains([]))
    runner.test_json("empty list returns error", result, {
        "has error": lambda d: "error" in d,
    })

    # Whitespace-only names
    result = run_sync(check_domains(["", "   "]))
    runner.test_json("whitespace-only names returns error", result, {
        "has error": lambda d: "error" in d,
    })

    # =========================================================================
    # check_handles - edge cases
    # =========================================================================
    runner.section("check_handles - edge cases")

    # Empty username
    result = check_handles("")
    runner.test_json("empty username returns error", result, {
        "has error": lambda d: "error" in d,
    })

    # Whitespace username
    result = check_handles("   ")
    runner.test_json("whitespace username returns error", result, {
        "has error": lambda d: "error" in d,
    })

    # Invalid platforms only
    result = check_handles("testuser", platforms=["invalid", "fake"])
    runner.test_json("invalid platforms returns error", result, {
        "has error": lambda d: "error" in d,
    })

    # Mixed valid/invalid platforms - should work with valid ones
    result = check_handles("testuser", platforms=["instagram", "invalid"])
    runner.test_json("mixed platforms uses valid ones", result, {
        "has available key": lambda d: "available" in d,
        "no error": lambda d: "error" not in d,
    })

    # =========================================================================
    # check_subreddits - edge cases
    # =========================================================================
    runner.section("check_subreddits - edge cases")

    # Empty list
    result = check_subreddits([])
    runner.test_json("empty list returns error", result, {
        "has error": lambda d: "error" in d,
    })

    # =========================================================================
    # check_domains - method parameter
    # =========================================================================
    runner.section("check_domains - method parameter")

    # Invalid method returns error
    result = run_sync(check_domains(["test"], tlds=["com"], method="invalid"))
    runner.test_json("invalid method returns error", result, {
        "has error": lambda d: "error" in d,
        "error mentions method": lambda d: "method" in d.get("error", "").lower(),
    })

    # Valid methods accepted (case insensitive)
    result = run_sync(check_domains(["test"], tlds=["com"], method="RDAP"))
    runner.test_json("method is case insensitive", result, {
        "no error": lambda d: "error" not in d,
    })

    # =========================================================================
    # check_everything - edge cases
    # =========================================================================
    runner.section("check_everything - edge cases")

    # Invalid method returns error
    result = run_sync(check_everything(["test"], method="invalid"))
    runner.test_json("invalid method returns error", result, {
        "has error": lambda d: "error" in d,
    })

    # Empty components
    result = run_sync(check_everything([]))
    runner.test_json("empty components returns error", result, {
        "has error": lambda d: "error" in d,
    })

    # Whitespace-only components
    result = run_sync(check_everything(["", "   "]))
    runner.test_json("whitespace components returns error", result, {
        "has error": lambda d: "error" in d,
    })

    # Empty TLDs
    result = run_sync(check_everything(["test"], tlds=[]))
    runner.test_json("empty TLDs returns error", result, {
        "has error": lambda d: "error" in d,
    })

    # Invalid platforms only
    result = run_sync(check_everything(["test"], platforms=["invalid"]))
    runner.test_json("invalid platforms returns error", result, {
        "has error": lambda d: "error" in d,
    })


def run_online_tests(runner: TestRunner):
    """Run tests that require API calls."""

    # Use a randomly generated unique string unlikely to be taken
    unique_name = generate_unique_name()
    print(f"\n  Using unique test name: {unique_name}")

    # =========================================================================
    # check_domains - method="rdap" (default)
    # =========================================================================
    runner.section("check_domains - method=rdap")

    # Check a known taken domain via RDAP
    result = run_sync(check_domains(["google"], tlds=["com"], method="rdap"))
    data = runner.test_json("rdap: google.com is unavailable", result, {
        "has available": lambda d: "available" in d,
        "has unavailable": lambda d: "unavailable" in d,
        "google.com in unavailable": lambda d: "google.com" in d["unavailable"],
    })

    # Check likely available domain via RDAP
    result = run_sync(check_domains([unique_name], tlds=["com", "net"], method="rdap"))
    data = runner.test_json("rdap: unique name returns valid structure", result, {
        "has available": lambda d: "available" in d,
        "available is list": lambda d: isinstance(d["available"], list),
    })

    if data and data.get("available") and len(data["available"]) > 0:
        first = data["available"][0]
        runner.test("rdap: available entry has domain field", "domain" in first)
        # RDAP does not include pricing
        runner.test("rdap: no price field (expected)", "price" not in first)
    else:
        runner.test("(skipped) rdap entry structure", True, "no available domains")

    # =========================================================================
    # check_domains - method="namesilo"
    # =========================================================================
    runner.section("check_domains - method=namesilo")

    # Check a known taken domain via NameSilo
    result = run_sync(check_domains(["google"], tlds=["com"], method="namesilo"))
    data = runner.test_json("namesilo: google.com is unavailable", result, {
        "has available": lambda d: "available" in d,
        "has unavailable": lambda d: "unavailable" in d,
        "google.com in unavailable": lambda d: "google.com" in d["unavailable"],
    })

    # Check likely available domain via NameSilo (includes pricing)
    result = run_sync(check_domains([unique_name], tlds=["com", "io"], method="namesilo"))
    data = runner.test_json("namesilo: unique name returns valid structure", result, {
        "has available": lambda d: "available" in d,
        "available is list": lambda d: isinstance(d["available"], list),
    })

    if data and data.get("available") and len(data["available"]) > 0:
        first = data["available"][0]
        runner.test("namesilo: available entry has domain field", "domain" in first)
        runner.test("namesilo: available entry has price", "price" in first)
    else:
        runner.test("(skipped) namesilo entry structure", True, "no available domains")

    # =========================================================================
    # check_domains - method="auto"
    # =========================================================================
    runner.section("check_domains - method=auto")

    # Auto should use NameSilo when API key is present (includes pricing)
    result = run_sync(check_domains([unique_name], tlds=["com"], method="auto"))
    data = runner.test_json("auto: returns valid structure", result, {
        "has available": lambda d: "available" in d,
    })

    if data and data.get("available") and len(data["available"]) > 0:
        first = data["available"][0]
        # With API key configured, auto should use NameSilo and include price
        runner.test("auto: has price (uses namesilo with API key)", "price" in first)
    else:
        runner.test("(skipped) auto entry structure", True, "no available domains")

    # =========================================================================
    # check_domains - additional tests
    # =========================================================================
    runner.section("check_domains - additional tests")

    # Test onlyReportAvailable
    result = run_sync(check_domains(["google"], tlds=["com"], onlyReportAvailable=True))
    runner.test_json("onlyReportAvailable omits unavailable", result, {
        "no unavailable key": lambda d: "unavailable" not in d,
    })

    # Test summary - only present when there are available domains
    result = run_sync(check_domains([unique_name], tlds=["com", "io", "ai"], method="namesilo"))
    data = runner.test_json("response is valid JSON", result, {
        "has available key": lambda d: "available" in d,
    })

    if data:
        has_available = len(data.get("available", [])) > 0
        has_summary = "summary" in data
        # Summary should be present if and only if there are available domains
        runner.test("summary present when domains available", has_summary == has_available,
                    f"available={has_available}, summary={has_summary}")
        if has_summary:
            summary = data["summary"]
            runner.test("summary has cheapestAvailable", "cheapestAvailable" in summary)
            runner.test("summary has shortestAvailable", "shortestAvailable" in summary)

    # =========================================================================
    # check_handles - real API (Sherlock, no Twitter for speed)
    # =========================================================================
    runner.section("check_handles - API tests (Sherlock)")

    # Check a known taken handle
    result = check_handles("billgates", platforms=["instagram", "youtube"])
    data = runner.test_json("billgates is taken on major platforms", result, {
        "has available": lambda d: "available" in d,
        "has unavailable": lambda d: "unavailable" in d,
    })

    if data and data.get("unavailable"):
        # Check structure of unavailable entries
        for entry in data["unavailable"]:
            if isinstance(entry, dict) and "platform" in entry:
                runner.test("unavailable entry has platform", True)
                if "url" in entry:
                    runner.test("unavailable entry has url", True)
                break

    # Check likely available handle
    result = check_handles(unique_name, platforms=["instagram", "youtube"])
    runner.test_json("unique name is likely available", result, {
        "has available": lambda d: "available" in d,
        "available has entries": lambda d: len(d["available"]) > 0,
    })

    # Test onlyReportAvailable
    result = check_handles("billgates", platforms=["instagram"], onlyReportAvailable=True)
    runner.test_json("onlyReportAvailable omits unavailable", result, {
        "no unavailable key": lambda d: "unavailable" not in d,
    })

    # =========================================================================
    # check_handles - Twitter (slower, separate test)
    # =========================================================================
    runner.section("check_handles - Twitter API test")

    result = check_handles("elonmusk", platforms=["twitter"])
    data = runner.test_json("elonmusk Twitter check works", result, {
        "has available": lambda d: "available" in d,
        "has unavailable": lambda d: "unavailable" in d,
    })

    if data:
        # elonmusk should be taken
        unavail_platforms = [
            e["platform"] if isinstance(e, dict) else e
            for e in data.get("unavailable", [])
        ]
        runner.test("elonmusk is taken on Twitter", "twitter" in unavail_platforms or any(
            isinstance(e, dict) and e.get("platform") == "twitter"
            for e in data.get("unavailable", [])
        ))

    # =========================================================================
    # check_subreddits - real API
    # =========================================================================
    runner.section("check_subreddits - API tests")

    # Check a known existing subreddit
    result = check_subreddits(["programming"])
    data = runner.test_json("r/programming exists", result, {
        "has available": lambda d: "available" in d,
        "has unavailable": lambda d: "unavailable" in d,
    })

    if data and data.get("unavailable"):
        # Find programming in unavailable
        prog = None
        for entry in data["unavailable"]:
            if isinstance(entry, dict) and entry.get("name") == "programming":
                prog = entry
                break
        if prog:
            runner.test("programming has subscribers count", "subscribers" in prog)
            runner.test("subscribers is int", isinstance(prog.get("subscribers"), int))

    # Check likely available subreddit
    result = check_subreddits([unique_name])
    runner.test_json("unique subreddit is available", result, {
        "has available": lambda d: "available" in d,
        "unique in available": lambda d: unique_name in d["available"],
    })

    # Test r/ prefix stripping
    result = check_subreddits(["r/programming"])
    data = runner.test_json("r/ prefix is stripped", result, {
        "programming in unavailable": lambda d: any(
            (isinstance(e, dict) and e.get("name") == "programming")
            for e in d.get("unavailable", [])
        ),
    })

    # Test onlyReportAvailable
    result = check_subreddits(["programming"], onlyReportAvailable=True)
    runner.test_json("onlyReportAvailable omits unavailable", result, {
        "no unavailable key": lambda d: "unavailable" not in d,
    })

    # =========================================================================
    # check_everything - real API
    # =========================================================================
    runner.section("check_everything - API tests")

    # Use unique components (derived from our random unique_name)
    comp1 = unique_name[:8]
    comp2 = unique_name[8:]
    result = run_sync(check_everything(
        components=[comp1, comp2],
        tlds=["com", "io"],
        platforms=["instagram", "youtube"]  # Skip twitter for speed
    ))
    data = runner.test_json("check_everything returns correct structure", result, {
        "has availableDomains": lambda d: "availableDomains" in d,
        "has domainSuccessfulBasenames": lambda d: "domainSuccessfulBasenames" in d,
        "has availableHandles": lambda d: "availableHandles" in d,
    })

    if data:
        # Verify structure is correct regardless of availability
        basenames = data.get("domainSuccessfulBasenames", [])
        runner.test("domainSuccessfulBasenames is list", isinstance(basenames, list))

        # If we got basenames, verify they look reasonable
        if basenames:
            runner.test("basenames are strings", all(isinstance(b, str) for b in basenames))
        else:
            # All domains might be taken - that's okay for this test
            runner.test("(skipped) basename content check", True, "no available basenames")

    # Test requireAllTLDsAvailable
    result = run_sync(check_everything(
        components=[unique_name],
        tlds=["com", "io"],
        platforms=["instagram"],
        requireAllTLDsAvailable=True
    ))
    data = runner.test_json("requireAllTLDsAvailable works", result, {
        "has structure": lambda d: "availableDomains" in d,
    })

    # Test onlyReportAvailable
    result = run_sync(check_everything(
        components=[unique_name],
        tlds=["com"],
        platforms=["instagram"],
        onlyReportAvailable=True
    ))
    runner.test_json("onlyReportAvailable omits unavailableHandles", result, {
        "no unavailableHandles": lambda d: "unavailableHandles" not in d,
    })

    # Test summary generation
    result = run_sync(check_everything(
        components=[unique_name],
        tlds=["com", "io"],
        platforms=["instagram", "youtube"]
    ))
    data = runner.test_json("check_everything generates summary", result, {
        "has summary": lambda d: "summary" in d or len(d.get("availableDomains", [])) == 0,
    })

    if data and data.get("summary"):
        summary = data["summary"]
        if "cheapestDomain" in summary:
            runner.test("cheapestDomain has domain and price",
                        "domain" in summary["cheapestDomain"] and "price" in summary["cheapestDomain"])

    # Test alsoIncludeHyphens - use unique components to ensure availability
    hyphen_comp1 = unique_name[:6]
    hyphen_comp2 = unique_name[6:]
    result = run_sync(check_everything(
        components=[hyphen_comp1, hyphen_comp2],
        tlds=["com"],
        platforms=["instagram"],
        alsoIncludeHyphens=True
    ))
    data = runner.test_json("alsoIncludeHyphens generates hyphenated names", result, {
        "has structure": lambda d: "availableDomains" in d or "domainSuccessfulBasenames" in d,
    })

    if data:
        basenames = data.get("domainSuccessfulBasenames", [])
        # With alsoIncludeHyphens=True, we should have hyphenated basenames if any are available
        if basenames:
            has_hyphen = any("-" in b for b in basenames)
            runner.test("hyphenated names in basenames", has_hyphen,
                        f"basenames={basenames}")
        else:
            # All domains taken - just pass since we can't verify
            runner.test("(skipped) hyphenated names check", True, "no available domains")

    # Test alsoIncludeHyphens=False (default) does NOT include hyphens
    result = run_sync(check_everything(
        components=["abc", "xyz"],
        tlds=["com"],
        platforms=["instagram"],
        alsoIncludeHyphens=False
    ))
    data = runner.test_json("alsoIncludeHyphens=False excludes hyphenated names", result, {
        "has structure": lambda d: "domainSuccessfulBasenames" in d,
    })

    if data:
        basenames = data.get("domainSuccessfulBasenames", [])
        no_hyphens = not any("-" in b for b in basenames)
        runner.test("no hyphenated basenames when alsoIncludeHyphens=False", no_hyphens,
                    f"basenames={basenames}")


def main():
    runner = TestRunner()

    print("\n" + "=" * 60)
    print("  INTERNET NAMES MCP SERVER - TEST SUITE")
    print(f"  Version: {VERSION}")
    print("=" * 60)

    start_time = time.time()

    run_offline_tests(runner)
    run_online_tests(runner)

    elapsed = time.time() - start_time

    all_passed = runner.summary()

    print(f"\nCompleted in {elapsed:.1f} seconds")

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
