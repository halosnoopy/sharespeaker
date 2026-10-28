"""Audit and package an explicit public source list, without local state or Git."""
import argparse
import hashlib
from pathlib import Path, PurePosixPath
import re
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MAC = re.compile(r"(?<![0-9a-f])(?:[0-9a-f]{2}[:_-]){5}[0-9a-f]{2}(?![0-9a-f])", re.I)
PERSONAL_PATH = re.compile(
    r"(?:[a-z]:[\\/](?:Users|Documents and Settings)[\\/][^\s\"'<>]+|"
    r"/mnt/[a-z]/" + r"Users/[^\s\"'<>]+|/(?:home|Users)/[^\s\"'<>]+)", re.I)
SECRET = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"AKIA[A-Z0-9]{16}|sk-[A-Za-z0-9_-]{24,})\b|"
    r"(?:password|api_key|access_token|client_secret)\s*[:=]\s*[\"'][^\"'\s]{8,}[\"']",
    re.I)
HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
PRIVATE_PARTS = {".git", ".local", "backups", "__pycache__", "dist", ".aws", ".codex", ".agents"}
PRIVATE_SUFFIXES = {".zip", ".log", ".png", ".wav", ".pem", ".key", ".pfx", ".p12", ".pyc"}


def release_paths(root=ROOT):
    names = (root / "release-files.txt").read_text(encoding="utf-8").splitlines()
    names = [name for name in names if name and not name.startswith("#")]
    if len(names) != len(set(name.casefold() for name in names)):
        raise ValueError("Duplicate release file")
    for name in names:
        relative = PurePosixPath(name)
        if (relative.is_absolute() or ".." in relative.parts or "\\" in name or ":" in name
                or any(part in PRIVATE_PARTS for part in relative.parts)
                or relative.suffix in PRIVATE_SUFFIXES or name.startswith(".env")
                or name != relative.as_posix()):
            raise ValueError("Unsafe release path")
        source = root / name
        # Refuse symlinks and junctions, including parent directory links.
        if source.resolve() != root.resolve().joinpath(*relative.parts):
            raise ValueError("Release path resolves outside its expected location")
        if not source.is_file():
            raise ValueError(f"Missing release file: {name}")
    return names


def audit_text(name, text):
    issues = []
    for number, line in enumerate(text.splitlines(), 1):
        if PERSONAL_PATH.search(line):
            issues.append(f"{name}:{number}: personal filesystem path")
        if SECRET.search(line):
            issues.append(f"{name}:{number}: possible credential")
        if HAN.search(line):
            issues.append(f"{name}:{number}: untranslated source text")
        for match in MAC.finditer(line):
            address = match.group().upper().replace("_", ":").replace("-", ":")
            if not (address.startswith(("02:00:00:00:00:", "AA:BB:CC:DD:EE:"))
                    or address == "00:00:00:00:00:00"):
                issues.append(f"{name}:{number}: non-synthetic Bluetooth address")
    return issues


def public_payload(root=ROOT):
    files = {}
    issues = []
    for name in release_paths(root):
        raw = (root / name).read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            issues.append(f"{name}: expected UTF-8 source text")
            continue
        issues.extend(audit_text(name, text))
        files[name] = raw
    if issues:
        raise ValueError("Release audit failed:\n" + "\n".join(issues))
    return files


def build(root=ROOT):
    files = public_payload(root)
    destination = root / "dist"
    destination.mkdir(exist_ok=True)
    archive = destination / "sharespeaker-source.zip"
    pending = destination / "sharespeaker-source.zip.tmp"
    try:
        with zipfile.ZipFile(pending, "w", zipfile.ZIP_DEFLATED) as output:
            for name, raw in files.items():
                # Fixed archive metadata contains no local paths or usernames.
                info = zipfile.ZipInfo("sharespeaker/" + name, date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                output.writestr(info, raw)
        with zipfile.ZipFile(pending) as output:
            expected = {"sharespeaker/" + name for name in files}
            if set(output.namelist()) != expected or output.testzip() is not None:
                raise ValueError("Archive verification failed")
            for name, raw in files.items():
                if output.read("sharespeaker/" + name) != raw:
                    raise ValueError("Archive content verification failed")
        pending.replace(archive)
    finally:
        pending.unlink(missing_ok=True)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    return archive, len(files)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="audit without creating an archive")
    args = parser.parse_args()
    try:
        if args.check:
            print(f"Release audit passed: {len(public_payload())} files.")
        else:
            path, count = build()
            print(f"Release audit passed: {count} files. Created dist/{path.name} and checksum.")
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
