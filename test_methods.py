#!/usr/bin/env python3
"""
Compare domain availability results between RDAP and NameSilo API methods.

This test runs the same set of domains through both methods and flags any
discrepancies in availability results.

Usage:
    source ./devsetup.sh
    python test_methods.py
"""

import asyncio
import sys

try:
    from internet_names_mcp.server import check_domains
except ImportError as e:
    print(f"Error: {e}")
    print()
    print("Set up the development environment first:")
    print("    source ./devsetup.sh")
    sys.exit(1)

import json

# Known taken domains - major sites across various TLDs
KNOWN_TAKEN = [
    # .com - major companies
    "google.com",
    "amazon.com",
    "facebook.com",
    "apple.com",
    "microsoft.com",
    "netflix.com",
    "twitter.com",
    "linkedin.com",

    # .net
    "speedtest.net",
    "behance.net",
    "slideshare.net",

    # .org
    "wikipedia.org",
    "mozilla.org",
    "apache.org",
    "python.org",

    # .io
    "github.io",
    "socket.io",
    "atom.io",

    # .dev
    "web.dev",
    "flutter.dev",
    "opensource.dev",

    # .app
    "google.app",
    "cash.app",
    "clock.app",

    # .ai
    "stability.ai",
    "character.ai",
    "perplexity.ai",

    # .co
    "twitter.co",
    "google.co",
    "amazon.co",

    # Other TLDs
    "google.info",
    "amazon.biz",
    "google.xyz",
]

# Likely available domains - random strings unlikely to be registered
# Using mix of random alphanumeric to minimize chance of collision
LIKELY_AVAILABLE = [
    # .com
    "xkq7vm9p2wz4.com",
    "j3nf8hq2x5m1.com",

    # .net
    "p9wz4kqv7x2m.net",
    "y5hj8nf3qw1z.net",

    # .org
    "m2xv7kp9qw4z.org",
    "f8hn3jq5wz1x.org",

    # .io
    "q4zx7kp2wm9v.io",
    "n1wz5hj8qf3x.io",

    # .dev
    "k7xm2pv9qw4z.dev",
    "h3jf8nq5wz1x.dev",

    # .app
    "z9wv4kq7xm2p.app",
    "x5hj1nf8qw3z.app",

    # .ai
    "v2xk7pm9qw4z.ai",
    "j8hn3fq5wz1x.ai",

    # .co
    "w4zx9kp2vm7q.co",
    "f1hj5nq8wz3x.co",

    # .info
    "q7xm2pv4kw9z.info",
    "n3jf8hq5wz1x.info",

    # .biz
    "k9wz4xp7vm2q.biz",
    "h5jn1fq8wz3x.biz",

    # .xyz
    "z2xv7kp4qw9m.xyz",
    "f8hn3jq1wz5x.xyz",
]

ALL_DOMAINS = KNOWN_TAKEN + LIKELY_AVAILABLE


def parse_results(json_str: str) -> dict[str, str]:
    """
    Parse check_domains JSON result into a dict of domain -> status.
    Status is 'available', 'taken', or 'error:<message>'
    """
    data = json.loads(json_str)

    if "error" in data:
        return {"_error": data["error"]}

    result = {}

    for item in data.get("available", []):
        domain = item["domain"] if isinstance(item, dict) else item
        result[domain] = "available"

    for item in data.get("unavailable", []):
        domain = item["domain"] if isinstance(item, dict) else item
        result[domain] = "taken"

    for item in data.get("errors", []):
        domain = item.get("domain", "unknown")
        error = item.get("error", "unknown error")
        result[domain] = f"error:{error}"

    return result


def compare_results(rdap_results: dict, namesilo_results: dict, domains: list[str]) -> list[dict]:
    """Compare results from both methods and return list of discrepancies."""
    discrepancies = []

    for domain in domains:
        rdap_status = rdap_results.get(domain, "missing")
        namesilo_status = namesilo_results.get(domain, "missing")

        # Normalize error statuses for comparison (errors don't count as discrepancies)
        rdap_is_error = rdap_status.startswith("error:") or rdap_status == "missing"
        namesilo_is_error = namesilo_status.startswith("error:") or namesilo_status == "missing"

        # Only flag as discrepancy if both have definitive results that differ
        if not rdap_is_error and not namesilo_is_error:
            if rdap_status != namesilo_status:
                discrepancies.append({
                    "domain": domain,
                    "rdap": rdap_status,
                    "namesilo": namesilo_status,
                })

    return discrepancies


