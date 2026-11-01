#!/usr/bin/env python3
"""Release helper: cut a version, and pull its notes out of CHANGELOG.md.

    scripts/release.py cut 0.1.0 [--push]   # move Unreleased to 0.1.0, bump, commit, tag
    scripts/release.py notes 0.1.0           # that version's section, for the GitHub release
    scripts/release.py check-tag v0.1.0      # the tag, pyproject and CHANGELOG agree (CI)

The CHANGELOG follows Keep a Changelog: `## [Unreleased]` on top, then `## [X.Y.Z] - date`.
Pushing the tag is what publishes: the release workflow tests, builds, writes the GitHub
release from these notes and deploys it to du7 (deploy/README.md).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"
PYPROJECT = ROOT / "pyproject.toml"
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
HEADING = re.compile(r"^## \[(?P<v>[^\]]+)\](?: - (?P<date>\d{4}-\d{2}-\d{2}))?\s*$", re.MULTILINE)
REPO_URL = "https://github.com/eduelias/famulus"
TITLE = "famulus"


class ReleaseError(Exception):
    pass


def sections(text: str) -> dict[str, str]:
    """{version: body} for every `## [...]` heading, in file order."""
    marks = list(HEADING.finditer(text))
    out = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = text[m.end() : end]
        body = re.split(r"^\[[^\]]+\]: ", body, maxsplit=1, flags=re.MULTILINE)[0]  # link refs
        out[m["v"]] = body.strip()
    return out


def notes(text: str, version: str) -> str:
    body = sections(text).get(version)
    if not body:
        raise ReleaseError(f"CHANGELOG.md has no notes for {version}")
    return body + "\n"


def project_version(pyproject: str) -> str:
    m = re.search(r'^version = "([^"]+)"', pyproject, re.MULTILINE)
    if not m:
        raise ReleaseError("no version in pyproject.toml")
    return m[1]


def check_tag(tag: str, pyproject: str, changelog: str) -> str:
    if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise ReleaseError(f"tag {tag!r} is not vX.Y.Z")
    version = tag[1:]
    if project_version(pyproject) != version:
        raise ReleaseError(f"tag {tag} but pyproject.toml says {project_version(pyproject)}")
    notes(changelog, version)
    return version


def bump(changelog: str, pyproject: str, version: str, today: str) -> tuple[str, str]:
    """The new CHANGELOG and pyproject texts for `version`."""
    if not VERSION_RE.match(version):
        raise ReleaseError(f"{version!r} is not X.Y.Z")
    current = project_version(pyproject)
    as_tuple = lambda v: tuple(int(x) for x in v.split("."))
    if as_tuple(version) <= as_tuple(current):
        raise ReleaseError(f"{version} is not newer than {current}")
    found = sections(changelog)
    if version in found:
        raise ReleaseError(f"CHANGELOG.md already has {version}")
    if not found.get("Unreleased"):
        raise ReleaseError("nothing under ## [Unreleased] to release")
    changelog = changelog.replace(
        "## [Unreleased]", f"## [Unreleased]\n\n## [{version}] - {today}", 1
    )
    prev = next((v for v in found if v != "Unreleased"), None)
    link = f"[{version}]: {REPO_URL}/" + (
        f"compare/v{prev}...v{version}" if prev else f"releases/tag/v{version}"
    )
    unreleased = f"[Unreleased]: {REPO_URL}/compare/v{version}...HEAD"
    changelog = re.sub(r"^\[Unreleased\]: .*$", unreleased, changelog, count=1, flags=re.MULTILINE)
    if unreleased not in changelog:
        changelog = changelog.rstrip("\n") + f"\n\n{unreleased}\n"
    changelog = changelog.replace(unreleased, f"{unreleased}\n{link}", 1)
    pyproject = re.sub(
        r'^version = "[^"]+"', f'version = "{version}"', pyproject, count=1, flags=re.MULTILINE
    )
    return changelog, pyproject


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def cmd_cut(version: str, push: bool) -> None:
    if git("status", "--porcelain"):
        raise ReleaseError("the working tree has changes; commit or stash them first")
    if git("rev-parse", "--abbrev-ref", "HEAD") != "main":
        raise ReleaseError("release from main")
    git("fetch", "-q", "origin", "main")
    if git("rev-parse", "HEAD") != git("rev-parse", "origin/main"):
        raise ReleaseError("main is not the same as origin/main; pull or push first")
    changelog, pyproject = bump(
        CHANGELOG.read_text(), PYPROJECT.read_text(), version, datetime.now().astimezone().date().isoformat()
    )
    CHANGELOG.write_text(changelog)
    PYPROJECT.write_text(pyproject)
    git("add", "CHANGELOG.md", "pyproject.toml")
    git("commit", "-q", "-m", f"Release {version}")
    git("tag", "-a", f"v{version}", "-m", f"{TITLE} {version}\n\n{notes(changelog, version)}")
    print(f"tagged v{version}")
    if push:
        git("push", "-q", "--atomic", "origin", "main", f"v{version}")
        print(f"pushed; follow it at {REPO_URL}/actions")
    else:
        print(f"publish with: git push --atomic origin main v{version}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="release.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("cut")
    c.add_argument("version")
    c.add_argument("--push", action="store_true")
    sub.add_parser("notes").add_argument("version")
    sub.add_parser("check-tag").add_argument("tag")
    args = p.parse_args(argv)
    try:
        if args.cmd == "cut":
            cmd_cut(args.version, args.push)
        elif args.cmd == "notes":
            sys.stdout.write(notes(CHANGELOG.read_text(), args.version))
        else:
            print(check_tag(args.tag, PYPROJECT.read_text(), CHANGELOG.read_text()))
    except (ReleaseError, subprocess.CalledProcessError) as e:
        print(f"release: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
