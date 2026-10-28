"""Hold one controller-specific BlueZ discovery client for a bounded interval."""
import re
import signal
import subprocess
import sys
import time


def main():
    controller, token = sys.argv[1:3]
    if not re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", controller) or not token.isalnum():
        raise ValueError("Invalid discovery identity")
    def finish(signum, frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, finish)
    process = subprocess.Popen(["bluetoothctl"], stdin=subprocess.PIPE,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
    try:
        process.stdin.write("select " + controller + "\n")
        process.stdin.flush()
        time.sleep(0.4)
        process.stdin.write("scan on\n")
        process.stdin.flush()
        deadline = time.monotonic() + 30
        while process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.2)
    finally:
        if process.poll() is None:
            try:
                process.stdin.write("scan off\nquit\n")
                process.stdin.flush()
                process.wait(timeout=3)
            except (BrokenPipeError, subprocess.TimeoutExpired):
                process.terminate()
                process.wait(timeout=3)
        process.stdin.close()


if __name__ == "__main__":
    main()