async def main():
    import logging
    logging.getLogger("httpx").setLevel(logging.WARNING)

    domains = ALL_DOMAINS

    print(f"Comparing RDAP vs NameSilo for {len(domains)} domains")
    print(f"  - Known taken: {len(KNOWN_TAKEN)}")
    print(f"  - Likely available: {len(LIKELY_AVAILABLE)}")
    print()

    # Run RDAP check
    print("Running RDAP check...")
    rdap_json = await check_domains(domains, tlds=[], method="rdap")
    rdap_results = parse_results(rdap_json)

    if "_error" in rdap_results:
        print(f"RDAP fatal error: {rdap_results['_error']}")
        return 1

    # Run NameSilo check
    print("Running NameSilo check...")
    namesilo_json = await check_domains(domains, tlds=[], method="namesilo")
    namesilo_results = parse_results(namesilo_json)

    if "_error" in namesilo_results:
        print(f"NameSilo fatal error: {namesilo_results['_error']}")
        return 1

    print()

    # Collect and display errors first
    rdap_errors = []
    namesilo_errors = []
    for domain in domains:
        rdap_status = rdap_results.get(domain, "missing")
        namesilo_status = namesilo_results.get(domain, "missing")
        if rdap_status.startswith("error:"):
            rdap_errors.append((domain, rdap_status[6:]))
        if namesilo_status.startswith("error:"):
            namesilo_errors.append((domain, namesilo_status[6:]))

    if rdap_errors or namesilo_errors:
        print("=" * 70)
        print("ERRORS")
        print("=" * 70)
        if rdap_errors:
            print(f"\nRDAP errors ({len(rdap_errors)}):")
            for domain, error in rdap_errors:
                print(f"  {domain}: {error}")
        if namesilo_errors:
            print(f"\nNameSilo errors ({len(namesilo_errors)}):")
            for domain, error in namesilo_errors:
                print(f"  {domain}: {error}")
        print()

    # Compare results and show discrepancies
    discrepancies = compare_results(rdap_results, namesilo_results, domains)

    if discrepancies:
        print("=" * 70)
        print(f"DISCREPANCIES ({len(discrepancies)})")
        print("=" * 70)
        for d in discrepancies:
            print(f"  ❌ {d['domain']}: RDAP={d['rdap']}, NameSilo={d['namesilo']}")
        print()

    # Results table
    print("=" * 70)
    print("RESULTS")
    print("=" * 70)
    print(f"{'Domain':<35} {'RDAP':<15} {'NameSilo':<15}")
    print("-" * 70)

    for domain in domains:
        rdap_status = rdap_results.get(domain, "missing")
        namesilo_status = namesilo_results.get(domain, "missing")

        # Show error code in table (full error already logged above)
        if rdap_status.startswith("error:"):
            rdap_display = "ERROR"
        else:
            rdap_display = rdap_status

        if namesilo_status.startswith("error:"):
            namesilo_display = "ERROR"
        else:
            namesilo_display = namesilo_status

        # Mark discrepancies
        marker = ""
        if rdap_display != "ERROR" and namesilo_display != "ERROR":
            if rdap_display != namesilo_display:
                marker = " ❌ MISMATCH"

        print(f"{domain:<35} {rdap_display:<15} {namesilo_display:<15}{marker}")

    # Summary
    rdap_avail = sum(1 for s in rdap_results.values() if s == "available")
    rdap_taken = sum(1 for s in rdap_results.values() if s == "taken")
    namesilo_avail = sum(1 for s in namesilo_results.values() if s == "available")
    namesilo_taken = sum(1 for s in namesilo_results.values() if s == "taken")

    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Total domains: {len(domains)}")
    print(f"RDAP:     {rdap_avail} available, {rdap_taken} taken, {len(rdap_errors)} errors")
    print(f"NameSilo: {namesilo_avail} available, {namesilo_taken} taken, {len(namesilo_errors)} errors")
    print(f"Discrepancies: {len(discrepancies)}")

    return 0 if not discrepancies else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
