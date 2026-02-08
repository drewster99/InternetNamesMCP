"""
Internet Names MCP Server

An MCP server for checking availability of domain names, social media handles, and subreddits.
"""

__version__ = "0.1.1"


def main():
    """Main entry point for the CLI."""
    import sys

    # Handle CLI arguments before importing heavy dependencies
    if "--help" in sys.argv or "-h" in sys.argv:
        print_help()
        sys.exit(0)

    if "--version" in sys.argv or "-V" in sys.argv:
        print(f"internet-names-mcp {__version__}")
        sys.exit(0)

    if "--setup" in sys.argv:
        run_setup()
        sys.exit(0)

    if "--show-config" in sys.argv:
        show_config()
        sys.exit(0)

    # Default: run the MCP server
    from .server import mcp
    mcp.run()


def print_help():
    """Print help message."""
    print(f"""internet-names-mcp {__version__}

An MCP server for checking availability of domain names, social media handles, and subreddits.

Usage:
    internet-names-mcp              Run the MCP server
    internet-names-mcp --setup      Configure API keys interactively
    internet-names-mcp --show-config Show current configuration
    internet-names-mcp --version    Show version
    internet-names-mcp --help       Show this help

Configuration:
    The server works out of the box using RDAP for domain lookups (no API key required).

    For NameSilo integration (includes pricing), set your API key:
    1. Run: internet-names-mcp --setup
    2. Or set environment variable: NAMESILO_API_KEY=your-key
    3. Or add to Claude Code config:
       {{
         "mcpServers": {{
           "internet-names": {{
             "command": "uvx",
             "args": ["internet-names-mcp"],
             "env": {{
               "NAMESILO_API_KEY": "your-key"
             }}
           }}
         }}
       }}

    Get a free NameSilo API key at: https://www.namesilo.com/account/api-manager

Claude Code Setup:
    Add to ~/.claude/settings.json:
    {{
      "mcpServers": {{
        "internet-names": {{
          "command": "uvx",
          "args": ["internet-names-mcp"]
        }}
      }}
    }}
""")


def run_setup():
    """Interactive setup wizard."""
    import getpass
    from .config import get_namesilo_key, set_namesilo_key, get_config_file

    print("=" * 50)
    print("Internet Names MCP - Setup")
    print("=" * 50)
    print()

    # Check Playwright
    print("Checking dependencies...")
    check_playwright()
    print()

    # Check current API key status
    current_key = get_namesilo_key()
    if current_key:
        masked = mask_key(current_key)
        print(f"Current NameSilo API key: {masked}")
        print()
        response = input("Update API key? [y/N]: ").strip().lower()
        if response != "y":
            print("\nSetup complete. Your current configuration is preserved.")
            return

    # Prompt for API key
    print()
    print("NameSilo API key (optional - enables domain pricing)")
    print("Get one free at: https://www.namesilo.com/account/api-manager")
    print("Press Enter to skip (RDAP will be used for domain lookups)")
    print()

    key = getpass.getpass("API Key: ").strip()

    if key:
        if set_namesilo_key(key):
            print(f"\n✓ API key saved to {get_config_file()}")
            test_api_key(key)
        else:
            print("\n✗ Failed to save API key")
    else:
        print("\n✓ Skipped. RDAP will be used for domain lookups (no pricing info).")

    print()
    print("Setup complete!")
    print()
    print("Add this to ~/.claude/settings.json:")
    print("""
{
  "mcpServers": {
    "internet-names": {
      "command": "uvx",
      "args": ["internet-names-mcp"]
    }
  }
}
""")


def show_config():
    """Show current configuration."""
    from .config import get_namesilo_key, get_config_file, load_config

    print("Configuration")
    print("=" * 50)
    print()

    config_file = get_config_file()
    print(f"Config file: {config_file}")
    print(f"  Exists: {config_file.exists()}")
    print()

    key = get_namesilo_key()
    if key:
        masked = mask_key(key)
        print(f"NameSilo API key: {masked}")

        # Determine source
        config = load_config()
        if config.get('namesilo_api_key'):
            print("  Source: config file")
        elif 'NAMESILO_API_KEY' in __import__('os').environ:
            print("  Source: environment variable")
        else:
            print("  Source: macOS Keychain")
    else:
        print("NameSilo API key: Not configured")
        print("  Domain lookups will use RDAP (no pricing)")


def mask_key(key: str) -> str:
    """Mask an API key for display."""
    if len(key) > 8:
        return key[:4] + "*" * (len(key) - 8) + key[-4:]
    elif len(key) > 4:
        return key[:2] + "*" * (len(key) - 2)
    else:
        return "*" * len(key)


def check_playwright():
    """Check if Playwright is installed and has browsers."""
    try:
        from playwright.sync_api import sync_playwright
        print("  ✓ Playwright installed")

        # Check for Chromium
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                browser.close()
            print("  ✓ Chromium browser available")
        except Exception:
            print("  ✗ Chromium not installed")
            print("    Run: playwright install chromium")

    except ImportError:
        print("  ✗ Playwright not installed")
        print("    Run: pip install playwright && playwright install chromium")


def test_api_key(key: str):
    """Test the NameSilo API key."""
    try:
        import httpx
        print("\nTesting NameSilo API...")

        response = httpx.get(
            "https://www.namesilo.com/api/checkRegisterAvailability",
            params={
                "version": "1",
                "type": "json",
                "key": key,
                "domains": "test-domain-check-12345.com"
            },
            timeout=10
        )
        data = response.json()
        code = data.get("reply", {}).get("code")

        if str(code) == "300":
            print("✓ API key is valid")
        else:
            detail = data.get("reply", {}).get("detail", "Unknown error")
            print(f"✗ API error: {detail}")

    except Exception as e:
        print(f"✗ Test failed: {e}")
