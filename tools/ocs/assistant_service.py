#!/usr/bin/env python3
"""Loopback OCS Assistant bridge. Read-only by construction; stdlib only."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import queue
import socketserver
import subprocess
import threading
import time

import drawing_index as di
import drawing_qa as qa

MAX_LINE = 32768


class BridgeError(RuntimeError):
    pass


class MCP:
    """One bounded stdio JSON-RPC connection, never launching an editor."""
    def __init__(self, exe, timeout=20):
        self.timeout = timeout
        self.serial = 0
        self.lock = threading.Lock()
        self.lines = queue.Queue(maxsize=128)
        self.proc = subprocess.Popen(
            [exe, "--mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        threading.Thread(target=self._pump, daemon=True).start()
        try:
            self.rpc("initialize", {"protocolVersion": "2025-11-25",
                     "capabilities": {}, "clientInfo": {
                         "name": "vibe-engineer", "version": "0.1"}})
            self._write({"jsonrpc": "2.0", "method": "notifications/initialized"})
            names = {t["name"] for t in self.rpc("tools/list", {}).get("tools", [])}
            if not {"ocs_sessions", "ocs_read"} <= names:
                raise BridgeError("Required MCP read tools are unavailable.")
        except Exception:
            self.close()
            raise

    def _pump(self):
        try:
            while True:
                line = self.proc.stdout.readline(8 * 1024 * 1024 + 1)
                if not line:
                    break
                if len(line) > 8 * 1024 * 1024 or not line.endswith("\n"):
                    break
                self.lines.put(line, timeout=1)
        finally:
            try:
                self.lines.put(None, timeout=1)
            except queue.Full:
                pass

    def _write(self, payload):
        self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def rpc(self, method, params):
        with self.lock:
            self.serial += 1
            serial = self.serial
            self._write({"jsonrpc": "2.0", "id": serial,
                         "method": method, "params": params})
            deadline = time.monotonic() + self.timeout
            try:
                while True:
                    remaining = deadline-time.monotonic()
                    if remaining <= 0:
                        raise queue.Empty()
                    line = self.lines.get(timeout=remaining)
                    if line is None:
                        raise BridgeError("CAD MCP process disconnected.")
                    value = json.loads(line)
                    if "method" in value:
                        # No elicitation, sampling or server-initiated actions.
                        if "id" in value:
                            self._write({"jsonrpc": "2.0", "id": value["id"],
                                         "error": {"code": -32601, "message": "Unsupported"}})
                        continue
                    if value.get("id") != serial or "error" in value:
                        self.close()
                        raise BridgeError("CAD MCP reply was refused or mismatched.")
                    return value["result"]
            except (queue.Empty, ValueError, KeyError, TypeError):
                self.close()  # never reuse a connection with an uncertain response
                raise BridgeError("CAD MCP timed out or returned an invalid reply.") from None

    def tool(self, name, arguments):
        if name not in {"ocs_sessions", "ocs_read"}:
            raise BridgeError("Mutating MCP tools are disabled.")
        if name == "ocs_sessions" and arguments != {"launch_if_none": False}:
            raise BridgeError("Launching an editor is disabled.")
        if name == "ocs_read" and arguments.get("op") not in {"state", "query"}:
            raise BridgeError("Only state and entity queries are enabled.")
        result = self.rpc("tools/call", {"name": name, "arguments": arguments})
        value = result.get("structuredContent")
        if result.get("isError") or not isinstance(value, dict) or value.get("ok") is False:
            raise BridgeError("CAD read failed; no drawing answer was produced.")
        return value

    def close(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=3)
        for stream in (self.proc.stdin, self.proc.stdout):
            if stream:
                stream.close()


class LiveCAD:
    """Read-only Serve-compatible adapter for the existing drawing index."""
    def __init__(self, mcp):
        self.mcp = mcp
        self.session = None
        self.entity_count = 0

    def connect(self):
        sessions = self.mcp.tool("ocs_sessions", {"launch_if_none": False}).get("result")
        if not isinstance(sessions, list) or len(sessions) != 1:
            raise BridgeError("Open exactly one CAD editor session before connecting.")
        self.session = sessions[0]["session_id"]

    def read(self, request):
        op = request["op"]
        if op not in {"state", "query"}:
            raise BridgeError("Unsupported read operation.")
        args = {"ocs_session_id": self.session, "op": op}
        params = {k: v for k, v in request.items() if k not in {"protocol", "op"}}
        if params:
            args["parameters"] = params
        result = self.mcp.tool("ocs_read", args)
        if op == "query":
            self.entity_count += len(result.get("entities") or [])
            if self.entity_count > 10000:
                raise BridgeError("This prototype supports at most 10000 indexed entities.")
        return result

    def send(self, request):
        if request.get("op") == "file_identity":
            return {"ok": False, "code": "not_probed_read_only_bridge"}
        return self.read(request)

    def active(self):
        state = self.read({"op": "state"})
        doc_id = state.get("document_id")
        doc = next((d for d in state.get("documents", []) if d.get("id") == doc_id), None)
        if doc is None or doc.get("start") or doc.get("revision") is None or not state.get("session_id"):
            raise BridgeError("The active drawing identity cannot be verified.")
        return state, doc

    def index(self):
        self.entity_count = 0
        _, doc = self.active()
        idx = di.index_document(self, doc["id"])
        return LiveIndex(idx.data, self)


class LiveIndex(di.DrawingIndex):
    """QA's default freshness calls must always inspect the live GUI drawing."""
    def __init__(self, data, cad):
        super().__init__(data)
        self.cad = cad

    def check_fresh(self, srv=None, document_id=None):
        _, doc = self.cad.active()
        if doc["id"] != self.data["ocs"]["document_id"]:
            raise di.IndexStaleError(["The active drawing changed; ask again."])
        return super().check_fresh(self.cad, doc["id"])


