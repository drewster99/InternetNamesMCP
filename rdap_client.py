#!/usr/bin/env python3
"""
Async RDAP Client with Per-Host Rate Limiting

Provides parallel RDAP queries with proper rate limiting per registry host,
exponential backoff, and correct error categorization.
"""

import asyncio
import random
import time
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from enum import Enum
from urllib.parse import urlparse

import httpx

from rdap_bootstrap import get_rdap_server


class DomainStatus(Enum):
    """Status categories for domain availability checks."""

    AVAILABLE = "available"  # 404 - can register
    UNAVAILABLE = "unavailable"  # 200 - already taken
    ERROR = "error"  # timeout, rate_limit, network - RETRY LATER
    UNSUPPORTED = "unsupported"  # TLD not in bootstrap


@dataclass
class DomainResult:
    """Result of a domain availability check with proper error categorization."""

    domain: str
    status: DomainStatus
    price: float | None = None
    error_type: str | None = None  # "timeout", "rate_limit", "network", "tld_unsupported"
    error_message: str | None = None
    retry_after: float | None = None

    @property
    def available(self) -> bool:
        """Backward compatibility: True only if confirmed available."""
        return self.status == DomainStatus.AVAILABLE

    @property
    def error(self) -> str | None:
        """Backward compatibility: Returns error message if any."""
        return self.error_message


@dataclass
class HostRateLimiter:
    """Per-host rate limiter with concurrency control and backoff."""

    host: str
    max_concurrent: int = 2
    min_delay: float = 0.5

    _semaphore: asyncio.Semaphore = field(init=False, repr=False)
    _last_request_time: float = field(default=0.0, init=False)
    _retry_after_until: float = field(default=0.0, init=False)
    _consecutive_rate_limits: int = field(default=0, init=False)
    _lock: asyncio.Lock = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._semaphore = asyncio.Semaphore(self.max_concurrent)
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Acquire permission to make a request to this host."""
        await self._semaphore.acquire()

        async with self._lock:
            now = time.monotonic()

            # Honor retry_after from previous 429 response
            if now < self._retry_after_until:
                wait_time = self._retry_after_until - now
                await asyncio.sleep(wait_time)
                now = time.monotonic()

            # Enforce minimum delay between requests
            elapsed = now - self._last_request_time
            if elapsed < self.min_delay:
                await asyncio.sleep(self.min_delay - elapsed)

            self._last_request_time = time.monotonic()

    def release(
        self, rate_limited: bool = False, retry_after: float | None = None
    ) -> None:
        """Release the semaphore and update backoff state."""
        if rate_limited:
            self._consecutive_rate_limits += 1
            if retry_after is not None:
                # Use server-provided Retry-After
                self._retry_after_until = time.monotonic() + retry_after
            else:
                # Exponential backoff with jitter: 2^n seconds, max 32s
                backoff = min(2**self._consecutive_rate_limits, 32)
                jitter = backoff * 0.25 * (random.random() * 2 - 1)  # +/- 25%
                self._retry_after_until = time.monotonic() + backoff + jitter
        else:
            # Successful request resets consecutive rate limit counter
            self._consecutive_rate_limits = 0

        self._semaphore.release()


class RateLimiterRegistry:
    """Creates and manages HostRateLimiter instances by host."""

    def __init__(
        self, max_concurrent: int = 2, min_delay: float = 0.5
    ) -> None:
        self._limiters: dict[str, HostRateLimiter] = {}
        self._lock = asyncio.Lock()
        self._max_concurrent = max_concurrent
        self._min_delay = min_delay

    async def get_limiter(self, url: str) -> HostRateLimiter:
        """Get or create a rate limiter for the given URL's host."""
        parsed = urlparse(url)
        host = parsed.netloc.lower()

        async with self._lock:
            if host not in self._limiters:
                self._limiters[host] = HostRateLimiter(
                    host=host,
                    max_concurrent=self._max_concurrent,
                    min_delay=self._min_delay,
                )
            return self._limiters[host]


def _parse_retry_after(header: str | None) -> float | None:
    """
    Parse Retry-After header value.

    Supports:
    - Seconds: "120" -> 120.0
    - HTTP date: "Wed, 21 Oct 2015 07:28:00 GMT" -> seconds until that time
    """
    if not header:
        return None

    header = header.strip()

    # Try parsing as integer (seconds)
    try:
        return float(header)
    except ValueError:
        pass

    # Try parsing as HTTP date
    try:
        dt = parsedate_to_datetime(header)
        delta = dt.timestamp() - time.time()
        return max(0.0, delta)
    except (ValueError, TypeError):
        pass

    return None


