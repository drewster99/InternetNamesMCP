#!/usr/bin/env python3
"""
Setup CLI for Internet Names MCP.

Usage:
    source .venv/bin/activate
    python setup.py                    # Interactive setup
    python setup.py --set-api-key      # Set NameSilo API key
    python setup.py --show-api-key     # Show stored API key (masked)
    python setup.py --delete-api-key   # Delete stored API key
    python setup.py --test             # Test the configuration
"""

import sys

# Check Python version and virtual environment early
if sys.version_info < (3, 10):
    print("Error: Python 3.10+ required")
    print()
    print("Activate the virtual environment:")
    print("    source .venv/bin/activate")
    print("    python setup.py")
    sys.exit(1)

import argparse
import getpass

# Check for httpx (only needed for --test)
try:
    import httpx
except ImportError:
    httpx = None

from keychain import get_api_key, set_api_key, delete_api_key, get_namesilo_key


def cmd_set_api_key(key: str | None = None) -> bool:
    """Set the NameSilo API key in keychain."""
    if key is None:
        print("Enter your NameSilo API key.")
        print("(Get one free at: https://www.namesilo.com/account/api-manager)")
        print()
        key = getpass.getpass("API Key: ").strip()

    if not key:
        print("Error: No API key provided")
        return False

    if set_api_key("namesilo", key):
        print("✓ API key stored in keychain")
        return True
    else:
        print("✗ Failed to store API key")
        return False


def cmd_show_api_key() -> bool:
    """Show the stored API key (masked)."""
    key = get_namesilo_key()

    if key:
        # Show first 4 and last 4 characters
        if len(key) > 10:
            masked = key[:4] + "*" * (len(key) - 8) + key[-4:]
        else:
            masked = key[:2] + "*" * (len(key) - 2)
        print(f"Stored API key: {masked}")
        print(f"Source: {'keychain' if get_api_key('namesilo') else 'environment variable'}")
        return True
    else:
        print("No API key found in keychain or environment")
        return False


def cmd_delete_api_key() -> bool:
    """Delete the stored API key."""
    if delete_api_key("namesilo"):
        print("✓ API key deleted from keychain")
        return True
    else:
        print("✗ Failed to delete API key")
        return False


def cmd_test() -> bool:
    """Test the configuration by making a simple API call."""
    if httpx is None:
        print("✗ Cannot test: httpx not installed")
        print()
        print("Activate the virtual environment first:")
        print("    source .venv/bin/activate")
        print("    python setup.py --test")
        return False

    key = get_namesilo_key()

    if not key:
        print("✗ No API key configured")
        print("  Run: python setup.py --set-api-key")
        return False

    print(f"API key: {'*' * 4}{key[-4:]}")

    # Test the API
    print("\nTesting NameSilo API...")

    try:
        response = httpx.get(
            "https://www.namesilo.com/api/checkRegisterAvailability",
            params={
                "version": "1",
                "type": "json",
                "key": key,
                "domains": "test-domain-12345.com"
            },
            timeout=10
        )
        data = response.json()
        code = data.get("reply", {}).get("code")

        if code == 300 or code == "300":
            print("✓ NameSilo API working")
            return True
        else:
            detail = data.get("reply", {}).get("detail", "Unknown error")
            print(f"✗ API returned error: {detail}")
            return False

    except Exception as e:
        print(f"✗ API test failed: {e}")
        return False


def cmd_interactive() -> bool:
    """Interactive setup wizard."""
    print("=" * 50)
    print("Internet Names MCP - Setup")
    print("=" * 50)
    print()

    # Check current status
    key = get_namesilo_key()
    if key:
        print(f"Current API key: {'*' * 4}{key[-4:]}")
        print()
        response = input("Update API key? [y/N]: ").strip().lower()
        if response != "y":
            print("\nTesting current configuration...")
            return cmd_test()

    # Set up API key
    print()
    if not cmd_set_api_key():
        return False

    # Test
    print()
    print("Testing configuration...")
    return cmd_test()


def main():
    parser = argparse.ArgumentParser(
        description="Setup Internet Names MCP",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    %(prog)s                    Interactive setup
    %(prog)s --set-api-key      Set your NameSilo API key
    %(prog)s --test             Test the configuration

Get your free API key at:
    https://www.namesilo.com/account/api-manager
        """
    )

    parser.add_argument(
        "--set-api-key",
        action="store_true",
        help="Set the NameSilo API key (prompts for input)"
    )
    parser.add_argument(
        "--api-key",
        type=str,
        metavar="KEY",
        help="Provide the API key directly (use with --set-api-key)"
    )
    parser.add_argument(
        "--show-api-key",
        action="store_true",
        help="Show the stored API key (masked)"
    )
    parser.add_argument(
        "--delete-api-key",
        action="store_true",
        help="Delete the stored API key"
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Test the configuration"
    )

    args = parser.parse_args()

    # Handle commands
    if args.set_api_key:
        success = cmd_set_api_key(args.api_key)
    elif args.show_api_key:
        success = cmd_show_api_key()
    elif args.delete_api_key:
        success = cmd_delete_api_key()
    elif args.test:
        success = cmd_test()
    else:
        success = cmd_interactive()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
