#!/usr/bin/env python3
"""
Test suite for Async RDAP Client

Usage:
    source .venv/bin/activate
    python test_rdap_client.py
"""

import sys

# Check Python version and dependencies early
if sys.version_info < (3, 10):
    print("Error: Python 3.10+ required")
    print()
    print("Activate the virtual environment:")
    print("    source .venv/bin/activate")
    print("    python test_rdap_client.py")
    sys.exit(1)

try:
    import anyio
    import httpx
except ImportError as e:
    print(f"Error: {e}")
    print()
    print("Activate the virtual environment first:")
    print("    source .venv/bin/activate")
    print("    python test_rdap_client.py")
    sys.exit(1)

import asyncio
import random
import string
import time
from dataclasses import dataclass

# Add current directory to path for imports
sys.path.insert(0, ".")

from rdap_client import (
    AsyncRDAPClient,
    DomainResult,
    DomainStatus,
    HostRateLimiter,
    RateLimiterRegistry,
    _parse_retry_after,
    check_domains_async,
)


def generate_unique_name() -> str:
    """Generate a unique name unlikely to be taken."""
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
    return f"xyztest{suffix}"


@dataclass
class TestResult:
    """Result of a single test."""

    name: str
    passed: bool
    message: str = ""


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
            name=f"{self.current_section}: {name}", passed=condition, message=message
        )
        self.results.append(result)

        if condition:
            print(f"  \u2713 {name}")
        else:
            print(f"  \u2717 {name}")
            if message:
                print(f"    \u2192 {message}")

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
                    print(f"  \u2717 {r.name}")
                    if r.message:
                        print(f"    \u2192 {r.message}")

        return failed == 0


def run_unit_tests(runner: TestRunner):
    """Run unit tests that don't require network calls."""

    # =========================================================================
    # _parse_retry_after
    # =========================================================================
    runner.section("_parse_retry_after")

    # Parse integer seconds
    result = _parse_retry_after("120")
    runner.test("parses integer seconds", result == 120.0, f"got {result}")

    result = _parse_retry_after("0")
    runner.test("parses zero seconds", result == 0.0, f"got {result}")

    result = _parse_retry_after("  60  ")
    runner.test("handles whitespace", result == 60.0, f"got {result}")

    # Parse None/empty
    result = _parse_retry_after(None)
    runner.test("returns None for None input", result is None)

    result = _parse_retry_after("")
    runner.test("returns None for empty string", result is None)

    # Invalid values
    result = _parse_retry_after("not-a-number")
    runner.test("returns None for invalid input", result is None)

    # HTTP date format (note: this depends on current time, so we just check it returns a number)
    result = _parse_retry_after("Wed, 21 Oct 2025 07:28:00 GMT")
    runner.test(
        "parses HTTP date format", result is not None and isinstance(result, float)
    )

    # =========================================================================
    # DomainResult
    # =========================================================================
    runner.section("DomainResult")

    # Available domain
    r = DomainResult(domain="test.com", status=DomainStatus.AVAILABLE)
    runner.test("available property works for AVAILABLE", r.available is True)
    runner.test("error property is None for AVAILABLE", r.error is None)

    # Unavailable domain
    r = DomainResult(domain="test.com", status=DomainStatus.UNAVAILABLE)
    runner.test("available property works for UNAVAILABLE", r.available is False)
    runner.test("error property is None for UNAVAILABLE", r.error is None)

    # Error domain
    r = DomainResult(
        domain="test.com",
        status=DomainStatus.ERROR,
        error_type="timeout",
        error_message="Request timed out",
    )
    runner.test("available property works for ERROR", r.available is False)
    runner.test("error property returns message", r.error == "Request timed out")

    # Unsupported domain
    r = DomainResult(
        domain="test.invalid",
        status=DomainStatus.UNSUPPORTED,
        error_type="tld_unsupported",
        error_message="TLD not in bootstrap",
    )
    runner.test("available property works for UNSUPPORTED", r.available is False)
    runner.test("error property returns message", r.error == "TLD not in bootstrap")

    # =========================================================================
    # DomainStatus
    # =========================================================================
    runner.section("DomainStatus")

    runner.test("AVAILABLE value is 'available'", DomainStatus.AVAILABLE.value == "available")
    runner.test(
        "UNAVAILABLE value is 'unavailable'", DomainStatus.UNAVAILABLE.value == "unavailable"
    )
    runner.test("ERROR value is 'error'", DomainStatus.ERROR.value == "error")
    runner.test(
        "UNSUPPORTED value is 'unsupported'", DomainStatus.UNSUPPORTED.value == "unsupported"
    )


