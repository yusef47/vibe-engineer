"""Offline bridge tests: fake MCP/model and real loopback panel protocol."""
import json
import os
import socket
import threading
import unittest
import queue
from unittest.mock import Mock, patch

import assistant_service as service
import drawing_qa as qa
from test_drawing_qa import CANNED, fake_state


class FakeMCP:
    def __init__(self):
        self.state = fake_state(None, dirty=True)
        self.calls = []
        self.closed = False
        self.sessions = [{"session_id": "fake-session"}]

    def tool(self, name, args):
        self.calls.append((name, args))
        if name == "ocs_sessions":
            assert args == {"launch_if_none": False}
            return {"result": self.sessions}
        assert name == "ocs_read", "Mutating tool attempted"
        if args["op"] == "state":
            return self.state
        assert args["op"] == "query"
        return {"ok": True, "entities": CANNED}

    def close(self):
        self.closed = True


class FakeModel:
    model = "fake / offline"
    api_key = "fake-not-a-real-key"
    tracked_spend_usd = 0

    def __init__(self, callback=None):
        self.calls = 0
        self.callback = callback

    def complete(self, system, user_content, meta=None):
        self.calls += 1
        if self.callback:
            self.callback()
        return json.dumps({"answerable": True, "answer": "6",
                           "citations": [{"fact": "entity_counts.total"}]}), {}


def conversation(model=None):
    mcp = FakeMCP()
    cad = service.LiveCAD(mcp)
    cad.connect()
    return service.Conversation(cad, model), mcp


def run(conv, text):
    events = []
    conv.chat(text, events.append)
    return events


