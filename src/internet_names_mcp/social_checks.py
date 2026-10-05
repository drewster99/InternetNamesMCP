"""
Social media handle and subreddit availability checks.

Each platform is checked with the most authoritative signal that works without
an account:

- Twitter/X: the signup flow's username_available endpoint (knows about taken,
  suspended, reserved and malformed names)
- Instagram: the profile page settles taken names; when no profile is visible,
  the signup form's username validation (driven in a headless browser)
  decides. Instagram throttles that validation by answering "not available"
  for everything, so that answer is cross-checked with a random control name
- Threads: Threads handles are Instagram usernames, so availability follows
  Instagram; the Threads profile page only supplies a URL when one exists
- Bluesky: the bsky.social account server's checkHandleAvailability endpoint
- Reddit users and subreddits: Reddit's own JSON endpoints, requested from
  inside a headless browser session, because Reddit answers non-browser
  clients with a JavaScript challenge page
- TikTok: the statusCode embedded in the profile page's user-detail data
- Twitch: Twitch's web GraphQL user lookup, which includes suspended and
  deleted accounts
- YouTube: the HTTP status of the @handle page
- GitHub: the REST API user lookup (covers users and organizations)
- Snapchat: the HTTP status of the @username page
- Pinterest: the profile page title, or its embedded "User not found" error
- Kick: the public channel API (plus the hyphenated slug Kick gives
  underscore usernames)
- Substack: whether <name>.substack.com serves or redirects (publications,
  not Substack user handles)

A result is only AVAILABLE when the platform positively reported that the name
can be registered (or, where no such signal exists, that no account has it).
Anything ambiguous (block pages, rate limits, unexpected responses) is an
ERROR, never a guess.
"""

import asyncio
import json
import random
import re
import string
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum, StrEnum
from types import TracebackType
from urllib.parse import parse_qs, quote

import httpx
from playwright.async_api import (
    APIRequestContext,
    APIResponse,
    Browser,
    BrowserContext,
    Page,
    Playwright,
    Response,
    async_playwright,
)
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError


class Platform(StrEnum):
    """Social platforms whose handles can be checked."""

    INSTAGRAM = "instagram"
    TWITTER = "twitter"
    REDDIT = "reddit"
    YOUTUBE = "youtube"
    TIKTOK = "tiktok"
    TWITCH = "twitch"
    THREADS = "threads"
    BLUESKY = "bluesky"
    GITHUB = "github"
    SNAPCHAT = "snapchat"
    PINTEREST = "pinterest"
    KICK = "kick"
    SUBSTACK = "substack"


