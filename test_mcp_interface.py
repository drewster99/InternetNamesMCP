#!/usr/bin/env python3
"""
Test suite for Internet Names MCP Server via MCP Protocol

This tests the server through its actual MCP interface using stdio transport,
verifying that the MCP layer works correctly in addition to the underlying functions.

Usage:
    source ./devsetup.sh
    python test_mcp_interface.py
"""

import sys

# Check Python version and dependencies early
if sys.version_info < (3, 10):
    print("Error: Python 3.10+ required")
    print()
    print("Set up the development environment:")
    print("    source ./devsetup.sh")
    sys.exit(1)

try:
    import anyio
except ImportError:
    print("Error: anyio not found")
    print()
    print("Set up the development environment first:")
    print("    source ./devsetup.sh")
    sys.exit(1)

try:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
except ImportError as e:
    print(f"Error: {e}")
    print()
    print("Set up the development environment first:")
    print("    source ./devsetup.sh")
    sys.exit(1)

import json
import os
import random
import string
import time
from dataclasses import dataclass, field
from pathlib import Path


def generate_unique_name() -> str:
    """Generate a unique name unlikely to be taken."""
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
    return f"xyztest{suffix}"


@dataclass
class TestResult:
    """Result of a single test."""
    name: str
    passed: bool
    message: str = ""
    duration: float = 0.0


@dataclass
class TestRunner:
    """Runs tests and collects results."""
    results: list[TestResult] = field(default_factory=list)
    current_section: str = ""

    def section(self, name: str):
        """Start a new test section."""
        self.current_section = name
        print(f"\n{'=' * 60}")
        print(f"  {name}")
        print(f"{'=' * 60}")

    def test(self, name: str, condition: bool, message: str = ""):
        """Record a test result."""
        result = TestResult(
            name=f"{self.current_section}: {name}",
            passed=condition,
            message=message
        )
        self.results.append(result)

        if condition:
            print(f"  ✓ {name}")
        else:
            print(f"  ❌ {name}")
            if message:
                print(f"    → {message}")

    def test_json(self, name: str, json_str: str, checks: dict):
        """Test JSON response against expected checks."""
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            self.test(name, False, f"Invalid JSON: {e}")
            return None

        all_passed = True
        messages = []

        for check_name, check_fn in checks.items():
            try:
                if not check_fn(data):
                    all_passed = False
                    messages.append(f"{check_name} failed")
            except Exception as e:
                all_passed = False
                messages.append(f"{check_name} raised {e}")

        self.test(name, all_passed, "; ".join(messages) if messages else "")
        return data

    def summary(self) -> bool:
        """Print summary and return True if all tests passed."""
        passed = sum(1 for r in self.results if r.passed)
        failed = sum(1 for r in self.results if not r.passed)
        total = len(self.results)

        print(f"\n{'=' * 60}")
        print(f"  SUMMARY: {passed}/{total} passed, {failed} failed")
        print(f"{'=' * 60}")

        if failed > 0:
            print("\nFailed tests:")
            for r in self.results:
                if not r.passed:
                    print(f"  ❌ {r.name}")
                    if r.message:
                        print(f"    → {r.message}")

        return failed == 0


def extract_text(result) -> str:
    """Extract text content from MCP CallToolResult."""
    if result.content:
        for content in result.content:
            if hasattr(content, "text"):
                return content.text
    return ""


