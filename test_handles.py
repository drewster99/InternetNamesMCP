#!/usr/bin/env python3
"""
Test script for social media handle/username availability checking.
Tests usernames across multiple platforms and reports timing and results.
"""

import os
import sys
import time
import subprocess
from dataclasses import dataclass

# Add parent dir to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from check_subreddits import check_subreddits
from check_x import check_x_usernames

# =============================================================================
# CONFIGURE YOUR TEST USERNAMES HERE
# =============================================================================

USERNAMES = [
    "drewben99",
    "coolstartup123xyz",
    "nike",  # Known taken on most platforms
]

# Platforms to check via Sherlock
SHERLOCK_PLATFORMS = [
    "Instagram",
    "Reddit",
    "YouTube",
    "TikTok",
    "Twitch",
    "threads",
]

# Expected results for validation
# Format: {"platform:username": True (available) or False (taken)}
EXPECTED_RESULTS = {
    # nike should be taken on major platforms
    "Instagram:nike": False,
    "Reddit:nike": False,
    "YouTube:nike": False,
    "TikTok:nike": False,
    "X:nike": False,
    # drewben99 expected available (based on earlier test)
    "Instagram:drewben99": True,
    "Reddit:drewben99": True,
    "YouTube:drewben99": True,
    "TikTok:drewben99": True,
    "Twitch:drewben99": True,
    "threads:drewben99": True,
    "X:drewben99": True,
    "subreddit:drewben99": True,
}

# =============================================================================


@dataclass
class HandleResult:
    platform: str
    username: str
    available: bool | None
    time_taken: float
    error: str | None = None


def check_sherlock(username: str, platforms: list[str], timeout: int = 60) -> list[HandleResult]:
    """Check username via Sherlock and return results with timing."""
    results = []

    cmd = [
        "sherlock", username,
        "--print-all", "--no-txt", "--timeout", "15"
    ]
    for p in platforms:
        cmd.extend(["--site", p])

    start_time = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        output = proc.stdout + proc.stderr
    except subprocess.TimeoutExpired:
        end_time = time.time()
        for p in platforms:
            results.append(HandleResult(
                platform=p, username=username, available=None,
                time_taken=end_time - start_time, error="Timeout"
            ))
        return results
    except FileNotFoundError:
        for p in platforms:
            results.append(HandleResult(
                platform=p, username=username, available=None,
                time_taken=0, error="sherlock not found"
            ))
        return results

    end_time = time.time()
    total_time = end_time - start_time
    per_platform_time = total_time / len(platforms) if platforms else 0

    # Parse output
    found_platforms = set()
    for line in output.split("\n"):
        line = line.strip()
        if not line or line.startswith("[*]"):
            continue

        if line.startswith("[+]"):
            # Found - taken
            parts = line[4:].split(": ", 1)
            if len(parts) >= 1:
                platform = parts[0]
                found_platforms.add(platform)
                results.append(HandleResult(
                    platform=platform, username=username, available=False,
                    time_taken=per_platform_time
                ))

        elif line.startswith("[-]"):
            parts = line[4:].split(": ", 1)
            if len(parts) == 2:
                platform, status = parts
                found_platforms.add(platform)

                if status == "Not Found!":
                    results.append(HandleResult(
                        platform=platform, username=username, available=True,
                        time_taken=per_platform_time
                    ))
                elif "Error" in status or "Illegal" in status:
                    results.append(HandleResult(
                        platform=platform, username=username, available=None,
                        time_taken=per_platform_time, error=status
                    ))
                else:
                    results.append(HandleResult(
                        platform=platform, username=username, available=True,
                        time_taken=per_platform_time
                    ))

    # Fill missing platforms
    for p in platforms:
        if p not in found_platforms:
            results.append(HandleResult(
                platform=p, username=username, available=None,
                time_taken=per_platform_time, error="No response"
            ))

    return results


