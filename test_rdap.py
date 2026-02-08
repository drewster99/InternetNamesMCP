#!/usr/bin/env python3
"""
Test RDAP domain availability checking with a batch of domains.

Usage:
    source ./devsetup.sh
    python test_rdap.py
"""

import sys

try:
    from internet_names_mcp.server import _check_domains_rdap
    from internet_names_mcp.rdap_bootstrap import get_supported_tlds, refresh_bootstrap
except ImportError as e:
    print(f"Error: {e}")
    print()
    print("Set up the development environment first:")
    print("    source ./devsetup.sh")
    sys.exit(1)

DEFAULT_DOMAINS = [
    # Known taken - major companies
    "google.com",
    "facebook.com",
    "amazon.com",
    "apple.com",
    "microsoft.com",
    "github.com",
    # Known taken - other supported TLDs
    "google.net",
    "google.org",
    "google.dev",
    "stripe.com",
    # Expanded TLD coverage (supported via IANA bootstrap)
    "google.xyz",
    "google.tech",
    "google.biz",
    "google.info",
    # Likely available - random strings
    "xyznotreal98765.com",
    "qwerty123456abc.com",
    "asdfghjkl99999.net",
    "zxcvbnm87654.org",
    "randomtest11111.dev",
    "randomtest22222.xyz",
    # Edge cases - short words
    "example.com",
    "test.com",
    "mail.com",
    "app.com",
    "dev.com",
]

if __name__ == "__main__":
    # Ensure bootstrap is loaded
    refresh_bootstrap()
    supported = get_supported_tlds()
    print(f"RDAP bootstrap loaded: {len(supported)} TLDs supported")
    print()

    domains = sys.argv[1:] or DEFAULT_DOMAINS

    print(f"Checking {len(domains)} domains via RDAP...\n")

    available_count = 0
    taken_count = 0
    error_count = 0

    results = _check_domains_rdap(domains)

    for r in results:
        if r.error:
            status = f"❌ error ({r.error})"
            error_count += 1
        elif r.available:
            status = "available"
            available_count += 1
        else:
            status = "taken"
            taken_count += 1
        print(f"{r.domain}: {status}")

    print(f"\n--- Summary ---")
    print(f"Available: {available_count}")
    print(f"Taken:     {taken_count}")
    print(f"Errors:    {error_count}")
    print(f"Total:     {len(domains)}")