class AvailabilityStatus(Enum):
    """Outcome of a single availability check."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


@dataclass(frozen=True)
class HandleResult:
    """Availability of one handle on one platform."""

    status: AvailabilityStatus
    url: str | None = None
    note: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class SubredditResult:
    """Availability of one subreddit name."""

    name: str
    status: AvailabilityStatus
    subscribers: int | None = None
    note: str | None = None
    error: str | None = None


class ChromiumUnavailableError(Exception):
    """Raised when the headless browser cannot be launched or installed."""


class RedditBlockedError(Exception):
    """Raised when Reddit keeps serving its bot-check page to the browser session."""


# Failures that mean "could not determine", as opposed to bugs in this module.
CHECK_FAILURES = (
    httpx.HTTPError,
    PlaywrightError,
    ChromiumUnavailableError,
    RedditBlockedError,
    ValueError,
)


class _RedditSessionCookies:
    """
    Reddit cookies from the last browser session that cleared Reddit's challenge.

    Kept for the life of the server process so each tool call reuses the cleared
    session, as a normal browser would. Fresh challenges in quick succession make
    Reddit escalate to a CAPTCHA. In memory only; never written to disk.
    """

    def __init__(self) -> None:
        self.cookies: list[dict] = []


_reddit_session_cookies = _RedditSessionCookies()


def _available() -> HandleResult:
    return HandleResult(status=AvailabilityStatus.AVAILABLE)


def _unavailable(url: str | None = None, note: str | None = None) -> HandleResult:
    return HandleResult(status=AvailabilityStatus.UNAVAILABLE, url=url, note=note)


def _failed(error: str) -> HandleResult:
    return HandleResult(status=AvailabilityStatus.ERROR, error=error)


# =============================================================================
# Constants
# =============================================================================

HTTP_TIMEOUT_SECONDS = 15.0
PAGE_LOAD_TIMEOUT_MS = 30_000
PAGE_RESPONSE_TIMEOUT_MS = 15_000
REDDIT_CHALLENGE_ATTEMPTS = 3
SUBREDDIT_REQUEST_INTERVAL_SECONDS = 0.5

# TikTok and YouTube serve reduced or consent-gated pages to unknown agents.
# The headless browser builds its own agent string from its real version instead.
WEB_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)
# Platform messages below are matched in English, so the browser is pinned to it.
BROWSER_LOCALE = "en-US"

X_USERNAME_AVAILABLE_URL = "https://api.x.com/i/users/username_available.json"
X_TAKEN_REASON = "taken"

INSTAGRAM_SIGNUP_URL = "https://www.instagram.com/accounts/emailsignup/"
INSTAGRAM_USERNAME_FIELD_SELECTOR = "input[aria-label='Username']"
INSTAGRAM_GRAPHQL_PATH = "/api/graphql"
INSTAGRAM_VALIDATION_QUERY_NAME = "useCAARegistrationFieldValidationQuery"
INSTAGRAM_VALIDATION_SUCCESS = "SUCCESS"
INSTAGRAM_VALIDATION_ERROR = "VALIDATION_ERROR"
INSTAGRAM_CONTROL_NAME_LENGTH = 24
INSTAGRAM_NO_PROFILE_MARKERS = [
    "Profile isn't available",
    "Sorry, this page isn't available",
    "Sorry, something went wrong",
]
INSTAGRAM_LOGIN_PATH_PREFIXES = ["/accounts/login", "/challenge"]

THREADS_LOGIN_PATH_PREFIXES = ["/login"]

BLUESKY_HANDLE_DOMAIN = "bsky.social"
BLUESKY_CHECK_AVAILABILITY_URL = "https://bsky.social/xrpc/com.atproto.temp.checkHandleAvailability"
BLUESKY_RESOLVE_HANDLE_URL = "https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle"
BLUESKY_RESULT_AVAILABLE = "com.atproto.temp.checkHandleAvailability#resultAvailable"
BLUESKY_RESULT_UNAVAILABLE = "com.atproto.temp.checkHandleAvailability#resultUnavailable"

# Public client ID of the twitch.tv web app; the GQL endpoint rejects requests without one.
TWITCH_GQL_URL = "https://gql.twitch.tv/gql"
TWITCH_WEB_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
TWITCH_USER_QUERY = (
    "query($login: String!) {"
    " anyUser: user(login: $login, lookupType: ALL) { id deletedAt }"
    " activeUser: user(login: $login) { id }"
    " }"
)

TIKTOK_USER_DATA_PATTERN = re.compile(
    r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">(.*?)</script>',
    re.DOTALL,
)
TIKTOK_STATUS_FOUND = 0
TIKTOK_STATUS_NOT_FOUND = 10221
TIKTOK_STATUS_PRIVATE = 10222

REDDIT_BASE_URL = "https://www.reddit.com"
REDDIT_SEARCH_REDIRECT_PATH = "/subreddits/search"
REDDIT_COOKIE_DOMAIN = "reddit.com"
REDDIT_CAPTCHA_MARKER = "Prove your humanity"
REDDIT_BAD_USERNAME_ERROR = "BAD_USERNAME"

GITHUB_USERS_API_URL = "https://api.github.com/users"
GITHUB_ORGANIZATION_TYPE = "Organization"

PINTEREST_NOT_FOUND_MARKERS = ['"httpStatus":404', "User not found."]

KICK_CHANNELS_API_URL = "https://kick.com/api/v2/channels"

SUBSTACK_HOST_SUFFIX = "substack.com"
# Any name must at least be a valid DNS label before it is placed in a hostname.
DNS_LABEL_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


# =============================================================================
# Username format rules
# =============================================================================
# Only for platforms whose lookup just finds nothing for a malformed name, and
# only applied after that "not found", so legacy accounts that predate a rule
# are still reported as taken (with their URL).

@dataclass(frozen=True)
class UsernameRule:
    """A platform's username format, with a human-readable description."""

    pattern: re.Pattern[str]
    description: str

    def allows(self, username: str) -> bool:
        return self.pattern.fullmatch(username) is not None


TWITCH_RULE = UsernameRule(
    re.compile(r"[A-Za-z0-9][A-Za-z0-9_]{3,24}"),
    "4-25 letters, numbers or underscores, not starting with an underscore",
)
TIKTOK_RULE = UsernameRule(
    re.compile(r"[A-Za-z0-9_.]{2,24}"),
    "2-24 letters, numbers, underscores or periods",
)
# YouTube handles may use letters and digits from many scripts, hence \w rather than ASCII.
YOUTUBE_RULE = UsernameRule(
    re.compile(r"[\w.-]{3,30}"),
    "3-30 letters, numbers, underscores, hyphens or periods",
)
GITHUB_RULE = UsernameRule(
    re.compile(r"(?=.{1,39}\Z)[A-Za-z0-9](?:-?[A-Za-z0-9])*"),
    "up to 39 letters, numbers or single hyphens, not starting or ending with a hyphen",
)
SNAPCHAT_RULE = UsernameRule(
    re.compile(r"[A-Za-z][A-Za-z0-9._-]{1,13}[A-Za-z0-9]"),
    "3-15 letters, numbers, hyphens, underscores or periods, starting with a letter and ending with a letter or number",
)
PINTEREST_RULE = UsernameRule(
    re.compile(r"(?=.*[A-Za-z_])[A-Za-z0-9_]{3,30}"),
    "3-30 letters, numbers or underscores, not only numbers",
)
# Kick does not publish length limits, so only the character set is enforced.
KICK_RULE = UsernameRule(
    re.compile(r"[A-Za-z0-9_-]+"),
    "letters, numbers, underscores or hyphens",
)
SUBSTACK_RULE = UsernameRule(
    re.compile(r"[A-Za-z0-9]{4,32}"),
    "4-32 letters or numbers",
)
SUBREDDIT_RULE = UsernameRule(
    re.compile(r"[A-Za-z0-9][A-Za-z0-9_]{2,20}"),
    "3-21 letters, numbers or underscores, not starting with an underscore",
)


