from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKIP_FILES = {Path("scripts/audit_public_repo.py"), Path("scripts/audit_public_repo.ps1")}
FORBIDDEN_SUFFIXES = {
    ".key",
    ".pem",
    ".p12",
    ".pfx",
    ".cer",
    ".crt",
    ".p8",
    ".mobileprovision",
    ".ipa",
}
PATTERNS = {
    "Tailscale key": re.compile(r"tskey-(?:auth|client|api)-[A-Za-z0-9_-]{12,}"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "Apple private key": re.compile(r"-----BEGIN PRIVATE KEY-----|AuthKey_[A-Z0-9]+\.p8"),
    "ZEGO secret assignment": re.compile(
        r"(?i)(?:zego[_-]?(?:app[_-]?sign|server[_-]?secret)|appSign)\s*[:=]\s*['\"][^'\"]{12,}"
    ),
    "Tailscale OAuth assignment": re.compile(
        r"(?i)TS_OAUTH_SECRET\s*[:=]\s*['\"]?[A-Za-z0-9_-]{12,}"
    ),
}


def candidate_files() -> list[Path]:
    git = shutil.which("git")
    if (ROOT / ".git").exists() and git:
        result = subprocess.run(
            [git, "ls-files", "-z"], cwd=ROOT, check=False, capture_output=True
        )
        if result.returncode == 0:
            return [ROOT / value.decode() for value in result.stdout.split(b"\0") if value]
    ignored_parts = {"runtime", "tools", "build", "dist", "DerivedData", ".git"}
    return [
        path
        for path in ROOT.rglob("*")
        if path.is_file() and not any(part in ignored_parts for part in path.relative_to(ROOT).parts)
    ]


def main() -> int:
    findings: list[str] = []
    for path in candidate_files():
        relative = path.relative_to(ROOT)
        if relative in SKIP_FILES:
            continue
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            findings.append(f"forbidden file type: {relative}")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for label, pattern in PATTERNS.items():
            if pattern.search(text):
                findings.append(f"{label}: {relative}")

    if findings:
        print("PUBLIC REPOSITORY AUDIT: FAIL", file=sys.stderr)
        for finding in findings:
            print(f"- {finding}", file=sys.stderr)
        return 1
    print("PUBLIC REPOSITORY AUDIT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
