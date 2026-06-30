# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the dnf-plugin-anyrepo project.

"""Generate a packaged Python tree with build metadata injected into __init__.py."""

import argparse
import os
import re
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = ROOT / "dnf_plugin_anyrepo"
BUILD_DIR = ROOT / "build" / "goreleaser" / "dnf_plugin_anyrepo"
VERSION_PATTERN = re.compile(r'^__version__ = ".*"$', re.MULTILINE)
COMMIT_PATTERN = re.compile(r'^__commit__ = ".*"$', re.MULTILINE)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("snapshot", "release"))
    parser.add_argument("--version")
    parser.add_argument("--commit")
    args = parser.parse_args()

    version = args.version or _resolve_version(args.mode)
    commit = args.commit or _resolve_commit()

    # Build a temporary package tree so release metadata can be embedded
    # without editing tracked source files in the repository.
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    shutil.copytree(SOURCE_DIR, BUILD_DIR)

    init_path = BUILD_DIR / "__init__.py"
    init_source = init_path.read_text(encoding="utf-8")
    init_source = VERSION_PATTERN.sub(f'__version__ = "{version}"', init_source, count=1)
    init_source = COMMIT_PATTERN.sub(f'__commit__ = "{commit}"', init_source, count=1)
    init_path.write_text(init_source, encoding="utf-8")
    return 0


def _resolve_version(mode: str) -> str:
    tag = _git_output_or_none("describe", "--tags", "--abbrev=0", "--match", "v[0-9]*")
    if tag is None:
        # CI environments that unpack a source tree without .git should still
        # be able to build snapshot artifacts with the default package metadata.
        return _source_metadata("__version__")

    version = tag[1:] if tag.startswith("v") else tag
    if mode == "release":
        return version

    major, minor, patch = version.split(".", 2)
    return f"{major}.{minor}.{int(patch) + 1}-next"


def _resolve_commit() -> str:
    commit = _git_output_or_none("rev-parse", "HEAD")
    if commit is not None:
        return commit
    return os.environ.get("GITHUB_SHA") or _source_metadata("__commit__")


def _source_metadata(name: str) -> str:
    init_source = (SOURCE_DIR / "__init__.py").read_text(encoding="utf-8")
    pattern = re.compile(rf'^{re.escape(name)} = "(.*)"$', re.MULTILINE)
    match = pattern.search(init_source)
    if match is None:
        raise RuntimeError(f"{name} is not defined in {SOURCE_DIR / '__init__.py'}")
    return match.group(1)


def _git_output_or_none(*args: str) -> str | None:
    git_dir = ROOT / ".git"
    if not git_dir.exists():
        return None
    try:
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
