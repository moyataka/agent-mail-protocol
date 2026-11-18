"""End-to-end tests for a copied installation using a simulated native Herdr CLI."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest


HERDR = """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

agents = {
    ("local", "w1:p1"): ("alice", "w1:p1"),
    ("local", "alice"): ("alice", "w1:p1"),
    ("other", "w1:p1"): ("bob", "w1:p1"),
    ("other", "bob"): ("bob", "w1:p1"),
    ("other", "eve"): ("eve", "w1:p2"),
    ("other", "w1:p2"): ("eve", "w1:p2"),
}
arguments = sys.argv[1:]
if arguments == ["session", "list", "--json"]:
    print(json.dumps({"sessions": [
        {"name": name, "running": True, "socket_path": f"/fixtures/{name}.sock"}
        for name in ("local", "other")
    ]}))
    sys.exit(0)
flag, session, group, command, target, *values = arguments
assert (flag, group) == ("--session", "agent")
if command == "get":
    name, pane = agents[(session, target)]
    print(json.dumps({"result": {
        "agent": {"name": name, "pane_id": pane, "terminal_id": f"{session}-{pane}"}
    }}))
elif command == "prompt":
    assert target in ("alice", "bob", "eve")
    assert len(values) == 1
    mode = os.environ.get("HERDR_SIMULATE")
    if mode == "blocked":
        print(json.dumps({"error": {"code": "agent_blocked"}}), file=sys.stderr)
        sys.exit(1)
    with Path(os.environ["HERDR_NOTICES"]).open("a") as notices:
        notices.write(json.dumps([session, target, values[0]]) + "\\n")
    if mode == "uncertain":
        sys.exit(1)
    print(json.dumps({"result": {"type": "agent_prompted"}}))
else:
    raise AssertionError(arguments)
