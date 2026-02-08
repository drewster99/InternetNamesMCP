"""
Cross-platform configuration storage for Internet Names MCP.

Config file location:
- macOS/Linux: ~/.config/internet-names-mcp/config.json
- Windows: %APPDATA%/internet-names-mcp/config.json

API key lookup order:
1. Config file
2. Environment variable (NAMESILO_API_KEY)
3. macOS Keychain (backwards compatibility)
"""

import json
import os
from pathlib import Path


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


def load_config() -> dict:
    """Load configuration from file."""
    config_file = get_config_file()
    if config_file.exists():
        try:
            return json.loads(config_file.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_config(config: dict) -> None:
    """Save configuration to file."""
    config_dir = get_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)

    config_file = get_config_file()
    config_file.write_text(json.dumps(config, indent=2))


def get_namesilo_key() -> str | None:
    """
    Get NameSilo API key from available sources.

    Lookup order:
    1. Config file
    2. Environment variable (NAMESILO_API_KEY)
    3. macOS Keychain (backwards compatibility)
    """
    # 1. Check config file
    config = load_config()
    if key := config.get('namesilo_api_key'):
        return key

    # 2. Check environment variable
    if key := os.environ.get('NAMESILO_API_KEY'):
        return key

    # 3. Try macOS Keychain (backwards compatibility)
    try:
        import subprocess
        result = subprocess.run(
            ['security', 'find-generic-password', '-s', 'internet-names-mcp-namesilo', '-w'],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        pass

    return None


def set_namesilo_key(key: str) -> bool:
    """Store NameSilo API key in config file."""
    try:
        config = load_config()
        config['namesilo_api_key'] = key
        save_config(config)
        return True
    except OSError:
        return False


def delete_namesilo_key() -> bool:
    """Remove NameSilo API key from config file."""
    try:
        config = load_config()
        if 'namesilo_api_key' in config:
            del config['namesilo_api_key']
            save_config(config)
        return True
    except OSError:
        return False
