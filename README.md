# Internet Names MCP Server

An MCP server for checking availability of domain names, social media handles, and subreddits. Returns clean JSON responses suitable for programmatic use.

## Features

- **Domain names** - Check availability via RDAP (free) or NameSilo API (includes pricing)
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

The server works without an API key using RDAP. To get domain pricing info, add a free NameSilo API key:

1. Create an account at [namesilo.com](https://www.namesilo.com) (or log in)
2. Go to **API Manager**: https://www.namesilo.com/account/api-manager
3. Click **Generate New API Key**
4. Copy the key and run:

```bash
uvx internet-names-mcp --setup
```

Or set via environment variable:

```bash
export NAMESILO_API_KEY="your-key-here"
```

## CLI Commands

```bash
uvx internet-names-mcp              # Run the MCP server
uvx internet-names-mcp --setup      # Configure API keys interactively
uvx internet-names-mcp --show-config # Show current configuration
uvx internet-names-mcp --version    # Show version
uvx internet-names-mcp --help       # Show help
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

## Configuration

Configuration is stored in:
- **macOS/Linux:** `~/.config/internet-names-mcp/config.json`
- **Windows:** `%APPDATA%/internet-names-mcp/config.json`

API key lookup order:
1. Config file (set via `--setup`)
2. Environment variable (`NAMESILO_API_KEY`)
3. macOS Keychain (legacy)

## Development

### Local Setup

```bash
git clone <repo-url> InternetNamesMCP
cd InternetNamesMCP
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium
```

### Running Tests

```bash
source .venv/bin/activate
python test_server.py
```

### Project Structure

```
├── src/internet_names_mcp/
│   ├── __init__.py      # CLI entry point
│   ├── server.py        # MCP server
│   ├── config.py        # Configuration management
│   ├── rdap_bootstrap.py # RDAP bootstrap cache
│   └── rdap_client.py   # Async RDAP client
├── pyproject.toml       # Package configuration
└── README.md
```

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

## License

MIT
