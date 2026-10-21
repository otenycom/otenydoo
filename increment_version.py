#!/usr/bin/env python3
#
# Bump the last segment of each generic module's manifest version so an
# Odoo upgrade sees a new version. A missing file is an error.
#
# Usage:
#   python3 increment_version.py          # every module in this repository
#   python3 increment_version.py --backup # only oteny_backup_trigger

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent

GENERIC_MODULES = [
    "oteny_audit",
    "oteny_shortcut",
    "oteny_bot",
    "oteny_backup_trigger",
    "oteny_article",
    "oteny_knowledge_sync",
    "oteny_cloud_filestore",
    "riverflow",
    "odoo_parallel_tests",
]

_VERSION_RE = re.compile(r'"version":\s*"(\d+(?:\.\d+)+)"')


def manifest_path(module: str) -> Path:
    return REPO_ROOT / module / "__manifest__.py"


def selected_manifests(backup: bool) -> list[Path]:
    if backup:
        return [manifest_path("oteny_backup_trigger")]
    return [manifest_path(name) for name in GENERIC_MODULES]


def increment_version(match: re.Match) -> str:
    version = match.group(1)
    parts = version.split(".")
    parts[-1] = str(int(parts[-1]) + 1)
    return f'"version": "{".".join(parts)}"'


def bump_manifest(manifest: Path) -> None:
    content = manifest.read_text()
    updated, n = _VERSION_RE.subn(increment_version, content)
    if n == 0:
        raise SystemExit(f"No version string in {manifest}")
    manifest.write_text(updated)


def git_add(manifest: Path) -> None:
    subprocess.check_call(
        ["git", "add", str(manifest.relative_to(REPO_ROOT))],
        cwd=REPO_ROOT,
    )


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    backup = "--backup" in argv
    if backup:
        print("Backup trigger mode: incrementing only oteny_backup_trigger")
    else:
        print("Standard mode: incrementing all generic modules")

    manifests = selected_manifests(backup)
    missing = [path for path in manifests if not path.exists()]
    if missing:
        for path in missing:
            print(f"Missing: {path}")
        return 1

    for path in manifests:
        bump_manifest(path)
        git_add(path)
        print(f"Updated version in {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
