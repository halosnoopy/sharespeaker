#!/usr/bin/env python3
"""WSL Bluetooth hub. One JSON request -> one JSON result; no GUI dependencies."""
import base64
import concurrent.futures
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import uuid
import unicodedata


class HubError(RuntimeError):
    pass


def clean(text):
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text).strip()


def device_role(device, overrides):
    explicit = overrides.get(device["mac"])
    if explicit in ("input", "output"):
        return explicit
    # Some speakers advertise Audio Source too. That alone does not make a phone.
    if device.get("icon") in ("phone", "smartphone"):
        return "input"
    if "0000110a-" in device.get("uuids", "") and "0000110b-" not in device.get("uuids", ""):
        return "input"
    return "output"


def node_mac(node):
    props = node.get("properties", {})
    address = props.get("api.bluez5.address") or props.get("device.string", "")
    if re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", address):
        return address.upper()
    match = re.search(r"(?:[0-9A-Fa-f]{2}_){5}[0-9A-Fa-f]{2}", node.get("name", ""))
    return match.group().replace("_", ":").upper() if match else ""


def has_device_name(device):
    name = str(device.get("name", "")).strip()
    return bool(name) and not re.fullmatch(r"(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}", name)


def validate_bluetooth_name(value):
    if not isinstance(value, str):
        raise HubError("Enter a Bluetooth name.")
    name = value.strip()
    if not name or any(unicodedata.category(char) == "Cc" for char in value):
        raise HubError("Names cannot be blank or contain control characters.")
    if len(name.encode("utf-8")) > 248:
        raise HubError("Bluetooth names must be at most 248 UTF-8 bytes.")
    return name


