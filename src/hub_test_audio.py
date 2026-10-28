"""Quiet built-in calibration audio; managed by hub_backend, no new dependencies."""
import math
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import wave


def main():
    target, token = sys.argv[1:3]
    if not target.startswith("snoopy_hub_bus_") or not token.isalnum():
        raise ValueError("Invalid test player identity")
    path = Path(os.environ["XDG_RUNTIME_DIR"]) / "snoopy-hub/local-test.wav"
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
        data = bytearray()
        for sample in range(48000 * 8):
            beat, phase = divmod(sample, 48000)
            envelope = min(1, phase / 480, max(0, (5760 - phase) / 480)) if phase < 5760 else 0
            value = int(2300 * envelope * math.sin(2 * math.pi * (660 if beat % 2 else 880) * sample / 48000))
            data.extend(struct.pack("<hh", value, value))
        wav.writeframes(data)
    child = None
    def finish(signum, frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, finish)
    try:
        while True:
            child = subprocess.Popen(["pw-play", "--target", target, "--properties",
                                      "media.name=snoopy_hub_local_test_" + token, str(path)],
                                     stdin=subprocess.DEVNULL)
            code = child.wait()
            if code:
                raise SystemExit(code)
    finally:
        if child and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill()


if __name__ == "__main__":
    main()
