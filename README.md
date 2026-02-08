# Internet Names MCP Server

An MCP server for checking availability of domain names, social media handles, and subreddits. Returns clean JSON responses suitable for programmatic use.

## Features

- **Domain names** - Check availability via RDAP (free) or NameSilo API (free, but requires API key - responses include domain prices too)
- **Social media handles** - Instagram, Twitter/X, Reddit, YouTube, TikTok, Twitch, Threads
- **Subreddits** - Check if subreddit names are available on Reddit
- **Comprehensive search** - Generate name combinations and check everything at once

## Quick Start

### 1. Add to Claude Code

```bash
claude mcp add --scope user internet-names-mcp uvx internet-names-mcp
```

That's it! The server works immediately using RDAP for domain lookups.

### 2. Optional: Configure NameSilo API (for domain pricing)

Checking domain name availability works best if you configure a NameSilo API key. It's free and requires just a basic registration. Otherwise we fall back to using RDAP, which has some significant limitations (see below).

## Set up your NameSilo API key

1. Create an account at [namesilo.com](https://www.namesilo.com) (or log in)
2. Go to [API Manager](https://www.namesilo.com/account/api-manager)
3. Click **Generate New API Key**
4. Copy the key and run:

```bash
uvx internet-names-mcp --setup
```

You'll be prompted to paste your API key.
On macOS, your API key is stored safely in your iCloud or login keychain as `internet-names-mcp.namesilo`. On other platforms, it's stored in `~/.config/internet-names-mcp/config.json`.

Or, you could set it via environment variable `NAMESILO_API_KEY` in your LLM's MCP server configuration file.


## CLI Commands

```bash
uvx internet-names-mcp --setup         # Configure API keys interactively
uvx internet-names-mcp --show-config   # Show current configuration
uvx internet-names-mcp --version       # Show version
uvx internet-names-mcp --help          # Show help
```

## Tools

### get_supported_socials()

Returns list of supported social media platforms.

**Response:**
```json
{
  "platforms": ["instagram", "twitter", "reddit", "youtube", "tiktok", "twitch", "threads", "subreddit"]
}
```

Note: `subreddit` is checked via `check_subreddits()`, not `check_handles()`.

---

### check_domains(names, tlds?, method?, onlyReportAvailable?)

Check domain name availability and pricing.

**Parameters:**
| Name | Type | Default | Description |
|------|------|---------|-------------|
| `names` | list[str] | required | Domain names or base names to check |
| `tlds` | list[str] | `["com", "io", "ai", "co", "app", "dev", "net", "org"]` | TLDs to check |
| `method` | str | `"auto"` | `"auto"`, `"rdap"`, or `"namesilo"` |
| `onlyReportAvailable` | bool | `false` | If true, omit unavailable domains from response |

If a name contains a dot, it's treated as a full domain. Otherwise, it's combined with each TLD.

**Response:**
```json
{
  "available": [
    {"domain": "myapp.com", "price": 17.29},
    {"domain": "myapp.io", "price": 34.99}
  ],
  "unavailable": ["myapp.ai"],
  "summary": {
    "cheapestAvailable": {"domain": "myapp.com", "price": 17.29},
    "shortestAvailable": {"domain": "myapp.io", "price": 34.99}
  }
}
```

---

### check_handles(username, platforms?, onlyReportAvailable?)

Check social media handle availability across platforms.

**Parameters:**
| Name | Type | Default | Description |
|------|------|---------|-------------|
| `username` | str | required | The username/handle to check |
| `platforms` | list[str] | all platforms | Platforms to check |
| `onlyReportAvailable` | bool | `false` | If true, omit unavailable handles from response |

Supported platforms: `instagram`, `twitter`, `reddit`, `youtube`, `tiktok`, `twitch`, `threads`

Note: Twitter/X checking uses a headless browser and takes ~4 seconds.

**Response:**
```json
{
  "available": ["instagram", "tiktok", "youtube"],
  "unavailable": [
    {"platform": "twitter", "url": "https://x.com/myapp"},
    {"platform": "reddit", "url": "https://reddit.com/user/myapp"}
  ]
}
```

---

### check_subreddits(names, onlyReportAvailable?)

Check subreddit name availability on Reddit.

**Parameters:**
| Name | Type | Default | Description |
|------|------|---------|-------------|
| `names` | list[str] | required | Subreddit names to check (with or without r/ prefix) |
| `onlyReportAvailable` | bool | `false` | If true, omit unavailable subreddits from response |

**Response:**
```json
{
  "available": ["mynewsubreddit"],
  "unavailable": [
    {"name": "programming", "subscribers": 6835000},
    {"name": "privatesubreddit", "note": "private"}
  ]
}
```

---

### check_everything(components, tlds?, platforms?, method?, requireAllTLDsAvailable?, onlyReportAvailable?, alsoIncludeHyphens?)

Comprehensive check across domains and social media. Generates name combinations from components, checks domains first (fast), then checks social handles for names that pass the domain check.

**Parameters:**
| Name | Type | Default | Description |
|------|------|---------|-------------|
| `components` | list[str] | required | Name components to combine (e.g., `["red", "sweater"]`) |
| `tlds` | list[str] | `["com", "net", "org", "io", "ai"]` | TLDs to check |
| `platforms` | list[str] | all platforms | Social platforms to check |
| `method` | str | `"auto"` | `"auto"`, `"rdap"`, or `"namesilo"` |
| `requireAllTLDsAvailable` | bool | `false` | If true, name must be available in ALL TLDs to pass |
| `onlyReportAvailable` | bool | `false` | If true, omit unavailable items from response |
| `alsoIncludeHyphens` | bool | `false` | If true, also check hyphenated versions |

**Name Generation:**
From components `["red", "sweater"]`, generates:
- Single components: `red`, `sweater`
- Concatenations: `redsweater`, `sweaterred`

**Response:**
```json
{
  "availableDomains": [
    {"domain": "redsweater.com", "price": 17.29},
    {"domain": "redsweater.io", "price": 34.99}
  ],
  "domainSuccessfulBasenames": ["redsweater", "sweaterred"],
  "availableHandles": {
    "redsweater": ["instagram", "twitter", "youtube"],
    "sweaterred": ["instagram", "tiktok"]
  },
  "unavailableHandles": {
    "redsweater": [{"platform": "reddit", "url": "..."}]
  },
  "summary": {
    "fullyAvailable": ["sweaterred"],
    "cheapestDomain": {"domain": "redsweater.com", "price": 17.29}
  }
}
```

The `fullyAvailable` list contains names that are available on ALL checked platforms.

## Domain Lookup Methods

| Method | Description | Pricing | Speed |
|--------|-------------|---------|-------|
| `auto` | Uses NameSilo if API key configured, otherwise RDAP | With NameSilo | Fast |
| `rdap` | Direct registry queries via IANA bootstrap | No | Fast |
| `namesilo` | NameSilo API (requires API key) | Yes | Fast |

### RDAP Limitations

RDAP is free and requires no API key, but has some limitations:

- **TLD coverage** - Not all top level domains (TLDs) have RDAP servers. Queries for unsupported TLDs will fail. RDAP works for .com, .net, .org, .app, .ai and more. See [deployment.rdap.org](https://deployment.rdap.org) for an up-to-date list (look for 'Yes' in the 'RDAP' column).
- **No pricing** - RDAP only reports availability, not the cost to register the domain.
- **False positives** - A domain may appear available via RDAP but actually be reserved or considered "premium" by registrars, making it effectively unavailable or prohibitively expensive to purchase.

For reliable results with pricing, configure a NameSilo API key.

## Configuration

API key storage:
- **macOS:** Keychain (as `internet-names-mcp.namesilo`)
- **Linux:** `~/.config/internet-names-mcp/config.json`
- **Windows:** `%APPDATA%/internet-names-mcp/config.json`

API key lookup order (first match wins):
1. macOS Keychain (on macOS only)
2. Environment variable (`NAMESILO_API_KEY`)
3. Config file (fallback)

## Development

### Local Setup

The `devsetup.sh` script handles virtual environment creation and dependency installation:

```bash
git clone <repo-url> InternetNamesMCP
cd InternetNamesMCP
source devsetup.sh          # Creates .venv, activates it, installs dependencies
playwright install chromium # Required for Twitter/X handle checking
```

Options:
- `source devsetup.sh` - Set up environment (default)
- `source devsetup.sh --clean` - Delete venv and caches
- `source devsetup.sh --clean --setup` - Clean rebuild

### Running Tests

```bash
source devsetup.sh  # Activate environment first

# Main test suites
python test_server.py         # Full test suite - offline validation + online API tests
python test_mcp_interface.py  # Tests via MCP protocol (stdio transport)
python test_rdap_client.py    # Async RDAP client, rate limiter, batch queries

# Comparison/diagnostic tests
python test_methods.py        # Compare RDAP vs NameSilo results for discrepancies
python test_rdap.py           # Quick RDAP-only domain check
```

| Test File | Description |
|-----------|-------------|
| `test_server.py` | Main test suite covering all MCP tools, edge cases, and API calls |
| `test_mcp_interface.py` | Tests the server through actual MCP protocol via stdio |
| `test_rdap_client.py` | Tests async RDAP client, rate limiting, and batch queries |
| `test_methods.py` | Compares RDAP vs NameSilo to detect availability discrepancies |
| `test_rdap.py` | Simple RDAP-only test for quick domain availability checks |

### Project Structure

```
├── src/internet_names_mcp/
│   ├── __init__.py       # CLI entry point
│   ├── __main__.py       # Module runner
│   ├── server.py         # MCP server
│   ├── config.py         # Configuration management
│   ├── rdap_bootstrap.py # RDAP bootstrap cache
│   └── rdap_client.py    # Async RDAP client
├── pyproject.toml       # Package configuration
└── README.md
```

### Data Files

**RDAP Bootstrap Cache** - Maps TLDs to their authoritative RDAP servers (auto-downloaded from IANA):
- **macOS/Linux:** `~/.cache/internet-names-mcp/rdap_bootstrap.json`
- **Windows:** `%APPDATA%/internet-names-mcp/rdap_bootstrap.json`

The cache is automatically refreshed when expired (default 24h TTL from IANA's Cache-Control headers).

## Troubleshooting

### "sherlock not found"

Sherlock is installed automatically as a dependency. If you see this error, reinstall:
```bash
uvx --reinstall internet-names-mcp
```

### "playwright not installed" or Chromium errors

Install Playwright browser:
```bash
playwright install chromium
```

Or with uvx:
```bash
uvx --from playwright install chromium
```

### Twitter checks fail or timeout

Twitter/X checks use a headless browser which can be slow or blocked. If checks consistently fail, Twitter may be rate-limiting or blocking automated access.

## Copyright

Copyright (C) 2026 Nuclear Cyborg Corp

## License

[MIT](license.md)