class AsyncRDAPClient:
    """
    Async RDAP client with connection pooling and per-host rate limiting.

    Usage:
        async with AsyncRDAPClient() as client:
            results = await client.check_domains(["example.com", "test.net"])
    """

    def __init__(
        self,
        timeout: float = 10.0,
        max_retries: int = 3,
        max_concurrent_per_host: int = 2,
        min_delay_per_host: float = 0.5,
    ) -> None:
        self._timeout = timeout
        self._max_retries = max_retries
        self._client: httpx.AsyncClient | None = None
        self._registry = RateLimiterRegistry(
            max_concurrent=max_concurrent_per_host,
            min_delay=min_delay_per_host,
        )

    async def __aenter__(self) -> "AsyncRDAPClient":
        self._client = httpx.AsyncClient(
            timeout=self._timeout,
            headers={"Accept": "application/rdap+json"},
            follow_redirects=True,
            limits=httpx.Limits(
                max_connections=100,
                max_keepalive_connections=20,
            ),
        )
        return self

    async def __aexit__(self, *args) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _check_single(self, domain: str) -> DomainResult:
        """Check a single domain with retries and rate limiting."""
        if self._client is None:
            raise RuntimeError("Client not initialized. Use 'async with' context.")

        # Extract TLD
        tld = domain.rsplit(".", 1)[-1].lower() if "." in domain else ""

        # Get RDAP server from bootstrap
        rdap_server = get_rdap_server(tld)
        if not rdap_server:
            return DomainResult(
                domain=domain,
                status=DomainStatus.UNSUPPORTED,
                error_type="tld_unsupported",
                error_message=f"TLD .{tld} not in RDAP bootstrap",
            )

        # Ensure server URL ends with /
        if not rdap_server.endswith("/"):
            rdap_server += "/"

        url = f"{rdap_server}domain/{domain}"

        # Get rate limiter for this host
        limiter = await self._registry.get_limiter(rdap_server)

        last_error: Exception | None = None
        last_error_type: str | None = None
        last_retry_after: float | None = None

        for attempt in range(self._max_retries):
            await limiter.acquire()
            rate_limited = False
            retry_after: float | None = None

            try:
                response = await self._client.get(url)

                if response.status_code == 404:
                    limiter.release(rate_limited=False)
                    return DomainResult(domain=domain, status=DomainStatus.AVAILABLE)

                if response.status_code == 200:
                    limiter.release(rate_limited=False)
                    return DomainResult(domain=domain, status=DomainStatus.UNAVAILABLE)

                if response.status_code == 429:
                    retry_after = _parse_retry_after(
                        response.headers.get("Retry-After")
                    )
                    rate_limited = True
                    last_error_type = "rate_limit"
                    last_retry_after = retry_after
                    last_error = None
                    limiter.release(rate_limited=True, retry_after=retry_after)

                    # Continue to next retry attempt
                    if attempt < self._max_retries - 1:
                        # Wait before retry (limiter will also wait)
                        wait_time = retry_after if retry_after else 0.5 * (attempt + 1)
                        await asyncio.sleep(wait_time)
                    continue

                # Other status codes are errors
                limiter.release(rate_limited=False)
                return DomainResult(
                    domain=domain,
                    status=DomainStatus.ERROR,
                    error_type="http_error",
                    error_message=f"RDAP status {response.status_code}",
                )

            except httpx.TimeoutException:
                last_error_type = "timeout"
                last_error = None
                limiter.release(rate_limited=False)

                if attempt < self._max_retries - 1:
                    # Linear backoff for timeouts
                    await asyncio.sleep(0.5 * (attempt + 1))
                continue

            except httpx.HTTPError as e:
                last_error_type = "network"
                last_error = e
                limiter.release(rate_limited=False)

                if attempt < self._max_retries - 1:
                    # Linear backoff for network errors
                    await asyncio.sleep(0.5 * (attempt + 1))
                continue

        # All retries exhausted
        error_message = str(last_error)[:100] if last_error else None
        if last_error_type == "timeout":
            error_message = "Request timed out after retries"
        elif last_error_type == "rate_limit":
            error_message = "Rate limited, please retry later"

        return DomainResult(
            domain=domain,
            status=DomainStatus.ERROR,
            error_type=last_error_type,
            error_message=error_message,
            retry_after=last_retry_after,
        )

    async def check_domains(self, domains: list[str]) -> list[DomainResult]:
        """
        Check multiple domains in parallel with per-host rate limiting.

        Domains are grouped by their RDAP server host, and each host has its
        own concurrency limit and rate limiting.
        """
        if not domains:
            return []

        # Launch all checks concurrently - rate limiting is handled per-host
        tasks = [self._check_single(domain) for domain in domains]
        results = await asyncio.gather(*tasks)
        return list(results)

    async def check_domain(self, domain: str) -> DomainResult:
        """Check a single domain."""
        return await self._check_single(domain)


async def check_domains_async(
    domains: list[str],
    timeout: float = 10.0,
    max_retries: int = 3,
) -> list[DomainResult]:
    """
    Convenience function for checking domains without managing client lifecycle.

    Args:
        domains: List of domain names to check
        timeout: Request timeout in seconds
        max_retries: Maximum number of retries per domain

    Returns:
        List of DomainResult objects
    """
    async with AsyncRDAPClient(
        timeout=timeout, max_retries=max_retries
    ) as client:
        return await client.check_domains(domains)
