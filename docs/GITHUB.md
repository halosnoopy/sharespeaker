# Publish ShareSpeaker on GitHub

Suggested repository name: **sharespeaker**.

Suggested description: **Share phone audio across Bluetooth speakers with independent volume and delay controls on Windows and WSL.**

## Prepare a clean repository

From the project directory:

```text
python scripts/prepare_github.py
```

This audits the public file list and creates `dist/github-ready/sharespeaker/` with exactly one initial commit on `main`. It copies no previous history, tags, remotes, local settings, screenshots, generated results or logs. It does not upload anything. An existing destination is never overwritten; use `--output` to choose another new location.

The initial commit uses the project identity `ShareSpeaker Contributors` with the reserved placeholder email `contributors@example.invalid`, so the local computer's Git name and email are not exposed. Before making future commits, you can set your own intended public identity or GitHub no-reply address in the clean repository.

## Push the prepared copy

Create a new, empty GitHub repository named `sharespeaker`. Leave automatic README, license and gitignore initialization unchecked because the prepared repository already has source and a first commit.

Open a terminal inside `dist/github-ready/sharespeaker/`, replace `YOUR-ACCOUNT` in the example below with your GitHub account or organization, then run:

```sh
git remote add origin https://github.com/YOUR-ACCOUNT/sharespeaker.git
git push -u origin main
```

Use this newly prepared directory for the push. The original working repository still has private historical content and is not the publishing repository. Do not copy its `.git/` directory or add its old commits as parents of the public commit.

## Archive option

`python scripts/build_release.py` also writes an audited `dist/sharespeaker-source.zip` and checksum. This archive contains source only, without Git history. Extract it into a new directory if you prefer to initialize a repository yourself.
