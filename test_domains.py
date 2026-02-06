#!/usr/bin/env python3
"""
Test script for domain name availability checking.
Tests a set of domain names and reports timing and results.
"""

import os
import sys
import time

# Add parent dir to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from check_domains import check_domains

# =============================================================================
# CONFIGURE YOUR TEST DOMAINS HERE
# =============================================================================

# Base names to check
BASE_NAMES = [
    "drewbenson",
    "drewben",
    "drewster",
]

# TLDs to check for each base name
TLDS = ["com", "net", "io", "ai", "org"]

# Expected results (for validation) - set to None if unknown
# Format: {"domain.tld": True (available) or False (taken)}
EXPECTED_RESULTS = {
    "drewbenson.com": False,  # Known taken
    "drewster.com": False,    # Known taken
    "drewster.net": False,    # Known taken
    "drewster.org": False,    # Known taken
    # Add more known results here for validation
}

# =============================================================================


def build_domain_list(base_names: list[str], tlds: list[str]) -> list[str]:
    """Build list of full domain names from base names and TLDs."""
    domains = []
    for name in base_names:
        for tld in tlds:
            domains.append(f"{name}.{tld}")
    return domains


def run_tests():
    """Run domain availability tests with timing."""
    api_key = os.environ.get("NAMESILO_API_KEY")
    if not api_key:
        # Try to read from apikey.txt
        key_file = os.path.join(os.path.dirname(__file__), "apikey.txt")
        if os.path.exists(key_file):
            with open(key_file) as f:
                api_key = f.read().strip()

    if not api_key:
        print("Error: NAMESILO_API_KEY not set and apikey.txt not found")
        sys.exit(1)

    domains = build_domain_list(BASE_NAMES, TLDS)

    print("=" * 60)
    print("DOMAIN AVAILABILITY TEST")
    print("=" * 60)
    print(f"Testing {len(domains)} domains...")
    print(f"Base names: {', '.join(BASE_NAMES)}")
    print(f"TLDs: {', '.join(TLDS)}")
    print("=" * 60)
    print()

    # Run the test with timing
    start_time = time.time()
    results = check_domains(domains, api_key)
    end_time = time.time()

    total_time = end_time - start_time

    # Process and display results
    available = []
    taken = []
    errors = []
    validation_passed = 0
    validation_failed = 0

    print(f"{'Domain':<25} {'Status':<12} {'Price':<10} {'Validation':<12}")
    print("-" * 60)

    for result in results:
        # Determine status
        if result.error:
            status = f"ERROR"
            errors.append(result)
            price_str = "-"
            validation = "-"
        elif result.available:
            status = "AVAILABLE"
            available.append(result)
            price_str = f"${result.price:.2f}" if result.price else "-"
        else:
            status = "TAKEN"
            taken.append(result)
            price_str = "-"

        # Validate against expected results
        if result.domain in EXPECTED_RESULTS and not result.error:
            expected = EXPECTED_RESULTS[result.domain]
            if result.available == expected:
                validation = "✓ PASS"
                validation_passed += 1
            else:
                validation = "✗ FAIL"
                validation_failed += 1
        else:
            validation = "-"

        # Color coding
        if result.error:
            symbol = "!"
        elif result.available:
            symbol = "+"
        else:
            symbol = "-"

        print(f"[{symbol}] {result.domain:<22} {status:<12} {price_str:<10} {validation:<12}")

    # Summary
    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Total domains checked: {len(domains)}")
    print(f"  Available: {len(available)}")
    print(f"  Taken: {len(taken)}")
    print(f"  Errors: {len(errors)}")
    print()
    print(f"Total time: {total_time:.2f} seconds")
    print(f"Average per domain: {total_time / len(domains):.2f} seconds")
    print()

    if validation_passed > 0 or validation_failed > 0:
        print(f"Validation results:")
        print(f"  Passed: {validation_passed}")
        print(f"  Failed: {validation_failed}")

    # Show cheapest available
    if available:
        print()
        print("Cheapest available domains:")
        sorted_available = sorted(
            [r for r in available if r.price],
            key=lambda x: x.price
        )
        for r in sorted_available[:5]:
            print(f"  {r.domain}: ${r.price:.2f}")

    print("=" * 60)

    return {
        "total": len(domains),
        "available": len(available),
        "taken": len(taken),
        "errors": len(errors),
        "time": total_time,
        "validation_passed": validation_passed,
        "validation_failed": validation_failed,
    }


if __name__ == "__main__":
    run_tests()
