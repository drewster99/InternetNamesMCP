#!/usr/bin/env python3
"""
CLI tool to check domain name availability using NameSilo API.

Usage:
    python check_domains.py example.com example.net
    python check_domains.py coolstartup --tlds com,io,ai,co,app

Environment:
    NAMESILO_API_KEY - Your NameSilo API key (get from namesilo.com account)
"""

import argparse
import os
import sys
from dataclasses import dataclass

import httpx


NAMESILO_API_URL = "https://www.namesilo.com/api/checkRegisterAvailability"

# Sandbox/test API URL (for testing without real transactions)
# NAMESILO_API_URL = "https://sandbox.namesilo.com/api/checkRegisterAvailability"

DEFAULT_TLDS = ["com", "io", "ai", "co", "app", "dev", "net", "org"]


@dataclass
class DomainResult:
    domain: str
    available: bool
    price: float | None = None
    error: str | None = None


def check_domains(domains: list[str], api_key: str) -> list[DomainResult]:
    """
    Check availability of domains using NameSilo API.

    Args:
        domains: List of full domain names (e.g., ["example.com", "test.io"])
        api_key: NameSilo API key

    Returns:
        List of DomainResult objects
    """
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

    # Check for API errors
    code = reply.get("code")
    if code and int(code) != 300:
        detail = reply.get("detail", "Unknown error")
        return [DomainResult(domain=d, available=False, error=f"API Error {code}: {detail}") for d in domains]

    results = []

    # Process available domains
    available = reply.get("available", {})
    if isinstance(available, dict) and "domain" in available:
        # Single domain response
        available = [available]
    elif isinstance(available, list):
        pass
    else:
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
    elif isinstance(unavailable, list):
        pass
    else:
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
    elif isinstance(invalid, list):
        pass
    else:
        invalid = []

    for domain in invalid:
        if isinstance(domain, str):
            results.append(DomainResult(domain=domain, available=False, error="Invalid domain name"))
        elif isinstance(domain, dict):
            results.append(DomainResult(domain=domain.get("domain", ""), available=False, error="Invalid domain name"))

    return results


def expand_tlds(name: str, tlds: list[str]) -> list[str]:
    """Expand a base name with multiple TLDs."""
    # Remove any existing TLD if present
    if "." in name:
        return [name]  # Already a full domain
    return [f"{name}.{tld}" for tld in tlds]


def main():
    parser = argparse.ArgumentParser(
        description="Check domain name availability using NameSilo API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    %(prog)s example.com example.net
    %(prog)s coolstartup --tlds com,io,ai
    %(prog)s myapp --tlds com,io,ai,co,app,dev

Environment:
    NAMESILO_API_KEY    Your NameSilo API key
        """
    )
    parser.add_argument(
        "names",
        nargs="+",
        help="Domain names or base names to check"
    )
    parser.add_argument(
        "--tlds",
        type=str,
        default=None,
        help=f"Comma-separated list of TLDs to check (default: {','.join(DEFAULT_TLDS)})"
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=None,
        help="NameSilo API key (or set NAMESILO_API_KEY env var)"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON"
    )

    args = parser.parse_args()

    # Get API key
    api_key = args.api_key or os.environ.get("NAMESILO_API_KEY")
    if not api_key:
        print("Error: No API key provided.", file=sys.stderr)
        print("Set NAMESILO_API_KEY environment variable or use --api-key", file=sys.stderr)
        print("\nGet your free API key at: https://www.namesilo.com/account/api-manager", file=sys.stderr)
        sys.exit(1)

    # Parse TLDs
    tlds = args.tlds.split(",") if args.tlds else DEFAULT_TLDS

    # Expand names with TLDs
    domains = []
    for name in args.names:
        domains.extend(expand_tlds(name, tlds))

    # Remove duplicates while preserving order
    seen = set()
    unique_domains = []
    for d in domains:
        if d not in seen:
            seen.add(d)
            unique_domains.append(d)
    domains = unique_domains

    # Check domains
    results = check_domains(domains, api_key)

    # Output results
    if args.json:
        import json
        output = [
            {
                "domain": r.domain,
                "available": r.available,
                "price": r.price,
                "error": r.error
            }
            for r in results
        ]
        print(json.dumps(output, indent=2))
    else:
        # Pretty print
        for result in results:
            if result.error:
                status = f"ERROR: {result.error}"
                symbol = "!"
            elif result.available:
                price_str = f" (${result.price:.2f})" if result.price else ""
                status = f"AVAILABLE{price_str}"
                symbol = "+"
            else:
                status = "TAKEN"
                symbol = "-"

            print(f"[{symbol}] {result.domain}: {status}")


if __name__ == "__main__":
    main()