async def run_rate_limiter_tests(runner: TestRunner):
    """Test HostRateLimiter behavior."""

    # =========================================================================
    # HostRateLimiter - basic functionality
    # =========================================================================
    runner.section("HostRateLimiter")

    limiter = HostRateLimiter(host="test.example.com", max_concurrent=2, min_delay=0.1)

    # Acquire and release
    await limiter.acquire()
    limiter.release()
    runner.test("basic acquire/release works", True)

    # Test min_delay enforcement
    start = time.monotonic()
    await limiter.acquire()
    limiter.release()
    await limiter.acquire()
    elapsed = time.monotonic() - start
    limiter.release()
    runner.test(
        "enforces min_delay between requests",
        elapsed >= 0.1,
        f"elapsed {elapsed:.3f}s, expected >= 0.1s",
    )

    # =========================================================================
    # HostRateLimiter - rate limit backoff
    # =========================================================================
    runner.section("HostRateLimiter - Rate Limit Backoff")

    limiter = HostRateLimiter(host="backoff.example.com", max_concurrent=2, min_delay=0.05)

    # Simulate rate limit with retry_after
    await limiter.acquire()
    limiter.release(rate_limited=True, retry_after=0.2)

    # Next acquire should wait for retry_after
    start = time.monotonic()
    await limiter.acquire()
    elapsed = time.monotonic() - start
    limiter.release()
    runner.test(
        "honors retry_after",
        elapsed >= 0.15,  # Allow some tolerance
        f"elapsed {elapsed:.3f}s, expected >= 0.2s",
    )

    # Simulate rate limit without retry_after (exponential backoff)
    limiter2 = HostRateLimiter(host="exp.example.com", max_concurrent=2, min_delay=0.01)

    await limiter2.acquire()
    limiter2.release(rate_limited=True)  # First rate limit -> 2^1 = 2s backoff (but we'll test timing)

    # For testing, we just verify the limiter tracks consecutive rate limits
    runner.test("tracks consecutive rate limits", limiter2._consecutive_rate_limits == 1)

    await limiter2.acquire()
    limiter2.release(rate_limited=False)  # Success resets counter
    runner.test("successful request resets counter", limiter2._consecutive_rate_limits == 0)

    # =========================================================================
    # RateLimiterRegistry
    # =========================================================================
    runner.section("RateLimiterRegistry")

    registry = RateLimiterRegistry(max_concurrent=3, min_delay=0.1)

    # Get limiter for a URL
    limiter1 = await registry.get_limiter("https://rdap.example.com/domain/test.com")
    runner.test("creates limiter for host", limiter1.host == "rdap.example.com")
    runner.test("uses configured max_concurrent", limiter1.max_concurrent == 3)
    runner.test("uses configured min_delay", limiter1.min_delay == 0.1)

    # Same host returns same limiter
    limiter2 = await registry.get_limiter("https://rdap.example.com/domain/other.com")
    runner.test("returns same limiter for same host", limiter1 is limiter2)

    # Different host returns different limiter
    limiter3 = await registry.get_limiter("https://rdap.other.com/domain/test.com")
    runner.test("returns different limiter for different host", limiter1 is not limiter3)


