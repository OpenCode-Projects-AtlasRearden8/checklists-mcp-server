"""Tests for checklists-mcp-server: model round-trips, storage, security,
and end-to-end JSON-RPC smoke tests. Run with: python3 -m unittest -v
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import server as srv


def make_store():
    return srv.Store(Path(tempfile.mkdtemp(dir=Path("/tmp/opencode"))))


class TestSlugSafety(unittest.TestCase):
    def test_valid_slugs(self):
        self.assertEqual(srv.safe_slug("Launch Day"), "launch-day")
        self.assertEqual(srv.safe_slug("book-publishing"), "book-publishing")
        self.assertEqual(srv.safe_slug("Ünïcode Thing"), "unicode-thing")

    def test_rejects_dangerous(self):
        for bad in ["", "a" * 70, "../evil", "x/y", "x\\y", "..", "."]:
            with self.assertRaises(ValueError):
                srv.safe_slug(bad)


class TestPathSafety(unittest.TestCase):
    def setUp(self):
        self.store = make_store()

    def test_traversal_rejected(self):
        for evil in ["../etc", "a/../../etc/passwd", "x/../../y", "~", "/etc/passwd", "x\\y"]:
            with self.assertRaises(ValueError):
                self.store.load("checklists", evil)
            with self.assertRaises(ValueError):
                self.store.save("checklists", evil, {})

    def test_files_stay_inside(self):
        cl = srv.new_checklist("safe test", ["a"])
        self.store.save("checklists", cl["slug"], cl)
        p = (self.store.base / "checklists" / "safe-test.json").resolve()
        self.assertTrue(p.is_relative_to(self.store.base.resolve()))
        self.assertFalse(p.is_symlink())


class TestRoundTrip(unittest.TestCase):
    def setUp(self):
        self.store = make_store()
        self.cl = srv.new_checklist("Trip Prep", ["pack bag", "book flight", "charge phone"], "desc here")
        self.store.save("checklists", self.cl["slug"], self.cl)
        loaded = self.store.load("checklists", self.cl["slug"])
        self.cl = loaded

    def test_save_load_roundtrip(self):
        self.assertEqual(self.cl["name"], "Trip Prep")
        self.assertEqual([i["title"] for i in self.cl["items"]], ["pack bag", "book flight", "charge phone"])
        self.assertEqual(self.cl["description"], "desc here")

    def test_check_uncheck_reset(self):
        srv.check_item(self.cl, 1, note="carry-on only")
        self.assertTrue(self.cl["items"][0]["done"])
        self.assertEqual(self.cl["items"][0]["note"], "carry-on only")
        srv.uncheck_item(self.cl, "pack")
        self.assertFalse(self.cl["items"][0]["done"])
        srv.reset_checklist(self.cl, keep_notes=False)
        self.assertFalse(self.cl["items"][0]["done"])
        self.assertEqual(self.cl["items"][0]["note"], "")

    def test_check_by_number_and_title(self):
        self.assertEqual(srv.check_item(self.cl, 3)["title"], "charge phone")
        self.assertEqual(srv.check_item(self.cl, "book")["title"], "book flight")

    def test_report_remaining(self):
        srv.check_item(self.cl, 1)
        srv.check_item(self.cl, 2)
        r = srv.report(self.cl)
        self.assertEqual(r["remaining"], 1)
        self.assertEqual(r["percent"], 67)
        self.assertEqual(r["remaining_items"][0]["title"], "charge phone")

    def test_ambiguous_ref(self):
        self.cl["items"].append({"title": "book hotel", "done": False, "note": ""})
        with self.assertRaises(srv.ChecklistError):
            srv.check_item(self.cl, "book")

    def test_out_of_range(self):
        with self.assertRaises(srv.ChecklistError):
            srv.check_item(self.cl, 99)

    def test_backup_written_and_removed_on_success(self):
        srv.check_item(self.cl, 1)
        self.store.save("checklists", self.cl["slug"], self.cl)
        reloaded = self.store.load("checklists", self.cl["slug"])
        self.assertTrue(reloaded["items"][0]["done"])
        leftovers = list((self.store.base / "checklists").glob("*.bak*"))
        leftovers += list((self.store.base / "checklists").glob("*.tmp"))
        self.assertEqual(leftovers, [])

    def test_delete(self):
        self.assertTrue(self.store.delete("checklists", self.cl["slug"]))
        self.assertFalse(self.store.delete("checklists", self.cl["slug"]))
        self.assertIsNone(self.store.load("checklists", self.cl["slug"]))


class TestRendering(unittest.TestCase):
    def test_render_text(self):
        cl = srv.new_checklist("demo", ["one", "two"])
        srv.check_item(cl, 1, note="did it")
        text = srv.render_text(cl)
        self.assertIn("[x] 1. one", text)
        self.assertIn("[ ] 2. two", text)
        self.assertIn("note: did it", text)


class TestRpc(unittest.TestCase):
    def setUp(self):
        self.srv = srv.McpServer(make_store())

    def call(self, tool, **kwargs):
        msg = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": kwargs},
        }
        return self.srv.handle(msg)

    def test_initialize(self):
        r = self.srv.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}})
        self.assertEqual(r["result"]["protocolVersion"], srv.PROTOCOL_VERSION)

    def test_tools_list(self):
        r = self.srv.handle({"jsonrpc": "2.0", "id": 0, "method": "tools/list"})
        names = [t["name"] for t in r["result"]["tools"]]
        self.assertIn("create_checklist", names)
        self.assertIn("reset_checklist", names)

    def test_create_and_full_workflow(self):
        self.call("create_checklist", name="Launch Day", template="launch-day")
        slug = "launch-day"
        state = json.loads(self.call("report_remaining", slug=slug)["result"]["content"][0]["text"])
        self.assertEqual(state["remaining"], state["total"])
        self.call("check_item", slug=slug, ref=1, note="date locked")
        state = json.loads(self.call("report_remaining", slug=slug)["result"]["content"][0]["text"])
        self.assertEqual(state["remaining"], state["total"] - 1)
        self.call("reset_checklist", slug=slug)
        state = json.loads(self.call("report_remaining", slug=slug)["result"]["content"][0]["text"])
        self.assertEqual(state["remaining"], state["total"])
        text = self.call("get_checklist", slug=slug)["result"]["content"][0]["text"]
        self.assertIn("Launch Day", text)

    def test_error_is_tool_error_not_crash(self):
        self.call("create_checklist", name="greeting", items=["say hi"])
        r = self.call("check_item", slug="greeting", ref="nonsense-xyz")
        self.assertIn("error", r)

    def test_template_traversal_rejected(self):
        r = self.call("create_checklist", name="my name", template="../../etc")
        self.assertIn("error", r)


class TestStdioSmoke(unittest.TestCase):
    """Spawn server.py over real stdio and run a protocol round-trip."""

    def run_lines(self, lines):
        env = dict(os.environ)
        data_dir = Path(tempfile.mkdtemp(dir=Path("/tmp/opencode")))
        proc = subprocess.Popen(
            [sys.executable, str(Path(__file__).parent / "server.py"), str(data_dir)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env, text=True,
        )
        out, _ = proc.communicate("\n".join(lines) + "\n", timeout=20)
        return data_dir, [json.loads(l) for l in out.splitlines() if l.strip()]

    def test_stdio_round_trip(self):
        _, responses = self.run_lines([
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                        "params": {"name": "create_checklist",
                                   "arguments": {"name": "Smoke Test", "items": ["a", "b"]}}}),
            json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                        "params": {"name": "check_item",
                                   "arguments": {"slug": "smoke-test", "ref": 1}}}),
        ])
        self.assertIn("protocolVersion", responses[0]["result"])
        created = json.loads(responses[1]["result"]["content"][0]["text"])
        self.assertEqual(created["created"], "smoke-test")
        checked = json.loads(responses[2]["result"]["content"][0]["text"])
        self.assertEqual(checked["report"]["done"], 1)

    def test_rejects_lsp_framing(self):
        _, responses = self.run_lines(["Content-Length: 39\r\n\r\n{\"jsonrpc\":\"2.0\"}"])
        self.assertEqual(responses[0]["error"]["code"], -32700)

    def test_invalid_json(self):
        _, responses = self.run_lines(["{not json"])
        self.assertEqual(responses[0]["error"]["code"], -32700)


if __name__ == "__main__":
    unittest.main(verbosity=2)