async def run_mcp_tests(runner: TestRunner, session: ClientSession):
    """Run all tests via MCP interface."""

    # =========================================================================
    # MCP Protocol Tests
    # =========================================================================
    runner.section("MCP Protocol - Tool Discovery")

    # List available tools
    tools_result = await session.list_tools()
    tools = tools_result.tools
    tool_names = [t.name for t in tools]

    runner.test("list_tools returns tools", len(tools) > 0)
    runner.test("get_supported_socials tool exists", "get_supported_socials" in tool_names)
    runner.test("check_domains tool exists", "check_domains" in tool_names)
    runner.test("check_handles tool exists", "check_handles" in tool_names)
    runner.test("check_subreddits tool exists", "check_subreddits" in tool_names)
    runner.test("check_everything tool exists", "check_everything" in tool_names)
    runner.test("exactly 5 tools exposed", len(tools) == 5, f"Found {len(tools)} tools")

    # Check tool schemas
    for tool in tools:
        if tool.name == "check_domains":
            schema = tool.inputSchema
            runner.test(
                "check_domains has names parameter",
                "names" in schema.get("properties", {}),
            )
            runner.test(
                "check_domains has tlds parameter",
                "tlds" in schema.get("properties", {}),
            )
            runner.test(
                "check_domains has method parameter",
                "method" in schema.get("properties", {}),
            )
        if tool.name == "check_everything":
            schema = tool.inputSchema
            runner.test(
                "check_everything has method parameter",
                "method" in schema.get("properties", {}),
            )

    # =========================================================================
    # get_supported_socials
    # =========================================================================
    runner.section("get_supported_socials via MCP")

    result = await session.call_tool("get_supported_socials", {})
    text = extract_text(result)

    data = runner.test_json("returns valid JSON", text, {
        "has platforms": lambda d: "platforms" in d,
        "platforms is list": lambda d: isinstance(d["platforms"], list),
    })

    if data:
        platforms = data["platforms"]
        runner.test("includes instagram", "instagram" in platforms)
        runner.test("includes twitter", "twitter" in platforms)
        runner.test("includes reddit", "reddit" in platforms)
        runner.test("includes youtube", "youtube" in platforms)
        runner.test("includes subreddit", "subreddit" in platforms)

    # =========================================================================
    # check_domains - edge cases
    # =========================================================================
    runner.section("check_domains - edge cases via MCP")

    # Empty list
    result = await session.call_tool("check_domains", {"names": []})
    text = extract_text(result)
    runner.test_json("empty list returns error", text, {
        "has error": lambda d: "error" in d,
    })

    # Invalid method
    result = await session.call_tool("check_domains", {"names": ["test"], "method": "invalid"})
    text = extract_text(result)
    runner.test_json("invalid method returns error", text, {
        "has error": lambda d: "error" in d,
        "error mentions method": lambda d: "method" in d.get("error", "").lower(),
    })

    # Whitespace-only names
    result = await session.call_tool("check_domains", {"names": ["", "   "]})
    text = extract_text(result)
    runner.test_json("whitespace-only names returns error", text, {
        "has error": lambda d: "error" in d,
    })

    # =========================================================================
    # check_handles - edge cases
    # =========================================================================
    runner.section("check_handles - edge cases via MCP")

    # Empty username
    result = await session.call_tool("check_handles", {"username": ""})
    text = extract_text(result)
    runner.test_json("empty username returns error", text, {
        "has error": lambda d: "error" in d,
    })

    # Whitespace username
    result = await session.call_tool("check_handles", {"username": "   "})
    text = extract_text(result)
    runner.test_json("whitespace username returns error", text, {
        "has error": lambda d: "error" in d,
    })

    # Invalid platforms only
    result = await session.call_tool("check_handles", {
        "username": "testuser",
        "platforms": ["invalid", "fake"],
    })
    text = extract_text(result)
    runner.test_json("invalid platforms returns error", text, {
        "has error": lambda d: "error" in d,
    })

    # Mixed valid/invalid platforms - should work with valid ones
    result = await session.call_tool("check_handles", {
        "username": "testuser",
        "platforms": ["instagram", "invalid"],
    })
    text = extract_text(result)
    runner.test_json("mixed platforms uses valid ones", text, {
        "has available key": lambda d: "available" in d,
        "no error": lambda d: "error" not in d,
    })

    # =========================================================================
    # check_subreddits - edge cases
    # =========================================================================
    runner.section("check_subreddits - edge cases via MCP")

    # Empty list
    result = await session.call_tool("check_subreddits", {"names": []})
    text = extract_text(result)
    runner.test_json("empty list returns error", text, {
        "has error": lambda d: "error" in d,
    })

    # =========================================================================
    # check_everything - edge cases
    # =========================================================================
    runner.section("check_everything - edge cases via MCP")

    # Empty components
    result = await session.call_tool("check_everything", {"components": []})
    text = extract_text(result)
    runner.test_json("empty components returns error", text, {
        "has error": lambda d: "error" in d,
    })

    # Whitespace-only components
    result = await session.call_tool("check_everything", {"components": ["", "   "]})
    text = extract_text(result)
    runner.test_json("whitespace components returns error", text, {
        "has error": lambda d: "error" in d,
    })

    # Empty TLDs
    result = await session.call_tool("check_everything", {
        "components": ["test"],
        "tlds": [],
    })
    text = extract_text(result)
    runner.test_json("empty TLDs returns error", text, {
        "has error": lambda d: "error" in d,
    })

    # Invalid platforms only
    result = await session.call_tool("check_everything", {
        "components": ["test"],
        "platforms": ["invalid"],
    })
    text = extract_text(result)
    runner.test_json("invalid platforms returns error", text, {
        "has error": lambda d: "error" in d,
    })