"""


class AgentMailTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.install = self.install_into("organization")
        self.db = self.root / "organization/organization-state/agent-mail/mailbox.sqlite3"
        binary = self.root / "bin"
        binary.mkdir()
        herdr = binary / "herdr"
        herdr.write_text(HERDR, encoding="utf-8")
        herdr.chmod(0o700)
        self.notices = self.root / "notices"
        self.environment = {
            **os.environ,
            "PATH": f"{binary}:{os.environ['PATH']}",
            "HERDR_ENV": "1",
            "HERDR_NOTICES": str(self.notices),
        }
        self.checkout = self.root / "unrelated-checkout"
        self.checkout.mkdir()

    def install_into(self, name):
        tool = self.root / name / "tools/agent-mail"
        tool.mkdir(parents=True)
        return Path(shutil.copy2(Path(__file__).with_name("agent-mail"), tool))

    def cli(self, *arguments, session="local", pane="w1:p1", mode="", code=0, install=None):
        result = subprocess.run(
            [str(install or self.install), *arguments], cwd=self.checkout,
            env={
                **self.environment,
                "HERDR_SOCKET_PATH": f"/fixtures/{session}.sock",
                "HERDR_PANE_ID": pane, "HERDR_SIMULATE": mode,
            },
            capture_output=True, text=True, timeout=15, check=False,
        )
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return result

    def send(self, *arguments, **options):
        return self.cli(
            "send", "--from-team", "alpha", "--to", "beta/other/bob", "--project", "delivery",
            "--task", "work#32", "--kind", "handoff", "--subject", "Delivery handoff",
            *arguments, **options,
        )

    def test_cross_team_and_agent_view(self):
        handoff = self.root / "handoff.md"
        handoff.write_bytes(b"stored body line 1\r\nline 2 without a final newline")
        self.assertEqual(self.send("--from-file", str(handoff)).stdout, "M1 submitted=1\n")
        self.assertEqual(
            json.loads(self.notices.read_text()),
            ["other", "bob", "MAIL M1 read | Delivery handoff"],
        )
        self.assertEqual(self.cli("inbox", session="other").stdout, "MAIL M1 read | Delivery handoff\n")
        self.assertEqual(
            self.cli("read", "M1", session="other").stdout,
            handoff.read_text(),
        )
        self.cli("read", "M1", session="other", pane="w1:p2", code=2)
        self.cli("ack", "M1", code=2)
        self.assertEqual(self.cli("ack", "M1", session="other").stdout, "M1 acknowledged\n")
        first = self.cli("show", "M1").stdout
        self.cli("ack", "M1", session="other")
        self.assertEqual(first, self.cli("show", "M1").stdout)
        self.assertEqual(self.cli("inbox", session="other").stdout, "")
        report = self.cli("history", "--team", "beta", "--from-team", "alpha", "--task", "work#32").stdout
        for field in ("FROM TEAM", "TO TEAM", "alpha", "beta", "alice", "bob", "delivery", "acknowledged"):
            self.assertIn(field, report)
        self.assertNotIn("stored body", report)
        self.assertNotIn("M1", self.cli("history", "--from-team", "beta").stdout)
        with sqlite3.connect(self.db) as database:
            self.assertEqual(database.execute("SELECT body FROM message").fetchone()[0].encode(), handoff.read_bytes())

    def test_recovery_and_repeated_notice(self):
        stored = self.send("--body", "Recover me", mode="blocked", code=1)
        self.assertEqual(stored.stdout, "M1 rejected=1\n")
        self.assertFalse(self.notices.exists())
        self.assertIn('"notification_error": "agent_blocked"', self.cli("show", "M1").stdout)
        self.assertIn("M1", self.cli("inbox", session="other").stdout)
        self.assertEqual(self.cli("notify", "M1", mode="uncertain", code=1).stdout, "M1 uncertain=1\n")
        self.cli("notify", "M1", code=1)
        self.assertEqual(len(self.notices.read_text().splitlines()), 1)
        with ThreadPoolExecutor(max_workers=2) as pool:
            retries = [pool.submit(self.cli, "notify", "M1", "--retry-uncertain") for _ in range(2)]
            for retry in retries:
                self.assertEqual(retry.result().stdout, "M1 submitted=1\n")
        notices = self.notices.read_text().splitlines()
        self.assertEqual(len(notices), 2)
        self.assertEqual(notices[0], notices[1])
        self.cli("ack", "M1", session="other")
        self.assertEqual(self.cli("status", "M1").stdout, "M1 acknowledged=1\n")

    def test_fanout_reply_and_portability(self):
        self.assertEqual(
            self.send("--to", "gamma/other/eve", "--action", "ack").stdout,
            "M1 submitted=2\n",
        )
        self.cli("ack", "M1", session="other")
        self.assertEqual(self.cli("status", "M1").stdout, "M1 acknowledged=1 submitted=1\n")
        self.cli(
            "send", "--from-team", "beta", "--to", "alpha/local/alice", "--reply-to", "M1",
            "--action", "ack", "--subject", "READY / HOLD — waiting for lead GO",
            session="other",
        )
        self.assertIn('"M2"', self.cli("show", "M1").stdout)
        self.assertIn('"reply_to": "M1"', self.cli("show", "M2").stdout)
        self.assertIn("MAIL M2 ack | READY / HOLD", self.cli("inbox").stdout)
        second = self.install_into("another-organization")
        self.assertNotIn("M1", self.cli("history", install=second).stdout)
        self.assertTrue((self.root / "another-organization/organization-state/agent-mail/mailbox.sqlite3").exists())

    def test_concurrent_first_use(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            sends = [
                pool.submit(self.send, "--body", "Handoff to Bob"),
                pool.submit(self.send, "--body", "A second handoff"),
            ]
            self.assertEqual(
                {result.result().stdout for result in sends},
                {"M1 submitted=1\n", "M2 submitted=1\n"},
            )


if __name__ == "__main__":
    unittest.main()
