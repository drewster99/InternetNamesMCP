#!/usr/bin/env python3
"""
Publish script for internet-names-mcp.

Usage:
    python publish.py           # Auto-increment patch version (0.1.0 -> 0.1.1)
    python publish.py minor     # Increment minor version (0.1.0 -> 0.2.0)
    python publish.py major     # Increment major version (0.1.0 -> 1.0.0)
    python publish.py 0.2.0     # Set specific version
"""

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent

# The single source of truth for the package version. pyproject.toml declares
# the version as dynamic and hatchling reads it from this file at build time;
# server.py imports it from the package.
VERSION_FILE_PATH = REPO_ROOT / "src" / "internet_names_mcp" / "__init__.py"
VERSION_LINE_PATTERN = r'^__version__\s*=\s*"([^"]+)"'

# Only canonical MAJOR.MINOR.PATCH (no leading zeros, signs or underscores), so the
# string written to __version__ is exactly what hatchling puts in the artifact names
# and on PyPI. Hatchling would otherwise normalize e.g. "0.1.011" to "0.1.11".
CANONICAL_VERSION_PATTERN = r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"


def get_current_version() -> str:
    """Read the current version from the package's __version__."""
    content = VERSION_FILE_PATH.read_text()
    matches = re.findall(VERSION_LINE_PATTERN, content, re.MULTILINE)
    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one __version__ assignment in {VERSION_FILE_PATH}, found {len(matches)}"
        )
    return matches[0]


def parse_version(version: str) -> tuple[int, int, int]:
    """Parse a canonical MAJOR.MINOR.PATCH version string into (major, minor, patch)."""
    match = re.fullmatch(CANONICAL_VERSION_PATTERN, version)
    if not match:
        raise ValueError(f"Invalid version format (expected MAJOR.MINOR.PATCH): {version}")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def increment_version(current: str, bump: str) -> str:
    """Increment version based on bump type."""
    major, minor, patch = parse_version(current)

    if bump == "major":
        return f"{major + 1}.0.0"
    elif bump == "minor":
        return f"{major}.{minor + 1}.0"
    elif bump == "patch":
        return f"{major}.{minor}.{patch + 1}"
    else:
        # Assume it's a specific version
        parse_version(bump)  # Validate format
        return bump


def update_version(new_version: str) -> None:
    """Write the new version to the single version source."""
    content = VERSION_FILE_PATH.read_text()
    new_content, count = re.subn(
        VERSION_LINE_PATTERN,
        f'__version__ = "{new_version}"',
        content,
        flags=re.MULTILINE,
    )
    if count != 1:
        raise ValueError(
            f"Expected to replace exactly one __version__ assignment in {VERSION_FILE_PATH}, replaced {count}"
        )
    VERSION_FILE_PATH.write_text(new_content)


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run a command from the repository root and return the result."""
    print(f"  $ {' '.join(cmd)}")
    # Build output, the dist/* upload glob and git all assume the repository root,
    # regardless of the directory publish.py was invoked from.
    return subprocess.run(cmd, check=check, capture_output=True, text=True, cwd=REPO_ROOT)


def check_dependencies() -> bool:
    """Check that build and twine are installed."""
    missing = []

    try:
        import build  # noqa: F401
    except ImportError:
        missing.append("build")

    try:
        import twine  # noqa: F401
    except ImportError:
        missing.append("twine")

    if missing:
        print(f"Error: Missing required packages: {', '.join(missing)}")
        print()
        print("Install dev dependencies with:")
        print('    pip install -e ".[dev]"')
        print()
        print("Or set up the full dev environment:")
        print("    source devsetup.sh")
        return False

    return True


def main():
    # Check dependencies first
    if not check_dependencies():
        sys.exit(1)

    # Determine version bump type
    if len(sys.argv) > 1:
        bump = sys.argv[1]
    else:
        bump = "patch"

    current_version = get_current_version()
    new_version = increment_version(current_version, bump)

    print(f"\n{'=' * 50}")
    print("  Publishing internet-names-mcp")
    print(f"{'=' * 50}\n")
    print(f"  Current version: {current_version}")
    print(f"  New version:     {new_version}")
    print()

    # Confirm
    response = input("Proceed? [y/N]: ").strip().lower()
    if response != "y":
        print("Aborted.")
        sys.exit(1)

    print()

    # Check for uncommitted changes. The version file is deliberately not filtered
    # out: any pre-existing edits in it would be swept into the version-bump commit.
    print("Checking git status...")
    result = run(["git", "status", "--porcelain"], check=False)
    changes = [line for line in result.stdout.splitlines() if line]
    if changes:
        print("\n  Warning: You have uncommitted changes:")
        for line in changes:
            print(f"    {line}")
        print()
        response = input("Continue anyway? [y/N]: ").strip().lower()
        if response != "y":
            print("Aborted.")
            sys.exit(1)

    # Update versions
    print(f"\nUpdating version to {new_version}...")
    update_version(new_version)
    print(f"  ✓ Updated {VERSION_FILE_PATH.relative_to(REPO_ROOT)}")

    # Clean old builds
    print("\nCleaning old builds...")
    dist_dir = REPO_ROOT / "dist"
    if dist_dir.exists():
        for f in dist_dir.iterdir():
            f.unlink()
        print("  ✓ Cleaned dist/")

    # Build
    print("\nBuilding package...")
    result = run([sys.executable, "-m", "build"])
    if result.returncode != 0:
        print(f"  ✗ Build failed: {result.stderr}")
        sys.exit(1)
    # Guard against hatchling resolving a different version than the one we wrote
    # (e.g. a mis-pointed [tool.hatch.version] path) before anything reaches PyPI.
    expected_artifacts = [
        dist_dir / f"internet_names_mcp-{new_version}-py3-none-any.whl",
        dist_dir / f"internet_names_mcp-{new_version}.tar.gz",
    ]
    built_artifacts = sorted(dist_dir.iterdir())
    if sorted(expected_artifacts) != built_artifacts:
        print("  ✗ Built artifacts do not match the expected version:")
        for artifact in built_artifacts:
            print(f"    {artifact.name}")
        sys.exit(1)
    print("  ✓ Built successfully")

    # Upload to PyPI
    print("\nUploading to PyPI...")
    result = run([sys.executable, "-m", "twine", "upload", "dist/*"], check=False)
    if result.returncode != 0:
        print("  ✗ Upload failed:")
        print(result.stderr)
        print(f"\n{VERSION_FILE_PATH.relative_to(REPO_ROOT)} has been updated but not committed. You may need to:")
        print("  1. Configure PyPI credentials: python -m twine upload dist/* --username __token__")
        print("  2. Or create ~/.pypirc with your credentials")
        sys.exit(1)
    print("  ✓ Uploaded to PyPI")

    # Git commit
    print("\nCommitting version bump...")
    # Committing by pathspec records only the version file, even if other changes
    # are already staged.
    run(["git", "commit", "-m", f"Bump version to {new_version}", "--", str(VERSION_FILE_PATH)])
    print("  ✓ Committed")

    # Git push
    print("\nPushing to remote...")
    result = run(["git", "push"], check=False)
    if result.returncode != 0:
        print(f"  ✗ Push failed: {result.stderr}")
        print("  You may need to push manually: git push")
    else:
        print("  ✓ Pushed")

    print(f"\n{'=' * 50}")
    print(f"  ✓ Published internet-names-mcp {new_version}")
    print(f"{'=' * 50}\n")


if __name__ == "__main__":
    main()