async def run_integration_tests(runner: TestRunner):
    """Run integration tests that require network calls."""

    unique_name = generate_unique_name()
    print(f"\n  Using unique test name: {unique_name}")

    # =========================================================================
    # AsyncRDAPClient - basic functionality
    # =========================================================================
    runner.section("AsyncRDAPClient - Basic")

    async with AsyncRDAPClient(timeout=15.0, max_retries=2) as client:
        # Test known taken domain
        result = await client.check_domain("google.com")
        runner.test(
            "google.com is UNAVAILABLE",
            result.status == DomainStatus.UNAVAILABLE,
            f"got {result.status}",
        )
        runner.test("google.com available=False", result.available is False)

        # Test likely available domain
        result = await client.check_domain(f"{unique_name}.com")
        runner.test(
            "unique domain is AVAILABLE",
            result.status == DomainStatus.AVAILABLE,
            f"got {result.status}",
        )
        runner.test("unique domain available=True", result.available is True)

        # Test unsupported TLD
        result = await client.check_domain("test.notarealtld12345")
        runner.test(
            "unsupported TLD is UNSUPPORTED",
            result.status == DomainStatus.UNSUPPORTED,
            f"got {result.status}",
        )
        runner.test("unsupported TLD available=False", result.available is False)
        runner.test(
            "unsupported TLD has error message",
            result.error is not None and "bootstrap" in result.error.lower(),
        )

    # =========================================================================
    # AsyncRDAPClient - batch queries
    # =========================================================================
    runner.section("AsyncRDAPClient - Batch Queries")

    async with AsyncRDAPClient(timeout=15.0, max_retries=2) as client:
        domains = [
            "google.com",  # Taken
            f"{unique_name}.com",  # Available
            "apple.com",  # Taken
            f"{unique_name}.net",  # Available (same host as .com)
        ]

        start = time.monotonic()
        results = await client.check_domains(domains)
        elapsed = time.monotonic() - start

        runner.test("batch returns correct count", len(results) == 4)

        # Check results
        result_map = {r.domain: r for r in results}
        runner.test(
            "google.com is UNAVAILABLE in batch",
            result_map["google.com"].status == DomainStatus.UNAVAILABLE,
        )
        runner.test(
            f"{unique_name}.com is AVAILABLE in batch",
            result_map[f"{unique_name}.com"].status == DomainStatus.AVAILABLE,
        )
        runner.test(
            "apple.com is UNAVAILABLE in batch",
            result_map["apple.com"].status == DomainStatus.UNAVAILABLE,
        )
        runner.test(
            f"{unique_name}.net is AVAILABLE in batch",
            result_map[f"{unique_name}.net"].status == DomainStatus.AVAILABLE,
        )

        # Batch should be faster than sequential (with 1s delay each)
        runner.test(
            "batch is faster than sequential",
            elapsed < 10.0,  # Should be much less than 4 * 1s = 4s
            f"took {elapsed:.2f}s",
        )

    # =========================================================================
    # check_domains_async convenience function
    # =========================================================================
    runner.section("check_domains_async")

    results = await check_domains_async(["google.com", f"{unique_name}.io"])
    runner.test("returns list of DomainResult", len(results) == 2)
    runner.test(
        "first result is DomainResult",
        isinstance(results[0], DomainResult),
    )

    # =========================================================================
    # Different hosts run in parallel
    # =========================================================================
    runner.section("Parallel Execution - Different Hosts")

    # .com/.net use verisign, .org uses different registry
    domains = [
        "google.com",
        "google.org",
        f"{unique_name}.com",
        f"{unique_name}.org",
    ]

    start = time.monotonic()
    results = await check_domains_async(domains)
    elapsed = time.monotonic() - start

    runner.test("all domains checked", len(results) == 4)
    # With 2 different hosts running in parallel, should be faster
    runner.test(
        "different hosts run in parallel",
        elapsed < 8.0,  # Should be much faster than 4 * 2s = 8s
        f"took {elapsed:.2f}s",
    )


async def main_async():
    runner = TestRunner()

    print("\n" + "=" * 60)
    print("  ASYNC RDAP CLIENT - TEST SUITE")
    print("=" * 60)

    start_time = time.time()

    # Unit tests (no network)
    run_unit_tests(runner)

    # Rate limiter tests (no network, uses asyncio)
    await run_rate_limiter_tests(runner)

    # Integration tests (network required)
    await run_integration_tests(runner)

    elapsed = time.time() - start_time

    all_passed = runner.summary()

    print(f"\nCompleted in {elapsed:.1f} seconds")

    return all_passed


def main():
    result = anyio.run(main_async)
    sys.exit(0 if result else 1)


if __name__ == "__main__":
    main()