class Conversation:
    def __init__(self, cad, client=None):
        self.cad, self.client = cad, client
        self.running = False
        self.cancel = threading.Event()
        self.tools = 0

    def status(self):
        result = {"type": "status", "ocs": False, "drawing": "", "path": "",
                  "model": self.client.model if self.client else "local / no key",
                  "effort": "read-only", "tools": self.tools,
                  "api_key": bool(self.client and self.client.api_key),
                  "running": self.running,
                  "cost_usd": getattr(self.client, "tracked_spend_usd", 0.0)}
        try:
            _, doc = self.cad.active()
            result.update(ocs=True, drawing=doc.get("title") or doc.get("name") or "Untitled",
                          path=doc.get("path") or "")
        except Exception:
            pass
        return result

    def chat(self, text, emit):
        sources = None
        try:
            if not text.strip() or len(text) > 8000:
                raise BridgeError("Use a question of 1–8000 characters.")
            if text.strip().lower() in {"/help", "help", "مساعدة"}:
                output = ("Read-only assistant. /summary reads the active drawing; "
                          "/entity HANDLE reads one entity. With a local OpenRouter key, "
                          "ask about recorded counts, layers or text in Arabic/English. "
                          "Edits, structural assessment and automatic design are disabled.")
            elif qa.classify(text) in {qa.INTENT_STRUCTURAL, qa.INTENT_EDIT}:
                output = "This version cannot edit drawings or assess structural safety. No change was made."
            else:
                self.tools += 1
                tool_id = str(self.tools)
                emit({"type": "tool_start", "id": tool_id, "name": "drawing_index",
                      "title": "Read active drawing", "args": "Read-only snapshot"})
                started = time.monotonic()
                try:
                    idx = self.cad.index()
                    emit({"type": "tool_end", "id": tool_id, "ok": True,
                          "ms": round((time.monotonic()-started)*1000),
                          "preview": "Drawing snapshot ready; CAD data is not a structural classification.",
                          "images": []})
                except Exception:
                    emit({"type": "tool_end", "id": tool_id, "ok": False,
                          "ms": round((time.monotonic()-started)*1000),
                          "preview": "Could not verify the active drawing.", "images": []})
                    raise
                if self.cancel.is_set():
                    return
                if text.strip() == "/summary":
                    summary = idx.summary()
                    output = "Recorded CAD counts:\n```json\n" + json.dumps(summary["counts"], ensure_ascii=False, indent=2) + "\n```"
                elif text.strip().startswith("/entity "):
                    handle = text.strip().split(maxsplit=1)[1]
                    rec = idx.entity(handle)
                    output = "Recorded entity (classification is unconfirmed):\n```json\n" + json.dumps(rec, ensure_ascii=False, indent=2) + "\n```"
                elif self.client is None:
                    output = "No OpenRouter key is configured. Use /summary or /entity HANDLE without a key."
                else:
                    answer = qa.answer_question(idx, text, self.client)
                    if answer.status != "answered":
                        # Never show model/error prose or arbitrary citation errors.
                        output = "No verified answer was produced (" + answer.status + "). Ask about recorded CAD data or refresh the drawing."
                    else:
                        output = answer.answer_text
                idx.check_fresh()  # includes active-tab check before display
                meta = idx.data["ocs"]
                handles = []
                fact_paths = []
                if text.strip() == "/summary":
                    fact_paths = ["counts.entities", "counts.by_type", "counts.by_layer"]
                elif text.strip().startswith("/entity "):
                    handles = [str(rec["handle"])]
                elif self.client is not None and 'answer' in locals() and answer.status == "answered":
                    handles = answer.evidence.get("displayed_handles", [])
                    fact_paths = answer.evidence.get("displayed_fact_paths", [])
                sources = {"type": "sources", "session_id": meta["session_id"],
                           "document_id": meta["document_id"],
                           "revision": meta["document_revision"],
                           "path": idx.data["source"]["path"],
                           "handles": handles, "fact_paths": fact_paths}
                output += (f"\n\nSource: session `{meta['session_id']}`, document "
                           f"`{meta['document_id']}`, revision `{meta['document_revision']}`.")
                if self.client is not None and 'answer' in locals() and answer.status == "answered":
                    output += "\nEvidence:\n```json\n" + json.dumps(answer.evidence, ensure_ascii=False, indent=2) + "\n```"
            if not self.cancel.is_set():
                if len(output) > 24000:
                    raise BridgeError("Recorded result exceeds this prototype's display limit.")
                if sources is not None:
                    emit(sources)
                emit({"type": "text_start"})
                emit({"type": "text", "text": output})
        except Exception:
            if not self.cancel.is_set():
                emit({"type": "error", "text": "Request failed safely. Check the open drawing and MCP connection; no edit was sent."})
        finally:
            if self.cancel.is_set():
                emit({"type": "cancelled"})
            self.running = False
            emit({"type": "done", "cost_usd": getattr(self.client, "tracked_spend_usd", 0.0)})
            emit(self.status())


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        # One active conversation avoids concurrent spend counters and GUI races.
        # A panel that is closed and immediately reopened can race its old
        # socket's cleanup. Give that socket a short chance to release the
        # single-conversation lock before reporting a genuine conflict.
        if not self.server.conversation_lock.acquire(timeout=2):
            self.wfile.write(b'{"type":"error","text":"Another assistant conversation is connected."}\n')
            return
        lock = threading.Lock()
        def emit(value):
            # Defensive key redaction covers even drawing text containing a key.
            wire = json.dumps(value, ensure_ascii=False)
            wire = qa.redact(wire, os.environ.get("OPENROUTER_API_KEY"))
            with lock:
                try:
                    self.wfile.write(wire.encode("utf-8") + b"\n")
                    self.wfile.flush()
                except OSError:
                    # Disconnect cancels display; no raw payload/traceback is logged.
                    pass
        worker = None
        mcp = None
        try:
            mcp = self.server.mcp_factory()
            cad = LiveCAD(mcp)
            cad.connect()
            conv = Conversation(cad, self.server.client_factory())
            emit(conv.status())
            while True:
                line = self.rfile.readline(MAX_LINE+1)
                if not line:
                    break
                if len(line) > MAX_LINE or not line.endswith(b"\n"):
                    emit({"type": "error", "text": "Message too large."})
                    break
                try:
                    msg = json.loads(line.decode("utf-8"))
                    if not isinstance(msg, dict):
                        raise ValueError()
                except (ValueError, UnicodeDecodeError):
                    emit({"type": "error", "text": "Expected a UTF-8 JSON object."})
                    continue
                kind = msg.get("type")
                if kind == "refresh":
                    # Do not contend with an active MCP step; the idle refresh follows.
                    if not conv.running:
                        emit(conv.status())
                elif kind in {"stop", "reset"}:
                    conv.cancel.set()
                    if not conv.running:
                        emit(conv.status())
                elif kind == "chat":
                    if conv.running:
                        emit({"type": "error", "text": "Wait for the current request to finish."})
                        continue
                    if not isinstance(msg.get("text"), str):
                        emit({"type": "error", "text": "chat.text must be a string."})
                        continue
                    conv.cancel.clear()
                    conv.running = True
                    worker = threading.Thread(target=conv.chat, args=(msg["text"], emit), daemon=True)
                    worker.start()
        except Exception:
            try:
                emit({"type": "error", "text": "CAD connection unavailable. Open one editor session and reconnect."})
                emit({"type": "status", "ocs": False, "api_key": bool(os.environ.get("OPENROUTER_API_KEY")), "running": False})
            except OSError:
                pass
        finally:
            if 'conv' in locals():
                conv.cancel.set()
            if worker:
                worker.join(timeout=65)
            if mcp:
                mcp.close()
            self.server.conversation_lock.release()


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    def __init__(self, address, mcp_factory, client_factory):
        self.mcp_factory, self.client_factory = mcp_factory, client_factory
        self.conversation_lock = threading.Lock()
        super().__init__(address, Handler)


