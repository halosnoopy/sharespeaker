# Privacy and publishing

## Public source versus local state

The public source uses synthetic Bluetooth addresses beginning with `02:00:00:00:00:` or conventional `AA:BB:CC:DD:EE:` test values. They identify no real equipment. USB vendor/product IDs in hardware tests describe device models, not unique device identities. The example configuration has empty adapter, controller, role and speaker fields.

The code has no application telemetry, analytics or external upload implementation. It invokes local Windows/WSL commands and Bluetooth services. This review does not cover the behavior of installed operating-system services or third-party dependencies.

Keep these private:

- `.local/` and `hub-settings.json`: device names, addresses, adapter selections and preferences.
- Logs, diagnostic output and screenshots: may contain names, addresses, account paths, service metadata and media information.
- Configuration exports: contain device identities and preferences, even though they do not include BlueZ pairing keys.
- Linux pairing databases, user audio configuration backups and runtime state.
- Bluetooth aliases stored by BlueZ: custom nicknames may reveal room, device or owner names. Renaming uses local Bluetooth services and does not upload these names.
- Old archives and private backups: may contain previous code and local data.
- `.git/`: previous commits, tags, remote configuration and author metadata can retain information removed from the current files.

Ignore rules reduce accidental tracking. They do not remove previously tracked files or protect files from manual uploads.

## Release workflow

1. Run the tests described in [Testing](TESTING.md).
2. Review any new source or documentation and update `release-files.txt` deliberately.
3. Run `python scripts/prepare_github.py`. It audits the public files and creates `dist/github-ready/sharespeaker/` as a fresh repository with one commit, a neutral author identity, and no old history, tags or remotes.
4. Follow [Publishing on GitHub](GITHUB.md) to push that new repository. Do not push the original working repository or copy its `.git/` directory into the public copy.
5. For a source-only archive, run `python scripts/build_release.py` instead.

The original working repository's history is preserved privately and has not been rewritten. The newly prepared repository contains none of that history. If an older version has already been published, this cleanup cannot remove remote copies or existing remote history.

ShareSpeaker does not save application logs, player output logs, or a message history. Only the current status or error is displayed. System services may maintain their own diagnostics outside this project's control.

The release scan rejects common credential patterns, personal absolute paths and non-synthetic MAC-address literals. It reports file and line numbers without printing matched values. Human review is still required for free-form personal text and future additions.

## Cleanup scope

Source, tests and documentation were reviewed for personal device identities, local paths, credentials and upload behavior. Real device names and addresses were removed from retained examples, tests and UI checks. The hardcoded legacy settings migration was removed because it encoded a previous installation's speakers. Version 2 settings remain supported.

Machine-specific verification notes, one-off hardware diagnostics, stale screenshots, runtime JSON files, logs and old distribution archives were removed from the project after a private backup was saved outside it. Existing backup folders were preserved outside the project. Current local preferences were retained in ignored `.local/`. Release output is built from a fresh explicit allowlist and never from an entire project-directory archive.
