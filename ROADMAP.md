# Internet Names MCP Server - Roadmap

## Current State

The server supports domain availability checking (via RDAP and NameSilo) and social media handle checking across the following platforms:
- Instagram
- Twitter/X
- Reddit
- YouTube
- TikTok
- Twitch
- Threads
- Bluesky
- GitHub
- Snapchat
- Pinterest
- Kick
- Substack (publication subdomains)
- Subreddits (checked separately via `check_subreddits`)

Each platform is checked directly against its own signup or lookup endpoints (see `social_checks.py`), with Playwright (headless browser) for Instagram, Threads and Reddit.

---

## Feature Requests

### Additional Social Media Platforms for Handle Checking

Expand `check_handles` to support the following platforms:

- **LinkedIn** - Professional networking. Handle format: `linkedin.com/in/{username}`. Widely used for professional/brand identity.
- **Mastodon** - Federated social network (ActivityPub). Handle format varies by instance, e.g., `@username@mastodon.social`. Checking would likely need to target one or more popular instances (mastodon.social, mastodon.online, etc.) or accept an instance parameter.
- **Facebook** - Meta's primary social platform. Handle format: `facebook.com/{username}`. Relevant for brand name availability.

**Notes and considerations:**
- Some of these platforms (LinkedIn, Facebook) may have stricter bot detection, potentially requiring Playwright-based checking similar to the current Instagram approach.
- Mastodon's federated nature means a username could be available on one instance but taken on another. A practical approach might be to check the largest instances (mastodon.social, etc.) and document the limitation.
- TikTok, YouTube, Twitch, GitHub, Snapchat, Pinterest and Kick currently report "no account found" rather than signup availability; banned, removed or reserved names can look available. Their signup checks require a login, request signing, or are blocked for automated clients.
- Facebook (HTTP 400), LinkedIn (HTTP 999), Medium and Tumblr (Cloudflare challenge) block unauthenticated checks; Telegram shows the same page for missing and private users.
