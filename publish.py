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

# Files that contain the version
PYPROJECT_PATH = Path(__file__).parent / "pyproject.toml"
INIT_PATH = Path(__file__).parent / "src" / "internet_names_mcp" / "__init__.py"
SERVER_PATH = Path(__file__).parent / "src" / "internet_names_mcp" / "server.py"


def get_current_version() -> str:
    """Read current version from pyproject.toml."""
    content = PYPROJECT_PATH.read_text()
    match = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
    if not match:
        raise ValueError("Could not find version in pyproject.toml")
    return match.group(1)


def parse_version(version: str) -> tuple[int, int, int]:
    """Parse version string into (major, minor, patch)."""
    parts = version.split(".")
    if len(parts) != 3:
        raise ValueError(f"Invalid version format: {version}")
    return int(parts[0]), int(parts[1]), int(parts[2])


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


def update_file(path: Path, pattern: str, replacement: str) -> bool:
    """Update version in a file using regex substitution."""
    content = path.read_text()
    new_content, count = re.subn(pattern, replacement, content, flags=re.MULTILINE)

    if count == 0:
        return False

    path.write_text(new_content)
    return True


def update_versions(new_version: str) -> None:
    """Update version in all relevant files."""
    # Update pyproject.toml
    if not update_file(
        PYPROJECT_PATH,
        r'^version\s*=\s*"[^"]+"',
        f'version = "{new_version}"'
    ):
        raise ValueError("Failed to update version in pyproject.toml")

    # Update __init__.py
    if not update_file(
        INIT_PATH,
        r'^__version__\s*=\s*"[^"]+"',
        f'__version__ = "{new_version}"'
    ):
        raise ValueError("Failed to update version in __init__.py")

    # Update server.py
    if not update_file(
        SERVER_PATH,
        r'^VERSION\s*=\s*"[^"]+"',
        f'VERSION = "{new_version}"'
    ):
        raise ValueError("Failed to update version in server.py")


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run a command and return the result."""
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


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
    print(f"  Publishing internet-names-mcp")
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

    # Check for uncommitted changes (besides version files)
    print("Checking git status...")
    result = run(["git", "status", "--porcelain"], check=False)
    if result.stdout.strip():
        # Filter out the files we're about to change
        changes = [
            line for line in result.stdout.strip().split("\n")
            if line and not any(f in line for f in ["pyproject.toml", "__init__.py", "server.py"])
        ]
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
    update_versions(new_version)
    print("  ✓ Updated pyproject.toml")
    print("  ✓ Updated src/internet_names_mcp/__init__.py")
    print("  ✓ Updated src/internet_names_mcp/server.py")

    # Clean old builds
    print("\nCleaning old builds...")
    dist_dir = Path(__file__).parent / "dist"
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
    print("  ✓ Built successfully")

    # Upload to PyPI
    print("\nUploading to PyPI...")
    result = run([sys.executable, "-m", "twine", "upload", "dist/*"], check=False)
    if result.returncode != 0:
        print(f"  ✗ Upload failed:")
        print(result.stderr)
        print("\nVersion files have been updated. You may need to:")
        print("  1. Configure PyPI credentials: python -m twine upload dist/* --username __token__")
        print("  2. Or create ~/.pypirc with your credentials")
        sys.exit(1)
    print("  ✓ Uploaded to PyPI")

    # Git commit
    print("\nCommitting version bump...")
    run(["git", "add", str(PYPROJECT_PATH), str(INIT_PATH), str(SERVER_PATH)])
    run(["git", "commit", "-m", f"Bump version to {new_version}"])
    print(f"  ✓ Committed")

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
