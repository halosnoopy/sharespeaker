# Testing

## Isolated backend tests

From the project directory inside WSL:

```sh
python3 -m unittest discover -s tests -v
```

The suite mocks hardware and audio operations. It covers routing reuse, output selection, volume and delay changes, rollback, input recovery, local tones, pause/resume, dynamic endpoints, adapter isolation and discovery. It does not connect, pair or detach real devices.

## Windows interface checks

Run from the project directory in PowerShell:

```powershell
powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File tests/test_hardware_selector.ps1
powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File src/HubWindow.ps1 -CheckUi -SnapshotFile tests/fixtures/preview.json
```

The hardware test extracts selected GUI functions and uses simulated operations. The UI check uses only synthetic device data, checks playback requests, draft preservation, pause/resume, numeric controls, complete-card pagination, current-status/menu behavior, and device/adapter naming. Naming checks include cancellation, validation, correct device/controller targets, refreshed nicknames, default reset, and preservation of playback settings. It writes `dist/gui-routing-check.json`. Preview and UI checks do not load private settings or issue backend commands.

Generate previews for each tab (`0` Playback, `1` Devices, `2` Hardware):

```powershell
powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File src/HubWindow.ps1 -Preview -SnapshotFile tests/fixtures/preview.json -PreviewTab 0 -CapturePath dist/playback.png
```

Use `-PreviewWidth 950 -PreviewHeight 710` to inspect the minimum supported window. Generated previews stay in ignored `dist/`.

## Release checks

```text
python -m unittest discover -s tests -p test_release.py -v
python scripts/build_release.py
python scripts/prepare_github.py
```

The release builder uses an explicit file list, checks paths, text encoding, English source text, synthetic Bluetooth examples, credential patterns and personal filesystem paths, and inspects the resulting archive. This is a guardrail, not a guarantee that all possible personal information can be detected.

## Validation for this cleanup

- All 59 isolated backend tests passed in WSL, including Bluetooth name validation, controller-specific renaming, custom alias preservation, and safe control-only device classification after renaming.
- The simulated Windows hardware switch checks passed. The local-player privacy regression confirms that player output is discarded and no player log is created.
- All 38 interface checks passed with synthetic devices. All three English tabs were rendered for review, including the minimum-size playback and device layouts.
- All 5 release boundary and privacy regression tests passed. The public archive was audited and its contents verified against the release file list.
- Hardware playback, RF stability, dropout behavior and acoustic synchronization require manual testing on the target equipment. Simulations and interface screenshots do not establish those properties.
