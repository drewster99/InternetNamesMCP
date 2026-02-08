#!/usr/bin/env python3
"""
Check social media handle availability via MCP server.

Usage:
    source ./devsetup.sh
    python test_socials.py <username> [platform1] [platform2] ...

Examples:
    python test_socials.py fubar              # Check all platforms
    python test_socials.py fubar twitter      # Check only Twitter
    python test_socials.py fubar instagram youtube  # Check specific platforms
"""

import sys

# Check Python version and dependencies early
if sys.version_info < (3, 10):
    print("Error: Python 3.10+ required")
    print()
    print("Set up the development environment:")
    print("    source ./devsetup.sh")
    sys.exit(1)

try:
    import anyio
except ImportError:
    print("Error: anyio not found")
    print()
    print("Set up the development environment first:")
    print("    source ./devsetup.sh")
    sys.exit(1)

try:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
except ImportError as e:
    print(f"Error: {e}")
    print()
    print("Set up the development environment first:")
    print("    source ./devsetup.sh")
    sys.exit(1)

import json
import os
from pathlib import Path


def print_usage():
    """Print usage information."""
    print(__doc__)
    print("Available platforms will be fetched from the MCP server.")


async def main_async(username: str, platforms: list[str]) -> int:
    """
    Check social handle availability via MCP.

    Returns exit code (0 = success, 1 = error).
    """
    # Set up server parameters - run the package as a module
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "internet_names_mcp"],
        cwd=str(Path(__file__).parent),
    )

    # Suppress server stderr
    with open(os.devnull, "w") as devnull:
        async with stdio_client(server_params, errlog=devnull) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                # Initialize the session
                await session.initialize()

                # Get supported platforms
                result = await session.call_tool("get_supported_socials", {})
                supported_data = json.loads(result.content[0].text)
                supported_platforms = supported_data.get("platforms", [])

                # Filter out 'subreddit' since that's checked differently
                supported_platforms = [p for p in supported_platforms if p != "subreddit"]

                print(f"Checking handle: {username}")
                print()

                # Validate requested platforms
                if platforms:
                    invalid = [p for p in platforms if p not in supported_platforms]
                    if invalid:
                        print(f"Error: Unknown platform(s): {', '.join(invalid)}")
                        print(f"Available: {', '.join(supported_platforms)}")
                        return 1
                    check_platforms = platforms
                else:
                    check_platforms = supported_platforms
                    print(f"Checking all {len(check_platforms)} platforms...")
                    print()

                # Call check_handles
                result = await session.call_tool("check_handles", {
                    "username": username,
                    "platforms": check_platforms,
                })
                data = json.loads(result.content[0].text)

                # Display results
                available = data.get("available", [])
                unavailable = data.get("unavailable", [])

                # Separate unavailable into "taken" (has url, no error) vs "error" (has error field)
                taken_map = {}
                error_map = {}
                for item in unavailable:
                    platform = item["platform"]
                    if "error" in item:
                        error_map[platform] = item
                    else:
                        taken_map[platform] = item

                # Print results in order of requested platforms
                print(f"{'Platform':<15} {'Status':<12} {'Details'}")
                print("-" * 60)

                for platform in check_platforms:
                    if platform in available:
                        status = "✓ available"
                        details = ""
                    elif platform in taken_map:
                        status = "✗ taken"
                        details = taken_map[platform].get("url", "")
                    elif platform in error_map:
                        status = "❌ error"
                        details = error_map[platform].get("error", "unknown error")
                    else:
                        status = "? unknown"
                        details = ""

                    print(f"{platform:<15} {status:<12} {details}")

                # Summary
                print()
                summary_prefix = "❌ " if error_map else ""
                print(f"{summary_prefix}Summary: {len(available)} available, {len(taken_map)} taken, {len(error_map)} errors")

                return 0


def main():
    """Main entry point."""
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help"):
        print_usage()
        sys.exit(0)

    username = args[0]
    platforms = args[1:] if len(args) > 1 else []

    # Validate username
    if not username or username.isspace():
        print("Error: Username cannot be empty")
        sys.exit(1)

    try:
        exit_code = anyio.run(main_async, username, platforms)
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\nInterrupted")
        sys.exit(130)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
