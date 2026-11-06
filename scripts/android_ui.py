#!/usr/bin/env python3
"""Small, guarded ADB UI operations; runtime evidence stays outside the repository."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import struct
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET


class Refused(RuntimeError):
    """A precondition failed before input was dispatched."""


class OutcomeUnknown(RuntimeError):
    """Input may have taken effect: inspect fresh evidence, never blindly repeat."""


def private_write(path, data):
    """Create a private artifact without following/replacing an existing file."""
    raw = data if isinstance(data, bytes) else data.encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def parse_xml(raw):
    if len(raw) > 20_000_000 or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise Refused("Unexpected XML size or document declarations")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise Refused("Fresh UI dump is not valid XML") from exc
    if root.tag != "hierarchy" or not list(root.iter("node")):
        raise Refused("Fresh dump lacks a nonempty hierarchy")
    return root


def bounds(node):
    match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", node.get("bounds", ""))
    if not match:
        raise Refused("Missing or invalid node bounds")
    x1, y1, x2, y2 = map(int, match.groups())
    if x2 <= x1 or y2 <= y1:
        raise Refused("Node has empty bounds")
    return x1, y1, x2, y2


def select(root, package, criteria):
    if not package or not criteria or any(value is None for value in criteria.values()):
        raise Refused("An expected package and at least one exact selector are required")
    if set(criteria) - {"resource-id", "text", "content-desc"}:
        raise Refused("Unsupported selector")
    matches = [node for node in root.iter("node") if node.get("package") == package
               and all(node.get(key) == value for key, value in criteria.items())]
    if len(matches) != 1:
        raise Refused(f"Expected exactly one matching node; found {len(matches)}")
    node = matches[0]
    if node.get("enabled") != "true":
        raise Refused("Selected node is disabled or lacks enabled state")
    if node.get("password") != "false":
        raise Refused("Password or unknown-password-state nodes are not supported")
    bounds(node)
    return node


def encode_input_text(value):
    # Android's input text converts the literal sequence %s into a space.
    # Reject percent rather than silently altering user input, and reject text
    # that the stock keyboard event injector cannot reliably represent.
    if not value or any(ord(c) < 32 or ord(c) > 126 for c in value) or "%" in value:
        raise Refused("Input must be nonempty printable ASCII without '%' or newlines")
    return value.replace(" ", "%s")


def node_summary(node):
    keys = ("package", "resource-id", "class", "text", "content-desc", "bounds",
            "clickable", "enabled", "focusable", "focused", "checked", "selected", "password")
    result = {key: node.get(key) for key in keys if key in node.attrib}
    if node.get("password") != "false":
        result["text"] = "[redacted]"
        result.pop("content-desc", None)
    return result


class AndroidUI:
    def __init__(self, serial, state_dir=None, adb="adb", runner=subprocess.run):
        if not serial or serial.startswith("-") or any(c.isspace() for c in serial):
            raise Refused("Provide one explicit ADB serial without whitespace")
        self.serial, self.adb, self.runner = serial, adb, runner
        default_state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "android-phone-control"
        state = Path(state_dir or default_state).expanduser()
        if state.is_symlink():
            raise Refused("State directory cannot be a symlink")
        state = state.resolve()
        repository = Path(__file__).resolve().parents[1]
        if state == repository or repository in state.parents or any((p / ".git").exists() for p in [state, *state.parents]):
            raise Refused("Keep runtime artifacts outside source repositories")
        state.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(state, 0o700)
        self.run_dir = state / (time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex)
        self.run_dir.mkdir(mode=0o700)
        self.journal = {"serial": serial, "started_at": time.time(), "status": "created"}
        self.started = False
        self.save_journal()

    def save_journal(self):
        temporary = self.run_dir / ("journal-" + uuid.uuid4().hex + ".tmp")
        private_write(temporary, json.dumps(self.journal, indent=2, ensure_ascii=False))
        temporary.replace(self.run_dir / "journal.json")

    def adb_run(self, *args, timeout=30):
        try:
            result = self.runner([self.adb, "-s", self.serial, *args], capture_output=True,
                                 timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("ADB command failed or timed out; inspect the action journal") from exc
        if result.returncode:
            private_write(self.run_dir / ("adb-error-" + uuid.uuid4().hex + ".txt"),
                          result.stderr or result.stdout or b"ADB failed")
            raise RuntimeError("ADB returned an error; private diagnostics saved")
        return result.stdout

    def shell(self, *args, timeout=30):
        # No local shell. ADB shell still has a REMOTE shell: quote each argument
        # there as well, so text such as $(...) and quotes remains literal.
        return self.adb_run("shell", shlex.join([str(a) for a in args]), timeout=timeout)

    def foreground(self):
        raw = self.shell("dumpsys", "window").decode("utf-8", "replace")
        matches = re.findall(r"mCurrentFocus=Window\{[^\n}]*\s([A-Za-z0-9_.]+)/[^\s}]+", raw)
        if len(matches) != 1:
            raise Refused("Cannot establish a unique focused-window package")
        return matches[0]

    def guard_package(self, expected):
        actual = self.foreground()
        if actual != expected:
            raise Refused(f"Expected foreground package {expected}; found {actual}")

    def snapshot(self, package=None, filter_package=None):
        if package:
            self.guard_package(package)
        token = uuid.uuid4().hex
        remote = f"/data/local/tmp/android-ui-{token}.xml"
        local = self.run_dir / f"ui-{token}.xml"
        try:
            output = self.shell("uiautomator", "dump", remote, timeout=25)
            # DumpCommand can print an error and return normally. Never trust
            # exit code alone and never reuse /sdcard/window_dump.xml.
            if b"ERROR:" in output:
                raise Refused("UIAutomator could not capture a fresh hierarchy")
            raw = self.shell("cat", remote)
            root = parse_xml(raw)
            private_write(local, raw)
        finally:
            try:
                self.shell("rm", "-f", remote)
            except Exception as exc:
                self.journal.setdefault("cleanup_errors", []).append(str(exc))
                self.save_journal()
        if package:
            self.guard_package(package)
            if not any(n.get("package") == package for n in root.iter("node")):
                raise Refused("Expected package is absent from the fresh hierarchy")
        output_package = package or filter_package
        nodes = [node_summary(n) for n in root.iter("node")
                 if output_package is None or n.get("package") == output_package]
        summary = {"xml": str(local), "package": output_package, "nodes": nodes}
        private_write(local.with_suffix(".json"), json.dumps(summary, ensure_ascii=False, indent=2))
        return root, summary

    def screenshot(self, package=None):
        if package:
            self.guard_package(package)
        raw = self.adb_run("exec-out", "screencap", "-p")
        if len(raw) < 24 or raw[:8] != b"\x89PNG\r\n\x1a\n" or raw[12:16] != b"IHDR":
            raise Refused("Screenshot did not return a PNG image")
        width, height = struct.unpack(">II", raw[16:24])
        if not width or not height:
            raise Refused("Screenshot has invalid dimensions")
        path = self.run_dir / ("screen-" + uuid.uuid4().hex + ".png")
        private_write(path, raw)
        if package:
            self.guard_package(package)
        return {"screenshot": str(path), "width": width, "height": height}

    def begin_input(self, operation, before, details):
        self.journal.update(operation=operation, before=before["xml"], details=details,
                            status="mutation-started", mutation_started_at=time.time())
        self.save_journal()
        self.started = True

    def perform(self, operation, package, criteria=None, value=None, expected_text=None,
                replace=False, start=None, end=None, duration=300):
        if self.started:
            raise OutcomeUnknown("This action object already dispatched input; reconcile its journal instead of replaying it")
        try:
            if not package or not re.fullmatch(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+", package):
                raise Refused("Provide an exact expected application package")
            encoded = encode_input_text(value) if operation == "type" else None
            root, before = self.snapshot(package)
            node = select(root, package, criteria or {}) if operation in ("tap", "type") else None
            details = {"package": package, "selector": criteria}
            if operation == "tap":
                x1, y1, x2, y2 = bounds(node)
                point = ((x1 + x2) // 2, (y1 + y2) // 2)
                details["point"] = point
                commands = [("input", "tap", *point)]
            elif operation == "type":
                if node.get("class") not in {"android.widget.EditText", "android.widget.AutoCompleteTextView", "android.widget.MultiAutoCompleteTextView"}:
                    raise Refused("Selected node is not a supported editable input")
                if node.get("focused") != "true" or node.get("focusable") != "true":
                    raise Refused("Type requires the selected input to be focused already")
                if expected_text is None or node.get("text", "") != expected_text:
                    raise Refused("Current field text differs from --expected-text")
                if expected_text and not replace:
                    raise Refused("Nonempty inputs require --replace; cursor position is unknown")
                details.update(replace=replace, value_sha256=hashlib.sha256(value.encode()).hexdigest())
                commands = []
                if replace:
                    commands.append(("input", "keycombination", "KEYCODE_CTRL_LEFT", "KEYCODE_A"))
                commands.append(("input", "text", encoded))
            elif operation == "back":
                commands = [("input", "keyevent", "KEYCODE_BACK")]
            elif operation == "swipe":
                image = self.screenshot(package)
                if not start or not end or not 50 <= duration <= 3000 or start == end:
                    raise Refused("Swipe needs different start/end points and duration 50–3000 ms")
                if any(not (0 <= p[0] < image["width"] and 0 <= p[1] < image["height"]) for p in (start, end)):
                    raise Refused("Swipe points must be inside the current screen")
                details.update(start=start, end=end, duration=duration, screenshot=image["screenshot"])
                commands = [("input", "swipe", *start, *end, duration)]
            else:
                raise Refused("Unsupported operation")
            # Focus can change while building a command; guard again immediately
            # before dispatch. XML + coordinates cannot make an atomic UI action.
            self.guard_package(package)
            self.begin_input(operation, before, details)
            for command in commands:
                self.shell(*command)
            after_root, after = self.snapshot(package if operation == "type" else None, filter_package=package)
            if operation == "type":
                # Text selectors may intentionally change: reuse the stable ID
                # or description; otherwise identify the same focused class.
                stable = {k: v for k, v in (criteria or {}).items() if k != "text"}
                if stable:
                    field = select(after_root, package, stable)
                else:
                    candidates = [n for n in after_root.iter("node") if n.get("package") == package
                                  and n.get("class") == node.get("class") and n.get("focused") == "true"]
                    if len(candidates) != 1:
                        raise RuntimeError("Cannot identify the input for text read-back")
                    field = candidates[0]
                if field.get("password") != "false" or field.get("text") != value:
                    raise RuntimeError("Input text read-back does not match the requested value")
            self.journal.update(status="verified-text" if operation == "type" else "dispatched-and-observed",
                                after=after["xml"], completed_at=time.time())
            self.save_journal()
            return {"status": self.journal["status"], "journal": str(self.run_dir / "journal.json"), **after}
        except Exception as exc:
            self.journal.update(status="outcome-unknown" if self.started else "refused", error=str(exc))
            self.save_journal()
            if self.started:
                raise OutcomeUnknown("Input may have taken effect. Inspect fresh UI and journal before any further action.") from exc
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True, help="Exact adb devices serial (including Wi-Fi port)")
    parser.add_argument("--state-dir", type=Path, help="Private runtime directory outside repositories")
    parser.add_argument("--adb", default="adb", help="ADB executable path")
    subs = parser.add_subparsers(dest="command", required=True)
    for name in ("snapshot", "screenshot", "tap", "type", "back", "swipe"):
        child = subs.add_parser(name)
        child.add_argument("--package", required=name in {"tap", "type", "back", "swipe"})
        if name in {"tap", "type"}:
            child.add_argument("--rid", help="Exact resource ID; selectors are ANDed")
            child.add_argument("--text", help="Exact visible text")
            child.add_argument("--desc", help="Exact content description")
        if name == "type":
            child.add_argument("--value", required=True, help="Printable ASCII, excluding percent")
            child.add_argument("--expected-text", required=True, help="Exact CURRENT field text")
            child.add_argument("--replace", action="store_true", help="Ctrl+A, then replace current text")
        if name == "swipe":
            child.add_argument("--start", type=int, nargs=2, required=True, metavar=("X", "Y"))
            child.add_argument("--end", type=int, nargs=2, required=True, metavar=("X", "Y"))
            child.add_argument("--duration", type=int, default=300)
    args = parser.parse_args(argv)
    ui = None
    try:
        ui = AndroidUI(args.serial, args.state_dir, args.adb)
        if args.command == "snapshot":
            _, result = ui.snapshot(args.package)
        elif args.command == "screenshot":
            result = ui.screenshot(args.package)
        else:
            criteria = {field: getattr(args, arg) for arg, field in
                        (("rid", "resource-id"), ("text", "text"), ("desc", "content-desc"))
                        if getattr(args, arg, None) is not None}
            result = ui.perform(args.command, args.package, criteria,
                                value=getattr(args, "value", None), expected_text=getattr(args, "expected_text", None),
                                replace=getattr(args, "replace", False), start=getattr(args, "start", None),
                                end=getattr(args, "end", None), duration=getattr(args, "duration", 300))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc), "status": "outcome-unknown" if isinstance(exc, OutcomeUnknown) else "failed",
                          "journal": str(ui.run_dir / "journal.json") if ui else None}), file=sys.stderr)
        return 3 if isinstance(exc, OutcomeUnknown) else 2


if __name__ == "__main__":
    sys.exit(main())
