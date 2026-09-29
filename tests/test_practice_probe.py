"""Offline coverage for the manually triggered, session-targeted practice probe."""

import argparse
import importlib.util
import io
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ow174.jam.codec import Schemas

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("practice_probe", ROOT / "tools/probe_practice.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def event(msg=24000, conn=7, player="Researcher", **changes):
    value = {"+0x78": 2, "+0x80": "", "+0xA8": 4} if msg == 24000 else {"+0x78": True}
    result = {
        "crc": "A6E53896" if msg == 24000 else "888716D3",
        "msg": msg,
        "conn": conn,
        "player": player,
        "value": value,
    }
    result.update(changes)
    return result


class ProbeTests(unittest.TestCase):
    def test_cli_help_handles_legacy_redirected_output_encoding(self):
        output = io.BytesIO()
        errors = io.BytesIO()
        stdout = io.TextIOWrapper(output, encoding="ascii")
        stderr = io.TextIOWrapper(errors, encoding="ascii")
        with patch.object(sys, "stdout", stdout), patch.object(sys, "stderr", stderr):
            with self.assertRaises(SystemExit) as raised:
                probe.main(["--help"])
            self.assertEqual(raised.exception.code, 0)
            stdout.flush()
            self.assertIn("practice range", output.getvalue().decode("utf-8"))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.log = self.repo / "client_msgs.log"
        self.log.write_text(json.dumps(event()) + "\n", encoding="utf-8")
        self.schemas = Schemas()
        self.ticks = 0
        self.steps = []
        self.output = []
        self.resets = []
        self.consume_resets = True

    def append(self, *events):
        with self.log.open("a", encoding="utf-8") as stream:
            for item in events:
                stream.write(json.dumps(item) + "\n")

    def sleep(self, seconds):
        self.ticks += seconds
        if self.steps:
            self.steps.pop(0)()
        path = self.repo / "inject.jsonl"
        if self.consume_resets and path.exists():
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return
            if item.get("value", {}).get("+0x78", {}).get("+0x60") == 0:
                self.resets.append(item)
                path.unlink()

    def run_probe(self, **kwargs):
        return probe.run_probe(
            self.repo,
            "Researcher",
            timeout=1,
            token=0,
            schemas=self.schemas,
            clock=lambda: self.ticks,
            sleep=self.sleep,
            emit=self.output.append,
            **kwargs,
        )

    def test_wire_bytes_independently_match_scalar_layout(self):
        # 32-byte tuple + 3*u32 + three16-byte ids =92 bytes; state byte, bool, u64.
        for token in (0, 0x0123456789ABCDEF):
            item = probe.build_probe(self.schemas, token)
            raw = self.schemas.encode(int(item["crc"], 16), item["msg"], item["value"])
            self.assertEqual(raw, bytes(92) + b"\x04\x00" + struct.pack("<Q", token))
            self.assertEqual(len(raw), 102)

    def test_old_request_at_eof_is_not_replayed(self):
        code, report = self.run_probe()
        self.assertNotEqual(code, 0)
        self.assertEqual(report["status"], "request_timeout")
        self.assertFalse((self.repo / "inject.jsonl").exists())
        self.assertEqual(len(list((self.repo / "logs/probes").glob("*.json"))), 1)

    def test_exact_new_request_and_targeted_ack(self):
        sent = []

        def receive_injection():
            path = self.repo / "inject.jsonl"
            sent.append(json.loads(path.read_text(encoding="utf-8")))
            path.unlink()  # Simulate the server hook consuming it.
            self.append(event(52903, conn=8), event(52903, player="Other"))

        other_request = event(value={"+0x78": 2, "+0x80": "", "+0xA8": 5})
        self.steps = [
            lambda: self.append(event(player="Other"), other_request),
            lambda: self.append(event()),
            receive_injection,
            lambda: self.append(event(52903)),
        ]
        code, report = self.run_probe()
        self.assertEqual(code, 0)
        self.assertTrue(report["ack"])
        self.assertEqual(sent[0]["conn"], 7)
        self.assertEqual(sent[0]["player"], "Researcher")
        self.assertEqual(report["status"], "acknowledged")
        self.assertIn("no game handoff", report["scope"])
        self.assertEqual(report["restore_status"], "consumed")
        self.assertEqual(self.resets[0]["conn"], 7)
        self.assertEqual(self.resets[0]["player"], "Researcher")
        self.assertEqual(self.resets[0]["value"]["+0x78"]["+0x60"], 0)
        self.assertEqual(self.resets[0]["value"]["+0xE8"], 0)
        self.assertGreater(self.resets[0]["expires_at"], 0)

    def test_existing_research_input_is_preserved(self):
        path = self.repo / "inject.jsonl"
        path.write_bytes(b"existing research input\n")
        self.steps = [lambda: self.append(event())]
        code, report = self.run_probe()
        self.assertNotEqual(code, 0)
        self.assertEqual(report["status"], "inject_busy")
        self.assertEqual(path.read_bytes(), b"existing research input\n")
        self.assertEqual(report["restore_status"], "not_needed")
        self.assertEqual(self.resets, [])

    def test_ack_timeout_removes_only_unconsumed_own_packet(self):
        self.steps = [lambda: self.append(event())]
        code, report = self.run_probe()
        self.assertNotEqual(code, 0)
        self.assertEqual(report["status"], "ack_timeout")
        self.assertFalse((self.repo / "inject.jsonl").exists())

    def test_false_ack_is_saved_and_reported_as_nonzero(self):
        self.steps = [lambda: self.append(event()), lambda: self.append(event(52903, value={"+0x78": False}))]
        code, report = self.run_probe()
        self.assertNotEqual(code, 0)
        self.assertIs(report["ack"], False)

    def test_cleanup_preserves_replacement_research_input(self):
        def replace_injection():
            path = self.repo / "inject.jsonl"
            path.unlink()
            path.write_text("another request\n", encoding="utf-8")

        self.steps = [lambda: self.append(event()), replace_injection]
        code, report = self.run_probe()
        self.assertNotEqual(code, 0)
        self.assertFalse(report["removed_unconsumed_packet"])
        self.assertEqual((self.repo / "inject.jsonl").read_text(), "another request\n")
        self.assertEqual(report["restore_status"], "blocked")

    def test_keep_state_is_explicit_and_does_not_send_reset(self):
        self.steps = [lambda: self.append(event()), lambda: self.append(event(52903))]
        code, report = self.run_probe(keep_state=True)
        self.assertEqual(code, 0)
        self.assertEqual(report["restore_status"], "kept_by_request")
        self.assertEqual(self.resets, [])

    def test_unconsumed_reset_is_left_for_watcher_with_expiry(self):
        self.consume_resets = False
        self.steps = [lambda: self.append(event()), lambda: self.append(event(52903))]
        code, report = self.run_probe()
        self.assertNotEqual(code, 0)
        self.assertEqual(report["restore_status"], "pending")
        reset = json.loads((self.repo / "inject.jsonl").read_text())
        self.assertEqual(reset["value"]["+0x78"]["+0x60"], 0)
        self.assertEqual((reset["conn"], reset["player"]), (7, "Researcher"))
        self.assertIn("expires_at", reset)

    def test_partial_line_is_not_processed_before_newline(self):
        def partial():
            with self.log.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event()))

        def complete():
            self.assertFalse((self.repo / "inject.jsonl").exists())
            with self.log.open("a", encoding="utf-8") as stream:
                stream.write("\n")

        self.steps = [partial, complete, lambda: self.append(event(52903))]
        code, _ = self.run_probe()
        self.assertEqual(code, 0)

    def test_log_truncation_stops_without_replaying_content(self):
        self.steps = [lambda: self.log.write_text("", encoding="utf-8")]
        code, report = self.run_probe()
        self.assertNotEqual(code, 0)
        self.assertEqual(report["status"], "log_changed")
        self.assertFalse((self.repo / "inject.jsonl").exists())

    def test_invalid_args_rejected_without_writes(self):
        for bad in ("nan", "inf", "-1", "0"):
            with self.assertRaises(argparse.ArgumentTypeError):
                probe.finite_timeout(bad)
        for bad in ("-1", str(1 << 64), "1.5"):
            with self.assertRaises(argparse.ArgumentTypeError):
                probe.uint64(bad)

    def test_atomic_publish_never_overwrites(self):
        path = self.repo / "inject.jsonl"
        path.write_text("keep", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            probe.publish_exclusive(path, {"msg": 53000})
        self.assertEqual(path.read_text(), "keep")
        self.assertEqual(list(self.repo.glob(".practice-probe-*")), [])


if __name__ == "__main__":
    unittest.main()