class Hub:
    def __init__(self, request):
        self.request = request
        self.roles = {key.upper(): value for key, value in request.get("roles", {}).items()}
        self.home = Path.home()
        self.runtime = Path(f"/run/user/{os.getuid()}")
        self.socket = self.runtime / "snoopy-hub/pulse/native"
        self.state_file = self.runtime / "snoopy-hub/route.json"
        self.player_file = self.runtime / "snoopy-hub/local-test-player.json"
        self.env = dict(os.environ, LC_ALL="C", XDG_RUNTIME_DIR=str(self.runtime),
                        PIPEWIRE_RUNTIME_DIR=str(self.runtime), PIPEWIRE_REMOTE="pipewire-0",
                        PULSE_SERVER=f"unix:{self.socket}")
        self.message = ""
        self._objects_at = 0
        self._objects_value = {}

    def run(self, args, timeout=12, check=True, input_text=None):
        try:
            result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", env=self.env,
                                    timeout=timeout, input=input_text)
        except subprocess.TimeoutExpired:
            raise HubError(f"Operation timed out: {args[0]} ({timeout} seconds)")
        output = clean(result.stdout)
        if check and result.returncode:
            raise HubError(clean(result.stderr) or output or f"Command failed: {args[0]}")
        if result.returncode and not check:
            return clean(output + "\n" + result.stderr)
        return output

    def pulse(self, *args):
        return self.run(["pactl", f"--server=unix:{self.socket}", *args])

    def require_audio(self):
        info = self.pulse("info")
        if not re.search(r"Server Name:.*PipeWire", info, re.I):
            raise HubError("Audio server is not local PipeWire. Click Start hub.")
        return info

    def audio_items(self, kind):
        return json.loads(self.pulse("--format=json", "list", kind))

    def split_input(self):
        return bool(self.request.get("inputController") or self.request.get("inputUsbId"))

    def input_hub(self):
        if getattr(self, "_input_context", None) is not None:
            return self._input_context
        request = dict(self.request)
        request["controller"] = request.pop("inputController", "")
        request["usbId"] = request.pop("inputUsbId", "")
        self._input_context = Hub(request)
        self._input_context.objects = self.objects
        return self._input_context

    def is_input_mac(self, mac):
        source = self.request.get("source") or self.route_state().get("inputSource", "")
        return (self.roles.get(mac) == "input" or source == "phone:" + mac or
                (self.request.get("mac") == mac and self.request.get("deviceDirection") == "input"))

    def endpoint_matches(self, node, mac, direction=None):
        if node_mac(node) != mac:
            return False
        if not (self.request.get("controller") or self.request.get("usbId")):
            return True
        props = node.get("properties", {})
        path = props.get("api.bluez5.path") or props.get("bluez5.path") or props.get("api.bluez5.transport")
        use_input = self.split_input() and (direction == "input" or (direction is None and self.is_input_mac(mac)))
        controller = self.input_hub().controller() if use_input else self.controller()
        controller_path = controller.get("path", "")
        if not controller_path:
            return False
        # Older PipeWire versions may omit the BlueZ path. Refuse ambiguous
        # endpoints rather than silently route to another adapter's same MAC.
        if not path:
            count = sum("org.bluez.Adapter1" in faces for faces in self.objects().values())
            return count == 1
        return path.startswith(controller_path + "/")

    def controller(self):
        controllers = []
        for path, interfaces in self.objects().items():
            if "org.bluez.Adapter1" not in interfaces:
                continue
            props = self.properties(interfaces["org.bluez.Adapter1"])
            usb_id = ""
            device_path = Path("/sys/class/bluetooth") / path.rsplit("/", 1)[1] / "device"
            for parent in [device_path.resolve(), *device_path.resolve().parents]:
                if (parent / "idVendor").exists() and (parent / "idProduct").exists():
                    usb_id = (parent / "idVendor").read_text().strip() + ":" + (parent / "idProduct").read_text().strip()
                    break
            controllers.append({"available": True, "path": path, "mac": props.get("Address", ""),
                                "powered": props.get("Powered", False), "name": props.get("Alias") or props.get("Name", ""),
                                "originalName": props.get("Name", ""), "usbId": usb_id})
        wanted = self.request.get("controller", "")
        usb_id = self.request.get("usbId", "").lower()
        matches = [c for c in controllers if c["mac"] == wanted] if wanted else []
        if not matches and usb_id:
            matches = [c for c in controllers if c["usbId"].lower() == usb_id]
        if not wanted and not usb_id:
            matches = controllers
        if len(matches) > 1:
            raise HubError("Multiple matching adapters found. Specify a controller address in Hardware.")
        return matches[0] if matches else {"available": False, "mac": "", "powered": False}

    @staticmethod
    def properties(props):
        return {key: val.get("data") if isinstance(val, dict) else val for key, val in props.items()}

    def objects(self):
        if time.monotonic() - self._objects_at < 1:
            return self._objects_value
        try:
            raw = self.run(["busctl", "--system", "--timeout=4", "--json=short", "call", "org.bluez", "/",
                            "org.freedesktop.DBus.ObjectManager", "GetManagedObjects"], timeout=5)
            result = json.loads(raw)["data"][0]
        except (HubError, ValueError, KeyError):
            # Some systemd busctl versions fail JSON serialization for BlueZ
            # media objects (for example very large AVRCP item identifiers).
            # Read the same API directly instead of pretending hardware vanished.
            try:
                import dbus
                def native(value):
                    if isinstance(value, dbus.Boolean):
                        return bool(value)
                    if isinstance(value, dict):
                        return {str(k): native(v) for k, v in value.items()}
                    if isinstance(value, (list, tuple)):
                        return [native(v) for v in value]
                    if isinstance(value, str):
                        return str(value)
                    if isinstance(value, int):
                        return int(value)
                    if isinstance(value, float):
                        return float(value)
                    return value
                bus = dbus.SystemBus(private=True)
                try:
                    objects = bus.get_object("org.bluez", "/").GetManagedObjects(
                        dbus_interface="org.freedesktop.DBus.ObjectManager", timeout=5)
                    result = {str(path): {str(interface): {str(key): {"data": native(value)}
                              for key, value in props.items()} for interface, props in faces.items()}
                              for path, faces in objects.items()}
                finally:
                    bus.close()
            except Exception as exc:
                raise HubError("Cannot read Bluetooth status; adapter availability is unknown: " + str(exc)) from exc
        self._objects_at = time.monotonic()
        self._objects_value = result
        return result

    def device_path(self, mac):
        controller = self.input_hub().controller() if self.split_input() and self.is_input_mac(mac) else self.controller()
        if not controller["available"]:
            raise HubError("Selected Bluetooth adapter is not ready in WSL.")
        return controller["path"] + "/dev_" + mac.replace(":", "_")

    def dbus_call(self, path, interface, method, *args, timeout=15, check=True):
        return self.run(["busctl", "--system", "--timeout=" + str(timeout), "call", "org.bluez", path,
                         interface, method, *args], timeout=timeout + 1, check=check)

    def set_property(self, path, interface, name, signature, value):
        return self.run(["busctl", "--system", "--timeout=5", "set-property", "org.bluez", path,
                         interface, name, signature, value], timeout=6)

    def read_device(self, mac):
        objects = self.objects()
        props = self.properties(objects.get(self.device_path(mac), {}).get("org.bluez.Device1", {}))
        return self.make_device(mac, props)

    def make_device(self, mac, props):
        device = {"mac": mac, "name": props.get("Alias") or props.get("Name") or mac,
                  "originalName": props.get("Name") or props.get("Alias") or mac,
                  "paired": props.get("Paired", False), "trusted": props.get("Trusted", False),
                  "connected": props.get("Connected", False), "icon": props.get("Icon", ""),
                  "uuids": " ".join(props.get("UUIDs", []))}
        device["role"] = device_role(device, self.roles)
        device["controlOnly"] = (any(name.lower().startswith("le-bose") for name in (device["name"], device["originalName"])) and
                                 "0000febe-" in device["uuids"].lower() and
                                 "0000110b-" not in device["uuids"].lower())
        return device

    def devices(self, discovered=False):
        controller = self.controller()
        if not controller["available"]:
            return []
        result = []
        for path, interfaces in self.objects().items():
            if not path.startswith(controller["path"] + "/") or "org.bluez.Device1" not in interfaces:
                continue
            props = self.properties(interfaces["org.bluez.Device1"])
            device = self.make_device(props["Address"], props)
            if self.split_input() and device["role"] == "input":
                continue
            device["adapterRole"] = "output" if self.split_input() else "shared"
            result.append(device)
        if self.split_input():
            for device in self.input_hub().devices(discovered):
                if device["role"] == "input":
                    device["adapterRole"] = "input"
                    result.append(device)
        return [d for d in result if has_device_name(d) and
                (discovered or d["paired"] or d["connected"] or d["mac"] in self.roles)]

    def discovery_session(self):
        path = self.runtime / "snoopy-hub/discovery.json"
        try:
            session = json.loads(path.read_text())
            proc = Path("/proc") / str(int(session["pid"]))
            start = proc.joinpath("stat").read_text().rsplit(")", 1)[1].split()[19]
            args = proc.joinpath("cmdline").read_bytes().split(b"\0")
            if (start == str(session["start"]) and session["token"].encode() in args and
                    str(Path(__file__).with_name("hub_discovery.py")).encode() in args):
                return session
        except (OSError, ValueError, KeyError, IndexError):
            pass
        return {}

    def stop_discovery(self):
        session = self.discovery_session()
        if session:
            os.killpg(session["pid"], signal.SIGTERM)
        (self.runtime / "snoopy-hub/discovery.json").unlink(missing_ok=True)

    def start_discovery(self):
        controller = self.wait_controller()
        session = self.discovery_session()
        if session.get("controller") == controller["mac"]:
            return
        self.stop_discovery()
        self.set_property(controller["path"], "org.bluez.Adapter1", "Powered", "b", "true")
        token = uuid.uuid4().hex
        process = subprocess.Popen([sys.executable, str(Path(__file__).with_name("hub_discovery.py")),
                                    controller["mac"], token], env=self.env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        start = (Path("/proc") / str(process.pid) / "stat").read_text().rsplit(")", 1)[1].split()[19]
        path = self.runtime / "snoopy-hub/discovery.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"pid": process.pid, "start": start, "token": token, "controller": controller["mac"]}))
        self.message = ("Scanning; devices appear as discovered. Discovery stops after about 30 seconds.")

    def snapshot(self, discovered=False):
        server_error = ""
        sinks, sources, cards = [], [], []
        try:
            self.require_audio()
            sinks = self.audio_items("sinks")
            sources = self.audio_items("sources")
            cards = self.audio_items("cards")
        except (HubError, ValueError) as exc:
            server_error = str(exc)
        session = self.discovery_session()
        discovering = bool(session and session.get("controller") == self.controller().get("mac"))
        devices = self.devices(discovered or discovering)
        for device in devices:
            sink = next((s for s in sinks if self.endpoint_matches(s, device["mac"])), None)
            source = next((s for s in sources if self.endpoint_matches(s, device["mac"], "input") and not s["name"].endswith(".monitor")), None)
            device["sink"] = sink["name"] if sink else ""
            device["source"] = source["name"] if source else ""
            device["ready"] = bool(source if device["role"] == "input" else sink)
            device["codec"] = (sink or source or {}).get("properties", {}).get("api.bluez5.codec", "")
            device["playerState"] = ""
            device["mediaState"] = ""
            device["phoneVolume"] = None
            device["inputGain"] = None
            if device["role"] == "input":
                path = self.input_hub().device_path(device["mac"]) if self.split_input() else self.device_path(device["mac"])
                for interfaces in self.objects().values():
                    player = self.properties(interfaces.get("org.bluez.MediaPlayer1", {}))
                    transport = self.properties(interfaces.get("org.bluez.MediaTransport1", {}))
                    if player.get("Device") == path:
                        device["playerState"] = player.get("Status", "")
                    if transport.get("Device") == path and transport.get("UUID", "").startswith("0000110b-"):
                        device["mediaState"] = transport.get("State", "")
                        if "Volume" in transport:
                            device["phoneVolume"] = round(transport["Volume"] * 100 / 127)
                if source:
                    try:
                        node = self.input_node(source)
                        gain = next((p["volume"] for p in node["info"].get("params", {}).get("Props", []) if "volume" in p), None)
                        device["inputGain"] = round(gain * 100) if gain is not None else None
                    except (HubError, ValueError, KeyError):
                        pass
            device["status"] = ("Control channel; no audio" if device.get("controlOnly") else
                                "Audio ready" if device["ready"] else
                                "Player active; media channel inactive" if device["connected"] and device["playerState"] == "playing" else
                                "Connected; waiting for audio" if device["connected"] else
                                "Paired; disconnected" if device["paired"] else "Not paired")
            device.pop("uuids", None)
        route = self.route_state()
        if route:
            route["paused"] = bool(route.get("paused", False))
            route["outputsReady"] = (not server_error and all(any(s["name"] == output["sink"] for s in sinks)
                                  for output in route.get("outputs", [])) and
                                any(s["name"] == route.get("target") for s in sinks))
            if route["outputsReady"]:
                owned = self.owned_modules()
                route["outputsReady"] = all(index in owned for index in route.get("modules", []) if index != route.get("inputModule"))
            route["healthy"] = route["outputsReady"]
            if route.get("source", "").startswith("phone:"):
                phone_mac = route["source"].split(":", 1)[1]
                live_source = next((s for s in sources if self.endpoint_matches(s, phone_mac) and not s["name"].endswith(".monitor")), None)
                owned = self.owned_modules() if not server_error else {}
                route["waitingInput"] = not bool(live_source)
                phone = next((d for d in devices if d["mac"] == phone_mac), {})
                route["inputProblem"] = bool(route["waitingInput"] and phone.get("playerState") == "playing")
                route["needsInputLink"] = bool(live_source and route["outputsReady"] and
                                               (route.get("sourceName") != live_source["name"] or route.get("inputModule") not in owned))
                route["healthy"] = route["healthy"] and not route["waitingInput"] and not route["needsInputLink"]
            if route["paused"]:
                route["healthy"] = False
            if route.get("source") == "local-test":
                route["localTestRunning"] = self.local_player_alive(self.local_player())
                route["healthy"] = route["healthy"] and route["localTestRunning"]
        return {"controller": self.controller(),
                "inputController": self.input_hub().controller() if self.split_input() else self.controller(),
                "splitAdapters": self.split_input(), "discovering": discovering, "serverReady": not server_error,
                "serverError": server_error, "devices": devices, "route": route,
                "sinkCount": len(sinks), "cardCount": len(cards)}

    def write_changed(self, path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_text() == content:
            return False
        if path.exists():
            backup = path.with_name(path.name + ".before-hub-v2")
            if not backup.exists():
                backup.write_bytes(path.read_bytes())
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(content)
        temporary.replace(path)
        return True

    @staticmethod
    def default_adapter_name(direction):
        return "ShareSpeaker Input" if direction == "input" else "ShareSpeaker"

    def ensure_adapter_name(self, controller, default):
        # Preserve user aliases across initialization and pairing windows.
        current = controller.get("name", "")
        legacy_defaults = ("Snoopy", "Snoopy Home Hub")
        if not current or current == controller.get("originalName") or current in legacy_defaults:
            if current != default:
                self.set_property(controller["path"], "org.bluez.Adapter1", "Alias", "s", default)
                self._objects_at = 0
            return default
        return current

    def rename_device(self, mac, value):
        name = validate_bluetooth_name(value)
        self.set_property(self.device_path(mac), "org.bluez.Device1", "Alias", "s", name)
        self._objects_at = 0
        if getattr(self, "_input_context", None) is not None:
            self._input_context._objects_at = 0
        self.message = ("Device nickname saved for this Bluetooth adapter.")

    def rename_adapter(self, direction, value):
        if direction not in ("input", "output"):
            raise HubError("Select an input or output adapter.")
        name = validate_bluetooth_name(value)
        receiver = self.input_hub() if direction == "input" and self.split_input() else self
        controller = receiver.controller()
        if not controller.get("available"):
            raise HubError("Start the selected adapter before renaming it.")
        receiver.set_property(controller["path"], "org.bluez.Adapter1", "Alias", "s", name)
        receiver._objects_at = self._objects_at = 0
        self.message = ("Bluetooth adapter name saved. Paired phones may cache its previous name.")

    def wait_controller(self, timeout=12):
        """USB attachment can finish before firmware and BlueZ enumeration."""
        deadline = time.monotonic() + timeout
        while True:
            self._objects_at = 0
            controller = self.controller()
            if (controller.get("available") and controller.get("mac") and
                    controller["mac"] != "00:00:00:00:00:00"):
                return controller
            if time.monotonic() >= deadline:
                raise HubError("Selected adapter did not initialize after waiting for USB and firmware. Check attachment and firmware logs; previous hardware selection preserved.")
            time.sleep(0.25)

    def initialize(self):
        """Only this explicit action may change configuration or restart audio services."""
        output_controller = self.wait_controller()
        self.ensure_adapter_name(output_controller, "ShareSpeaker")
        if self.split_input():
            receiver = self.input_hub()
            input_controller = receiver.wait_controller()
            receiver.ensure_adapter_name(input_controller, "ShareSpeaker Input")
            receiver.set_property(input_controller["path"], "org.bluez.Adapter1", "Powered", "b", "true")
            self.set_property(self.controller()["path"], "org.bluez.Adapter1", "Powered", "b", "true")
        socket_dropin = self.home / ".config/systemd/user/pipewire-pulse.socket.d/90-snoopy-hub.conf"
        service_dropin = self.home / ".config/systemd/user/pipewire-pulse.service.d/90-snoopy-hub.conf"
        changed_socket = self.write_changed(socket_dropin,
            "[Socket]\nListenStream=\nListenStream=%t/snoopy-hub/pulse/native\nDirectoryMode=0700\n")
        changed_service = self.write_changed(service_dropin,
            "[Service]\nEnvironment=PULSE_RUNTIME_PATH=%t/snoopy-hub/pulse\n"
            "Environment=PIPEWIRE_RUNTIME_DIR=%t\nEnvironment=PIPEWIRE_REMOTE=pipewire-0\n")
        wp = self.run(["wireplumber", "--version"])
        root = self.home / ".config/wireplumber"
        if "0.4." in wp:
            config = root / "bluetooth.lua.d/51-bluetooth-phone-input.lua"
            obsolete = root / "wireplumber.conf.d/51-bluetooth-phone-input.conf"
            content = '''-- ShareSpeaker: use hardware codec defaults; expose phone media as input.
bluez_monitor.properties["with-logind"] = false
table.insert(bluez_monitor.rules, {
  matches = { { { "node.name", "matches", "bluez_input.*" } } },
  apply_properties = { ["bluez5.media-source-role"] = "input" },
})
'''
        elif re.search(r"\b0\.[5-9]\.|\b[1-9]\d*\.", wp):
            config = root / "wireplumber.conf.d/51-bluetooth-phone-input.conf"
            obsolete = root / "bluetooth.lua.d/51-bluetooth-phone-input.lua"
            content = '''# ShareSpeaker: default codecs, dedicated headless Bluetooth session.
wireplumber.profiles = { main = { monitor.bluez.seat-monitoring = disabled } }
monitor.bluez.rules = [
  { matches = [ { node.name = "~bluez_input.*" } ]
    actions = { update-props = { bluez5.media-source-role = "input" } } }
]
'''
        else:
            raise HubError("Unrecognized WirePlumber version: " + wp)
        changed_wp = self.write_changed(config, content)
        if obsolete.exists():
            obsolete.rename(obsolete.with_name(obsolete.name + f".disabled-{int(time.time())}"))
            changed_wp = True
        if changed_socket or changed_service:
            self.run(["systemctl", "--user", "daemon-reload"])
            self.run(["systemctl", "--user", "stop", "pipewire-pulse.service", "pipewire-pulse.socket"])
            self.run(["systemctl", "--user", "start", "pipewire-pulse.socket", "pipewire-pulse.service"])
            self.message = ("Dedicated PipeWire interface created, bypassing the WSLg audio bridge.")
        else:
            self.run(["systemctl", "--user", "start", "pipewire", "pipewire-pulse", "wireplumber"])
        if changed_wp:
            self.run(["systemctl", "--user", "restart", "wireplumber"])
            self.message = ("Phone input configured; system default codec negotiation restored.")
        for _ in range(30):
            try:
                self.require_audio()
                self.message = ("Hub ready. Normal connections and routing keep audio services running.")
                return self.snapshot()
            except HubError:
                time.sleep(0.2)
        raise HubError("Dedicated PipeWire interface unavailable. Check diagnostics.")

    def require_mac(self, mac):
        if not re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", mac):
            raise HubError("Invalid Bluetooth address.")
        return mac.upper()

    def find_endpoint(self, mac, direction):
        kind = "sinks" if direction == "output" else "sources"
        return next((n for n in self.audio_items(kind)
                     if self.endpoint_matches(n, mac, direction) and not n["name"].endswith(".monitor")), None)

    def ensure_profile(self, mac, direction):
        cards = [c for c in self.audio_items("cards") if self.endpoint_matches(c, mac, direction)]
        if not cards:
            return
        card = cards[0]
        profiles = card.get("profiles", {})
        current = card.get("active_profile", "")
        if isinstance(current, dict):
            current = current.get("name", "")
        # PipeWire 1.0 exposes a phone's incoming A2DP under audio-gateway.
        if direction == "input" and current == "audio-gateway":
            return
        prefix = "a2dp-sink" if direction == "output" else "a2dp-source"
        if str(current).startswith(prefix):
            return
        available = [name for name, props in profiles.items()
                     if name.startswith(prefix) and props.get("available") != "no"]
        if available:
            chosen = prefix if prefix in available else sorted(available, key=lambda n: ("sbc" not in n, n))[0]
            self.pulse("set-card-profile", str(card.get("index", card["name"])), chosen)

    def connect(self, mac, direction=None):
        self.require_audio()
        mac = self.require_mac(mac)
        device = self.read_device(mac)
        direction = direction or device["role"]
        self.require_audio_device(device)
        if not device["paired"]:
            raise HubError(f"{device['name']} is not paired with this adapter. Click Pair / Trust before connecting.")
        if not device["trusted"]:
            self.set_property(self.device_path(mac), "org.bluez.Device1", "Trusted", "b", "true")
        if not device["connected"] or (direction == "input" and not self.find_endpoint(mac, direction)):
            self.message = (f"Connecting {device['name']}…")
            profile = "0000110b-0000-1000-8000-00805f9b34fb" if direction == "output" else "0000110a-0000-1000-8000-00805f9b34fb"
            detail = self.dbus_call(self.device_path(mac), "org.bluez.Device1", "ConnectProfile", "s", profile, check=False)
            if detail:
                self.message = (detail)
        profile_requested = False
        for attempt in range(20):
            endpoint = self.find_endpoint(mac, direction)
            if endpoint:
                return endpoint
            if attempt == 4:
                self.ensure_profile(mac, direction)
            if attempt == 8 and not profile_requested:
                profile = "0000110b-0000-1000-8000-00805f9b34fb" if direction == "output" else "0000110a-0000-1000-8000-00805f9b34fb"
                self.dbus_call(self.device_path(mac), "org.bluez.Device1", "ConnectProfile", "s", profile, timeout=10, check=False)
                profile_requested = True
            if direction == "input" and attempt >= 5:
                # Phones may expose a source only when media starts. Do not churn profiles.
                device = self.read_device(mac)
                if device["connected"]:
                    self.message = ("Phone connected. Select the input adapter name shown in Hardware on your phone and start playback.")
                    return None
            time.sleep(0.4)
        device = self.read_device(mac)
        raise HubError(f"{device['name']}: " + ("Bluetooth connected, but no audio endpoint is available." if device["connected"] else "Connection failed. Check device power and pairing."))

    @staticmethod
    def require_audio_device(device):
        if device.get("controlOnly"):
            raise HubError("This is a Bose LE control channel without audio. Enter speaker pairing mode, scan again, and select the Bose entry without LE-. Click Pair / Trust.")

    def route_state(self):
        try:
            return json.loads(self.state_file.read_text())
        except (OSError, ValueError):
            return {}

    def owned_modules(self):
        # PulseAudio utils 16 omits module indexes from JSON; short output retains them.
        result = {}
        for line in self.pulse("list", "short", "modules").splitlines():
            fields = line.split("\t", 2)
            if len(fields) == 3 and fields[0].isdigit() and "snoopy_hub_" in fields[2]:
                result[int(fields[0])] = {"name": fields[1], "argument": fields[2]}
        return result

    def unload(self, ids):
        if not ids:
            return
        owned = self.owned_modules()
        for module_id in reversed(ids):
            if module_id in owned:
                self.run(["pactl", f"--server=unix:{self.socket}", "unload-module", str(module_id)], check=False)

    def stop(self, release=False):
        self.require_audio()
        state = self.route_state()
        if release:
            self.stop_discovery()
            self.stop_local_player()
            self.unload(state.get("modules", []))
            self.state_file.unlink(missing_ok=True)
            self.message = ("Hub audio routes released.")
            return
        if not state.get("target"):
            self.message = ("No audio is being forwarded.")
            return
        # Gate the bus instead of releasing the phone's A2DP consumer. Releasing
        # it can leave an active transport producing silence after a rebuild.
        self.pulse("set-sink-mute", state["target"], "1")
        state["paused"] = True
        state["updated"] = time.time()
        self.state_file.write_text(json.dumps(state))
        self.message = ("Forwarding paused; inputs, outputs and volumes preserved. Click Resume / Apply.")

    def resolve_source(self, source):
        if source in ("none", "test", "local-test"):
            return ""
        if not source.startswith("phone:"):
            raise HubError("Select a phone or a device assigned as an input.")
        mac = self.require_mac(source.split(":", 1)[1])
        device = self.read_device(mac)
        if device["role"] != "input":
            raise HubError("This device defaults to output. Assign it as an input in Devices first.")
        endpoint = self.connect(mac, "input")
        if endpoint:
            return endpoint["name"]
        self.message = ("Outputs ready; waiting for phone audio. Playback will connect automatically.")
        return ""

    def apply_route(self):
        self.require_audio()
        outputs = self.request.get("outputs", [])
        if not outputs:
            raise HubError("Select at least one output device.")
        seen, resolved = set(), []
        for spec in outputs:
            mac = self.require_mac(spec.get("mac", ""))
            if mac in seen:
                raise HubError("An output device cannot be selected twice.")
            seen.add(mac)
            if self.read_device(mac)["role"] == "input":
                raise HubError("An input device cannot also be an output.")
            volume, delay = int(spec.get("volume", 50)), int(spec.get("delay", 0))
            if not 0 <= volume <= 100 or not 0 <= delay <= 3000:
                raise HubError("Volume must be 0–100% and delay 0–3000 ms.")
            endpoint = self.find_endpoint(mac, "output") or self.connect(mac, "output")
            resolved.append({"mac": mac, "sink": endpoint["name"], "volume": volume, "delay": delay})
            serial = endpoint.get("properties", {}).get("object.serial")
            if serial is not None:
                resolved[-1]["nodeSerial"] = str(serial)
            sink_target = endpoint.get("index", serial)
            if sink_target is not None:
                resolved[-1]["sinkTarget"] = str(sink_target)
        previous = self.route_state()
        # A test is an overlay on the output bus, never a replacement input.
        # Also protect older GUI clients that still submit source=test.
        testing = self.request.get("action") == "test" or self.request.get("source") == "test"
        paused = bool(previous.get("paused", False)) if testing or self.request.get("preservePause") else False
        source = previous.get("source", "none") if testing else self.request.get("source", "none")
        if source == "test":
            source = "none"
        if testing:
            endpoint = self.find_endpoint(source.split(":", 1)[1], "input") if source.startswith("phone:") else None
            source_name = endpoint["name"] if endpoint else ""
        else:
            source_name = self.resolve_source(source)
        input_source = source
        previous_input = previous.get("inputSource", previous.get("source", ""))
        if source == "local-test" and previous_input.startswith("phone:"):
            # Retain the phone consumer, muted, so switching back does not have
            # to reopen its media transport. Local tones and phone never mix.
            input_source = previous_input
            endpoint = self.find_endpoint(previous_input.split(":", 1)[1], "input")
            source_name = endpoint["name"] if endpoint else ""
        offsets_changed = self.clear_legacy_offsets(resolved)
        owned = self.owned_modules()
        topology = lambda items: [(o["mac"], o["sink"], o["delay"], o.get("nodeSerial"), o.get("sinkTarget", o["sink"])) for o in items]
        if (not offsets_changed and previous.get("busModule") in owned and
                topology(previous.get("outputs", [])) == topology(resolved) and previous.get("source") == source and
                previous.get("sourceName") == source_name and
                all(i in owned for i in previous.get("modules", [])) and
                any(s["name"] == previous.get("target") for s in self.audio_items("sinks"))):
            changed = False
            if previous["outputs"] != resolved:
                for output in resolved:
                    self.pulse("set-sink-volume", output.get("sinkTarget", output["sink"]), str(output["volume"]) + "%")
                    self.pulse("set-sink-mute", output.get("sinkTarget", output["sink"]), "0")
                previous["outputs"] = resolved
                changed = True
                self.message = ("Volumes updated; connections and routes preserved.")
            else:
                self.message = ("Current route is ready; no rebuild needed.")
            if previous.get("paused", False) != paused:
                self.pulse("set-sink-mute", previous["target"], "1" if paused else "0")
                previous["paused"] = paused
                changed = True
                self.message = ("Playback resumed; volumes and audio connections preserved.")
            if changed:
                previous["updated"] = time.time()
                self.state_file.write_text(json.dumps(previous))
            if testing:
                self.play_route_test(previous)
            else:
                self.sync_local_player(previous)
            return previous
        # All modes use one stable bus. Changing output selection must never tear
        # down the phone input, its transport, or the already-playing branches.
        modules, created, branches = [], [], {}
        input_module = None
        token = uuid.uuid4().hex[:10]
        reuse_bus = (previous.get("busModule") in owned and
                     any(s["name"] == previous.get("target") for s in self.audio_items("sinks")))
        target = previous["target"] if reuse_bus else "snoopy_hub_bus_" + token
        def load(*args):
            index = int(self.pulse("load-module", *args))
            created.append(index)
            modules.append(index)
            return index
        try:
            if reuse_bus:
                bus_module = previous["busModule"]
                modules.append(bus_module)
            else:
                bus_module = load("module-null-sink", "sink_name=" + target,
                                  "sink_properties=device.description=ShareSpeaker")
                self.pulse("set-sink-volume", target, "100%")
                if paused:
                    self.pulse("set-sink-mute", target, "1")
            old_outputs = {o["mac"]: o for o in previous.get("outputs", [])}
            for i, output in enumerate(resolved):
                old = old_outputs.get(output["mac"], {})
                branch = previous.get("outputModules", {}).get(output["mac"])
                if (reuse_bus and branch in owned and old.get("sink") == output["sink"] and
                        old.get("delay") == output["delay"] and old.get("nodeSerial") == output.get("nodeSerial") and
                        old.get("sinkTarget", old.get("sink")) == output.get("sinkTarget", output["sink"]) and output["mac"] not in offsets_changed):
                    modules.append(branch)
                else:
                    tag = f"snoopy_hub_{token}_out_{i}"
                    branch = load("module-loopback", "source=" + target + ".monitor", "sink=" + output.get("nodeSerial", output["sink"]),
                                  "latency_msec=" + str(max(20, output["delay"])), "source_dont_move=true", "sink_dont_move=true",
                                  "source_output_properties=media.name=" + tag, "sink_input_properties=media.name=" + tag)
                branches[output["mac"]] = branch
            if (reuse_bus and previous_input == input_source and previous.get("sourceName") == source_name and
                    source_name and previous.get("inputModule") in owned):
                input_module = previous["inputModule"]
                modules.append(input_module)
            elif source_name:
                tag = "snoopy_hub_" + token + "_input"
                input_module = load("module-loopback", "source=" + source_name, "sink=" + target, "latency_msec=200",
                     "source_dont_move=true", "sink_dont_move=true",
                     "source_output_properties=media.name=" + tag, "sink_input_properties=media.name=" + tag)
            for output in resolved:
                self.pulse("set-sink-mute", output.get("sinkTarget", output["sink"]), "0")
                self.pulse("set-sink-volume", output.get("sinkTarget", output["sink"]), str(output["volume"]) + "%")
            self.pulse("set-default-sink", target)
            if source != "local-test":
                self.stop_local_player()
            if input_module and (source == "local-test" or previous.get("source") == "local-test"):
                self.set_input_gate(input_module, target, source == "local-test")
            if reuse_bus and previous.get("paused", False) != paused:
                self.pulse("set-sink-mute", target, "1" if paused else "0")
        except Exception:
            self.unload(created)
            raise
        state = {"outputs": resolved, "source": source, "sourceName": source_name, "inputSource": input_source,
                 "target": target, "busModule": bus_module, "outputModules": branches,
                 "modules": modules, "inputModule": input_module, "paused": paused, "updated": time.time()}
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(state))
        self.unload([i for i in previous.get("modules", []) if i not in modules])
        self.message = (f"Applied routes for {len(resolved)} speakers.")
        if testing:
            self.play_route_test(state)
        else:
            self.sync_local_player(state)
        return state

    def set_input_gate(self, module, target, muted):
        for _ in range(15):
            stream = next((s for s in self.audio_items("sink-inputs")
                           if str(s.get("properties", {}).get("pulse.module.id")) == str(module) and
                           s.get("properties", {}).get("target.object") == target), None)
            if stream:
                self.pulse("set-sink-input-mute", str(stream["index"]), "1" if muted else "0")
                return
            time.sleep(0.1)
        raise HubError("Phone input is changing. Retry the local test tone shortly.")

    def local_player(self):
        try:
            return json.loads(self.player_file.read_text())
        except (OSError, ValueError):
            return {}

    def local_player_alive(self, player):
        try:
            proc = Path("/proc") / str(int(player["pid"]))
            start = proc.joinpath("stat").read_text().rsplit(")", 1)[1].split()[19]
            args = proc.joinpath("cmdline").read_bytes().split(b"\0")
            return (start == str(player["start"]) and str(Path(__file__).with_name("hub_test_audio.py")).encode() in args
                    and player["token"].encode() in args)
        except (OSError, ValueError, KeyError, IndexError):
            return False

    def stop_local_player(self):
        player = self.local_player()
        if self.local_player_alive(player):
            os.killpg(player["pid"], signal.SIGTERM)
            for _ in range(20):
                if not self.local_player_alive(player):
                    break
                time.sleep(0.05)
        self.player_file.unlink(missing_ok=True)

    def sync_local_player(self, state):
        if state.get("source") != "local-test":
            self.stop_local_player()
            return
        player = self.local_player()
        if player.get("target") == state["target"] and self.local_player_alive(player):
            return
        self.stop_local_player()
        self.player_file.parent.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        process = subprocess.Popen([sys.executable, str(Path(__file__).with_name("hub_test_audio.py")), state["target"], token],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   env=self.env, start_new_session=True)
        start = (Path("/proc") / str(process.pid) / "stat").read_text().rsplit(")", 1)[1].split()[19]
        self.player_file.write_text(json.dumps({"pid": process.pid, "start": start, "token": token, "target": state["target"]}))
        time.sleep(0.15)
        if process.poll() is not None:
            raise HubError("Local test tone failed to start. Check that pw-play is installed and the audio service is ready.")
        self.message = ("Local test tone is looping. Pause mutes it; switching to phone stops it.")

    def clear_legacy_offsets(self, outputs):
        """Remove hidden port calibration from old hub versions for selected speakers only."""
        selected = {o["mac"] for o in outputs}
        changed = set()
        backup_file = self.home / ".local/state/snoopy-hub/legacy-port-offsets.json"
        for card in self.audio_items("cards"):
            mac = node_mac(card)
            if mac not in selected or not self.endpoint_matches(card, mac):
                continue
            port = card.get("ports", {}).get("speaker-output", {})
            match = re.match(r"^-?\d+", str(port.get("latency_offset", 0)))
            offset = int(match.group()) if match else 0
            if not offset:
                continue
            backup_file.parent.mkdir(parents=True, exist_ok=True)
            backup = json.loads(backup_file.read_text()) if backup_file.exists() else {}
            backup.setdefault(mac, {"port": "speaker-output", "offsetUsec": offset})
            backup_file.write_text(json.dumps(backup))
            self.pulse("set-port-latency-offset", card["name"], "speaker-output", "0")
            changed.add(mac)
            self.message = (f"Cleared legacy port offset of {offset / 1000:g} ms for {mac}. Previous value backed up; delay is now managed by the hub.")
        return changed

    def recover_input(self):
        """Explicit bounded recovery; preserve volume, speakers, and the USB adapter."""
        self.require_audio()
        state = self.route_state()
        source = state.get("source", "")
        if not source.startswith("phone:") or self.request.get("source", source) != source:
            raise HubError("Select a phone and click Play / Apply before recovering phone audio.")
        if state.get("paused"):
            raise HubError("Forwarding is paused. Click Resume / Apply before recovering phone audio.")
        mac = self.require_mac(source.split(":", 1)[1])
        if not self.read_device(mac)["connected"]:
            raise HubError("Phone is disconnected. Connect it in Devices first.")
        endpoint = self.find_endpoint(mac, "input")
        if endpoint:
            self.reconcile_input()
            probe = self.probe_input(endpoint["name"])
            if probe["peak"] > 0:
                self.message = ("Phone audio detected and route checked. Playback and volumes preserved.")
                return self.snapshot()
        path = self.device_path(mac)
        player = next((p for p, interfaces in self.objects().items()
                       if self.properties(interfaces.get("org.bluez.MediaPlayer1", {})).get("Device") == path), None)
        if not player:
            self.message = ("Input route checked. No phone media controls available; pause and play on the phone. Volumes preserved.")
            return self.snapshot()
        props = self.properties(self.objects()[player]["org.bluez.MediaPlayer1"])
        if props.get("Status") == "playing":
            self.dbus_call(player, "org.bluez.MediaPlayer1", "Pause", timeout=5)
            time.sleep(0.4)
        self.dbus_call(player, "org.bluez.MediaPlayer1", "Play", timeout=5)
        self.message = ("Requested phone playback recovery. Input and speaker volumes preserved.")
        for _ in range(20):
            endpoint = self.find_endpoint(mac, "input")
            if endpoint:
                self.reconcile_input()
                probe = self.probe_input(endpoint["name"])
                if probe["peak"] > 0:
                    self.message = ("Recovered phone audio detected.")
                    return self.snapshot()
                break
            time.sleep(0.4)
        self._objects_at = 0
        current_player = self.properties(self.objects().get(player, {}).get("org.bluez.MediaPlayer1", {}))
        if current_player.get("Status") == "playing":
            # A live endpoint can still produce only zero PCM. One targeted
            # A2DP renegotiation is allowed on this explicit button, never on polls.
            profile = "0000110a-0000-1000-8000-00805f9b34fb"
            self.message = ("Phone reports playback but sends no audio. Renegotiating its media channel once; speakers and volumes preserved.")
            self.dbus_call(path, "org.bluez.Device1", "DisconnectProfile", "s", profile, timeout=10)
            time.sleep(0.5)
            self.dbus_call(path, "org.bluez.Device1", "ConnectProfile", "s", profile, timeout=12)
            self._objects_at = 0
            self.message = ("Phone media channel renegotiated. Select the input adapter name shown in Hardware on your phone and start playback.")
            self.reconcile_input()
            return self.snapshot()
        self.message = ("No phone audio received. On your phone select the input adapter name shown in Hardware again.")
        return self.snapshot()

    def probe_input(self, name):
        """A short in-memory level check, only for explicit recovery. No audio is saved."""
        import array
        process = subprocess.Popen(["parec", "--raw", "--format=s16le", "--rate=16000", "--channels=1",
                                    "--latency-msec=100", "--device=" + name], env=self.env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
        try:
            data, error = process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            process.terminate()
            data, error = process.communicate(timeout=2)
        samples = array.array("h", data[:len(data) // 2 * 2])
        if error and not samples:
            self.message = ("Phone audio channel changed during measurement: " + clean(error.decode(errors="replace")))
        return {"samples": len(samples), "peak": max(map(abs, samples), default=0)}

    def input_node(self, endpoint):
        node_id = str(endpoint.get("properties", {}).get("object.id", ""))
        if not node_id.isdigit():
            raise HubError("Phone audio endpoint changed. Refresh and retry.")
        nodes = json.loads(self.run(["pw-dump", node_id], timeout=5))
        node = next((n for n in nodes if n.get("type") == "PipeWire:Interface:Node" and
                     n.get("info", {}).get("props", {}).get("node.name") == endpoint["name"]), None)
        if not node:
            raise HubError("Phone audio endpoint changed. Refresh and retry.")
        return node

    def reconcile_input(self):
        """Resume the explicitly selected input when its dynamic source returns. No reconnects."""
        self.require_audio()
        state = self.route_state()
        if not state.get("source", "").startswith("phone:"):
            return self.snapshot()
        source = self.find_endpoint(state["source"].split(":", 1)[1], "input")
        sink_names = {s["name"] for s in self.audio_items("sinks")}
        owned = self.owned_modules()
        if (not source or state["target"] not in sink_names or
                any(o["sink"] not in sink_names for o in state.get("outputs", [])) or
                any(i not in owned for i in state.get("modules", []) if i != state.get("inputModule"))):
            return self.snapshot()
        old_input = state.get("inputModule")
        if old_input in owned and state.get("sourceName") == source["name"]:
            return self.snapshot()
        tag = "snoopy_hub_" + uuid.uuid4().hex[:10] + "_input"
        new_input = int(self.pulse("load-module", "module-loopback", "source=" + source["name"], "sink=" + state["target"],
                                  "latency_msec=200", "source_dont_move=true", "sink_dont_move=true",
                                  "source_output_properties=media.name=" + tag, "sink_input_properties=media.name=" + tag))
        # Pulse module IDs can be reused after source suspension. Never unload
        # a newly allocated module just because it has the previous numeric ID.
        if old_input in owned and old_input != new_input:
            self.unload([old_input])
        state["modules"] = [i for i in state.get("modules", []) if i != old_input] + [new_input]
        state["inputModule"] = new_input
        state["sourceName"] = source["name"]
        self.state_file.write_text(json.dumps(state))
        self.message = ("Phone audio received and connected to current outputs.")
        return self.snapshot()

    def play_route_test(self, state):
        if state.get("source") == "local-test":
            self.message = ("Local test tone selected. Click Resume to hear the loop." if state.get("paused") else "Local test tone is already looping.")
            return
        if not state.get("paused"):
            return self.play_test(state["target"])
        # While paused, allow only the test tone through the bus. Keep the phone
        # consumer alive and restore both mute states even if playback fails.
        inputs = self.audio_items("sink-inputs")
        phone = next((s for s in inputs if str(s.get("properties", {}).get("pulse.module.id")) == str(state.get("inputModule"))
                      and s.get("properties", {}).get("target.object") == state["target"]), None)
        if state.get("inputModule") in self.owned_modules() and phone is None:
            raise HubError("Phone input is changing. Retry the test shortly. Pause preserved.")
        if phone:
            self.pulse("set-sink-input-mute", str(phone["index"]), "1")
        try:
            self.pulse("set-sink-mute", state["target"], "0")
            self.play_test(state["target"])
        finally:
            self.pulse("set-sink-mute", state["target"], "1")
            if phone:
                live = next((s for s in self.audio_items("sink-inputs") if s.get("index") == phone["index"] and
                             s.get("properties", {}).get("pulse.module.id") == phone.get("properties", {}).get("pulse.module.id")), None)
                if live:
                    self.pulse("set-sink-input-mute", str(phone["index"]), "1" if phone.get("mute", False) else "0")

    def play_test(self, target):
        import math
        import struct
        import wave
        path = self.runtime / "snoopy-hub/test.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as wav:
            wav.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
            data = bytearray()
            for sample in range(48000 * 4):
                phase = sample % 48000
                envelope = min(1, phase / 240, max(0, (2400 - phase) / 240)) if phase < 2400 else 0
                value = int(3500 * envelope * math.sin(2 * math.pi * 800 * sample / 48000))
                data.extend(struct.pack("<hh", value, value))
            wav.writeframes(data)
        self.run(["pw-play", "--target", target, str(path)], timeout=12)
        self.message = ("Four-second test tone played.")

    def dispatch(self):
        action = self.request.get("action", "status")
        if action == "rename-device":
            self.rename_device(self.require_mac(self.request.get("mac", "")), self.request.get("name"))
            return self.snapshot(True)
        if action == "rename-adapter":
            self.rename_adapter(self.request.get("direction"), self.request.get("name"))
            return self.snapshot(True)
        if action == "diagnose":
            data = self.snapshot()
            data["transports"] = {path: self.properties(interfaces["org.bluez.MediaTransport1"])
                                  for path, interfaces in self.objects().items() if "org.bluez.MediaTransport1" in interfaces}
            data["players"] = {path: self.properties(interfaces["org.bluez.MediaPlayer1"])
                               for path, interfaces in self.objects().items() if "org.bluez.MediaPlayer1" in interfaces}
            data["services"] = self.run(["systemctl", "--user", "show", "pipewire", "pipewire-pulse", "wireplumber", "--property=Id,MainPID,ActiveState"], check=False)
            data["pulseIdentity"] = self.pulse("info") if data["serverReady"] else ""
            return data
        if action == "status":
            return self.snapshot(self.request.get("discovered", False))
        if action == "initialize":
            return self.initialize()
        if action in ("route", "test"):
            self.apply_route()
            return self.snapshot()
        if action == "recover-input":
            return self.recover_input()
        if action == "reconcile":
            return self.reconcile_input()
        if action in ("stop", "release"):
            self.stop(release=action == "release")
            return self.snapshot()
        if action == "scan":
            self.start_discovery()
            return self.snapshot(True)
        if action == "scan-stop":
            self.stop_discovery()
            return self.snapshot(True)
        if action == "pair-window":
            self.require_audio()
            receiver = self.input_hub() if self.split_input() else self
            controller = receiver.controller()
            path = controller.get("path")
            if not path:
                raise HubError("Start the hub and select an adapter first.")
            name = self.ensure_adapter_name(controller, self.default_adapter_name("input" if self.split_input() else "output"))
            self.message = ("On your phone, select " + name + ".")
            self.set_property(path, "org.bluez.Adapter1", "Powered", "b", "true")
            self.set_property(path, "org.bluez.Adapter1", "Pairable", "b", "true")
            self.set_property(path, "org.bluez.Adapter1", "Discoverable", "b", "true")
            try:
                receiver.agent_session([], 90)
            finally:
                self.set_property(path, "org.bluez.Adapter1", "Discoverable", "b", "false")
            return self.snapshot(True)
        mac = self.require_mac(self.request.get("mac", ""))
        if action == "connect":
            self.connect(mac)
        elif action == "pair":
            device = self.read_device(mac)
            self.require_audio_device(device)
            if not device["paired"]:
                receiver = self.input_hub() if self.split_input() and self.is_input_mac(mac) else self
                receiver.agent_session(["pair " + mac], 30, mac)
            if not self.read_device(mac)["paired"]:
                raise HubError("Pairing failed. Put the device in pairing mode and retry.")
            self.set_property(self.device_path(mac), "org.bluez.Device1", "Trusted", "b", "true")
        elif action == "disconnect":
            self.dbus_call(self.device_path(mac), "org.bluez.Device1", "Disconnect")
        elif action == "remove":
            self.dbus_call(self.controller()["path"], "org.bluez.Adapter1", "RemoveDevice", "o", self.device_path(mac))
        else:
            raise HubError("Unknown action: " + action)
        return self.snapshot(True)

    def agent_session(self, commands, seconds, pair_mac=None):
        import tempfile
        with tempfile.TemporaryFile(mode="w+") as output:
            process = subprocess.Popen(["bluetoothctl", "--agent", "NoInputNoOutput"],
                                       stdin=subprocess.PIPE, stdout=output, stderr=output,
                                       text=True, env=self.env)
            try:
                lines = ["select " + self.controller()["mac"], "default-agent", *commands]
                process.stdin.write("\n".join(lines) + "\n")
                process.stdin.flush()
                deadline = time.monotonic() + seconds
                while time.monotonic() < deadline and process.poll() is None:
                    if pair_mac and self.read_device(pair_mac)["paired"]:
                        break
                    time.sleep(0.5)
            finally:
                if process.poll() is None:
                    process.stdin.write("quit\n")
                    process.stdin.flush()
                    try:
                        process.wait(3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                process.stdin.close()


def main():
    hub = None
    try:
        request = json.loads(base64.b64decode(sys.argv[1]).decode("utf-8")) if len(sys.argv) > 1 else {"action": "status"}
        hub = Hub(request)
        import fcntl
        lock_path = hub.runtime / "snoopy-hub-operation.lock"
        with lock_path.open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise HubError("The hub is running another operation. Refresh shortly.")
            data = hub.dispatch()
        result = {"ok": True, "data": data, "message": hub.message}
    except Exception as exc:
        result = {"ok": False, "error": str(exc), "message": hub.message if hub else ""}
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
