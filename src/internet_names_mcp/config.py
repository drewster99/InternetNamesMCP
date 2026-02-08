"""
Configuration storage for Internet Names MCP.

On macOS: Uses Keychain for secure API key storage.
On other platforms: Falls back to config file.

API key lookup order:
1. macOS Keychain (if on macOS)
2. Environment variable (NAMESILO_API_KEY)
3. Config file (fallback)
"""

import os
import subprocess
import sys
from pathlib import Path

# Keychain service name
KEYCHAIN_SERVICE = "internet-names-mcp.namesilo"
KEYCHAIN_ACCOUNT = "namesilo"


def _is_macos() -> bool:
    """Check if running on macOS."""
    return sys.platform == "darwin"


def _keychain_get(service: str, account: str) -> str | None:
    """Get a password from macOS Keychain."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            capture_output=True,
            text=True
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        pass
    return None


def _keychain_set(service: str, account: str, password: str) -> bool:
    """Store a password in macOS Keychain."""
    try:
        # Delete existing entry first (ignore errors)
        subprocess.run(
            ["security", "delete-generic-password", "-s", service, "-a", account],
            capture_output=True
        )
        # Add new entry
        result = subprocess.run(
            ["security", "add-generic-password", "-s", service, "-a", account, "-w", password, "-U"],
            capture_output=True
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


def _keychain_delete(service: str, account: str) -> bool:
    """Delete a password from macOS Keychain."""
    try:
        result = subprocess.run(
            ["security", "delete-generic-password", "-s", service, "-a", account],
            capture_output=True,
            text=True
        )
        return result.returncode == 0 or "could not be found" in result.stderr.lower()
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


def get_config_dir() -> Path:
    """Get the config directory for this app."""
    if os.name == 'nt':  # Windows
        base = Path(os.environ.get('APPDATA', Path.home()))
    else:  # macOS, Linux
        base = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))

    config_dir = base / 'internet-names-mcp'
    return config_dir


def get_config_file() -> Path:
    """Get the path to the config file."""
    return get_config_dir() / 'config.json'


def get_namesilo_key() -> str | None:
    """
    Get NameSilo API key from available sources.

    Lookup order:
    1. macOS Keychain (if on macOS)
    2. Environment variable (NAMESILO_API_KEY)
    3. Config file (fallback for non-macOS or legacy)
    """
    # 1. Try macOS Keychain first
    if _is_macos():
        if key := _keychain_get(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT):
            return key

    # 2. Check environment variable
    if key := os.environ.get('NAMESILO_API_KEY'):
        return key

    # 3. Check config file (fallback)
    try:
        import json
        config_file = get_config_file()
        if config_file.exists():
            config = json.loads(config_file.read_text())
            if key := config.get('namesilo_api_key'):
                return key
    except (json.JSONDecodeError, OSError):
        pass

    return None


def set_namesilo_key(key: str) -> bool:
    """
    Store NameSilo API key.

    On macOS: Uses Keychain.
    On other platforms: Uses config file.
    """
    if _is_macos():
        return _keychain_set(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT, key)
    else:
        # Fall back to config file on non-macOS
        try:
            import json
            config_dir = get_config_dir()
            config_dir.mkdir(parents=True, exist_ok=True)

            config_file = get_config_file()
            config = {}
            if config_file.exists():
                try:
                    config = json.loads(config_file.read_text())
                except json.JSONDecodeError:
                    pass

            config['namesilo_api_key'] = key
            config_file.write_text(json.dumps(config, indent=2))
            return True
        except OSError:
            return False


def delete_namesilo_key() -> bool:
    """Remove NameSilo API key."""
    if _is_macos():
        return _keychain_delete(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
    else:
        try:
            import json
            config_file = get_config_file()
            if config_file.exists():
                config = json.loads(config_file.read_text())
                if 'namesilo_api_key' in config:
                    del config['namesilo_api_key']
                    config_file.write_text(json.dumps(config, indent=2))
            return True
        except (json.JSONDecodeError, OSError):
            return False


def get_key_source() -> str | None:
    """Determine where the API key is stored (for display purposes)."""
    if _is_macos():
        if _keychain_get(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT):
            return "macOS Keychain"

    if os.environ.get('NAMESILO_API_KEY'):
        return "environment variable"

    try:
        import json
        config_file = get_config_file()
        if config_file.exists():
            config = json.loads(config_file.read_text())
            if config.get('namesilo_api_key'):
                return "config file"
    except (json.JSONDecodeError, OSError):
        pass

    return None
