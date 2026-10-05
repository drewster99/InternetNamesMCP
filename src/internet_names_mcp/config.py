"""
Configuration storage for Internet Names MCP.

On macOS: Uses Keychain for secure API key storage.
On other platforms: Falls back to config file.

API key lookup order:
1. macOS Keychain (if on macOS)
2. Environment variable (NAMESILO_API_KEY)
3. Config file (fallback)
"""

import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Keychain service name
KEYCHAIN_SERVICE = "internet-names-mcp.namesilo"
KEYCHAIN_ACCOUNT = "namesilo"

# `security` can block indefinitely on a Keychain unlock or access-confirmation
# dialog. The server calls it while handling tool requests, so an unbounded wait
# would stall them; this still leaves a person time to answer the dialog.
KEYCHAIN_TIMEOUT_SECONDS = 30

# `security` reads a prompted password with getpass(3), which silently
# discards everything past _PASSWORD_LEN characters.
KEYCHAIN_PROMPTED_PASSWORD_MAX_LENGTH = 128

# `security` exits with this status when the requested item doesn't exist
# (errSecItemNotFound).
SECURITY_EXIT_ITEM_NOT_FOUND = 44


def _is_macos() -> bool:
    """Check if running on macOS."""
    return sys.platform == "darwin"


def _keychain_get(service: str, account: str) -> str | None:
    """Get a password from macOS Keychain."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            capture_output=True,
            text=True,
            timeout=KEYCHAIN_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired:
        logger.warning("Timed out after %ss reading Keychain item %s", KEYCHAIN_TIMEOUT_SECONDS, service)
        return None
    except (subprocess.SubprocessError, FileNotFoundError) as e:
        logger.warning("Could not read Keychain item %s: %s", service, e)
        return None
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    # Callers treat None as "no key here" and move on to other sources, so a
    # Keychain failure would otherwise look exactly like an unconfigured key.
    if result.returncode not in (0, SECURITY_EXIT_ITEM_NOT_FOUND):
        logger.warning(
            "Could not read Keychain item %s (security exit %s): %s",
            service, result.returncode, result.stderr.strip()
        )
    return None


def _validate_storable_via_security_prompt(password: str) -> None:
    """Raise ValueError unless `password` survives the `security` prompt and a read back unchanged."""
    # A password `security` can't read back intact must be refused up front:
    # a mangled prompt exchange (e.g. an embedded newline) makes `security`
    # overwrite the item with an empty password and still exit 0.
    if not password:
        raise ValueError("the key is empty")
    if len(password) > KEYCHAIN_PROMPTED_PASSWORD_MAX_LENGTH:
        raise ValueError(f"the key is longer than {KEYCHAIN_PROMPTED_PASSWORD_MAX_LENGTH} characters")
    # `find-generic-password -w` returns non-ASCII or non-printable data as hex.
    if not (password.isascii() and password.isprintable()):
        raise ValueError("the key contains non-ASCII or control characters")
    # `_keychain_get` strips surrounding whitespace.
    if password != password.strip():
        raise ValueError("the key has leading or trailing whitespace")


def _keychain_set(service: str, account: str, password: str) -> bool:
    """
    Store a password in macOS Keychain.

    Raises ValueError if `password` can't be stored and read back intact.
    """
    _validate_storable_via_security_prompt(password)
    try:
        # A trailing bare `-w` makes `security` prompt for the password instead
        # of taking it from argv, where any local process could read it. With
        # no controlling terminal, getpass(3) reads stdin; a new session drops
        # the terminal so `--setup` run from a shell doesn't block on /dev/tty.
        # `-U` updates the item in place, so a write `security` rejects leaves
        # the previous key intact.
        result = subprocess.run(
            ["security", "add-generic-password", "-s", service, "-a", account, "-U", "-w"],
            input=f"{password}\n{password}\n",
            capture_output=True,
            text=True,
            timeout=KEYCHAIN_TIMEOUT_SECONDS,
            start_new_session=True
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return False
    # `security` exits 0 even when the prompt exchange goes wrong, so the exit
    # status alone can't prove the intended key was stored.
    return result.returncode == 0 and _keychain_get(service, account) == password


def _keychain_delete(service: str, account: str) -> bool:
    """Delete a password from macOS Keychain."""
    try:
        result = subprocess.run(
            ["security", "delete-generic-password", "-s", service, "-a", account],
            capture_output=True,
            timeout=KEYCHAIN_TIMEOUT_SECONDS
        )
        return result.returncode in (0, SECURITY_EXIT_ITEM_NOT_FOUND)
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

    Returns False if the key couldn't be stored. On macOS, raises ValueError
    if the key can't be stored in Keychain and read back intact.
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