def _available_if_valid(username: str, rule: UsernameRule, platform_label: str) -> HandleResult:
    if rule.allows(username):
        return _available()
    return _unavailable(note=f"Not a valid {platform_label} username ({rule.description})")


def _path_segment(value: str) -> str:
    return quote(value, safe="")


# =============================================================================
# Browser support
# =============================================================================

async def _install_chromium() -> None:
    """Install Playwright's Chromium build, raising ChromiumUnavailableError on failure."""
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "playwright", "install", "chromium",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as e:
        raise ChromiumUnavailableError(f"Could not run Playwright installer: {e}") from e
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        output = (stderr.decode() or stdout.decode()).strip()
        raise ChromiumUnavailableError(f"Chromium install failed: {output[:200]}")


def _desktop_user_agent(browser_version: str) -> str:
    # Headless Chromium advertises "HeadlessChrome", which several platforms block outright.
    major_version = browser_version.split(".", 1)[0]
    return (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{major_version}.0.0.0 Safari/537.36"
    )


class PageOutcome(Enum):
    """What a rendered profile page turned out to show."""

    PROFILE = "profile"
    NO_PROFILE = "no_profile"
    LOGIN_REDIRECT = "login_redirect"


# Polled inside the page until it shows the profile, a no-profile message, or redirects to login.
# Profile titles look like "Name (@handle) • ..." or, without a display name, "@handle • ...".
_PAGE_OUTCOME_SCRIPT = """
({handle, noProfileMarkers, loginPathPrefixes}) => {
    const title = document.title.toLowerCase();
    if (title.includes(`(@${handle})`) || title.startsWith(`@${handle} `)) return "profile";
    if (loginPathPrefixes.some(prefix => location.pathname.startsWith(prefix))) return "login_redirect";
    const text = (document.body ? document.body.innerText : "").replace(/\u2019/g, "'");
    if (noProfileMarkers.some(marker => text.includes(marker))) return "no_profile";
    return null;
}
"""


class InstagramVerdict(Enum):
    """How Instagram's signup form judged a username."""

    ACCEPTED = "accepted"
    NOT_AVAILABLE = "not_available"
    REJECTED = "rejected"  # malformed or otherwise refused, with Instagram's explanation


@dataclass(frozen=True)
class InstagramValidation:
    verdict: InstagramVerdict
    message: str | None = None


def _is_instagram_validation_response(response: Response, username: str) -> bool:
    """True for the signup form's validation reply about exactly this username."""
    if not response.url.endswith(INSTAGRAM_GRAPHQL_PATH):
        return False
    post_data = response.request.post_data or ""
    if INSTAGRAM_VALIDATION_QUERY_NAME not in post_data:
        return False
    variables_values = parse_qs(post_data).get("variables")
    if not variables_values:
        return False
    try:
        variables = json.loads(variables_values[0])
        checked = variables["input"]["username"]["sensitive_string_value"]
    except (ValueError, KeyError, TypeError):
        return False
    return isinstance(checked, str) and checked.lower() == username.lower()


# =============================================================================
# Checker session
# =============================================================================

