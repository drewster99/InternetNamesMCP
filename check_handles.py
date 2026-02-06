#!/usr/bin/env python3
"""
CLI tool to check social media handle availability using Sherlock.

Usage:
    python check_handles.py coolstartup
    python check_handles.py coolstartup --platforms instagram,twitter,tiktok
"""

import argparse
import subprocess
import sys


DEFAULT_PLATFORMS = [
    "Instagram",
    "Twitter",
    "Reddit",
    "YouTube",
    "TikTok",
    "Twitch",
    "threads",
]


def check_handles(username: str, platforms: list[str], timeout: int = 15) -> dict:
    """
    Check handle availability across platforms using Sherlock.

    Returns dict mapping platform -> {"available": bool, "url": str|None, "error": str|None}
    """
    cmd = [
        "sherlock",
        username,
        "--print-all",
        "--no-txt",
        "--timeout", str(timeout),
    ]

    for platform in platforms:
        cmd.extend(["--site", platform])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout * len(platforms) + 30
        )
        output = result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return {p: {"available": None, "url": None, "error": "Timeout"} for p in platforms}
    except FileNotFoundError:
        print("Error: sherlock not found. Install with: pip install sherlock-project", file=sys.stderr)
        sys.exit(1)

    results = {}

    for line in output.split("\n"):
        line = line.strip()
        if not line or line.startswith("[*]"):
            continue

        # Parse Sherlock output lines like:
        # [+] Instagram: https://instagram.com/username
        # [-] Twitter: Not Found!
        # [-] TikTok: Error Connecting
        # [-] Twitter: Illegal Username Format For This Site!

        if line.startswith("[+]"):
            # Found - handle is TAKEN
            parts = line[4:].split(": ", 1)
            if len(parts) == 2:
                platform, url = parts
                results[platform] = {
                    "available": False,
                    "url": url,
                    "error": None
                }

        elif line.startswith("[-]"):
            # Not found or error
            parts = line[4:].split(": ", 1)
            if len(parts) == 2:
                platform, status = parts

                if status == "Not Found!":
                    results[platform] = {
                        "available": True,
                        "url": None,
                        "error": None
                    }
                elif "Error" in status or "Illegal" in status:
                    results[platform] = {
                        "available": None,
                        "url": None,
                        "error": status
                    }
                else:
                    results[platform] = {
                        "available": True,
                        "url": None,
                        "error": None
                    }

    # Fill in any missing platforms
    for platform in platforms:
        if platform not in results:
            results[platform] = {
                "available": None,
                "url": None,
                "error": "No response"
            }

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Check social media handle availability",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Supported platforms:
    Instagram, Twitter, Reddit, YouTube, TikTok, Twitch, threads

Examples:
    %(prog)s coolstartup
    %(prog)s myhandle --platforms instagram,tiktok,youtube
    %(prog)s brandname --json
        """
    )
    parser.add_argument(
        "username",
        help="Username/handle to check"
    )
    parser.add_argument(
        "--platforms",
        type=str,
        default=None,
        help=f"Comma-separated platforms (default: {','.join(DEFAULT_PLATFORMS)})"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=15,
        help="Timeout per platform in seconds (default: 15)"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON"
    )

    args = parser.parse_args()

    platforms = args.platforms.split(",") if args.platforms else DEFAULT_PLATFORMS

    results = check_handles(args.username, platforms, args.timeout)

    if args.json:
        import json
        print(json.dumps(results, indent=2))
    else:
        print(f"\nChecking handle: {args.username}\n")

        for platform, info in results.items():
            if info["error"]:
                symbol = "!"
                status = f"ERROR: {info['error']}"
            elif info["available"]:
                symbol = "+"
                status = "AVAILABLE"
            elif info["available"] is False:
                symbol = "-"
                status = f"TAKEN ({info['url']})"
            else:
                symbol = "?"
                status = "UNKNOWN"

            print(f"[{symbol}] {platform}: {status}")


if __name__ == "__main__":
    main()
