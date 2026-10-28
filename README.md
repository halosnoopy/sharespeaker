# ShareSpeaker — Bluetooth Audio Sharing for Windows + WSL

Play phone audio through **multiple Bluetooth speakers** using a Windows desktop app and a WSL audio backend. ShareSpeaker receives Bluetooth audio from your phone and sends it to selected speakers, with independent volume and delay controls.

Use it as a **Bluetooth audio splitter** or speaker hub when you want to share one phone's music across several speakers. It uses BlueZ and PipeWire in WSL, with a Windows interface for pairing, playback and device management.

## Features

- **Multiple Bluetooth speaker outputs:** select which paired speakers receive the phone audio.
- **Per-speaker volume and delay:** adjust each speaker independently and apply changes without rebuilding unaffected routes.
- **Bluetooth device naming:** give phones and speakers friendly names and customize adapter names shown during pairing.
- **One or two Bluetooth adapters:** share one adapter, or separate phone input from speaker output.
- **Local test audio and recovery:** check speakers without a phone and recover a silent phone input.
- **Private local settings:** no application log files, activity history or telemetry.

Simultaneous speaker connections depend on your Bluetooth hardware. Delay controls help compensate for different speaker latencies; precise acoustic synchronization and dropout-free playback are not guaranteed. This app receives phone Bluetooth audio; it does not capture Windows system audio.

## Requirements

- Windows with Windows PowerShell 5.1, WSL 2 and usbipd-win.
- A WSL Linux distribution with systemd, Python 3.9 or newer, BlueZ, PipeWire, WirePlumber and PulseAudio utilities (`pactl`, `parec`). `python3-dbus` provides a fallback for Bluetooth status queries.
- Bluetooth hardware supported by the Linux kernel, with any required firmware installed. One adapter can serve both roles; separate input and output adapters are also supported.

The application uses existing system dependencies. It does not install or upgrade them. Bluetooth support, simultaneous connection limits and firmware availability depend on your hardware and WSL environment.

## Start

1. Double-click **Start ShareSpeaker.cmd**.
2. In **Hardware**, choose your output adapter and an input adapter, or share the output adapter. Enter your WSL distribution name (default: `Ubuntu`). Click **Apply hardware**.
3. If an adapter is not shared, a message shows the `usbipd bind --busid <bus-id>` command to run in administrator PowerShell. Use the ID shown for your adapter, then try again.
4. In **Devices**, put your speakers in pairing mode, click **Scan**, then **Pair / Trust** and **Connect**. For a phone, click **Pair phone** and select **ShareSpeaker**, or **ShareSpeaker Input** when using a separate input adapter, on the phone.
5. In **Playback**, choose the phone, select speakers with **Use**, and click **Play / Apply**. Start media playback on the phone. The hub waits for audio if necessary.

Each adapter has its own pairings. Changing adapters may require pairing again; the application does not copy pairing keys.

## Bluetooth names

- In **Devices**, select a phone or speaker and click **Rename…**. The nickname appears in the device list, speaker cards and input selector. It is saved by BlueZ for that device on that adapter, and does not change the device's name on other computers.
- In **Hardware**, use **Rename output adapter…** or **Rename input adapter…** to set the name shown during Bluetooth pairing. Defaults are **ShareSpeaker** and **ShareSpeaker Input** for separate adapters. Shared hardware has one name for both roles. Apply any pending hardware selection before renaming.
- The name editor offers **Use default**. For devices this restores the original reported name; for adapters it restores the ShareSpeaker default. Names cannot be blank or contain control characters, and are limited to 248 UTF-8 bytes.
- Custom adapter names survive startup and new pairing windows. Phones may cache an old name until they refresh their Bluetooth list or pair again. Device nicknames and adapter aliases are stored in BlueZ, separately from the JSON settings export; set them again after moving to another computer or adapter.

## Playback controls

- Edit volume (0–100%) and delay targets (0–3000 ms), then click **Apply changes**. Increase delay for the speaker that plays first. Sliders, numeric fields and ±10 ms buttons edit pending values; refresh preserves those edits.
- **Pause** mutes forwarding while preserving connections and routing. **Resume / Apply** restores it.
- Choose **Local test tone · Loop** to check outputs without a phone. **More → Test outputs** plays a four-second tone while retaining the selected phone input.
- **More → Recover phone audio** checks the input and may request one media pause/resume or renegotiate the phone's media channel. You may need to select the hub again on the phone. Recovery preserves volumes and speaker connections.
- Closing the main window keeps the application in the system tray; **Exit hub** exits it. Sleep and shutdown interrupt playback.
- **Return to Windows** releases routes and returns the selected output adapter to Windows. A separately attached input adapter remains attached.

Delay is a loopback latency target, with a minimum effective target of 20 ms. It is not guaranteed extra delay or precise acoustic synchronization. Different speakers may have different natural latency and loudness. Intermittent dropouts may still occur. Windows system audio and arbitrary local media files are not input sources.

## Settings and privacy

Local settings are kept in `.local/`, excluded from Git and release archives. ShareSpeaker does not save application logs or keep an activity history; it displays only the current status or error message. **Export settings** creates a private JSON file containing device names, Bluetooth addresses, roles, volumes and delays. Pairing keys are managed by BlueZ and are not included in that export. Import preserves the current computer's adapter settings; pair devices again on a new computer.

The source contains no telemetry, analytics or external upload client. The application communicates with local Windows/WSL services and Bluetooth devices. Diagnostics, configuration exports and screenshots can contain personal information; do not publish them. See [Privacy and publishing](docs/PRIVACY.md).

## Project layout

```text
Start ShareSpeaker.cmd        Desktop launcher
src/                          WinForms interface and WSL backend
tests/                        Isolated tests and synthetic preview data
scripts/                      Release audit and archive builder
docs/                         Architecture, testing and publishing notes
hub-settings.example.json     Empty portable configuration example
release-files.txt             Explicit public release file list
.local/                       Private local settings (ignored)
dist/                         Generated checks and release archive (ignored)
```

Run backend tests inside WSL with `python3 -m unittest discover -s tests -v`, from this project directory. See [Testing](docs/TESTING.md) for interface checks and [Architecture](docs/ARCHITECTURE.md) for development details.

## Prepare for GitHub

Use **sharespeaker** as the repository name to match the app. Run:

```text
python scripts/prepare_github.py
```

This creates a fresh repository in `dist/github-ready/sharespeaker/` with one initial commit on `main`, a project-neutral author identity, and no private history or runtime files. It does not upload anything. Follow [Publishing on GitHub](docs/GITHUB.md) to connect this clean copy to an empty GitHub repository and push it.

For a source archive, run `python scripts/build_release.py`. This writes `dist/sharespeaker-source.zip` and a SHA-256 checksum.

The original working repository still contains private historical content. Use the newly prepared repository or source archive for publication.
