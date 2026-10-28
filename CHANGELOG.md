# Changelog

## Unreleased

- Remove application log files, the activity-history window, and player output logging; display only the current status or error.
- Remove duplicate legacy launchers; keep Start ShareSpeaker.cmd as the single desktop entry point.
- Add an audited GitHub preparation command that creates a fresh main branch with a neutral author identity and no private history.

- Rename the application and public source archive to ShareSpeaker.
- Add per-device nicknames and input/output adapter names, including default-name reset, validation, and preservation of custom adapter aliases across startup and pairing windows.
- Translate the interface, status messages, errors and documentation to English.
- Organize application source, tests, release tooling and documentation into separate directories.
- Remove personal device identifiers and installation-specific notes; use synthetic test data.
- Keep runtime configuration in ignored `.local/`.
- Add a public source archive builder with an explicit file list and privacy checks.
- Preserve version 2 routing, separate adapter roles, pause/resume, local test audio and independent speaker volume/delay controls.

## Version 2

- Support separate Bluetooth adapters for phone input and speaker output.
- Preserve per-adapter pairings and controller-specific endpoint selection.
- Add bounded discovery and resilient Bluetooth status reads.

## Version 1

- Provide phone Bluetooth input, multiple selected speaker outputs, volume/delay controls, pause/resume and local test audio.

Intermittent dropouts and differences in speaker latency remain hardware-dependent limitations.
