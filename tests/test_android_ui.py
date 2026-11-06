"""Behavior tests with a fake ADB transport; never contact a real phone."""
import importlib.util
import json
import os
from pathlib import Path
import shlex
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

MODULE = Path(__file__).resolve().parents[1] / "scripts/android_ui.py"
spec = importlib.util.spec_from_file_location("android_ui", MODULE)
ui_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui_module)


def xml_node(**updates):
    attrs = {"package": "com.example.app", "resource-id": "com.example.app:id/title",
             "text": "", "content-desc": "Title", "class": "android.widget.EditText",
             "enabled": "true", "password": "false", "focusable": "true", "focused": "true",
             "clickable": "true", "bounds": "[10,20][110,80]"}
    attrs.update(updates)
    return attrs


def hierarchy(nodes):
    root = ET.Element("hierarchy", rotation="0")
    for attrs in nodes:
        ET.SubElement(root, "node", attrs)
    return ET.tostring(root)


class FakeAdb:
    def __init__(self, nodes=None):
        self.nodes = nodes or [xml_node()]
        self.package = "com.example.app"
        self.files, self.calls, self.input_calls, self.dump_paths = {}, [], [], []
        self.dump_error = False
        self.malformed = False
        self.after_input_error = False
        self.timeout_after_input = False
        self.wrong_text = False
        self.selected_all = False
        self.focus_count = 0
        self.switch_on_focus = None

    def __call__(self, command, **kwargs):
        self.calls.append(command)
        assert command[1:3] == ["-s", "test-device:4321"], command
        result = lambda data=b"", code=0: subprocess.CompletedProcess(command, code, data, b"")
        if command[3] == "exec-out":
            assert command[4:] == ["screencap", "-p"]
            return result(b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 200, 400))
        assert command[3] == "shell"
        remote = shlex.split(command[4])
        if remote == ["dumpsys", "window"]:
            self.focus_count += 1
            if self.focus_count == self.switch_on_focus:
                self.package = "com.example.other"
            return result(f"mCurrentFocus=Window{{123 u0 {self.package}/.Main}}\n".encode())
        if remote[:2] == ["uiautomator", "dump"]:
            self.dump_paths.append(remote[2])
            if self.dump_error or (self.input_calls and self.after_input_error):
                return result(b"ERROR: could not get idle state.\n")
            self.files[remote[2]] = b"broken" if self.malformed else hierarchy(self.nodes)
            return result(f"UI hierchary dumped to: {remote[2]}\n".encode())
        if remote[0] == "cat":
            return result(self.files.get(remote[1], b""), 0 if remote[1] in self.files else 1)
        if remote[:2] == ["rm", "-f"]:
            self.files.pop(remote[2], None)
            return result()
        if remote[0] == "input":
            self.input_calls.append(remote)
            if remote[1] == "keycombination":
                self.selected_all = True
            elif remote[1] == "text":
                text = remote[2].replace("%s", " ")
                self.nodes[0]["text"] = "corrupted" if self.wrong_text else text
            if self.timeout_after_input:
                raise subprocess.TimeoutExpired(command, 30)
            return result()
        raise AssertionError(f"Unexpected ADB call: {command}")


class AndroidUITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fake = FakeAdb()
        self.ui = self.make_ui(self.fake)

    def make_ui(self, fake):
        return ui_module.AndroidUI("test-device:4321", Path(self.tmp.name) / "private", runner=fake)

    def action(self, operation="tap", criteria=None, **kwargs):
        return self.ui.perform(operation, "com.example.app",
                               criteria or {"resource-id": "com.example.app:id/title"}, **kwargs)

    def test_snapshots_never_reuse_paths_and_cleanup_only_own_files(self):
        self.fake.files["/sdcard/window_dump.xml"] = b"stale unrelated dump"
        first = self.ui.snapshot("com.example.app")[1]
        second = self.ui.snapshot("com.example.app")[1]
        self.assertNotEqual(first["xml"], second["xml"])
        self.assertEqual(len(set(self.fake.dump_paths)), 2)
        self.assertEqual(self.fake.files, {"/sdcard/window_dump.xml": b"stale unrelated dump"})
        self.assertEqual(os.stat(first["xml"]).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.ui.run_dir).st_mode & 0o777, 0o700)

    def test_zero_exit_dump_failure_and_malformed_dump_fail_before_input(self):
        for failure in ("dump_error", "malformed"):
            with self.subTest(failure=failure):
                fake = FakeAdb()
                setattr(fake, failure, True)
                controller = self.make_ui(fake)
                with self.assertRaises(ui_module.Refused):
                    controller.perform("tap", "com.example.app", {"text": ""})
                self.assertEqual(fake.input_calls, [])
                self.assertEqual(fake.files, {})

    def test_ambiguous_disabled_password_missing_password_and_empty_bounds_refused(self):
        cases = [[xml_node(), xml_node()], [xml_node(enabled="false")],
                 [xml_node(password="true")], [xml_node(password="")],
                 [xml_node(bounds="[10,20][10,80]")]]
        for nodes in cases:
            with self.subTest(nodes=nodes):
                fake = FakeAdb(nodes)
                controller = self.make_ui(fake)
                with self.assertRaises(ui_module.Refused):
                    controller.perform("tap", "com.example.app", {"content-desc": "Title"})
                self.assertEqual(fake.input_calls, [])

    def test_selector_ands_exact_matches_and_package(self):
        self.fake.nodes = [xml_node(text="Save", **{"class": "android.widget.Button"}),
                           xml_node(text="Save later"), xml_node(text="Save", package="com.example.other")]
        result = self.action(criteria={"text": "Save", "resource-id": "com.example.app:id/title"})
        self.assertEqual(self.fake.input_calls, [["input", "tap", "60", "50"]])
        self.assertEqual(result["status"], "dispatched-and-observed")

    def test_foreground_changes_before_dispatch_fail_closed(self):
        self.fake.switch_on_focus = 3
        with self.assertRaises(ui_module.Refused):
            self.action()
        self.assertEqual(self.fake.input_calls, [])

    def test_snapshot_filters_other_packages_and_redacts_password_text(self):
        self.fake.nodes += [xml_node(package="com.example.other", text="private other app"),
                            xml_node(password="true", text="secret")]
        result = self.ui.snapshot("com.example.app")[1]
        self.assertEqual(len(result["nodes"]), 2)
        self.assertEqual(result["nodes"][1]["text"], "[redacted]")
        self.assertNotIn("private other app", json.dumps(result))

    def test_type_preserves_shell_metacharacters_as_one_argument(self):
        value = "Joe's $(touch /tmp/never) `id`; a & b | c > d"
        result = self.action("type", value=value, expected_text="")
        self.assertEqual(self.fake.input_calls, [["input", "text", value.replace(" ", "%s")]])
        self.assertEqual(result["status"], "verified-text")
        self.assertEqual(self.fake.nodes[0]["text"], value)

    def test_type_refuses_percent_unicode_newline_without_any_input(self):
        for value in ("50%", "%s", "café", "one\ntwo", ""):
            with self.subTest(value=value), self.assertRaises(ui_module.Refused):
                self.action("type", value=value, expected_text="")
        self.assertEqual(self.fake.input_calls, [])

    def test_type_requires_focus_and_matching_current_value(self):
        self.fake.nodes[0]["focused"] = "false"
        with self.assertRaises(ui_module.Refused):
            self.action("type", value="new", expected_text="")
        self.fake.nodes[0]["focused"] = "true"
        with self.assertRaises(ui_module.Refused):
            self.action("type", value="new", expected_text="old")
        self.assertEqual(self.fake.input_calls, [])

    def test_nonempty_replace_uses_select_all_then_verifies_value(self):
        self.fake.nodes[0]["text"] = "old"
        with self.assertRaises(ui_module.Refused):
            self.action("type", value="new", expected_text="old")
        result = self.action("type", value="new", expected_text="old", replace=True)
        self.assertEqual([x[1] for x in self.fake.input_calls], ["keycombination", "text"])
        self.assertEqual(result["status"], "verified-text")

    def test_action_is_journaled_before_input(self):
        base_runner = self.fake
        observed = []
        def runner(command, **kwargs):
            if command[3] == "shell" and shlex.split(command[4])[0] == "input":
                observed.append(json.loads((self.ui.run_dir / "journal.json").read_text())["status"])
            return base_runner(command, **kwargs)
        self.ui.runner = runner
        self.action()
        self.assertEqual(observed, ["mutation-started"])

    def test_timeout_after_input_is_unknown_and_does_not_repeat(self):
        self.fake.timeout_after_input = True
        with self.assertRaises(ui_module.OutcomeUnknown):
            self.action()
        self.assertEqual(len(self.fake.input_calls), 1)
        self.assertEqual(json.loads((self.ui.run_dir / "journal.json").read_text())["status"], "outcome-unknown")
        with self.assertRaises(ui_module.OutcomeUnknown):
            self.action()
        self.assertEqual(len(self.fake.input_calls), 1)

    def test_readback_failure_does_not_report_success_or_repeat_mutation(self):
        self.fake.wrong_text = True
        with self.assertRaises(ui_module.OutcomeUnknown):
            self.action("type", value="requested", expected_text="")
        self.assertEqual(len(self.fake.input_calls), 1)

    def test_snapshot_failure_after_tap_is_unknown(self):
        self.fake.after_input_error = True
        with self.assertRaises(ui_module.OutcomeUnknown):
            self.action()
        self.assertEqual(len(self.fake.input_calls), 1)

    def test_swipe_cannot_leave_screen_or_use_wrong_package(self):
        with self.assertRaises(ui_module.Refused):
            self.action("swipe", start=(50, 50), end=(201, 20))
        self.assertEqual(self.fake.input_calls, [])
        self.action("swipe", start=(50, 50), end=(50, 300))
        self.assertEqual(self.fake.input_calls, [["input", "swipe", "50", "50", "50", "300", "300"]])

    def test_required_serial_parser_and_state_outside_repo(self):
        result = subprocess.run([sys_executable(), str(MODULE), "snapshot"], capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"--serial", result.stderr)
        with self.assertRaises(ui_module.Refused):
            ui_module.AndroidUI("test-device:4321", MODULE.parent / "artifacts", runner=self.fake)

    def test_default_state_respects_xdg_location(self):
        xdg = Path(self.tmp.name) / "xdg"
        with patch.dict(os.environ, {"XDG_STATE_HOME": str(xdg)}):
            controller = ui_module.AndroidUI("test-device:4321", runner=self.fake)
        self.assertEqual(controller.run_dir.parent, xdg / "android-phone-control")

    def test_cli_snapshot_uses_fake_executable_and_returns_private_filtered_artifacts(self):
        executable = Path(self.tmp.name) / "fake-adb"
        fixture = hierarchy([xml_node(text="Visible"), xml_node(package="com.other.app", text="Other")])
        executable.write_text("#!" + sys_executable() + "\n" +
            "import shlex, sys\n" +
            "assert sys.argv[1:3] == ['-s', 'test-device:4321']\n" +
            "assert sys.argv[3] == 'shell'\n" +
            "args = shlex.split(sys.argv[4])\n" +
            "if args[0] == 'dumpsys': print('mCurrentFocus=Window{123 u0 com.example.app/.Main}')\n" +
            "elif args[0] == 'uiautomator': print('UI hierchary dumped to: ' + args[2])\n" +
            "elif args[0] == 'cat': sys.stdout.buffer.write(" + repr(fixture) + ")\n" +
            "elif args[:2] != ['rm', '-f']: raise RuntimeError('Unexpected command')\n")
        executable.chmod(0o700)
        result = subprocess.run([sys_executable(), str(MODULE), "--serial", "test-device:4321",
                                 "--adb", str(executable), "--state-dir", str(Path(self.tmp.name) / "cli-state"),
                                 "snapshot", "--package", "com.example.app"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual([n["text"] for n in output["nodes"]], ["Visible"])
        self.assertTrue(Path(output["xml"]).is_file())
        self.assertEqual(os.stat(output["xml"]).st_mode & 0o777, 0o600)


def sys_executable():
    import sys
    return sys.executable


if __name__ == "__main__":
    unittest.main()