def main(argv=None):
    qa._seed_env_file()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=int(os.environ.get("OCS_ASSISTANT_PORT", "8766")))
    parser.add_argument("--ocs-exe", default=os.environ.get("OCS_EXE"))
    parser.add_argument("--model", default=os.environ.get("OPENROUTER_MODEL", ""))
    parser.add_argument("--spend-cap-usd", type=float, default=float(os.environ.get("OPENROUTER_SPEND_CAP_USD", "5")))
    args = parser.parse_args(argv)
    if not args.ocs_exe or not Path(args.ocs_exe).is_file():
        parser.error("Set OCS_EXE to the tested CAD executable.")
    if not 1 <= args.port <= 65535 or not math.isfinite(args.spend_cap_usd) or args.spend_cap_usd <= 0:
        parser.error("Use a valid port and a positive finite spend cap.")
    if os.environ.get("OPENROUTER_API_KEY") and not args.model:
        parser.error("Set OPENROUTER_MODEL explicitly before enabling paid calls.")
    def client_factory():
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            return None
        return qa.OpenRouterClient(model=args.model, api_key=key, max_tokens=600,
                   timeout_s=30, spend_cap_usd=args.spend_cap_usd,
                   usage_log_path=str(Path(__file__).parent / "out" / "assistant_usage.jsonl"))
    with Server(("127.0.0.1", args.port), lambda: MCP(args.ocs_exe), client_factory) as server:
        print(f"Vibe Engineer read-only assistant listening on 127.0.0.1:{args.port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