def run_tests():
    """Run social media handle tests with timing."""
    print("=" * 70)
    print("SOCIAL MEDIA HANDLE AVAILABILITY TEST")
    print("=" * 70)
    print(f"Testing usernames: {', '.join(USERNAMES)}")
    print(f"Platforms: {', '.join(SHERLOCK_PLATFORMS)} + X/Twitter + Subreddits")
    print("=" * 70)
    print()

    all_results = []
    total_start = time.time()

    # Test each username
    for username in USERNAMES:
        print(f"\n--- Testing: {username} ---\n")

        # Sherlock platforms
        print("Checking Sherlock platforms...")
        sherlock_results = check_sherlock(username, SHERLOCK_PLATFORMS)
        all_results.extend(sherlock_results)

        # X/Twitter
        print("Checking X/Twitter...")
        x_start = time.time()
        x_results = check_x_usernames([username])
        x_time = time.time() - x_start

        for r in x_results:
            all_results.append(HandleResult(
                platform="X",
                username=username,
                available=r.available,
                time_taken=x_time,
                error=r.error
            ))

        # Subreddit
        print("Checking subreddit...")
        sub_start = time.time()
        sub_results = check_subreddits([username], delay=0)
        sub_time = time.time() - sub_start

        for r in sub_results:
            all_results.append(HandleResult(
                platform="subreddit",
                username=username,
                available=r.available,
                time_taken=sub_time,
                error=r.error
            ))

    total_time = time.time() - total_start

    # Display results table
    print()
    print("=" * 70)
    print("RESULTS")
    print("=" * 70)
    print(f"{'Platform':<15} {'Username':<20} {'Status':<12} {'Time':<8} {'Valid':<8}")
    print("-" * 70)

    validation_passed = 0
    validation_failed = 0
    available_count = 0
    taken_count = 0
    error_count = 0

    for result in all_results:
        # Status
        if result.error:
            status = "ERROR"
            error_count += 1
            symbol = "!"
        elif result.available:
            status = "AVAILABLE"
            available_count += 1
            symbol = "+"
        elif result.available is False:
            status = "TAKEN"
            taken_count += 1
            symbol = "-"
        else:
            status = "UNKNOWN"
            error_count += 1
            symbol = "?"

        # Validation
        key = f"{result.platform}:{result.username}"
        if key in EXPECTED_RESULTS and result.available is not None:
            if result.available == EXPECTED_RESULTS[key]:
                validation = "✓ PASS"
                validation_passed += 1
            else:
                validation = "✗ FAIL"
                validation_failed += 1
        else:
            validation = "-"

        time_str = f"{result.time_taken:.2f}s"

        print(f"[{symbol}] {result.platform:<12} {result.username:<20} {status:<12} {time_str:<8} {validation:<8}")

    # Summary
    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Total checks: {len(all_results)}")
    print(f"  Available: {available_count}")
    print(f"  Taken: {taken_count}")
    print(f"  Errors: {error_count}")
    print()
    print(f"Total time: {total_time:.2f} seconds")
    print(f"Average per check: {total_time / len(all_results):.2f} seconds")
    print()

    if validation_passed > 0 or validation_failed > 0:
        print(f"Validation results:")
        print(f"  Passed: {validation_passed}")
        print(f"  Failed: {validation_failed}")
        if validation_failed > 0:
            print("  (Failed validations may indicate the handle was registered/released)")

    # Timing breakdown by platform
    print()
    print("Timing by platform:")
    platform_times = {}
    for r in all_results:
        if r.platform not in platform_times:
            platform_times[r.platform] = []
        platform_times[r.platform].append(r.time_taken)

    for platform, times in sorted(platform_times.items()):
        avg_time = sum(times) / len(times)
        print(f"  {platform}: {avg_time:.2f}s avg ({len(times)} checks)")

    print("=" * 70)

    return {
        "total": len(all_results),
        "available": available_count,
        "taken": taken_count,
        "errors": error_count,
        "time": total_time,
        "validation_passed": validation_passed,
        "validation_failed": validation_failed,
    }


if __name__ == "__main__":
    run_tests()