async def run_online_mcp_tests(runner: TestRunner, session: ClientSession):
    """Run tests that require API calls via MCP interface."""

    # Use a randomly generated unique string unlikely to be taken
    unique_name = generate_unique_name()
    print(f"\n  Using unique test name: {unique_name}")

    # =========================================================================
    # check_domains - method="rdap" (default)
    # =========================================================================
    runner.section("check_domains - method=rdap via MCP")

    # Check a known taken domain via RDAP
    result = await session.call_tool("check_domains", {
        "names": ["google"],
        "tlds": ["com"],
        "method": "rdap",
    })
    text = extract_text(result)
    data = runner.test_json("rdap: google.com is unavailable", text, {
        "has available": lambda d: "available" in d,
        "has unavailable": lambda d: "unavailable" in d,
        "google.com in unavailable": lambda d: "google.com" in d["unavailable"],
    })

    # Check likely available domain via RDAP
    result = await session.call_tool("check_domains", {
        "names": [unique_name],
        "tlds": ["com", "net"],
        "method": "rdap",
    })
    text = extract_text(result)
    data = runner.test_json("rdap: unique name returns valid structure", text, {
        "has available": lambda d: "available" in d,
        "available is list": lambda d: isinstance(d["available"], list),
    })

    if data and data.get("available") and len(data["available"]) > 0:
        first = data["available"][0]
        runner.test("rdap: available entry has domain field", "domain" in first)
        # RDAP does not include pricing
        runner.test("rdap: no price field (expected)", "price" not in first)
    else:
        runner.test("(skipped) rdap entry structure", True, "no available domains")

    # =========================================================================
    # check_domains - method="namesilo"
    # =========================================================================
    runner.section("check_domains - method=namesilo via MCP")

    # Check a known taken domain via NameSilo
    result = await session.call_tool("check_domains", {
        "names": ["google"],
        "tlds": ["com"],
        "method": "namesilo",
    })
    text = extract_text(result)
    data = runner.test_json("namesilo: google.com is unavailable", text, {
        "has available": lambda d: "available" in d,
        "has unavailable": lambda d: "unavailable" in d,
        "google.com in unavailable": lambda d: "google.com" in d["unavailable"],
    })

    # Check likely available domain via NameSilo (includes pricing)
    result = await session.call_tool("check_domains", {
        "names": [unique_name],
        "tlds": ["com", "io"],
        "method": "namesilo",
    })
    text = extract_text(result)
    data = runner.test_json("namesilo: unique name returns valid structure", text, {
        "has available": lambda d: "available" in d,
        "available is list": lambda d: isinstance(d["available"], list),
    })

    if data and data.get("available") and len(data["available"]) > 0:
        first = data["available"][0]
        runner.test("namesilo: available entry has domain field", "domain" in first)
        runner.test("namesilo: available entry has price", "price" in first)
    else:
        runner.test("(skipped) namesilo entry structure", True, "no available domains")

    # =========================================================================
    # check_domains - method="auto"
    # =========================================================================
    runner.section("check_domains - method=auto via MCP")

    # Auto should use NameSilo when API key is present (includes pricing)
    result = await session.call_tool("check_domains", {
        "names": [unique_name],
        "tlds": ["com"],
        "method": "auto",
    })
    text = extract_text(result)
    data = runner.test_json("auto: returns valid structure", text, {
        "has available": lambda d: "available" in d,
    })

    if data and data.get("available") and len(data["available"]) > 0:
        first = data["available"][0]
        # With API key configured, auto should use NameSilo and include price
        runner.test("auto: has price (uses namesilo with API key)", "price" in first)
    else:
        runner.test("(skipped) auto entry structure", True, "no available domains")

    # =========================================================================
    # check_domains - additional tests
    # =========================================================================
    runner.section("check_domains - additional tests via MCP")

    # Test onlyReportAvailable
    result = await session.call_tool("check_domains", {
        "names": ["google"],
        "tlds": ["com"],
        "onlyReportAvailable": True,
    })
    text = extract_text(result)
    runner.test_json("onlyReportAvailable omits unavailable", text, {
        "no unavailable key": lambda d: "unavailable" not in d,
    })

    # Test summary - only present when there are available domains
    result = await session.call_tool("check_domains", {
        "names": [unique_name],
        "tlds": ["com", "io", "ai"],
        "method": "namesilo",
    })
    text = extract_text(result)
    data = runner.test_json("response is valid JSON", text, {
        "has available key": lambda d: "available" in d,
    })

    if data:
        has_available = len(data.get("available", [])) > 0
        has_summary = "summary" in data
        # Summary should be present if and only if there are available domains
        runner.test(
            "summary present when domains available",
            has_summary == has_available,
            f"available={has_available}, summary={has_summary}",
        )
        if has_summary:
            summary = data["summary"]
            runner.test("summary has cheapestAvailable", "cheapestAvailable" in summary)
            runner.test("summary has shortestAvailable", "shortestAvailable" in summary)

    # =========================================================================
    # check_handles - real API (Sherlock, no Twitter for speed)
    # =========================================================================
    runner.section("check_handles - API tests via MCP (Sherlock)")

    # Check a known taken handle
    result = await session.call_tool("check_handles", {
        "username": "billgates",
        "platforms": ["instagram", "youtube"],
    })
    text = extract_text(result)
    data = runner.test_json("billgates is taken on major platforms", text, {
        "has available": lambda d: "available" in d,
        "has unavailable": lambda d: "unavailable" in d,
    })

    if data and data.get("unavailable"):
        # Check structure of unavailable entries
        for entry in data["unavailable"]:
            if isinstance(entry, dict) and "platform" in entry:
                runner.test("unavailable entry has platform", True)
                if "url" in entry:
                    runner.test("unavailable entry has url", True)
                break

    # Check likely available handle
    result = await session.call_tool("check_handles", {
        "username": unique_name,
        "platforms": ["instagram", "youtube"],
    })
    text = extract_text(result)
    runner.test_json("unique name is likely available", text, {
        "has available": lambda d: "available" in d,
        "available has entries": lambda d: len(d["available"]) > 0,
    })

    # Test onlyReportAvailable
    result = await session.call_tool("check_handles", {
        "username": "billgates",
        "platforms": ["instagram"],
        "onlyReportAvailable": True,
    })
    text = extract_text(result)
    runner.test_json("onlyReportAvailable omits unavailable", text, {
        "no unavailable key": lambda d: "unavailable" not in d,
    })

    # =========================================================================
    # check_handles - Twitter (slower, separate test)
    # =========================================================================
    runner.section("check_handles - Twitter API test via MCP")

    result = await session.call_tool("check_handles", {
        "username": "elonmusk",
        "platforms": ["twitter"],
    })
    text = extract_text(result)
    data = runner.test_json("elonmusk Twitter check works", text, {
        "has available": lambda d: "available" in d,
        "has unavailable": lambda d: "unavailable" in d,
    })

    if data:
        # elonmusk should be taken
        unavail_platforms = [
            e["platform"] if isinstance(e, dict) else e
            for e in data.get("unavailable", [])
        ]
        runner.test(
            "elonmusk is taken on Twitter",
            "twitter" in unavail_platforms or any(
                isinstance(e, dict) and e.get("platform") == "twitter"
                for e in data.get("unavailable", [])
            ),
        )

    # =========================================================================
    # check_subreddits - real API
    # =========================================================================
    runner.section("check_subreddits - API tests via MCP")

    # Check a known existing subreddit
    result = await session.call_tool("check_subreddits", {"names": ["programming"]})
    text = extract_text(result)
    data = runner.test_json("r/programming exists", text, {
        "has available": lambda d: "available" in d,
        "has unavailable": lambda d: "unavailable" in d,
    })

    if data and data.get("unavailable"):
        # Find programming in unavailable
        prog = None
        for entry in data["unavailable"]:
            if isinstance(entry, dict) and entry.get("name") == "programming":
                prog = entry
                break
        if prog:
            runner.test("programming has subscribers count", "subscribers" in prog)
            runner.test("subscribers is int", isinstance(prog.get("subscribers"), int))

    # Check likely available subreddit
    result = await session.call_tool("check_subreddits", {"names": [unique_name]})
    text = extract_text(result)
    runner.test_json("unique subreddit is available", text, {
        "has available": lambda d: "available" in d,
        "unique in available": lambda d: unique_name in d["available"],
    })

    # Test r/ prefix stripping
    result = await session.call_tool("check_subreddits", {"names": ["r/programming"]})
    text = extract_text(result)
    runner.test_json("r/ prefix is stripped", text, {
        "programming in unavailable": lambda d: any(
            isinstance(e, dict) and e.get("name") == "programming"
            for e in d.get("unavailable", [])
        ),
    })

    # Test onlyReportAvailable
    result = await session.call_tool("check_subreddits", {
        "names": ["programming"],
        "onlyReportAvailable": True,
    })
    text = extract_text(result)
    runner.test_json("onlyReportAvailable omits unavailable", text, {
        "no unavailable key": lambda d: "unavailable" not in d,
    })

    # =========================================================================
    # check_everything - real API
    # =========================================================================
    runner.section("check_everything - API tests via MCP")

    # Use unique components (derived from our random unique_name)
    comp1 = unique_name[:8]
    comp2 = unique_name[8:]
    result = await session.call_tool("check_everything", {
        "components": [comp1, comp2],
        "tlds": ["com", "io"],
        "platforms": ["instagram", "youtube"],  # Skip twitter for speed
    })
    text = extract_text(result)
    data = runner.test_json("check_everything returns correct structure", text, {
        "has availableDomains": lambda d: "availableDomains" in d,
        "has domainSuccessfulBasenames": lambda d: "domainSuccessfulBasenames" in d,
        "has availableHandles": lambda d: "availableHandles" in d,
    })

    if data:
        # Verify structure is correct regardless of availability
        basenames = data.get("domainSuccessfulBasenames", [])
        runner.test("domainSuccessfulBasenames is list", isinstance(basenames, list))

        # If we got basenames, verify they look reasonable
        if basenames:
            runner.test("basenames are strings", all(isinstance(b, str) for b in basenames))
        else:
            # All domains might be taken - that's okay for this test
            runner.test("(skipped) basename content check", True, "no available basenames")

    # Test requireAllTLDsAvailable
    result = await session.call_tool("check_everything", {
        "components": [unique_name],
        "tlds": ["com", "io"],
        "platforms": ["instagram"],
        "requireAllTLDsAvailable": True,
    })
    text = extract_text(result)
    runner.test_json("requireAllTLDsAvailable works", text, {
        "has structure": lambda d: "availableDomains" in d,
    })

    # Test onlyReportAvailable
    result = await session.call_tool("check_everything", {
        "components": [unique_name],
        "tlds": ["com"],
        "platforms": ["instagram"],
        "onlyReportAvailable": True,
    })
    text = extract_text(result)
    runner.test_json("onlyReportAvailable omits unavailableHandles", text, {
        "no unavailableHandles": lambda d: "unavailableHandles" not in d,
    })

    # Test summary generation
    result = await session.call_tool("check_everything", {
        "components": [unique_name],
        "tlds": ["com", "io"],
        "platforms": ["instagram", "youtube"],
    })
    text = extract_text(result)
    data = runner.test_json("check_everything generates summary", text, {
        "has summary": lambda d: "summary" in d or len(d.get("availableDomains", [])) == 0,
    })

    if data and data.get("summary"):
        summary = data["summary"]
        if "cheapestDomain" in summary:
            runner.test(
                "cheapestDomain has domain and price",
                "domain" in summary["cheapestDomain"] and "price" in summary["cheapestDomain"],
            )

    # Test alsoIncludeHyphens - use unique components to ensure availability
    hyphen_comp1 = unique_name[:6]
    hyphen_comp2 = unique_name[6:]
    result = await session.call_tool("check_everything", {
        "components": [hyphen_comp1, hyphen_comp2],
        "tlds": ["com"],
        "platforms": ["instagram"],
        "alsoIncludeHyphens": True,
    })
    text = extract_text(result)
    data = runner.test_json("alsoIncludeHyphens generates hyphenated names", text, {
        "has structure": lambda d: "availableDomains" in d or "domainSuccessfulBasenames" in d,
    })

    if data:
        basenames = data.get("domainSuccessfulBasenames", [])
        # With alsoIncludeHyphens=True, we should have more basenames than without
        # Expected: comp1, comp2, comp1+comp2, comp2+comp1, comp1-comp2, comp2-comp1 = 6
        # But some may be unavailable. Just check for hyphenated ones if any basenames exist
        if basenames:
            has_hyphen = any("-" in b for b in basenames)
            runner.test(
                "hyphenated names in basenames",
                has_hyphen,
                f"basenames={basenames}",
            )
        else:
            # All domains taken - just pass since we can't verify
            runner.test("(skipped) hyphenated names check", True, "no available domains")

    # Test alsoIncludeHyphens=False (default) does NOT include hyphens
    result = await session.call_tool("check_everything", {
        "components": ["abc", "xyz"],
        "tlds": ["com"],
        "platforms": ["instagram"],
        "alsoIncludeHyphens": False,
    })
    text = extract_text(result)
    data = runner.test_json("alsoIncludeHyphens=False excludes hyphenated names", text, {
        "has structure": lambda d: "domainSuccessfulBasenames" in d,
    })

    if data:
        basenames = data.get("domainSuccessfulBasenames", [])
        no_hyphens = not any("-" in b for b in basenames)
        runner.test(
            "no hyphenated basenames when alsoIncludeHyphens=False",
            no_hyphens,
            f"basenames={basenames}",
        )


