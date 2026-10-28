"""Create a fresh, audited ShareSpeaker Git repository without private history."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

from build_release import ROOT, public_payload

PUBLIC_AUTHOR = "ShareSpeaker Contributors"
PUBLIC_EMAIL = "contributors@example.invalid"


def prepare(destination):
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError("Destination already exists. Choose a new empty location; existing files will not be overwritten.")
    files = public_payload()
    # Read only audited payload bytes; never copy a whole checkout or any Git metadata.
    destination.mkdir(parents=True)
    for name, raw in files.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    environment = dict(os.environ)
    for name in tuple(environment):
        if name.startswith("GIT_"):
            environment.pop(name)
    environment.update(GIT_AUTHOR_NAME=PUBLIC_AUTHOR, GIT_COMMITTER_NAME=PUBLIC_AUTHOR,
                       GIT_AUTHOR_EMAIL=PUBLIC_EMAIL, GIT_COMMITTER_EMAIL=PUBLIC_EMAIL,
                       GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)

    def git(*args):
        return subprocess.run(["git", *args], cwd=destination, env=environment,
                              capture_output=True, text=True, check=True).stdout.strip()

    git("init", "--template=", "--initial-branch=main")
    git("config", "user.name", PUBLIC_AUTHOR)
    git("config", "user.email", PUBLIC_EMAIL)
    git("config", "core.logAllRefUpdates", "false")
    git("add", "--", *files)
    git("-c", "commit.gpgsign=false", "commit", "-m", "Initial public ShareSpeaker source")
    tracked = set(git("ls-files").splitlines())
    if tracked != set(files) or git("rev-list", "--count", "HEAD") != "1" or git("status", "--porcelain"):
        raise ValueError("Clean repository verification failed")
    if git("remote"):
        raise ValueError("Unexpected remote configuration")
    return len(files)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist/github-ready/sharespeaker")
    args = parser.parse_args()
    try:
        count = prepare(args.output)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        # Do not echo subprocess output, which may contain local paths or environment details.
        print(str(error) if isinstance(error, ValueError) else "Git preparation failed. Check Git installation and destination permissions.", file=sys.stderr)
        return 1
    print(f"Prepared sharespeaker: {count} audited files, one public commit on main, no remote, no private history.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
