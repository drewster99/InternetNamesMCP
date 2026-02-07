# Internet Names MCP Server

An MCP server for checking availability of domain names, social media handles, and subreddits. Returns clean JSON responses suitable for programmatic use.

## Features

- **Domain names** - Check availability and pricing via NameSilo API
- **Social media handles** - Instagram, Twitter/X, Reddit, YouTube, TikTok, Twitch, Threads
- **Subreddits** - Check if subreddit names are available on Reddit
- **Comprehensive search** - Generate name combinations and check everything at once

## Requirements

- Python 3.11+
- macOS (for Keychain API key storage)
- [Sherlock](https://github.com/sherlock-project/sherlock) for social media checks
- Playwright + Chromium for Twitter/X checks
- NameSilo API key (free) for domain checks

## Setup

### 1. Clone and create virtual environment

```bash
git clone <repo-url> InternetNamesMCP
cd InternetNamesMCP
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install Python dependencies

```bash
pip install mcp httpx sherlock-project playwright
```

### 3. Install Playwright browser

```bash
playwright install chromium
```

### 4. Set your NameSilo API key

Get a free API key from: https://www.namesilo.com/account/api-manager

```bash
python setup.py --set-api-key
```

This stores the key securely in your macOS Keychain.

Other setup commands:
```bash
python setup.py --show-api-key   # Show stored key (masked)
python setup.py --test           # Test the configuration
python setup.py --delete-api-key # Remove from keychain
```

### 5. Configure Claude Code

Add to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "internet-names": {
      "command": "/path/to/InternetNamesMCP/.venv/bin/python",
      "args": ["/path/to/InternetNamesMCP/server.py"]
    }
  }
}
```

Replace `/path/to/InternetNamesMCP` with your actual path.

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

### check_domains(names, tlds?, onlyReportAvailable?)

Check domain name availability and pricing.

**Parameters:**
| Name | Type | Default | Description |
|------|------|---------|-------------|
| `names` | list[str] | required | Domain names or base names to check |
| `tlds` | list[str] | `["com", "io", "ai", "co", "app", "dev", "net", "org"]` | TLDs to check |
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

### check_everything(components, tlds?, platforms?, requireAllTLDsAvailable?, onlyReportAvailable?)

Comprehensive check across domains and social media. Generates name combinations from components, checks domains first (fast), then checks social handles for names that pass the domain check.

**Parameters:**
| Name | Type | Default | Description |
|------|------|---------|-------------|
| `components` | list[str] | required | Name components to combine (e.g., `["red", "sweater"]`) |
| `tlds` | list[str] | `["com", "net", "org", "io", "ai"]` | TLDs to check |
| `platforms` | list[str] | all platforms | Social platforms to check |
| `requireAllTLDsAvailable` | bool | `false` | If true, name must be available in ALL TLDs to pass |
| `onlyReportAvailable` | bool | `false` | If true, omit unavailable items from response |

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

## Running Tests

The test suite verifies all functionality works correctly.

```bash
source .venv/bin/activate
python test_server.py
```

### Expected output

```
============================================================
  INTERNET NAMES MCP SERVER - TEST SUITE
============================================================

============================================================
  get_supported_socials
============================================================
  ✓ returns valid JSON
  ✓ includes instagram
  ...

============================================================
  SUMMARY: 50/50 passed, 0 failed
============================================================

Completed in 26.8 seconds
```

The test suite returns exit code 0 on success, 1 on failure.

## Files

```
├── server.py           # MCP server (main file)
├── setup.py            # API key setup utility
├── keychain.py         # macOS Keychain integration
├── test_server.py      # Test suite
├── check_domains.py    # Domain CLI (standalone)
├── check_handles.py    # Sherlock CLI (standalone)
├── check_subreddits.py # Reddit CLI (standalone)
└── README.md           # This file
```

## Troubleshooting

### "sherlock not found"

Install Sherlock:
```bash
pip install sherlock-project
```

### "playwright not installed"

Install Playwright and browser:
```bash
pip install playwright
playwright install chromium
```

### "No API key configured"

Set your NameSilo API key:
```bash
python setup.py --set-api-key
```

### Twitter checks fail or timeout

Twitter/X checks use a headless browser which can be slow or blocked. If checks consistently fail, Twitter may be rate-limiting or blocking automated access.

## License

MIT
