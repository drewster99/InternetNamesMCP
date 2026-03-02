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
- Subreddits (checked separately via `check_subreddits`)

Handle checking uses Sherlock for most platforms and Playwright (headless browser) for Twitter/X.

---

## Feature Requests

### Additional Social Media Platforms for Handle Checking

Expand `check_handles` to support the following platforms:

- **LinkedIn** - Professional networking. Handle format: `linkedin.com/in/{username}`. Widely used for professional/brand identity.
- **Bluesky** - Decentralized social network (AT Protocol). Handle format: `bsky.app/profile/{username}.bsky.social` or custom domains. Growing rapidly as a Twitter/X alternative.
- **Mastodon** - Federated social network (ActivityPub). Handle format varies by instance, e.g., `@username@mastodon.social`. Checking would likely need to target one or more popular instances (mastodon.social, mastodon.online, etc.) or accept an instance parameter.
- **Facebook** - Meta's primary social platform. Handle format: `facebook.com/{username}`. Relevant for brand name availability.
- **Pinterest** - Visual discovery and bookmarking platform. Handle format: `pinterest.com/{username}`. Important for brands with visual content.

**Notes and considerations:**
- Some of these platforms (LinkedIn, Facebook) may have stricter bot detection, potentially requiring Playwright-based checking similar to the current Twitter/X approach.
- Mastodon's federated nature means a username could be available on one instance but taken on another. A practical approach might be to check the largest instances (mastodon.social, etc.) and document the limitation.
- Bluesky has a public API that may simplify availability checking compared to scraping.
- Sherlock may already have support for some of these platforms, which would simplify implementation.