class BridgeTests(unittest.TestCase):
    def test_key_only_default_model_on_panel_reconnect(self):
        captured = {}
        class StubServer:
            def __init__(self, address, mcp_factory, client_factory):
                captured["factory"] = client_factory
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def serve_forever(self):
                pass
        calls = 0
        def seed_env():
            nonlocal calls
            calls += 1
            if calls == 2:
                os.environ["OPENROUTER_API_KEY"] = "fake-not-a-real-key"
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "", "OPENROUTER_MODEL": ""}), \
             patch.object(service.qa, "_seed_env_file", side_effect=seed_env), \
             patch.object(service, "Server", StubServer), \
             patch.object(service.qa, "OpenRouterClient") as client:
            self.assertEqual(service.main(["--ocs-exe", service.__file__]), 0)
            captured["factory"]()
            self.assertEqual(client.call_args.kwargs["model"], qa.DEFAULT_MODEL)
            self.assertEqual(client.call_args.kwargs["api_key"], "fake-not-a-real-key")

    def test_mcp_facade_cannot_launch_or_mutate(self):
        mcp = service.MCP.__new__(service.MCP)
        mcp.rpc = Mock()
        for name, args in [("ocs_execute", {}), ("ocs_read", {"op": "run"}),
                           ("ocs_sessions", {"launch_if_none": True})]:
            with self.assertRaises(service.BridgeError):
                mcp.tool(name, args)
        mcp.rpc.assert_not_called()

    def test_mcp_rpc_timeout_closes_uncertain_connection(self):
        mcp = service.MCP.__new__(service.MCP)
        mcp.lock = threading.Lock()
        mcp.serial = 0
        mcp.timeout = .01
        mcp.lines = queue.Queue()
        mcp._write = Mock()
        mcp.close = Mock()
        with self.assertRaises(service.BridgeError):
            mcp.rpc("tools/list", {})
        mcp.close.assert_called_once()

    def test_mcp_rpc_mismatch_or_error_closes_connection(self):
        for reply in [{"id": 99, "result": {}},
                      {"id": 1, "error": {"code": -1, "message": "not displayed"}}]:
            mcp = service.MCP.__new__(service.MCP)
            mcp.lock = threading.Lock()
            mcp.serial = 0
            mcp.timeout = .1
            mcp.lines = queue.Queue()
            mcp.lines.put(json.dumps(reply))
            mcp._write = Mock()
            mcp.close = Mock()
            with self.assertRaises(service.BridgeError):
                mcp.rpc("tools/list", {})
            mcp.close.assert_called_once()

    def test_mcp_rpc_rejects_server_requests_and_reads_reply(self):
        mcp = service.MCP.__new__(service.MCP)
        mcp.lock = threading.Lock()
        mcp.serial = 0
        mcp.timeout = .1
        mcp.lines = queue.Queue()
        mcp.lines.put(json.dumps({"id": 101, "method": "sampling/createMessage"}))
        mcp.lines.put(json.dumps({"id": 1, "result": {"tools": []}}))
        mcp._write = Mock()
        self.assertEqual(mcp.rpc("tools/list", {}), {"tools": []})
        self.assertEqual(mcp._write.call_args.args[0]["error"]["code"], -32601)

    def test_no_key_status(self):
        conv, _ = conversation()
        self.assertFalse(conv.status()["api_key"])
        self.assertTrue(conv.status()["ocs"])

    def test_local_summary_dirty_unsaved(self):
        conv, mcp = conversation()
        events = run(conv, "/summary")
        text = next(e["text"] for e in events if e["type"] == "text")
        self.assertIn('"entities": 6', text)
        self.assertIn("revision `0`", text)
        self.assertTrue(all(name in {"ocs_sessions", "ocs_read"} for name, _ in mcp.calls))

    def test_local_entity(self):
        conv, _ = conversation()
        events = run(conv, "/entity 8A")
        self.assertIn('"Circle"', next(e["text"] for e in events if e["type"] == "text"))

    def test_structured_sources_are_traceable(self):
        conv, _ = conversation()
        events = run(conv, "/summary")
        source = next(e for e in events if e["type"] == "sources")
        self.assertEqual(source, {"type": "sources", "session_id": "fake-session",
                          "document_id": 1, "revision": 0, "path": None,
                          "handles": [], "fact_paths": ["counts.entities", "counts.by_type", "counts.by_layer"]})
        self.assertLess(events.index(source), next(i for i, e in enumerate(events) if e["type"] == "text_start"))
        events = run(conv, "/entity 8A")
        self.assertEqual(next(e for e in events if e["type"] == "sources")["handles"], ["8A"])
        conv.client = FakeModel()
        events = run(conv, "How many entities?")
        source = next(e for e in events if e["type"] == "sources")
        self.assertIn("entity_counts.total", source["fact_paths"])

    def test_no_key_question_honest(self):
        conv, _ = conversation()
        self.assertIn("No OpenRouter key", str(run(conv, "How many entities?")))

    def test_edits_and_structural_requests_never_model(self):
        model = FakeModel()
        conv, mcp = conversation(model)
        for question in ["Delete all circles", "Is this structure safe?", "احذف الدائرة"]:
            events = run(conv, question)
            self.assertIn("cannot edit", str(events))
        self.assertEqual(model.calls, 0)
        self.assertFalse(any(args.get("op") == "query" for _, args in mcp.calls))

    def test_validated_fake_model(self):
        model = FakeModel()
        conv, _ = conversation(model)
        events = run(conv, "How many entities?")
        text = next(e["text"] for e in events if e["type"] == "text")
        self.assertIn("6", text)
        self.assertIn("Evidence", text)
        self.assertEqual(model.calls, 1)

    def test_revision_changes_during_model_discard_answer(self):
        conv, mcp = conversation()
        def change():
            mcp.state["documents"][0]["revision"] = 1
        conv.client = FakeModel(change)
        events = run(conv, "How many entities?")
        self.assertFalse(any(e["type"] == "text" for e in events))
        self.assertFalse(any(e["type"] == "sources" for e in events))
        self.assertTrue(any(e["type"] == "error" for e in events))

    def test_active_tab_changes_discard_answer(self):
        conv, mcp = conversation()
        def change():
            mcp.state["document_id"] = 2
            mcp.state["documents"].append({"id": 2, "revision": 0})
        conv.client = FakeModel(change)
        events = run(conv, "How many entities?")
        self.assertFalse(any(e["type"] == "text" for e in events))

    def test_cancel_after_model_hides_answer(self):
        conv, _ = conversation()
        conv.client = FakeModel(conv.cancel.set)
        events = run(conv, "How many entities?")
        self.assertTrue(any(e["type"] == "cancelled" for e in events))
        self.assertFalse(any(e["type"] == "text" for e in events))

    def test_ambiguous_sessions_refused(self):
        for sessions in [[], [{"session_id": "a"}, {"session_id": "b"}]]:
            mcp = FakeMCP()
            mcp.sessions = sessions
            with self.assertRaises(service.BridgeError):
                service.LiveCAD(mcp).connect()

    def test_missing_entity_fails_closed(self):
        conv, _ = conversation()
        self.assertTrue(any(e["type"] == "error" for e in run(conv, "/entity FFFF")))

    def test_oversized_display_refuses_without_partial_text(self):
        conv, _ = conversation()
        idx = conv.cad.index()
        idx.data["entities"][0]["geometry"] = {"large": "x" * 25000}
        conv.cad.index = lambda: idx
        events = run(conv, "/entity 86")
        self.assertFalse(any(e["type"] == "text" for e in events))
        self.assertTrue(any(e["type"] == "error" for e in events))

    def test_oversized_drawing_refuses_partial_index(self):
        conv, mcp = conversation()
        original = mcp.tool
        def tool(name, args):
            if args.get("op") == "query":
                return {"ok": True, "entities": [CANNED[0]] * 10001}
            return original(name, args)
        mcp.tool = tool
        events = run(conv, "/summary")
        self.assertFalse(any(e["type"] == "text" for e in events))
        self.assertTrue(any(e["type"] == "error" for e in events))

    def test_wire_protocol_without_key(self):
        with service.Server(("127.0.0.1", 0), FakeMCP, lambda: None) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with socket.create_connection(server.server_address, timeout=5) as sock:
                    file = sock.makefile("rwb")
                    status = json.loads(file.readline())
                    self.assertEqual(status["type"], "status")
                    self.assertFalse(status["api_key"])
                    file.write(b'[]\n{"type":"chat","text":"/summary"}\n')
                    file.flush()
                    events = []
                    while not events or events[-1]["type"] != "done":
                        events.append(json.loads(file.readline()))
                    self.assertEqual(events[0]["type"], "error")
                    self.assertTrue(any(e["type"] == "tool_end" and e["ok"] for e in events))
                    self.assertIn('"entities": 6', str(events))
                    file.close()
            finally:
                server.shutdown()
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