async def main_async():
    runner = TestRunner()

    print("\n" + "=" * 60)
    print("  INTERNET NAMES MCP SERVER - MCP INTERFACE TEST SUITE")
    print("=" * 60)

    start_time = time.time()

    # Get the path to server.py
    server_path = Path(__file__).parent / "server.py"

    # Set up server parameters
    server_params = StdioServerParameters(
        command=sys.executable,  # Use the same Python that's running this test
        args=[str(server_path)],
        cwd=str(Path(__file__).parent),
    )

    # Connect to the server via stdio (suppress server logs by redirecting to devnull)
    print("\nConnecting to MCP server via stdio...")

    with open(os.devnull, "w") as devnull:
        async with stdio_client(server_params, errlog=devnull) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                # Initialize the session
                init_result = await session.initialize()
                server_version = init_result.serverInfo.version
                print(f"Connected to: {init_result.serverInfo.name} v{server_version}")

                runner.section("MCP Connection")
                runner.test("server initialized", True)
                runner.test(
                    "server name is 'internet-names'",
                    init_result.serverInfo.name == "internet-names",
                    f"Got '{init_result.serverInfo.name}'",
                )
                runner.test(
                    "server version is set",
                    server_version is not None and server_version != "",
                    f"Got '{server_version}'",
                )

                # Run offline tests (edge cases, validation)
                await run_mcp_tests(runner, session)

                # Run online tests (actual API calls)
                await run_online_mcp_tests(runner, session)

    elapsed = time.time() - start_time

    all_passed = runner.summary()

    print(f"\nCompleted in {elapsed:.1f} seconds")

    return all_passed


def main():
    result = anyio.run(main_async)
    sys.exit(0 if result else 1)


if __name__ == "__main__":
    main()
