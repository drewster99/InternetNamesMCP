#!/usr/bin/env python3
"""
CLI tool to check subreddit name availability using Reddit's JSON API.

Usage:
    python check_subreddits.py coolstartup myapp brandname
    python check_subreddits.py gaming --json
"""

import argparse
import sys
import time
from dataclasses import dataclass

import httpx


@dataclass
class SubredditResult:
    name: str
    available: bool
    error: str | None = None
    subscribers: int | None = None
    description: str | None = None


def check_subreddit(name: str, client: httpx.Client) -> SubredditResult:
    """
    Check if a subreddit name is available.

    Args:
        name: Subreddit name (without r/ prefix)
        client: httpx Client instance

    Returns:
        SubredditResult with availability info
    """
    # Clean the name - remove r/ prefix if present
    name = name.lower().strip()
    if name.startswith("r/"):
        name = name[2:]

    url = f"https://www.reddit.com/r/{name}/about.json"

    try:
        response = client.get(url, follow_redirects=True)

        if response.status_code == 404:
            return SubredditResult(name=name, available=True)

        if response.status_code == 403:
            # Private subreddit - exists but not accessible
            return SubredditResult(name=name, available=False, error="Private subreddit")

        if response.status_code == 200:
            try:
                data = response.json()
                sub_data = data.get("data", {})

                # Check if it's a valid subreddit response
                if sub_data.get("display_name"):
                    return SubredditResult(
                        name=name,
                        available=False,
                        subscribers=sub_data.get("subscribers"),
                        description=sub_data.get("public_description", "")[:100]
                    )
                else:
                    # Empty response might mean available
                    return SubredditResult(name=name, available=True)

            except ValueError:
                return SubredditResult(name=name, available=False, error="Invalid JSON response")

        return SubredditResult(name=name, available=False, error=f"HTTP {response.status_code}")

    except httpx.TimeoutException:
        return SubredditResult(name=name, available=None, error="Timeout")
    except httpx.HTTPError as e:
        return SubredditResult(name=name, available=None, error=str(e))


def check_subreddits(names: list[str], delay: float = 1.0) -> list[SubredditResult]:
    """
    Check multiple subreddit names with rate limiting.

    Args:
        names: List of subreddit names to check
        delay: Delay between requests in seconds (Reddit rate limits)

    Returns:
        List of SubredditResult objects
    """
    results = []

    headers = {
        "User-Agent": "SubredditChecker/1.0 (checking availability)"
    }

    with httpx.Client(headers=headers, timeout=10) as client:
        for i, name in enumerate(names):
            result = check_subreddit(name, client)
            results.append(result)

            # Rate limiting - don't hammer Reddit
            if i < len(names) - 1:
                time.sleep(delay)

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Check subreddit name availability",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    %(prog)s coolstartup
    %(prog)s gaming programming python
    %(prog)s mybrand --json

Note:
    Reddit rate limits to ~10 requests/minute for unauthenticated users.
    A 1-second delay is added between requests by default.
        """
    )
    parser.add_argument(
        "names",
        nargs="+",
        help="Subreddit names to check (without r/ prefix)"
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Delay between requests in seconds (default: 1.0)"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON"
    )

    args = parser.parse_args()

    results = check_subreddits(args.names, args.delay)

    if args.json:
        import json
        output = [
            {
                "name": r.name,
                "available": r.available,
                "error": r.error,
                "subscribers": r.subscribers,
                "description": r.description
            }
            for r in results
        ]
        print(json.dumps(output, indent=2))
    else:
        print(f"\nChecking subreddits:\n")

        for result in results:
            if result.error:
                symbol = "!"
                status = f"ERROR: {result.error}"
            elif result.available:
                symbol = "+"
                status = "AVAILABLE"
            elif result.available is False:
                subs = f" ({result.subscribers:,} subscribers)" if result.subscribers else ""
                status = f"TAKEN{subs}"
            else:
                symbol = "?"
                status = "UNKNOWN"

            print(f"[{symbol}] r/{result.name}: {status}")


if __name__ == "__main__":
    main()
