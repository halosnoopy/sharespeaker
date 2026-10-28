# Architecture

## Application boundary

`src/HubWindow.ps1` is a Windows PowerShell WinForms application. It keeps the interface responsive by running backend commands asynchronously. Only one normal application instance is allowed. Closing the window hides it in the tray; the tray exit action ends it. A held WSL process keeps the distribution active while the application is running.

`src/hub_backend.py` runs in WSL. It accepts one base64-encoded JSON request as its first argument and returns one JSON result with `ok`, `data` or `error`, and a single current `message`. An operation lock serializes requests. BlueZ is accessed with `busctl`, with `python3-dbus` as a status-reading fallback. PipeWire's PulseAudio interface is accessed with `pactl`.

`src/hub_discovery.py` maintains a controller-specific discovery session for about 30 seconds. `src/hub_test_audio.py` generates a quiet local tone and manages playback through `pw-play`. Process identity checks protect against stopping unrelated processes.

## Audio routing

A stable virtual bus receives phone audio or the built-in local test tone. Selected speakers receive independent loopback branches from that bus. Output selection changes preserve existing input and reusable branches. Volume changes preserve routing modules; delay changes rebuild the affected branch. Pause gates the bus instead of destroying the phone media consumer. Release removes application-owned routes.

Input and output can use one adapter or separate adapters. Controller-specific BlueZ paths distinguish the same device paired on more than one adapter. Hardware selections are committed after initialization succeeds. Device roles and speaker preferences remain independent of adapter pairings.

Explicit recovery samples a short audio segment in memory to check signal level. It preserves gains and speaker connections, tries media controls when needed, and can renegotiate one phone media channel. Normal status polling does not repeatedly run recovery.

## Configuration and system effects

The GUI saves version 2 preferences in `.local/hub-settings.json`, without an activity history or application log files. Empty defaults contain no controller identity. Preview and UI checks use synthetic data and do not read or save private settings.

Bluetooth nicknames use the BlueZ `Device1.Alias` property on the selected device's controller. Adapter names use `Adapter1.Alias`. These aliases persist in BlueZ independently of portable JSON settings. Initialization upgrades old default adapter aliases to ShareSpeaker, while retaining custom aliases. Pairing windows also preserve custom aliases. The backend validates names before writes and resolves input devices on the input controller. Original device names remain available for default-name reset and control-only device classification.

Starting the hub can attach the selected USB adapters, load the Linux Bluetooth driver and start BlueZ. Explicit initialization creates user configuration under `~/.config/systemd/user/` and `~/.config/wireplumber/` for a dedicated PipeWire socket and phone input. Replaced configuration is backed up with `.before-hub-v2` suffixes. It can restart affected audio services when configuration changes; ordinary connection, route and refresh operations do not restart them.

WSL runtime files live under `/run/user/<uid>/snoopy-hub/`. The PipeWire socket is `pulse/native` within that directory, avoiding WSLg's RDP audio bridge. Legacy speaker-port offsets cleared by the hub are backed up under `~/.local/state/snoopy-hub/`. BlueZ stores pairing records separately in Linux; they are not project files.

The internal runtime paths, module ownership tags and service drop-in filenames retain their legacy identifiers to reuse existing routing state and system configuration after the product rename. Visible application and default Bluetooth names use ShareSpeaker.

## Development

Keep the Windows launcher and WSL Python files together in their documented layout. Python helpers resolve relative to the backend file; the GUI resolves the project root from its source directory. There are no third-party Python packages for the core backend. System audio and Bluetooth commands must run in Linux; Windows Python is suitable for release packaging and source checks.

The test-tone helper discards standard output and error output instead of writing a player log. Operation status uses a single in-memory message, replaced by the next status. Errors and setup instructions can appear in a transient dialog; no log window or message history is retained. System services may maintain their own operating-system diagnostics independently of ShareSpeaker.