class SocialChecker:
    """
    One session of handle and subreddit checks.

    Shares a single HTTP client and a single lazily-launched headless browser
    across every check in the session. Use as an async context manager so the
    browser is always shut down.
    """

    def __init__(self) -> None:
        self._http = httpx.AsyncClient(
            timeout=HTTP_TIMEOUT_SECONDS,
            headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._browser_context: BrowserContext | None = None
        self._browser_lock = asyncio.Lock()
        self._reddit_request: APIRequestContext | None = None
        self._reddit_lock = asyncio.Lock()
        # One signup form serves every Instagram check, so checks take turns on it.
        self._instagram_signup_page: Page | None = None
        self._instagram_lock = asyncio.Lock()
        self._instagram_results: dict[str, HandleResult] = {}

    async def __aenter__(self) -> "SocialChecker":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        """Shut down the browser (if launched) and the HTTP client."""
        try:
            if self._browser is not None:
                await self._browser.close()
            if self._playwright is not None:
                await self._playwright.stop()
        finally:
            self._browser = None
            self._playwright = None
            self._browser_context = None
            self._reddit_request = None
            self._instagram_signup_page = None
            await self._http.aclose()

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    async def check_handles(self, username: str, platforms: list[Platform]) -> dict[Platform, HandleResult]:
        """Check one username on each requested platform concurrently."""
        checks = [self._guarded(platform, self._checker_for(platform), username) for platform in platforms]
        return dict(zip(platforms, await asyncio.gather(*checks)))

    async def check_subreddits(self, names: list[str]) -> list[SubredditResult]:
        """Check subreddit names one at a time, pacing requests to stay under Reddit's limits."""
        results: list[SubredditResult] = []
        for index, name in enumerate(names):
            if index > 0:
                await asyncio.sleep(SUBREDDIT_REQUEST_INTERVAL_SECONDS)
            try:
                results.append(await self._check_subreddit(name))
            except CHECK_FAILURES as e:
                results.append(SubredditResult(name=name, status=AvailabilityStatus.ERROR, error=_describe(e)))
        return results

    # -------------------------------------------------------------------------
    # Dispatch and error containment
    # -------------------------------------------------------------------------

    def _checker_for(self, platform: Platform) -> Callable[[str], Awaitable[HandleResult]]:
        checkers: dict[Platform, Callable[[str], Awaitable[HandleResult]]] = {
            Platform.INSTAGRAM: self._check_instagram,
            Platform.TWITTER: self._check_twitter,
            Platform.REDDIT: self._check_reddit_user,
            Platform.YOUTUBE: self._check_youtube,
            Platform.TIKTOK: self._check_tiktok,
            Platform.TWITCH: self._check_twitch,
            Platform.THREADS: self._check_threads,
            Platform.BLUESKY: self._check_bluesky,
            Platform.GITHUB: self._check_github,
            Platform.SNAPCHAT: self._check_snapchat,
            Platform.PINTEREST: self._check_pinterest,
            Platform.KICK: self._check_kick,
            Platform.SUBSTACK: self._check_substack,
        }
        return checkers[platform]

    async def _guarded(
        self,
        platform: Platform,
        check: Callable[[str], Awaitable[HandleResult]],
        username: str,
    ) -> HandleResult:
        """Run a check, converting network, browser and parse failures into ERROR results."""
        try:
            return await check(username)
        except PlaywrightTimeoutError:
            return _failed(f"Timed out waiting for {platform.value} to respond")
        except CHECK_FAILURES as e:
            return _failed(_describe(e))

    # -------------------------------------------------------------------------
    # Browser session management
    # -------------------------------------------------------------------------

    async def _get_browser_context(self) -> BrowserContext:
        async with self._browser_lock:
            if self._browser_context is None:
                self._browser_context = await self._launch_browser_context()
            return self._browser_context

    async def _launch_browser_context(self) -> BrowserContext:
        if self._playwright is None:
            self._playwright = await async_playwright().start()
        try:
            browser = await self._playwright.chromium.launch(headless=True)
        except PlaywrightError as e:
            if "Executable doesn't exist" not in str(e):
                raise ChromiumUnavailableError(f"Chromium failed to launch: {_describe(e)}") from e
            await _install_chromium()
            browser = await self._playwright.chromium.launch(headless=True)
        self._browser = browser
        return await browser.new_context(
            user_agent=_desktop_user_agent(browser.version),
            locale=BROWSER_LOCALE,
        )

    async def _get_reddit_request(self) -> APIRequestContext:
        """
        Return a request context that Reddit accepts.

        Reddit answers fresh clients with a page whose script computes a token and
        resubmits the request; once the browser has run it, the context's cookies
        let JSON endpoints through. Cookies from an earlier cleared session in this
        process are tried first.
        """
        async with self._reddit_lock:
            if self._reddit_request is not None:
                return self._reddit_request
            context = await self._get_browser_context()
            if _reddit_session_cookies.cookies:
                await context.add_cookies(_reddit_session_cookies.cookies)
                if await self._reddit_accepts(context):
                    self._reddit_request = context.request
                    return self._reddit_request
            page = await context.new_page()
            try:
                for _ in range(REDDIT_CHALLENGE_ATTEMPTS):
                    await page.goto(f"{REDDIT_BASE_URL}/", wait_until="load", timeout=PAGE_LOAD_TIMEOUT_MS)
                    if await self._reddit_accepts(context):
                        _reddit_session_cookies.cookies = [
                            cookie for cookie in await context.cookies()
                            if cookie["domain"].endswith(REDDIT_COOKIE_DOMAIN)
                        ]
                        self._reddit_request = context.request
                        return self._reddit_request
                    if REDDIT_CAPTCHA_MARKER in await page.inner_text("body"):
                        raise RedditBlockedError(
                            "Reddit is asking this network to solve a CAPTCHA (too many recent checks); "
                            "try again later"
                        )
            finally:
                await page.close()
            raise RedditBlockedError("Reddit kept serving its bot-check page; try again later")

    async def _reddit_accepts(self, context: BrowserContext) -> bool:
        probe = await context.request.get(
            f"{REDDIT_BASE_URL}/api/username_available.json",
            params={"user": "reddit"},
            max_redirects=0,
        )
        return _is_json(probe)

    async def _reddit_get(self, url: str, params: dict[str, str] | None = None) -> APIResponse:
        """
        GET from Reddit inside the challenge-cleared session.

        Reddit can start blocking a session part-way through, so a blocked reply
        triggers one fresh pass of the challenge and one retry.
        """
        response = await (await self._get_reddit_request()).get(url, params=params, max_redirects=0)
        if _is_json(response) or 300 <= response.status < 400:
            return response
        async with self._reddit_lock:
            self._reddit_request = None
            _reddit_session_cookies.cookies = []
        return await (await self._get_reddit_request()).get(url, params=params, max_redirects=0)

    async def _get_instagram_signup_page(self) -> Page:
        """Return the open signup form. Caller must hold _instagram_lock."""
        if self._instagram_signup_page is None:
            context = await self._get_browser_context()
            page = await context.new_page()
            await page.goto(INSTAGRAM_SIGNUP_URL, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT_MS)
            await page.locator(INSTAGRAM_USERNAME_FIELD_SELECTOR).wait_for(timeout=PAGE_RESPONSE_TIMEOUT_MS)
            self._instagram_signup_page = page
        return self._instagram_signup_page

    # -------------------------------------------------------------------------
    # Platform checks
    # -------------------------------------------------------------------------

    async def _check_twitter(self, username: str) -> HandleResult:
        response = await self._http.get(X_USERNAME_AVAILABLE_URL, params={"username": username})
        if response.status_code != 200:
            return _failed(f"X returned HTTP {response.status_code}")
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("valid"), bool):
            return _failed(f"Unexpected X response: {response.text[:100]}")
        if payload["valid"]:
            return _available()
        if payload.get("reason") == X_TAKEN_REASON:
            return _unavailable(url=f"https://x.com/{_path_segment(username)}")
        # Malformed, reserved, or banned-word names can never be registered.
        return _unavailable(note=payload.get("desc") or payload.get("msg") or payload.get("reason"))

    async def _check_instagram(self, username: str) -> HandleResult:
        async with self._instagram_lock:
            # Cached (errors included) because Threads asks for the same verdict, and
            # repeating a failed check would spend more of Instagram's throttle budget.
            cached = self._instagram_results.get(username)
            if cached is not None:
                return cached
            try:
                result = await self._judge_instagram_username(username)
            except PlaywrightTimeoutError:
                result = _failed(f"Timed out waiting for {Platform.INSTAGRAM.value} to respond")
            except CHECK_FAILURES as e:
                result = _failed(_describe(e))
            self._instagram_results[username] = result
            return result

    async def _judge_instagram_username(self, username: str) -> HandleResult:
        """Decide Instagram availability. Caller must hold _instagram_lock."""
        # The profile page settles taken names without spending the signup check,
        # which Instagram throttles much sooner.
        url = f"https://www.instagram.com/{_path_segment(username)}/"
        outcome = await self._load_profile_page(
            url, username, INSTAGRAM_NO_PROFILE_MARKERS, INSTAGRAM_LOGIN_PATH_PREFIXES
        )
        if outcome == PageOutcome.PROFILE:
            return _unavailable(url=url)
        if outcome == PageOutcome.LOGIN_REDIRECT:
            return _failed("Instagram redirected to its login page (likely rate-limited); try again later")

        # No visible profile: the name is free, malformed, reserved, or held by a removed account.
        validation = await self._validate_instagram_username(username)
        if validation.verdict == InstagramVerdict.ACCEPTED:
            return _available()

        # Any refusal with no profile is also how Instagram answers every name while
        # throttling. A random control name tells the two apart.
        control_name = "".join(random.choices(string.ascii_lowercase, k=INSTAGRAM_CONTROL_NAME_LENGTH))
        control = await self._validate_instagram_username(control_name)
        if control.verdict != InstagramVerdict.ACCEPTED:
            return _failed(
                "No Instagram profile exists, but Instagram is throttling signup checks (it refused a random "
                "control name), so availability is unconfirmed; try again later"
            )
        if validation.verdict == InstagramVerdict.REJECTED:
            return _unavailable(note=validation.message)
        return _unavailable(note="Reserved by Instagram or held by a removed or deactivated account")

    async def _validate_instagram_username(self, username: str) -> InstagramValidation:
        """Enter a username into the signup form and read Instagram's validation reply. Caller must hold _instagram_lock."""
        page = await self._get_instagram_signup_page()
        field = page.locator(INSTAGRAM_USERNAME_FIELD_SELECTOR)
        async with page.expect_response(
            lambda response: _is_instagram_validation_response(response, username),
            timeout=PAGE_RESPONSE_TIMEOUT_MS,
        ) as response_info:
            # Clearing first guarantees a change event even when re-checking the previous name.
            await field.fill("")
            await field.fill(username)
            await field.press("Tab")
        response = await response_info.value
        return _interpret_instagram_validation(username, await response.json())

    async def _check_threads(self, username: str) -> HandleResult:
        instagram = await self._check_instagram(username)
        if instagram.status == AvailabilityStatus.ERROR:
            return _failed(f"Threads availability depends on Instagram, whose check failed: {instagram.error}")
        if instagram.status == AvailabilityStatus.AVAILABLE:
            return _available()
        reason = instagram.note or f"held by the Instagram account {instagram.url}"
        url = f"https://www.threads.com/@{_path_segment(username)}"
        # Instagram already settled availability; the Threads page only tells us whether to link a profile.
        try:
            # Threads sends visitors to its login page when no public profile exists.
            outcome = await self._load_profile_page(url, username, [], THREADS_LOGIN_PATH_PREFIXES)
        except PlaywrightError as e:
            return _unavailable(note=f"Threads handles are Instagram usernames: {reason} "
                                     f"(Threads profile lookup failed: {_describe(e)})")
        if outcome == PageOutcome.PROFILE:
            return _unavailable(url=url)
        return _unavailable(note=f"No Threads profile, but Threads handles are Instagram usernames: {reason}")

    async def _load_profile_page(
        self,
        url: str,
        handle: str,
        no_profile_markers: list[str],
        login_path_prefixes: list[str],
    ) -> PageOutcome:
        context = await self._get_browser_context()
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT_MS)
            outcome = await page.wait_for_function(
                _PAGE_OUTCOME_SCRIPT,
                arg={
                    "handle": handle.lower(),
                    "noProfileMarkers": no_profile_markers,
                    "loginPathPrefixes": login_path_prefixes,
                },
                timeout=PAGE_RESPONSE_TIMEOUT_MS,
            )
            return PageOutcome(await outcome.json_value())
        finally:
            await page.close()

    async def _check_bluesky(self, username: str) -> HandleResult:
        handle = f"{username.lower()}.{BLUESKY_HANDLE_DOMAIN}"
        response = await self._http.get(BLUESKY_CHECK_AVAILABILITY_URL, params={"handle": handle})
        if response.status_code == 400:
            payload = response.json()
            message = payload.get("message") if isinstance(payload, dict) else None
            if not isinstance(message, str):
                return _failed(f"Unexpected Bluesky response: {response.text[:100]}")
            return _unavailable(note=f"Not a valid Bluesky handle: {message}")
        if response.status_code != 200:
            return _failed(f"Bluesky returned HTTP {response.status_code}")
        payload = response.json()
        result = payload.get("result") if isinstance(payload, dict) else None
        result_type = result.get("$type") if isinstance(result, dict) else None
        if result_type == BLUESKY_RESULT_AVAILABLE:
            return _available()
        if result_type != BLUESKY_RESULT_UNAVAILABLE:
            return _failed(f"Unexpected Bluesky response: {response.text[:100]}")
        # Unavailable covers existing accounts and reserved names; only the former have a profile.
        resolved = await self._http.get(BLUESKY_RESOLVE_HANDLE_URL, params={"handle": handle})
        if resolved.status_code == 200:
            return _unavailable(url=f"https://bsky.app/profile/{handle}")
        if resolved.status_code == 400:
            return _unavailable(note="Reserved or not allowed by Bluesky")
        return _unavailable()

    async def _check_github(self, username: str) -> HandleResult:
        response = await self._http.get(
            f"{GITHUB_USERS_API_URL}/{_path_segment(username)}",
            headers={"Accept": "application/vnd.github+json"},
        )
        if response.status_code == 404:
            return _available_if_valid(username, GITHUB_RULE, "GitHub")
        if response.status_code in (403, 429) and response.headers.get("x-ratelimit-remaining") == "0":
            return _failed("GitHub API rate limit (60 lookups per hour without a token) reached; try again later")
        if response.status_code != 200:
            return _failed(f"GitHub returned HTTP {response.status_code}")
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("html_url"), str):
            return _failed(f"Unexpected GitHub response: {response.text[:100]}")
        note = "organization" if payload.get("type") == GITHUB_ORGANIZATION_TYPE else None
        return _unavailable(url=payload["html_url"], note=note)

    async def _check_snapchat(self, username: str) -> HandleResult:
        # Snapchat redirects mixed-case names to the lowercase URL.
        handle = username.lower()
        url = f"https://www.snapchat.com/@{_path_segment(handle)}"
        response = await self._http.get(url, headers={"User-Agent": WEB_USER_AGENT})
        if response.status_code == 404:
            return _available_if_valid(username, SNAPCHAT_RULE, "Snapchat")
        if response.status_code != 200:
            return _failed(f"Snapchat returned HTTP {response.status_code}")
        if f"(@{handle})" not in _page_title(response.text).lower():
            return _failed("Snapchat page did not show the profile")
        return _unavailable(url=url)

    async def _check_pinterest(self, username: str) -> HandleResult:
        url = f"https://www.pinterest.com/{_path_segment(username.lower())}/"
        response = await self._http.get(url, headers={"User-Agent": WEB_USER_AGENT})
        if response.status_code != 200:
            return _failed(f"Pinterest returned HTTP {response.status_code}")
        # Profile titles look like "Name (username) - Profile | Pinterest".
        if f"({username.lower()})" in _page_title(response.text).lower():
            return _unavailable(url=url)
        if all(marker in response.text for marker in PINTEREST_NOT_FOUND_MARKERS):
            return _available_if_valid(username, PINTEREST_RULE, "Pinterest")
        return _failed("Pinterest page showed neither a profile nor a not-found error")

    async def _check_kick(self, username: str) -> HandleResult:
        channel = await self._find_kick_channel(username.lower())
        if channel is None and "_" in username:
            # Kick has given some underscore usernames a hyphenated slug (user Adin_ross is
            # at /adin-ross), but a different channel may own that slug, so the owner must match.
            hyphenated = await self._find_kick_channel(username.lower().replace("_", "-"))
            owner = hyphenated.get("user") if hyphenated is not None else None
            owner_name = owner.get("username") if isinstance(owner, dict) else None
            if isinstance(owner_name, str) and owner_name.lower() == username.lower():
                channel = hyphenated
        if channel is None:
            return _available_if_valid(username, KICK_RULE, "Kick")
        slug = channel.get("slug")
        if not isinstance(slug, str):
            return _failed(f"Unexpected Kick response: {json.dumps(channel)[:100]}")
        note = "banned" if channel.get("is_banned") is True else None
        return _unavailable(url=f"https://kick.com/{_path_segment(slug)}", note=note)

    async def _find_kick_channel(self, slug: str) -> dict | None:
        """Return Kick's channel record for a slug, or None if there is no such channel."""
        response = await self._http.get(
            f"{KICK_CHANNELS_API_URL}/{_path_segment(slug)}",
            headers={"User-Agent": WEB_USER_AGENT},
        )
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise ValueError(f"Kick returned HTTP {response.status_code}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError(f"Unexpected Kick response: {response.text[:100]}")
        return payload

    async def _check_substack(self, username: str) -> HandleResult:
        subdomain = username.lower()
        if DNS_LABEL_PATTERN.fullmatch(subdomain) is None:
            return _unavailable(note=f"Not a valid Substack subdomain ({SUBSTACK_RULE.description})")
        url = f"https://{subdomain}.{SUBSTACK_HOST_SUFFIX}/"
        response = await self._http.get(url, headers={"User-Agent": WEB_USER_AGENT})
        if response.status_code == 404:
            return _available_if_valid(username, SUBSTACK_RULE, "Substack")
        if response.status_code == 200:
            return _unavailable(url=url)
        if 300 <= response.status_code < 400:
            # Subdomains held by a writer's profile or reserved by Substack redirect elsewhere.
            return _unavailable(note=f"{url} redirects to {response.headers.get('location', 'another page')}")
        return _failed(f"Substack returned HTTP {response.status_code}")

    async def _check_reddit_user(self, username: str) -> HandleResult:
        response = await self._reddit_get(f"{REDDIT_BASE_URL}/api/username_available.json", {"user": username})
        if not _is_json(response):
            return _failed(f"Reddit returned HTTP {response.status} without data (likely rate-limited)")
        payload = json.loads(await response.text())
        if payload is True:
            return _available()
        if payload is False:
            # Reddit never releases a username, including those of deleted or banned accounts.
            return _unavailable(url=f"{REDDIT_BASE_URL}/user/{_path_segment(username)}")
        if _has_reddit_error(payload, REDDIT_BAD_USERNAME_ERROR):
            return _unavailable(note="Not a valid Reddit username (3-20 letters, numbers, underscores or hyphens)")
        return _failed(f"Unexpected Reddit response: {json.dumps(payload)[:100]}")

    async def _check_youtube(self, username: str) -> HandleResult:
        url = f"https://www.youtube.com/@{_path_segment(username)}"
        response = await self._http.get(url, headers={"User-Agent": WEB_USER_AGENT})
        if response.status_code == 200:
            return _unavailable(url=url)
        if response.status_code == 404:
            return _available_if_valid(username, YOUTUBE_RULE, "YouTube")
        location = response.headers.get("location")
        detail = f" (redirect to {location})" if location else ""
        return _failed(f"YouTube returned HTTP {response.status_code}{detail}")

    async def _check_tiktok(self, username: str) -> HandleResult:
        url = f"https://www.tiktok.com/@{_path_segment(username)}"
        response = await self._http.get(url, headers={"User-Agent": WEB_USER_AGENT})
        if response.status_code != 200:
            return _failed(f"TikTok returned HTTP {response.status_code}")
        match = TIKTOK_USER_DATA_PATTERN.search(response.text)
        if match is None:
            return _failed("TikTok page did not include user data")
        page_data = json.loads(match.group(1))
        scope = page_data.get("__DEFAULT_SCOPE__") if isinstance(page_data, dict) else None
        user_detail = scope.get("webapp.user-detail") if isinstance(scope, dict) else None
        if not isinstance(user_detail, dict) or not isinstance(user_detail.get("statusCode"), int):
            return _failed("TikTok page did not include a user status")
        status_code = user_detail["statusCode"]
        if status_code == TIKTOK_STATUS_FOUND:
            return _unavailable(url=url)
        if status_code == TIKTOK_STATUS_PRIVATE:
            return _unavailable(url=url, note="private account")
        if status_code == TIKTOK_STATUS_NOT_FOUND:
            return _available_if_valid(username, TIKTOK_RULE, "TikTok")
        return _failed(f"TikTok returned user status {status_code}")

    async def _check_twitch(self, username: str) -> HandleResult:
        response = await self._http.post(
            TWITCH_GQL_URL,
            headers={"Client-Id": TWITCH_WEB_CLIENT_ID},
            json={"query": TWITCH_USER_QUERY, "variables": {"login": username}},
        )
        if response.status_code != 200:
            return _failed(f"Twitch returned HTTP {response.status_code}")
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("errors") or not isinstance(payload.get("data"), dict):
            return _failed(f"Unexpected Twitch response: {response.text[:100]}")
        data = payload["data"]
        any_user = data.get("anyUser")
        if any_user is None:
            return _available_if_valid(username, TWITCH_RULE, "Twitch")
        if not isinstance(any_user, dict):
            return _failed(f"Unexpected Twitch response: {response.text[:100]}")
        url = f"https://www.twitch.tv/{_path_segment(username.lower())}"
        if any_user.get("deletedAt"):
            return _unavailable(url=url, note="deleted account")
        if data.get("activeUser") is None:
            return _unavailable(url=url, note="suspended account")
        return _unavailable(url=url)

    async def _check_subreddit(self, name: str) -> SubredditResult:
        response = await self._reddit_get(f"{REDDIT_BASE_URL}/r/{_path_segment(name)}/about.json")
        if 300 <= response.status < 400:
            # Reddit redirects lookups of nonexistent subreddits to its search page.
            location = response.headers.get("location", "")
            if REDDIT_SEARCH_REDIRECT_PATH not in location:
                return SubredditResult(name=name, status=AvailabilityStatus.ERROR,
                                       error=f"Unexpected Reddit redirect to {location}")
            if SUBREDDIT_RULE.allows(name):
                return SubredditResult(name=name, status=AvailabilityStatus.AVAILABLE)
            return SubredditResult(name=name, status=AvailabilityStatus.UNAVAILABLE,
                                   note=f"Not a valid subreddit name ({SUBREDDIT_RULE.description})")
        if not _is_json(response):
            return SubredditResult(name=name, status=AvailabilityStatus.ERROR,
                                   error=f"Reddit returned HTTP {response.status} without data (likely rate-limited)")
        payload = json.loads(await response.text())
        if not isinstance(payload, dict):
            return SubredditResult(name=name, status=AvailabilityStatus.ERROR,
                                   error=f"Unexpected Reddit response: {json.dumps(payload)[:100]}")
        if response.status == 200 and payload.get("kind") == "t5":
            data = payload.get("data")
            subscribers = data.get("subscribers") if isinstance(data, dict) else None
            return SubredditResult(name=name, status=AvailabilityStatus.UNAVAILABLE,
                                   subscribers=subscribers if isinstance(subscribers, int) else None)
        reason = payload.get("reason")
        if response.status in (403, 404) and isinstance(reason, str):
            # private, gold_only, quarantined, banned, ... all mean the name is held.
            return SubredditResult(name=name, status=AvailabilityStatus.UNAVAILABLE, note=reason)
        if response.status == 404 and not SUBREDDIT_RULE.allows(name):
            return SubredditResult(name=name, status=AvailabilityStatus.UNAVAILABLE,
                                   note=f"Not a valid subreddit name ({SUBREDDIT_RULE.description})")
        return SubredditResult(name=name, status=AvailabilityStatus.ERROR,
                               error=f"Unexpected Reddit response (HTTP {response.status}): {json.dumps(payload)[:100]}")


# =============================================================================
# Helpers
# =============================================================================

def _interpret_instagram_validation(username: str, payload: object) -> InstagramValidation:
    data = payload.get("data") if isinstance(payload, dict) else None
    validation = data.get("xfb_caa_registration_field_validation") if isinstance(data, dict) else None
    if not isinstance(validation, dict):
        raise ValueError(f"Unexpected Instagram response: {json.dumps(payload)[:100]}")
    status = validation.get("status")
    if status == INSTAGRAM_VALIDATION_SUCCESS:
        return InstagramValidation(InstagramVerdict.ACCEPTED)
    if status != INSTAGRAM_VALIDATION_ERROR:
        raise ValueError(f"Unexpected Instagram validation status: {status}")
    error = validation.get("error")
    message = error.get("message") if isinstance(error, dict) else None
    if not isinstance(message, str) or not message:
        raise ValueError(f"Instagram rejected the username without a reason: {json.dumps(validation)[:100]}")
    if message.lower() == f"the username {username.lower()} is not available.":
        return InstagramValidation(InstagramVerdict.NOT_AVAILABLE, message)
    return InstagramValidation(InstagramVerdict.REJECTED, message)


def _page_title(html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL)
    return match.group(1) if match else ""


def _is_json(response: APIResponse) -> bool:
    return "application/json" in response.headers.get("content-type", "")


def _has_reddit_error(payload: object, error_code: str) -> bool:
    if not isinstance(payload, dict) or not isinstance(payload.get("json"), dict):
        return False
    errors = payload["json"].get("errors")
    if not isinstance(errors, list):
        return False
    return any(isinstance(error, list) and error and error[0] == error_code for error in errors)


def _describe(error: BaseException) -> str:
    text = str(error).strip()
    message = text.splitlines()[0] if text else type(error).__name__
    return message[:200]
