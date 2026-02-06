#!/usr/bin/env python3
"""
CLI tool to check X/Twitter username availability using Playwright.

Usage:
    python check_x.py coolstartup
    python check_x.py user1 user2 user3 --json

Requires: playwright install chromium
"""

import argparse
import sys
from dataclasses import dataclass

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("Error: playwright not installed. Run: pip install playwright && playwright install chromium", file=sys.stderr)
    sys.exit(1)


@dataclass
class XResult:
    username: str
    available: bool | None
    error: str | None = None
    note: str | None = None


def check_x_username(username: str, page) -> XResult:
    """Check if X/Twitter username is available."""
    url = f"https://x.com/{username}"

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)  # Wait for JS to render

        # Check for "This account doesn't exist" using the data-testid
        empty_state = page.query_selector('[data-testid="empty_state_header_text"]')

        if empty_state:
            text = empty_state.inner_text()
            # Handle both straight (') and curly (') apostrophes
            text_normalized = text.replace("'", "'").lower()
            if "doesn't exist" in text_normalized or "account doesn" in text_normalized:
                return XResult(username=username, available=True)
            elif "suspended" in text_normalized:
                return XResult(username=username, available=False, note="Account suspended")

        # Check for user profile indicators
        user_name = page.query_selector('[data-testid="UserName"]')
        if user_name:
            return XResult(username=username, available=False)

        # Check body text as fallback
        body_text = page.inner_text("body").replace("'", "'")
        if "This account doesn't exist" in body_text:
            return XResult(username=username, available=True)

        # If we see profile-like content, assume taken
        if f"@{username}" in body_text.lower():
            return XResult(username=username, available=False)

        return XResult(username=username, available=None, error="Could not determine")

    except Exception as e:
        return XResult(username=username, available=None, error=str(e)[:100])


def check_x_usernames(usernames: list[str]) -> list[XResult]:
    """Check multiple X/Twitter usernames."""
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        for username in usernames:
            result = check_x_username(username, page)
            results.append(result)

        browser.close()

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Check X/Twitter username availability",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    %(prog)s coolstartup
    %(prog)s user1 user2 user3
    %(prog)s mybrand --json

Note:
    Uses headless browser to render X.com pages.
    Slower than API methods but works without authentication.
        """
    )
    parser.add_argument(
        "usernames",
        nargs="+",
        help="Usernames to check"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON"
    )

    args = parser.parse_args()

    results = check_x_usernames(args.usernames)

    if args.json:
        import json
        output = [
            {
                "username": r.username,
                "available": r.available,
                "error": r.error,
                "note": r.note
            }
            for r in results
        ]
        print(json.dumps(output, indent=2))
    else:
        print(f"\nChecking X/Twitter:\n")

        for result in results:
            if result.error:
                symbol = "!"
                status = f"ERROR: {result.error}"
            elif result.available:
                symbol = "+"
                status = "AVAILABLE"
            elif result.available is False:
                note = f" ({result.note})" if result.note else ""
                status = f"TAKEN{note}"
            else:
                symbol = "?"
                status = "UNKNOWN"

            print(f"[{symbol}] @{result.username}: {status}")


if __name__ == "__main__":
    main()
