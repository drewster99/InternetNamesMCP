#!/usr/bin/env python3
"""
Keychain helper for storing and retrieving API keys securely.

Uses macOS Keychain via the `security` command-line tool.
"""

import subprocess
import sys

# Service name prefix for all keys stored by this tool
SERVICE_PREFIX = "internet-names-mcp"


def _run_security(args: list[str], input_data: str | None = None) -> tuple[int, str, str]:
    """Run a security command and return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            ["security"] + args,
            input=input_data,
            capture_output=True,
            text=True
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except FileNotFoundError:
        return 1, "", "security command not found (are you on macOS?)"


def set_api_key(name: str, value: str) -> bool:
    """
    Store an API key in the keychain.

    Args:
        name: Key name (e.g., "namesilo")
        value: The API key value

    Returns:
        True if successful, False otherwise
    """
    service = f"{SERVICE_PREFIX}.{name}"
    account = name

    # First try to delete any existing key (ignore errors)
    _run_security(["delete-generic-password", "-s", service, "-a", account])

    # Add the new key
    returncode, stdout, stderr = _run_security([
        "add-generic-password",
        "-s", service,
        "-a", account,
        "-w", value,
        "-U"  # Update if exists
    ])

    return returncode == 0


def get_api_key(name: str) -> str | None:
    """
    Retrieve an API key from the keychain.

    Args:
        name: Key name (e.g., "namesilo")

    Returns:
        The API key value, or None if not found
    """
    service = f"{SERVICE_PREFIX}.{name}"
    account = name

    returncode, stdout, stderr = _run_security([
        "find-generic-password",
        "-s", service,
        "-a", account,
        "-w"  # Output just the password
    ])

    if returncode == 0 and stdout:
        return stdout
    return None


def delete_api_key(name: str) -> bool:
    """
    Delete an API key from the keychain.

    Args:
        name: Key name (e.g., "namesilo")

    Returns:
        True if successful (or key didn't exist), False on error
    """
    service = f"{SERVICE_PREFIX}.{name}"
    account = name

    returncode, stdout, stderr = _run_security([
        "delete-generic-password",
        "-s", service,
        "-a", account
    ])

    # Return True if deleted or if it didn't exist
    return returncode == 0 or "could not be found" in stderr.lower()


def list_api_keys() -> list[str]:
    """
    List all API keys stored by this tool.

    Returns:
        List of key names (without the service prefix)
    """
    # This is a bit hacky - we dump the keychain and grep for our service prefix
    returncode, stdout, stderr = _run_security(["dump-keychain"])

    keys = []
    for line in stdout.split("\n"):
        if f'"{SERVICE_PREFIX}.' in line and "svce" in line:
            # Extract the key name from the service name
            try:
                start = line.index(f'"{SERVICE_PREFIX}.') + len(f'"{SERVICE_PREFIX}.')
                end = line.index('"', start)
                key_name = line[start:end]
                if key_name and key_name not in keys:
                    keys.append(key_name)
            except ValueError:
                pass

    return keys


# Convenience function for the main API key we use
def get_namesilo_key() -> str | None:
    """Get the NameSilo API key from keychain, falling back to environment variable."""
    import os

    # Try keychain first
    key = get_api_key("namesilo")
    if key:
        return key

    # Fall back to environment variable
    return os.environ.get("NAMESILO_API_KEY")


if __name__ == "__main__":
    # Simple test
    print("Testing keychain operations...")

    # Test set
    test_value = "test-key-12345"
    if set_api_key("test", test_value):
        print("[+] Set test key")
    else:
        print("[-] Failed to set test key")
        sys.exit(1)

    # Test get
    retrieved = get_api_key("test")
    if retrieved == test_value:
        print("[+] Retrieved test key correctly")
    else:
        print(f"[-] Retrieved wrong value: {retrieved}")
        sys.exit(1)

    # Test list
    keys = list_api_keys()
    if "test" in keys:
        print(f"[+] Found test key in list: {keys}")
    else:
        print(f"[-] Test key not in list: {keys}")

    # Test delete
    if delete_api_key("test"):
        print("[+] Deleted test key")
    else:
        print("[-] Failed to delete test key")
        sys.exit(1)

    # Verify deletion
    if get_api_key("test") is None:
        print("[+] Verified key is deleted")
    else:
        print("[-] Key still exists after deletion")
        sys.exit(1)

    print("\nAll tests passed!")
