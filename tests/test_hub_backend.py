"""Isolated backend tests; all Bluetooth addresses and device identities are synthetic."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hub_backend import Hub, HubError, device_role, node_mac, has_device_name, validate_bluetooth_name


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.hub = Hub({"source": "test", "outputs": [
            {"mac": f"AA:BB:CC:DD:EE:{i:02X}", "volume": 45, "delay": i * 30} for i in range(3)]})
        self.hub.state_file = Path(self.temp.name) / "route.json"
        self.hub.home = Path(self.temp.name)
        self.hub.require_audio = Mock()
        self.hub.read_device = Mock(side_effect=lambda mac: {"role": "output"})
        self.hub.find_endpoint = Mock(side_effect=lambda mac, direction: {"name": "bluez_output." + mac.replace(":", "_") + ".3"})
        self.hub.play_test = Mock()
        self.hub.stop_local_player = Mock()
        self.hub.sync_local_player = Mock()
        self.hub.set_input_gate = Mock()
        self.hub.unload = Mock()
        self.hub.owned_modules = Mock(return_value={})
        self.hub.audio_items = Mock(return_value=[])
        self.calls = []
        self.next_module = 100
        def pulse(*args):
            self.calls.append(args)
            if args[0] == "load-module":
                self.next_module += 1
                return str(self.next_module)
            return ""
        self.hub.pulse = Mock(side_effect=pulse)

    def test_three_outputs_receive_one_loopback_each(self):
        state = self.hub.apply_route()
        loads = [args for args in self.calls if args[0] == "load-module"]
        self.assertEqual(4, len(loads))
        self.assertEqual(3, len([args for args in loads if args[1] == "module-loopback"]))
        self.assertEqual(3, len(state["outputs"]))
        self.assertTrue(all(out["sink"].endswith(".3") for out in state["outputs"]))

    def test_missing_output_keeps_previous_route(self):
        previous = {"target": "previous", "modules": [77]}
        self.hub.state_file.write_text(json.dumps(previous))
        self.hub.find_endpoint = Mock(return_value=None)
        self.hub.connect = Mock(side_effect=HubError("offline"))
        with self.assertRaises(HubError):
            self.hub.apply_route()
        self.assertEqual(previous, json.loads(self.hub.state_file.read_text()))
        self.hub.unload.assert_not_called()

    def test_recreated_codec_node_rebinds_only_that_branch(self):
        serials = {o["mac"]: str(i + 10) for i, o in enumerate(self.hub.request["outputs"])}
        self.hub.find_endpoint.side_effect = lambda mac, direction: {
            "name": "bluez_output." + mac.replace(":", "_"), "properties": {"object.serial": serials[mac]}}
        self.hub.request["source"] = "phone:02:00:00:00:00:10"
        self.hub.resolve_source = Mock(return_value="bluez_input.phone")
        before = self.hub.apply_route()
        self.hub.owned_modules.return_value = dict.fromkeys(before["modules"], {})
        self.hub.audio_items.return_value = [{"name": before["target"]}]
        changed = self.hub.request["outputs"][0]["mac"]
        serials[changed] = "99"
        self.calls.clear()
        after = self.hub.apply_route()
        self.assertEqual(before["inputModule"], after["inputModule"])
        self.assertEqual(before["target"], after["target"])
        self.assertEqual(1, len([c for c in self.calls if c[0] == "load-module"]))
        self.assertNotEqual(before["outputModules"][changed], after["outputModules"][changed])
        for mac in serials:
            if mac != changed:
                self.assertEqual(before["outputModules"][mac], after["outputModules"][mac])

    def test_failed_candidate_rolls_back_only_new_modules(self):
        previous = {"target": "previous", "modules": [77]}
        self.hub.state_file.write_text(json.dumps(previous))
        self.hub.pulse = Mock(side_effect=["101", HubError("loopback failed")])
        with self.assertRaises(HubError):
            self.hub.apply_route()
        self.hub.unload.assert_called_once_with([101])
        self.assertEqual(previous, json.loads(self.hub.state_file.read_text()))

    def test_reapply_unchanged_route_does_not_rebuild(self):
        previous = {"outputs": [{"mac": o["mac"], "sink": self.hub.find_endpoint(o["mac"], "output")["name"],
                                 "volume": o["volume"], "delay": o["delay"]} for o in self.hub.request["outputs"]],
                    "source": "none", "sourceName": "", "target": "snoopy_hub_sync_old", "busModule": 77, "modules": [77]}
        self.hub.state_file.write_text(json.dumps(previous))
        self.hub.owned_modules.return_value = {77: {}}
        self.hub.audio_items.return_value = [{"name": previous["target"]}]
        self.hub.apply_route()
        self.hub.pulse.assert_not_called()

    def test_volume_only_change_preserves_modules(self):
        previous = self.hub.apply_route()
        self.calls.clear()
        self.hub.owned_modules.return_value = dict.fromkeys(previous["modules"], {})
        self.hub.audio_items.return_value = [{"name": previous["target"]}]
        self.hub.request["outputs"][0]["volume"] = 30
        current = self.hub.apply_route()
        self.assertEqual(previous["modules"], current["modules"])
        self.assertTrue(all(call[0] in ("set-sink-volume", "set-sink-mute") for call in self.calls))

    def test_single_dual_switch_keeps_phone_input_and_bus(self):
        self.hub.request["source"] = "phone:02:00:00:00:00:10"
        self.hub.resolve_source = Mock(return_value="bluez_input.02_00_00_00_00_10.2")
        original_outputs = list(self.hub.request["outputs"])
        self.hub.request["outputs"] = original_outputs[:1]
        single = self.hub.apply_route()
        self.hub.owned_modules.return_value = dict.fromkeys(single["modules"], {})
        self.hub.audio_items.return_value = [{"name": single["target"]}]
        self.calls.clear()
        self.hub.request["outputs"] = original_outputs[:2]
        dual = self.hub.apply_route()
        self.assertEqual(single["target"], dual["target"])
        self.assertEqual(single["inputModule"], dual["inputModule"])
        self.assertEqual(len(dual["modules"]), len(set(dual["modules"])))
        self.assertEqual(single["outputModules"][original_outputs[0]["mac"]], dual["outputModules"][original_outputs[0]["mac"]])
        self.assertEqual(1, len([c for c in self.calls if c[0] == "load-module"]))
        self.hub.owned_modules.return_value = dict.fromkeys(dual["modules"], {})
        self.calls.clear()
        self.hub.request["outputs"] = original_outputs[1:2]
        other_single = self.hub.apply_route()
        self.assertEqual(dual["inputModule"], other_single["inputModule"])
        self.assertFalse(any(c[0] == "load-module" for c in self.calls))
        self.hub.unload.assert_called_with([single["outputModules"][original_outputs[0]["mac"]]])

    def test_idle_phone_keeps_pending_route_then_joins_once(self):
        self.hub.request["source"] = "phone:02:00:00:00:00:10"
        self.hub.resolve_source = Mock(return_value="")
        state = self.hub.apply_route()
        self.assertIsNone(state["inputModule"])
        self.hub.play_test.assert_not_called()
        self.hub.snapshot = Mock(return_value={})
        self.hub.audio_items.return_value = [{"name": state["target"]}] + [{"name": o["sink"]} for o in state["outputs"]]
        self.hub.owned_modules.return_value = dict.fromkeys(state["modules"], {})
        self.hub.find_endpoint.return_value = None
        self.hub.find_endpoint.side_effect = None
        self.calls.clear()
        self.hub.reconcile_input()
        self.assertEqual([], self.calls)
        self.hub.find_endpoint.return_value = {"name": "bluez_input.02_00_00_00_00_10.4096"}
        self.hub.reconcile_input()
        updated = self.hub.route_state()
        self.assertEqual(1, len([c for c in self.calls if c[0] == "load-module"]))
        self.assertEqual(state["modules"], updated["modules"][:-1])
        self.hub.owned_modules.return_value = dict.fromkeys(updated["modules"], {})
        self.calls.clear()
        self.hub.reconcile_input()
        self.assertEqual([], self.calls)

    def test_delay_change_only_replaces_that_output_branch(self):
        self.hub.request["source"] = "phone:02:00:00:00:00:10"
        self.hub.resolve_source = Mock(return_value="bluez_input.02_00_00_00_00_10.2")
        previous = self.hub.apply_route()
        self.hub.audio_items.return_value = [{"name": previous["target"]}]
        self.hub.owned_modules.return_value = dict.fromkeys(previous["modules"], {})
        self.calls.clear()
        self.hub.request["outputs"][0]["delay"] = 250
        current = self.hub.apply_route()
        self.assertEqual(previous["inputModule"], current["inputModule"])
        self.assertEqual(previous["busModule"], current["busModule"])
        self.assertEqual(1, len([c for c in self.calls if c[0] == "load-module"]))
        self.hub.unload.assert_called_with([previous["outputModules"][self.hub.request["outputs"][0]["mac"]]])

    def test_input_does_not_resume_into_missing_output(self):
        state = {"source": "phone:02:00:00:00:00:10", "sourceName": "", "target": "snoopy_hub_sync_old",
                 "modules": [77], "outputs": [{"sink": "missing_speaker"}]}
        self.hub.state_file.write_text(json.dumps(state))
        self.hub.snapshot = Mock(return_value={})
        self.hub.owned_modules.return_value = {77: {}}
        self.hub.audio_items.return_value = [{"name": state["target"]}]
        self.hub.reconcile_input()
        self.hub.pulse.assert_not_called()

    def test_resumed_input_can_reuse_previous_module_id(self):
        state = {"source": "phone:02:00:00:00:00:10", "sourceName": "old_phone_source", "target": "snoopy_hub_bus_old",
                 "modules": [77, 78], "inputModule": 78, "outputs": [{"sink": "speaker"}]}
        self.hub.state_file.write_text(json.dumps(state))
        self.hub.snapshot = Mock(return_value={})
        self.hub.owned_modules.return_value = {77: {}}
        self.hub.audio_items.return_value = [{"name": state["target"]}, {"name": "speaker"}]
        self.hub.find_endpoint = Mock(return_value={"name": "new_phone_source"})
        self.hub.pulse = Mock(return_value="78")
        self.hub.reconcile_input()
        self.hub.unload.assert_not_called()
        self.assertEqual(78, self.hub.route_state()["inputModule"])

    def test_test_tone_preserves_phone_and_all_existing_modules(self):
        phone = "phone:02:00:00:00:00:10"
        self.hub.request["source"] = phone
        source = "bluez_input.02_00_00_00_00_10.2"
        self.hub.resolve_source = Mock(return_value=source)
        previous = self.hub.apply_route()
        self.hub.owned_modules.return_value = dict.fromkeys(previous["modules"], {})
        self.hub.audio_items.return_value = [{"name": previous["target"]}]
        original_find = self.hub.find_endpoint.side_effect
        self.hub.find_endpoint.side_effect = lambda mac, direction: ({"name": source} if direction == "input" else original_find(mac, direction))
        self.hub.resolve_source.reset_mock()
        self.calls.clear()
        # Legacy GUI test selection is supported without cutting the phone off.
        self.hub.request["source"] = "test"
        current = self.hub.apply_route()
        self.assertEqual(phone, current["source"])
        self.assertEqual(previous["modules"], current["modules"])
        self.assertEqual([], self.calls)
        self.hub.resolve_source.assert_not_called()
        self.hub.play_test.assert_called_once_with(previous["target"])

    def test_new_test_button_keeps_pending_phone_without_connecting_it(self):
        self.hub.request["source"] = "phone:02:00:00:00:00:10"
        self.hub.resolve_source = Mock(return_value="")
        previous = self.hub.apply_route()
        self.hub.owned_modules.return_value = dict.fromkeys(previous["modules"], {})
        self.hub.audio_items.return_value = [{"name": previous["target"]}]
        original_find = self.hub.find_endpoint.side_effect
        self.hub.find_endpoint.side_effect = lambda mac, direction: None if direction == "input" else original_find(mac, direction)
        self.hub.resolve_source.reset_mock()
        self.hub.request.update(action="test", source="none")
        current = self.hub.apply_route()
        self.assertEqual(previous["source"], current["source"])
        self.assertEqual(previous["modules"], current["modules"])
        self.hub.resolve_source.assert_not_called()

    def test_hidden_offset_is_backed_up_and_only_selected_branch_rebuilt(self):
        previous = self.hub.apply_route()
        self.hub.owned_modules.return_value = dict.fromkeys(previous["modules"], {})
        selected = self.hub.request["outputs"][0]["mac"]
        def card(mac):
            return {"name": "bluez_card." + mac.replace(":", "_"), "ports": {"speaker-output": {"latency_offset": "740000 usec"}}}
        self.hub.audio_items.side_effect = lambda kind: [card(selected), card("00:00:00:00:00:00")] if kind == "cards" else [{"name": previous["target"]}]
        self.calls.clear()
        current = self.hub.apply_route()
        offset_calls = [c for c in self.calls if c[0] == "set-port-latency-offset"]
        self.assertEqual([("set-port-latency-offset", card(selected)["name"], "speaker-output", "0")], offset_calls)
        self.assertEqual(1, len([c for c in self.calls if c[0] == "load-module"]))
        self.assertEqual(previous["busModule"], current["busModule"])
        self.assertEqual(740000, json.loads((self.hub.home / ".local/state/snoopy-hub/legacy-port-offsets.json").read_text())[selected]["offsetUsec"])

    def test_pause_resume_keeps_phone_bus_modules_and_all_volumes(self):
        self.hub.request["source"] = "phone:02:00:00:00:00:10"
        self.hub.resolve_source = Mock(return_value="phone_source")
        before = self.hub.apply_route()
        self.hub.owned_modules.return_value = dict.fromkeys(before["modules"], {})
        self.hub.audio_items.return_value = [{"name": before["target"]}]
        self.hub.unload.reset_mock()
        self.calls.clear()
        for _ in range(3):
            self.hub.stop()
            self.assertTrue(self.hub.route_state()["paused"])
            after = self.hub.apply_route()
            self.assertFalse(after["paused"])
            self.assertEqual(before["modules"], after["modules"])
            self.assertEqual(before["outputs"], after["outputs"])
        self.assertEqual([("set-sink-mute", before["target"], value) for value in ("1", "0") * 3], self.calls)
        self.hub.unload.assert_not_called()

    def test_release_is_distinct_from_pause(self):
        before = self.hub.apply_route()
        self.hub.unload.reset_mock()
        self.hub.stop(release=True)
        self.hub.unload.assert_called_once_with(before["modules"])
        self.assertEqual({}, self.hub.route_state())
        self.hub.stop_local_player.assert_called()

    def test_local_test_roundtrip_keeps_phone_consumer_and_speaker_volumes(self):
        phone = "phone:02:00:00:00:00:10"
        self.hub.request["source"] = phone
        self.hub.resolve_source = Mock(return_value="phone_source")
        before = self.hub.apply_route()
        self.hub.owned_modules.return_value = dict.fromkeys(before["modules"], {})
        self.hub.audio_items.return_value = [{"name": before["target"]}]
        original_find = self.hub.find_endpoint.side_effect
        self.hub.find_endpoint.side_effect = lambda mac, direction: {"name": "phone_source"} if direction == "input" else original_find(mac, direction)
        self.hub.request["source"] = "local-test"
        self.hub.resolve_source.return_value = ""
        local = self.hub.apply_route()
        self.assertEqual(before["inputModule"], local["inputModule"])
        self.assertEqual(before["outputs"], local["outputs"])
        self.hub.set_input_gate.assert_called_with(before["inputModule"], before["target"], True)
        self.hub.request["source"] = phone
        self.hub.resolve_source.return_value = "phone_source"
        restored = self.hub.apply_route()
        self.assertEqual(before["modules"], restored["modules"])
        self.assertEqual(before["outputs"], restored["outputs"])
        self.hub.set_input_gate.assert_called_with(before["inputModule"], before["target"], False)

    def test_paused_test_restores_mutes_even_when_tone_fails(self):
        state = {"paused": True, "target": "bus", "inputModule": 88}
        phone = {"index": 7, "mute": False, "properties": {"pulse.module.id": "88", "target.object": "bus"}}
        self.hub.audio_items.return_value = [phone]
        self.hub.owned_modules.return_value = {88: {}}
        self.hub.play_test.side_effect = HubError("test failed")
        with self.assertRaises(HubError):
            self.hub.play_route_test(state)
        self.assertEqual([("set-sink-input-mute", "7", "1"), ("set-sink-mute", "bus", "0"),
                          ("set-sink-mute", "bus", "1"), ("set-sink-input-mute", "7", "0")], self.calls)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.hub = Hub({"source": "phone:02:00:00:00:00:10"})
        self.hub.require_audio = Mock()
        self.hub.route_state = Mock(return_value={"source": self.hub.request["source"]})
        self.hub.read_device = Mock(return_value={"connected": True})
        self.hub.find_endpoint = Mock(return_value={"name": "phone_source"})
        self.hub.reconcile_input = Mock()
        self.hub.snapshot = Mock(return_value={})
        self.hub.dbus_call = Mock()
        self.hub.connect = Mock()
        self.hub.device_path = Mock(return_value="/phone")
        self.hub.objects = Mock(return_value={})
        self.hub.run = Mock()
        self.hub.pulse = Mock()
        self.hub.probe_input = Mock(return_value={"peak": 200, "samples": 32000})

    def test_ready_phone_never_gets_paused_or_reconnected(self):
        self.hub.recover_input()
        self.hub.reconcile_input.assert_called_once()
        self.hub.dbus_call.assert_not_called()
        self.hub.connect.assert_not_called()
        self.hub.run.assert_not_called()
        self.hub.pulse.assert_not_called()

    @patch("hub_backend.time.sleep")
    def test_idle_playing_phone_gets_one_media_resume(self, sleep):
        self.hub.find_endpoint.side_effect = [None, {"name": "phone_source"}]
        self.hub.device_path = Mock(return_value="/phone")
        self.hub.objects = Mock(return_value={"/phone/player0": {"org.bluez.MediaPlayer1": {"Device": "/phone", "Status": "playing"}}})
        self.hub.recover_input()
        self.assertEqual(["Pause", "Play"], [c.args[2] for c in self.hub.dbus_call.call_args_list])
        self.hub.reconcile_input.assert_called_once()
        self.hub.connect.assert_not_called()
        self.hub.run.assert_not_called()
        self.hub.pulse.assert_not_called()

    def test_recovery_cannot_silently_switch_selected_phone(self):
        self.hub.request["source"] = "phone:AA:BB:CC:DD:EE:FF"
        with self.assertRaises(HubError):
            self.hub.recover_input()
        self.hub.dbus_call.assert_not_called()

    def test_input_node_inspection_rejects_reused_node_id(self):
        hub = Hub({})
        hub.run = Mock(return_value=json.dumps([{"id": 63, "type": "PipeWire:Interface:Node", "info": {"props": {"node.name": "another_device"}}}]))
        with self.assertRaises(HubError):
            hub.input_node({"name": "phone_source", "properties": {"object.id": "63"}})
        self.assertEqual(1, hub.run.call_count)

    def test_recovery_does_not_resume_a_paused_route(self):
        self.hub.route_state.return_value["paused"] = True
        with self.assertRaises(HubError):
            self.hub.recover_input()
        self.hub.dbus_call.assert_not_called()
        self.hub.run.assert_not_called()

    @patch("hub_backend.time.sleep")
    def test_silent_playing_phone_gets_only_one_targeted_profile_cycle(self, sleep):
        self.hub.probe_input.return_value = {"peak": 0, "samples": 32000}
        self.hub.objects.return_value = {"/phone/player0": {"org.bluez.MediaPlayer1": {"Device": "/phone", "Status": "playing"}}}
        self.hub.recover_input()
        calls = self.hub.dbus_call.call_args_list
        self.assertEqual(["Pause", "Play", "DisconnectProfile", "ConnectProfile"], [c.args[2] for c in calls])
        for call in calls[2:]:
            self.assertEqual("/phone", call.args[0])
            self.assertEqual("0000110a-0000-1000-8000-00805f9b34fb", call.args[4])
        self.hub.run.assert_not_called()
        self.hub.pulse.assert_not_called()
        self.hub.connect.assert_not_called()

    @patch("hub_backend.time.sleep")
    def test_silent_stopped_player_is_not_profile_cycled(self, sleep):
        self.hub.probe_input.return_value = {"peak": 0, "samples": 32000}
        self.hub.objects.return_value = {"/phone/player0": {"org.bluez.MediaPlayer1": {"Device": "/phone", "Status": "stopped"}}}
        self.hub.recover_input()
        self.assertEqual(["Play"], [c.args[2] for c in self.hub.dbus_call.call_args_list])
        self.hub.pulse.assert_not_called()


class IdentityTests(unittest.TestCase):
    def test_module_ids_use_pulse_short_output(self):
        hub = Hub({})
        hub.pulse = Mock(return_value='536870913\tmodule-null-sink\tsink_name=snoopy_hub_sync_example\n22\tother-module\targ=value\n')
        self.assertEqual([536870913], list(hub.owned_modules()))

    def test_bose_with_source_uuid_remains_output(self):
        device = {"mac": "02:00:00:00:00:21", "icon": "audio-card", "uuids": "0000110a-0000 0000110b-0000"}
        self.assertEqual("output", device_role(device, {}))
        self.assertEqual("input", device_role(device, {device["mac"]: "input"}))

    def test_phone_is_input(self):
        self.assertEqual("input", device_role({"mac": "AA", "icon": "phone", "uuids": ""}, {}))

    def test_wslg_server_rejected_before_any_route(self):
        hub = Hub({})
        hub.pulse = Mock(return_value="Server Name: pulseaudio\nDefault Sink: RDPSink")
        with self.assertRaises(HubError):
            hub.require_audio()

    def test_external_controller_not_hardcoded_hci0(self):
        hub = Hub({"controller": "AA:BB:CC:DD:EE:FF"})
        hub.objects = Mock(return_value={"/org/bluez/hci7": {"org.bluez.Adapter1": {
            "Address": {"type": "s", "data": "AA:BB:CC:DD:EE:FF"}, "Powered": {"type": "b", "data": True}}}})
        self.assertEqual("/org/bluez/hci7/dev_02_00_00_00_00_40", hub.device_path("02:00:00:00:00:40"))

    def test_dynamic_endpoint_name_matches_mac(self):
        self.assertEqual("02:00:00:00:00:22", node_mac({"name": "bluez_output.02_00_00_00_00_22.a2dp-sink"}))

    def test_initialize_idempotent_and_native_config(self):
        with tempfile.TemporaryDirectory() as directory:
            hub = Hub({})
            hub.home = Path(directory)
            hub.controller = Mock(return_value={"available": True, "mac": "AA:BB:CC:DD:EE:FF", "path": "/org/bluez/hci0", "name": "ShareSpeaker"})
            hub.snapshot = Mock(return_value={})
            hub.require_audio = Mock(return_value="Server Name: PulseAudio (on PipeWire 1.0.5)")
            hub.run = Mock(side_effect=lambda args, **kw: "Compiled with libwireplumber 0.4.17" if args[0] == "wireplumber" else "")
            hub.initialize()
            cfg = hub.home / ".config/wireplumber/bluetooth.lua.d/51-bluetooth-phone-input.lua"
            self.assertIn('table.insert', cfg.read_text())
            self.assertNotIn('bluez5.codecs', cfg.read_text())
            hub.run.reset_mock()
            hub.initialize()
            self.assertFalse(any('restart' in call.args[0] or 'stop' in call.args[0] for call in hub.run.call_args_list))


class BluetoothNameTests(unittest.TestCase):
    def test_name_validation_uses_utf8_limit_and_rejects_controls(self):
        self.assertEqual("Living room", validate_bluetooth_name(" Living room "))
        self.assertEqual("A" * 248, validate_bluetooth_name("A" * 248))
        self.assertEqual(chr(0x1F3B5) * 62, validate_bluetooth_name(chr(0x1F3B5) * 62))
        for invalid in (None, 123, "", "  ", "name\n", "name\x00", "name\x7f", "A" * 249, chr(0x1F3B5) * 63):
            with self.subTest(value=repr(invalid)), self.assertRaises(HubError):
                validate_bluetooth_name(invalid)

    def test_device_alias_updates_only_the_selected_controller_device(self):
        phone = "02:00:00:00:00:10"
        hub = Hub({"action": "rename-device", "mac": phone, "name": " My phone ",
                   "deviceDirection": "input", "inputController": "02:00:00:00:00:31"})
        hub.controller = Mock(return_value={"available": True, "path": "/org/bluez/hci0"})
        hub.input_hub().controller = Mock(return_value={"available": True, "path": "/org/bluez/hci1"})
        hub.set_property = Mock()
        hub.snapshot = Mock(return_value={"devices": []})
        hub.dispatch()
        hub.set_property.assert_called_once_with("/org/bluez/hci1/dev_02_00_00_00_00_10", "org.bluez.Device1", "Alias", "s", "My phone")
        hub.snapshot.assert_called_once_with(True)
        self.assertEqual(0, hub._objects_at)

    def test_invalid_name_does_not_write_device_properties(self):
        hub = Hub({"action": "rename-device", "mac": "02:00:00:00:00:21", "name": "\n"})
        hub.set_property = Mock()
        with self.assertRaises(HubError):
            hub.dispatch()
        hub.set_property.assert_not_called()

    def test_renaming_a_control_channel_cannot_make_it_an_audio_speaker(self):
        hub = Hub({})
        device = hub.make_device("02:00:00:00:00:23", {
            "Name": "LE-Bose Color II SoundLink", "Alias": "Desk speaker",
            "UUIDs": ["0000febe-0000-1000-8000-00805f9b34fb"]})
        self.assertEqual("Desk speaker", device["name"])
        self.assertEqual("LE-Bose Color II SoundLink", device["originalName"])
        with self.assertRaises(HubError):
            hub.require_audio_device(device)

    def test_renaming_input_adapter_targets_receiver_and_not_output(self):
        hub = Hub({"action": "rename-adapter", "direction": "input", "name": "Studio receiver",
                   "inputController": "02:00:00:00:00:31"})
        receiver = hub.input_hub()
        receiver.controller = Mock(return_value={"available": True, "path": "/org/bluez/hci1"})
        receiver.set_property = Mock()
        hub.set_property = Mock()
        hub.snapshot = Mock(return_value={})
        hub.dispatch()
        receiver.set_property.assert_called_once_with("/org/bluez/hci1", "org.bluez.Adapter1", "Alias", "s", "Studio receiver")
        hub.set_property.assert_not_called()

    def test_shared_input_adapter_renames_the_shared_controller(self):
        hub = Hub({})
        hub.controller = Mock(return_value={"available": True, "path": "/org/bluez/hci0"})
        hub.set_property = Mock()
        hub.rename_adapter("input", "Shared receiver")
        hub.set_property.assert_called_once_with("/org/bluez/hci0", "org.bluez.Adapter1", "Alias", "s", "Shared receiver")

    def test_unavailable_adapter_and_invalid_direction_do_not_write(self):
        hub = Hub({})
        hub.controller = Mock(return_value={"available": False})
        hub.set_property = Mock()
        for direction in ("output", "unknown"):
            with self.subTest(direction=direction), self.assertRaises(HubError):
                hub.rename_adapter(direction, "Receiver")
        hub.set_property.assert_not_called()

    def test_default_adapter_name_migrates_legacy_and_preserves_custom_alias(self):
        hub = Hub({})
        hub.set_property = Mock()
        legacy = "Snoopy" + " Home Hub"
        self.assertEqual("ShareSpeaker", hub.ensure_adapter_name({"path": "/org/bluez/hci0", "name": legacy}, "ShareSpeaker"))
        hub.set_property.assert_called_once_with("/org/bluez/hci0", "org.bluez.Adapter1", "Alias", "s", "ShareSpeaker")
        hub.set_property.reset_mock()
        self.assertEqual("Kitchen receiver", hub.ensure_adapter_name({"path": "/org/bluez/hci0", "name": "Kitchen receiver", "originalName": "Bluetooth adapter"}, "ShareSpeaker"))
        hub.set_property.assert_not_called()

    def test_pairing_window_keeps_custom_adapter_name(self):
        hub = Hub({"action": "pair-window"})
        hub.controller = Mock(return_value={"available": True, "path": "/org/bluez/hci0", "name": "Music receiver"})
        hub.require_audio = Mock()
        hub.agent_session = Mock()
        hub.set_property = Mock()
        hub.snapshot = Mock(return_value={})
        hub.dispatch()
        self.assertFalse(any(call.args[2] == "Alias" for call in hub.set_property.call_args_list))
        self.assertIn("Music receiver", hub.message)
        hub.agent_session.assert_called_once_with([], 90)


class LoggingPrivacyTests(unittest.TestCase):
    def test_local_player_discards_output_without_creating_a_log(self):
        import os
        import subprocess
        with tempfile.TemporaryDirectory() as folder:
            hub = Hub({})
            hub.player_file = Path(folder) / "local-test-player.json"
            hub.local_player = Mock(return_value={})
            hub.local_player_alive = Mock(return_value=False)
            hub.stop_local_player = Mock()
            player = Mock(pid=os.getpid())
            player.poll.return_value = None
            with patch("hub_backend.subprocess.Popen", return_value=player) as launch, patch("hub_backend.time.sleep"):
                hub.sync_local_player({"source": "local-test", "target": "snoopy_hub_bus_example"})
            self.assertEqual(subprocess.DEVNULL, launch.call_args.kwargs["stderr"])
            self.assertEqual(subprocess.DEVNULL, launch.call_args.kwargs["stdout"])
            self.assertEqual(["local-test-player.json"], sorted(path.name for path in Path(folder).iterdir()))
            self.assertFalse(hasattr(hub, "logs"))


class DiscoveryAndMediaTests(unittest.TestCase):
    def test_bose_le_control_entry_is_not_treated_as_audio(self):
        hub = Hub({})
        device = hub.make_device("02:00:00:00:00:23", {"Name": "LE-Bose Color II SoundLink", "UUIDs": ["0000febe-0000-1000-8000-00805f9b34fb"]})
        self.assertTrue(device["controlOnly"])
        with self.assertRaisesRegex(HubError, "LE control channel"):
            hub.require_audio_device(device)
        audio = hub.make_device("02:00:00:00:00:21", {"Name": "Bose Color II SoundLink", "UUIDs": ["0000110b-0000-1000-8000-00805f9b34fb"]})
        hub.require_audio_device(audio)
        self.assertFalse(audio["controlOnly"])

    def test_only_named_devices_are_visible(self):
        for name in ("", "  ", "AA:BB:CC:DD:EE:FF", "aa-bb-cc-dd-ee-ff"):
            self.assertFalse(has_device_name({"name": name}), name)
        for name in ("Example phone", "ACTON III", "Bose Color II SoundLink", "LE-Bose Color II SoundLink"):
            self.assertTrue(has_device_name({"name": name}), name)

    def test_failed_busctl_json_uses_direct_dbus_without_losing_adapter(self):
        import types
        hub = Hub({})
        hub.run = Mock(side_effect=HubError("JSON serialization failed"))
        raw = {"/org/bluez/hci0": {"org.bluez.Adapter1": {"Address": "02:00:00:00:00:32", "Powered": True}},
               "/org/bluez/hci0/player/item": {"org.bluez.MediaItem1": {"Uid": 18446744073709551615}}}
        bus = Mock()
        bus.get_object.return_value.GetManagedObjects.return_value = raw
        fake = types.SimpleNamespace(Boolean=bool, SystemBus=Mock(return_value=bus))
        with patch.dict("sys.modules", {"dbus": fake}):
            result = hub.objects()
        self.assertEqual("02:00:00:00:00:32", hub.properties(result["/org/bluez/hci0"]["org.bluez.Adapter1"])["Address"])
        self.assertEqual(18446744073709551615, hub.properties(result["/org/bluez/hci0/player/item"]["org.bluez.MediaItem1"])["Uid"])
        bus.close.assert_called_once()

    def test_scan_dispatch_returns_without_blocking_status(self):
        hub = Hub({"action": "scan"})
        hub.start_discovery = Mock()
        hub.snapshot = Mock(return_value={"discovering": True})
        with patch("hub_backend.time.sleep") as sleep:
            self.assertTrue(hub.dispatch()["discovering"])
        sleep.assert_not_called()
        hub.start_discovery.assert_called_once()

    @patch("hub_backend.time.sleep")
    def test_connected_phone_without_media_gets_one_profile_request(self, sleep):
        hub = Hub({})
        hub.require_audio = Mock()
        hub.read_device = Mock(return_value={"paired": True, "trusted": True, "connected": True, "role": "input", "name": "Example phone"})
        hub.device_path = Mock(return_value="/org/bluez/hci0/dev_phone")
        hub.find_endpoint = Mock(return_value=None)
        hub.ensure_profile = Mock()
        hub.dbus_call = Mock(return_value="")
        self.assertIsNone(hub.connect("02:00:00:00:00:10", "input"))
        hub.dbus_call.assert_called_once_with("/org/bluez/hci0/dev_phone", "org.bluez.Device1", "ConnectProfile", "s", "0000110a-0000-1000-8000-00805f9b34fb", check=False)

    def test_phone_with_ready_media_is_not_renegotiated(self):
        hub = Hub({})
        hub.require_audio = Mock()
        hub.read_device = Mock(return_value={"paired": True, "trusted": True, "connected": True, "role": "input", "name": "Example phone"})
        endpoint = {"name": "bluez_input.phone"}
        hub.find_endpoint = Mock(return_value=endpoint)
        hub.dbus_call = Mock()
        self.assertEqual(endpoint, hub.connect("02:00:00:00:00:10", "input"))
        hub.dbus_call.assert_not_called()


class SplitAdapterTests(unittest.TestCase):
    def setUp(self):
        self.phone = "02:00:00:00:00:10"
        self.speaker = "02:00:00:00:00:22"
        self.hub = Hub({"controller": "02:00:00:00:00:32", "usbId": "7392:c611",
                        "inputController": "02:00:00:00:00:31", "inputUsbId": "8087:0026",
                        "source": "phone:" + self.phone, "roles": {self.phone: "input"}})
        objects = {}
        for path in ("/org/bluez/hci0", "/org/bluez/hci1"):
            objects[path] = {"org.bluez.Adapter1": {}}
            objects[path + "/dev_" + self.phone.replace(":", "_")] = {
                "org.bluez.Device1": {"Address": self.phone, "Name": "Example phone", "Icon": "phone", "Paired": True}}
            objects[path + "/dev_" + self.speaker.replace(":", "_")] = {
                "org.bluez.Device1": {"Address": self.speaker, "Name": "ACTON III", "Paired": True}}
        self.hub.objects = Mock(return_value=objects)
        self.hub.controller = Mock(return_value={"available": True, "path": "/org/bluez/hci0", "mac": "02:00:00:00:00:32"})
        self.receiver = self.hub.input_hub()
        self.receiver.controller = Mock(return_value={"available": True, "path": "/org/bluez/hci1", "mac": "02:00:00:00:00:31"})

    def test_device_actions_resolve_phone_on_intel_and_speaker_on_edimax(self):
        self.assertEqual("/org/bluez/hci1/dev_02_00_00_00_00_10", self.hub.device_path(self.phone))
        self.assertEqual("/org/bluez/hci0/dev_02_00_00_00_00_22", self.hub.device_path(self.speaker))

    def test_pairing_lists_merge_by_hardware_role_without_duplicates(self):
        devices = self.hub.devices(True)
        self.assertEqual(2, len(devices))
        self.assertEqual({self.phone: "input", self.speaker: "output"}, {d["mac"]: d["adapterRole"] for d in devices})

    def test_phone_endpoint_on_output_adapter_cannot_be_used(self):
        def node(path):
            return {"name": "bluez_input.phone", "properties": {"api.bluez5.address": self.phone, "api.bluez5.path": path + "/dev_phone"}}
        self.assertFalse(self.hub.endpoint_matches(node("/org/bluez/hci0"), self.phone))
        self.assertTrue(self.hub.endpoint_matches(node("/org/bluez/hci1"), self.phone))

    def test_phone_pairing_window_targets_receiver_and_keeps_output_adapter(self):
        self.hub.request["action"] = "pair-window"
        self.hub.require_audio = Mock()
        self.hub.set_property = Mock()
        self.receiver.agent_session = Mock()
        self.hub.snapshot = Mock(return_value={"splitAdapters": True})
        self.assertTrue(self.hub.dispatch()["splitAdapters"])
        self.receiver.agent_session.assert_called_once_with([], 90)
        self.assertTrue(all(c.args[0] == "/org/bluez/hci1" for c in self.hub.set_property.call_args_list))
        self.hub.set_property.assert_any_call("/org/bluez/hci1", "org.bluez.Adapter1", "Alias", "s", "ShareSpeaker Input")


class HardwareIsolationTests(unittest.TestCase):
    def test_existing_default_music_codec_is_never_forced(self):
        hub = Hub({})
        mac = "02:00:00:00:00:22"
        hub.pulse = Mock()
        for active in ("a2dp-sink", "a2dp-sink-sbc_xq"):
            hub.audio_items = Mock(return_value=[{"name": "bluez_card.02_00_00_00_00_22",
                "active_profile": active, "profiles": {"a2dp-sink": {"available": True}, "a2dp-sink-sbc_xq": {"available": True}}}])
            hub.ensure_profile(mac, "output")
        hub.pulse.assert_not_called()

    def test_fallback_music_profile_uses_unique_card_index(self):
        hub = Hub({})
        hub.pulse = Mock()
        hub.audio_items = Mock(return_value=[{"name": "bluez_card.02_00_00_00_00_22", "index": 4321,
            "active_profile": "off", "profiles": {"a2dp-sink": {"available": True}}}])
        hub.ensure_profile("02:00:00:00:00:22", "output")
        hub.pulse.assert_called_once_with("set-card-profile", "4321", "a2dp-sink")

    @patch("hub_backend.time.sleep")
    def test_initialize_waits_for_controller_after_usb_attach(self, sleep):
        hub = Hub({"usbId": "7392:c611"})
        ready = {"available": True, "mac": "02:00:00:00:00:32"}
        hub.controller = Mock(side_effect=[{"available": False},
            {"available": True, "mac": "00:00:00:00:00:00"}, ready])
        hub._objects_at = 999999
        self.assertEqual(ready, hub.wait_controller())
        self.assertEqual(2, sleep.call_count)
        self.assertEqual(0, hub._objects_at)

    def test_missing_controller_times_out_without_changing_route(self):
        hub = Hub({"usbId": "7392:c611"})
        hub.controller = Mock(return_value={"available": False})
        hub.pulse = Mock()
        with self.assertRaisesRegex(HubError, "previous hardware selection preserved"):
            hub.wait_controller(timeout=0)
        hub.pulse.assert_not_called()

    def setUp(self):
        self.hub = Hub({"controller": "02:00:00:00:00:32", "usbId": "7392:c611"})
        self.mac = "AA:BB:CC:DD:EE:01"
        self.hub.controller = Mock(return_value={"available": True, "path": "/org/bluez/hci1"})
        self.hub.objects = Mock(return_value={
            "/org/bluez/hci0": {"org.bluez.Adapter1": {}},
            "/org/bluez/hci1": {"org.bluez.Adapter1": {}},
            "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_01": {"org.bluez.Device1": {"Address": self.mac, "Alias": "Test speaker", "Paired": True}},
            "/org/bluez/hci1/dev_AA_BB_CC_DD_EE_01": {"org.bluez.Device1": {"Address": self.mac, "Alias": "Test speaker", "Paired": False}},
        })

    def test_pairings_are_not_inherited_from_other_adapter(self):
        self.assertEqual([], self.hub.devices())
        discovered = self.hub.devices(discovered=True)
        self.assertEqual(1, len(discovered))
        self.assertFalse(discovered[0]["paired"])

    def test_same_speaker_mac_resolves_to_selected_controller_only(self):
        nodes = [{"name": "bluez_output." + suffix, "properties": {
            "api.bluez5.address": self.mac,
            "api.bluez5.path": path + "/dev_AA_BB_CC_DD_EE_01"}}
            for suffix, path in (("intel", "/org/bluez/hci0"), ("edimax", "/org/bluez/hci1"))]
        self.hub.audio_items = Mock(return_value=nodes)
        self.assertEqual(nodes[1], self.hub.find_endpoint(self.mac, "output"))
        self.hub.audio_items.return_value = nodes[:1]
        self.assertIsNone(self.hub.find_endpoint(self.mac, "output"))

    def test_ambiguous_legacy_endpoint_is_not_used(self):
        self.assertFalse(self.hub.endpoint_matches({"properties": {"api.bluez5.address": self.mac}}, self.mac))


if __name__ == "__main__":
    unittest.main()
