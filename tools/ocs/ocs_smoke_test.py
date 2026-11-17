#!/usr/bin/env python3
"""Headless Open CAD Studio integration smoke test (Vibe Engineer milestone 1).

Drives a pinned OpenCADStudio build through its documented `--serve` JSON-line
channel (docs/automation/native.md in the OpenCADStudio repository) and proves,
against the real binary, the minimal edit cycle Vibe Engineer depends on:

  1. generate a synthetic DXF fixture (no customer data),
  2. close it and open it from disk,
  3. read document state and entities (circle / lines / text),
  4. make one bounded edit (one handle, one property) on a filesystem copy,
  5. verify exactly what changed (target changed, out-of-scope entity did not),
  6. undo, verify the original state is restored, redo,
  7. save, close, reopen the edited copy and verify persistence,
  8. (extra evidence) document audit and verified save with reopen + hash.

Every request and reply is appended verbatim to `<workdir>/requests.jsonl`
and summarized in `<workdir>/smoke_report.txt` / `smoke_report.json`.

Usage:
  python3 tools/ocs/ocs_smoke_test.py [--exe PATH] [--workdir DIR]

Exit code 0 iff every required step passed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import queue

OCS_VERSION = "2026.46"
OCS_REVISION = "8b4d27046040"  # git tag v2026.46 -> commit 8b4d27046040b27eb07d3faed7b7472bd7bdaea9
DEFAULT_EXE = os.path.expanduser("~/.local/opt/OpenCADStudio-v2026.46/OpenCADStudio.AppImage")

FIXTURE_TEXT = "SMOKE-TEST SYNTHETIC FIXTURE"
LAYER_BOUNDARY = "TEST-BOUNDARY"
LAYER_SYMBOLS = "TEST-SYMBOLS"
EDIT_COLOR = 1  # ACI 1 = red
BYLAYER_COLOR = {"Index": 256}


class StepFailure(Exception):
    """Raised when a step's assertion fails; message carries the evidence."""


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def color_of(entity: dict):
    """Extract the common color object from a `query` `detail:"full"` entity."""
    return (entity.get("properties") or {}).get("common", {}).get("color")


class Serve:
    """One `OpenCADStudio --serve` process; one JSON value per line."""

    def __init__(self, exe: str, stderr_path: str):
        self.exe = exe
        self.stderr_path = stderr_path
        self._err_fh = open(stderr_path, "wb")
        self.proc = subprocess.Popen(
            [exe, "--serve"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._err_fh,
            text=True,
            bufsize=1,
        )
        self._lines: queue.Queue = queue.Queue()
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()
        self.transcript_path: str | None = None
        first = self._lines.get(timeout=60)
        self.greeting = json.loads(first)
        self._record("greeting", self.greeting)
        if not self.greeting.get("ready"):
            raise StepFailure(f"no ready greeting from --serve: {self.greeting}")

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self._lines.put(line.rstrip("\n"))
        self._lines.put(None)  # EOF marker

    def _record(self, direction: str, payload) -> None:
        if self.transcript_path:
            with open(self.transcript_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"t": utcnow(), "dir": direction, "msg": payload},
                                    ensure_ascii=False, sort_keys=False) + "\n")

    def send(self, request: dict, timeout: float = 120.0) -> dict:
        """Send one JSON line, wait for the reply line, return it as a dict."""
        assert self.proc.stdin is not None
        self._record("req", request)
        self.proc.stdin.write(json.dumps(request) + "\n")
        self.proc.stdin.flush()
        item = self._lines.get(timeout=timeout)
        if item is None:
            raise StepFailure("OpenCADStudio --serve process ended before replying")
        reply = json.loads(item)
        self._record("res", reply)
        return reply

    def read(self, request: dict, timeout: float = 120.0) -> dict:
        """A read op: passive, retried verbatim, no envelope."""
        return self.send(request, timeout)

    def mutate(self, request: dict, document_id: int | None, revision: int | None,
               timeout: float = 120.0, stale_retries: int = 1) -> dict:
        """A mutation op: protocol-1 envelope, optimistic concurrency, stale retry.

        `document_id=None` omits the field (document-creating ops such as
        `open` are refused when addressed to a closed document).
        """
        req = {"protocol": 1, "request_id": f"ve-{int(time.time()*1000)}-{id(request)%100000}"}
        if document_id is not None:
            req["document_id"] = document_id
        if revision is not None:
            req["revision"] = revision
        req.update(request)
        reply = self.send(req, timeout)
        tries = 0
        while reply.get("ok") is False and reply.get("code") == "stale_state" and tries < stale_retries:
            tries += 1
            state = self.read({"protocol": 1, "op": "state"})
            new_rev = state.get("revision")
            req = dict(req)
            req["revision"] = new_rev
            self._record("note", {"stale_state": True, "retry": tries, "new_revision": new_rev})
            reply = self.send(req, timeout)
        return reply

    def close(self) -> None:
        try:
            if self.proc.stdin and not self.proc.stdin.closed:
                self.proc.stdin.close()
            self.proc.wait(timeout=15)
        except Exception:
            self.proc.kill()
            self.proc.wait(timeout=10)
        finally:
            self._err_fh.close()


class Report:
    def __init__(self, workdir: str):
        self.workdir = workdir
        self.steps: list[dict] = []
        self.env: dict = {}
        self.started = time.time()

    def env_line(self, key: str, value) -> None:
        self.env[key] = value

    def step(self, name: str, required: bool, ok: bool, detail: str, started: float) -> None:
        self.steps.append({
            "name": name,
            "required": required,
            "ok": ok,
            "detail": detail,
            "seconds": round(time.time() - started, 3),
        })

    def verdict(self) -> tuple[bool, bool]:
        all_ok = all(s["ok"] for s in self.steps)
        required_ok = all(s["ok"] for s in self.steps if s["required"])
        return all_ok, required_ok

    def write(self) -> str:
        all_ok, required_ok = self.verdict()
        lines: list[str] = []
        lines.append("# OpenCADStudio headless smoke test report")
        lines.append("")
        lines.append(f"- generated: {utcnow()}")
        lines.append(f"- total runtime: {round(time.time() - self.started, 1)} s")
        for key in sorted(self.env):
            lines.append(f"- {key}: {self.env[key]}")
        lines.append("")
        lines.append("| # | step | required | result | time (s) | detail |")
        lines.append("|---|---|---|---|---|---|")
        for i, s in enumerate(self.steps, 1):
            lines.append(f"| {i} | {s['name']} | {'yes' if s['required'] else 'no'} | "
                         f"{'PASS' if s['ok'] else 'FAIL'} | {s['seconds']} | {s['detail'][:400]} |")
        lines.append("")
        lines.append(f"verdict: {'PASS' if required_ok else 'FAIL'} (required steps); "
                     f"{'PASS' if all_ok else 'PASS/FAIL mixed in extra steps'} (all steps)")
        path = os.path.join(self.workdir, "smoke_report.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        with open(os.path.join(self.workdir, "smoke_report.json"), "w", encoding="utf-8") as fh:
            json.dump({"env": self.env, "steps": self.steps,
                       "required_pass": required_ok, "all_pass": all_ok,
                       "generated": utcnow()}, fh, indent=1, ensure_ascii=False)
        return path


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(2)


def disk_free(path: str) -> int:
    return shutil.disk_usage(path).free


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--exe", default=os.environ.get("OCS_APPIMAGE", DEFAULT_EXE),
                    help="OpenCADStudio AppImage path (default: pinned v2026.46 location)")
    ap.add_argument("--workdir", default=None, help="working dir for fixtures and reports")
    args = ap.parse_args()

    exe = os.path.abspath(os.path.expanduser(args.exe))
    if not os.path.isfile(exe):
        die(f"OpenCADStudio executable not found: {exe} (run tools/ocs/setup.sh first)")

    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    workdir = os.path.abspath(args.workdir or os.path.join(repo_root, "tools", "ocs", "out"))
    if os.path.isdir(workdir):
        shutil.rmtree(workdir)
    os.makedirs(workdir)
    sys.stderr.write(f"workdir: {workdir}\n")

    fixture = os.path.join(workdir, "fixture_base.dxf")
    edited = os.path.join(workdir, "fixture_edit.dxf")
    verified = os.path.join(workdir, "fixture_verified.dxf")
    stderr_log = os.path.join(workdir, "ocs_stderr.log")

    rep = Report(workdir)
    rep.env_line("host.os", f"{platform.system()} {platform.release()} ({platform.machine()})")
    rep.env_line("host.python", platform.python_version())
    rep.env_line("ocs.exe", exe)
    rep.env_line("ocs.exe.size_bytes", os.path.getsize(exe))
    rep.env_line("ocs.exe.sha256", sha256_file(exe))
    rep.env_line("disk.free_before_run_bytes", disk_free(os.path.expanduser("~")))
    rep.env_line("workdir", workdir)

    # ---- S1: version banner of the pinned build -------------------------------
    t0 = time.time()
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=60)
        banner = (out.stdout or "").strip()
        rep.env_line("ocs.version_banner", banner.replace("\n", " | "))
        if out.returncode != 0:
            raise StepFailure(f"--version exit={out.returncode} stderr={out.stderr[:300]}")
        if OCS_VERSION not in banner:
            raise StepFailure(f"--version banner lacks pinned version {OCS_VERSION}: {banner!r}")
        if OCS_REVISION not in banner:
            raise StepFailure(f"--version banner lacks pinned revision {OCS_REVISION}: {banner!r}")
        print(f"S1  PASS  --version reports {OCS_VERSION} rev {OCS_REVISION}")
        rep.step("S1 version banner matches pinned release", True, True,
                 banner.replace("\n", " | "), t0)
    except StepFailure as e:
        print(f"S1  FAIL  {e}")
        rep.step("S1 version banner matches pinned release", True, False, str(e), t0)

    # ---- everything else runs against one --serve process --------------------
    srv = None
    try:
        srv = Serve(exe, stderr_path=stderr_log)
        srv.transcript_path = os.path.join(workdir, "requests.jsonl")
        rep.env_line("ocs.serve.greeting", json.dumps(srv.greeting, sort_keys=True))

        # S2: greeting matches pinned version
        t0 = time.time()
        try:
            if srv.greeting.get("version") != OCS_VERSION:
                raise StepFailure(f"greeting version {srv.greeting.get('version')} != {OCS_VERSION}")
            print(f"S2  PASS  --serve greeting: version {OCS_VERSION}, "
                  f"session {srv.greeting.get('session_id')}")
            rep.step("S2 --serve greeting", True, True, json.dumps(srv.greeting), t0)
        except StepFailure as e:
            print(f"S2  FAIL  {e}")
            rep.step("S2 --serve greeting", True, False, str(e), t0)

        # S3: capabilities read — record what the build advertises
        t0 = time.time()
        try:
            caps = srv.read({"protocol": 1, "op": "capabilities"})
            if not caps.get("ok"):
                raise StepFailure(f"capabilities not ok: {caps}")
            ops = caps.get("operations") or {}
            # Only capability-gated ops belong here; `save`/`undo`/`redo` are
            # always-present protocol ops (docs/automation/native.md), so they
            # are not part of the advertisement.
            need = ["entities_create", "entities_set", "close"]
            missing = [op for op in need if not ops.get(op)]
            tx = caps.get("transactions") or {}
            if not tx.get("undo") or not tx.get("redo"):
                missing.append("transactions.undo/redo")
            if missing:
                raise StepFailure(f"build does not advertise required operations: {missing}")
            detail = (f"operations required all advertised; transactions={json.dumps(tx)}; "
                      f"records.collections={len(caps.get('records', {}).get('collections', []))}")
            print(f"S3  PASS  capabilities advertise required operations")
            rep.step("S3 capabilities advertise required operations", True, True, detail, t0)
        except StepFailure as e:
            print(f"S3  FAIL  {e}")
            rep.step("S3 capabilities advertise required operations", True, False, str(e), t0)

        # S4: generate the synthetic fixture (new -> entities_create -> save)
        t0 = time.time()
        create_state = None
        try:
            r = srv.send({"op": "new"})
            if not r.get("ok"):
                raise StepFailure(f"new failed: {r}")
            state = srv.read({"protocol": 1, "op": "state"})
            doc_id = state["document_id"]
            entities = [
                {"type": "Line", "layer": LAYER_BOUNDARY, "start": [0, 0, 0], "end": [100, 0, 0]},
                {"type": "Line", "layer": LAYER_BOUNDARY, "start": [100, 0, 0], "end": [100, 60, 0]},
                {"type": "Line", "layer": LAYER_BOUNDARY, "start": [100, 60, 0], "end": [0, 60, 0]},
                {"type": "Line", "layer": LAYER_BOUNDARY, "start": [0, 60, 0], "end": [0, 0, 0]},
                {"type": "Circle", "layer": LAYER_SYMBOLS, "center": [50, 30, 0], "radius": 5},
                {"type": "Text", "layer": LAYER_SYMBOLS, "value": FIXTURE_TEXT,
                 "position": [50, 66, 0], "height": 2.5},
            ]
            r = srv.mutate({"op": "entities_create", "entities": entities}, doc_id, state.get("revision"))
            if not r.get("ok"):
                raise StepFailure(f"entities_create failed: {r}")
            res = r.get("result") or {}
            if res.get("created") != 6 or len(res.get("handles") or []) != 6:
                raise StepFailure(f"expected 6 created entities, got: {json.dumps(res)}")
            if set(res.get("layers_created") or []) != {LAYER_BOUNDARY, LAYER_SYMBOLS}:
                raise StepFailure(f"unexpected layers_created: {json.dumps(res)}")
            r = srv.mutate({"op": "save", "path": fixture}, doc_id, None)
            if not r.get("ok"):
                raise StepFailure(f"save failed: {r}")
            saved = (r.get("result") or {}).get("saved")
            if saved != fixture or not os.path.isfile(fixture):
                raise StepFailure(f"save wrote {saved!r} instead of {fixture!r}")
            rep.env_line("fixture.sha256", sha256_file(fixture))
            create_state = {"document_id": doc_id, "handles": res.get("handles")}
            print(f"S4  PASS  fixture created and saved ({os.path.getsize(fixture)} bytes)")
            rep.step("S4 synthetic fixture generated via OCS (new/create/save)",
                     True, True, f"handles={res.get('handles')}", t0)
        except StepFailure as e:
            print(f"S4  FAIL  {e}")
            rep.step("S4 synthetic fixture generated via OCS (new/create/save)",
                     True, False, str(e), t0)

        if create_state:
            # S5: close and reopen from disk; read state + entities
            t0 = time.time()
            open_state = None
            try:
                r = srv.mutate({"op": "close"}, create_state["document_id"], None)
                if not r.get("ok"):
                    raise StepFailure(f"close failed: {r}")
                r = srv.mutate({"op": "open", "path": fixture}, None, None)
                if not r.get("ok"):
                    raise StepFailure(f"open failed: {r}")
                state = srv.read({"protocol": 1, "op": "state"})
                doc_id = state["document_id"]
                if doc_id == create_state["document_id"]:
                    raise StepFailure(f"open did not create a new document id ({doc_id})")
                docs = [d for d in state.get("documents") or [] if d.get("id") == doc_id]
                if not docs or docs[0].get("path") != fixture:
                    raise StepFailure(f"active document path wrong: {json.dumps(docs)}")
                circ = srv.read({"protocol": 1, "op": "query", "document_id": doc_id,
                                 "type": "Circle", "detail": "geometry"}).get("entities") or []
                if len(circ) != 1 or circ[0].get("radius") != 5.0:
                    raise StepFailure(f"circle readback wrong: {json.dumps(circ)}")
                h_target = circ[0]["handle"]
                lns = srv.read({"protocol": 1, "op": "query", "document_id": doc_id,
                                "type": "Line", "detail": "geometry"}).get("entities") or []
                if len(lns) != 4:
                    raise StepFailure(f"expected 4 lines, got {len(lns)}: {json.dumps(lns)[:300]}")
                h_control = next((e["handle"] for e in lns
                                  if e.get("start") == [0, 0, 0] and e.get("end") == [100, 0, 0]), None)
                if not h_control:
                    raise StepFailure(f"control line (0,0)->(100,0) not found in {json.dumps(lns)[:300]}")
                txt = srv.read({"protocol": 1, "op": "query", "document_id": doc_id,
                                "type": "Text", "detail": "full"}).get("entities") or []
                if len(txt) != 1 or txt[0].get("value") != FIXTURE_TEXT:
                    raise StepFailure(f"text readback wrong: {json.dumps(txt)[:300]}")
                open_state = {"document_id": doc_id, "h_target": h_target,
                              "h_control": h_control,
                              "revision": state.get("revision"),
                              "hand_seed": state.get("hand_seed")}
                print(f"S5  PASS  opened from disk; state doc_id={doc_id} rev={state.get('revision')}; "
                      f"circle handle={h_target} radius=5.0; 4 boundary lines; text value ok")
                rep.step("S5 close + open from disk + read state/entities", True, True,
                         f"doc_id={doc_id} h_target={h_target} h_control={h_control}", t0)
            except StepFailure as e:
                print(f"S5  FAIL  {e}")
                rep.step("S5 close + open from disk + read state/entities", True, False, str(e), t0)

            if open_state:
                doc_id = open_state["document_id"]
                h_target = open_state["h_target"]
                h_control = open_state["h_control"]

                # S6: filesystem copy — the edit happens on a copy, never the fixture
                t0 = time.time()
                try:
                    shutil.copy2(fixture, edited)
                    if sha256_file(fixture) != sha256_file(edited):
                        raise StepFailure("copy is not byte-identical to the fixture")
                    print(f"S6  PASS  copy created {os.path.basename(edited)} "
                          f"(sha256 identical, {os.path.getsize(edited)} bytes)")
                    rep.step("S6 byte-identical copy for the edit", True, True,
                             f"{edited}", t0)
                except StepFailure as e:
                    print(f"S6  FAIL  {e}")
                    rep.step("S6 byte-identical copy for the edit", True, False, str(e), t0)

                # S7: open the copy, capture baseline (target + out-of-scope entity)
                t0 = time.time()
                baseline = None
                try:
                    r = srv.mutate({"op": "open", "path": edited}, None, None)
                    if not r.get("ok"):
                        raise StepFailure(f"open copy failed: {r}")
                    state = srv.read({"protocol": 1, "op": "state"})
                    doc2 = state["document_id"]
                    if state.get("mode") != "headless":
                        raise StepFailure(f"unexpected mode {state.get('mode')}")
                    e_t = (srv.read({"protocol": 1, "op": "query", "document_id": doc2,
                                     "handles": [h_target], "detail": "full"}).get("entities") or [None])[0]
                    e_c = (srv.read({"protocol": 1, "op": "query", "document_id": doc2,
                                     "handles": [h_control], "detail": "full"}).get("entities") or [None])[0]
                    if not e_t or not e_c:
                        raise StepFailure("baseline entities not found in the copy")
                    c_t = color_of(e_t)
                    c_c = color_of(e_c)
                    if c_t == {"Index": EDIT_COLOR}:
                        raise StepFailure(f"target already has edit color: {json.dumps(c_t)}")
                    baseline = {"document_id": doc2, "target_color": c_t,
                                "control_color": c_c, "revision": state.get("revision")}
                    print(f"S7  PASS  copy opened doc_id={doc2}; baseline target color="
                          f"{json.dumps(c_t)} control color={json.dumps(c_c)}")
                    rep.step("S7 open copy + baseline read", True, True,
                             f"target_color={json.dumps(c_t)} control_color={json.dumps(c_c)}", t0)
                except StepFailure as e:
                    print(f"S7  FAIL  {e}")
                    rep.step("S7 open copy + baseline read", True, False, str(e), t0)

                if baseline:
                    doc2 = baseline["document_id"]

                    # S8: the bounded edit — one handle, one property, revision check
                    t0 = time.time()
                    try:
                        before_rev = baseline["revision"]
                        r = srv.mutate({"op": "entities_set", "handles": [h_target],
                                        "color": EDIT_COLOR}, doc2, before_rev)
                        if not r.get("ok"):
                            raise StepFailure(f"entities_set refused: {r}")
                        res = r.get("result") or {}
                        if res.get("written") != 1 or res.get("requested") != 1:
                            raise StepFailure(f"edit touched unexpected count: {json.dumps(res)}")
                        changes = r.get("changes") or []
                        if changes != [{"handle": h_target, "kind": "Modified"}]:
                            raise StepFailure(f"unexpected changes list: {json.dumps(changes)}")
                        after_rev = (r.get("state") or {}).get("revision")
                        if after_rev is None or after_rev == before_rev:
                            raise StepFailure(f"revision did not advance: {before_rev} -> {after_rev}")
                        print(f"S8  PASS  bounded edit written=1 handle={h_target} color={EDIT_COLOR}; "
                              f"revision {before_rev} -> {after_rev}")
                        rep.step("S8 bounded edit (one handle, one property, revision checked)",
                                 True, True, f"rev {before_rev} -> {after_rev}", t0)
                    except StepFailure as e:
                        print(f"S8  FAIL  {e}")
                        rep.step("S8 bounded edit (one handle, one property, revision checked)",
                                 True, False, str(e), t0)

                    # S9: verify what changed — target changed, control untouched
                    t0 = time.time()
                    try:
                        e_t = (srv.read({"protocol": 1, "op": "query", "document_id": doc2,
                                         "handles": [h_target], "detail": "full"}).get("entities") or [{}])[0]
                        e_c = (srv.read({"protocol": 1, "op": "query", "document_id": doc2,
                                         "handles": [h_control], "detail": "full"}).get("entities") or [{}])[0]
                        if color_of(e_t) != {"Index": EDIT_COLOR}:
                            raise StepFailure(f"target color wrong after edit: {json.dumps(color_of(e_t))}")
                        if color_of(e_c) != baseline["control_color"]:
                            raise StepFailure(f"out-of-scope entity changed: {json.dumps(color_of(e_c))}")
                        if e_t.get("radius") != 5.0 or e_t.get("center") != [50, 30, 0]:
                            raise StepFailure(f"edit changed geometry, not the property: {json.dumps(e_t)[:200]}")
                        print(f"S9  PASS  target color now {json.dumps(color_of(e_t))}; "
                              f"out-of-scope line unchanged (geometry intact: radius/center)")
                        rep.step("S9 verify change (target changed, out-of-scope unchanged)",
                                 True, True, f"target={json.dumps(color_of(e_t))}", t0)
                    except StepFailure as e:
                        print(f"S9  FAIL  {e}")
                        rep.step("S9 verify change (target changed, out-of-scope unchanged)",
                                 True, False, str(e), t0)

                    # S10: undo — original state restored
                    t0 = time.time()
                    try:
                        r = srv.mutate({"op": "undo"}, doc2, None)
                        if not r.get("ok"):
                            raise StepFailure(f"undo refused: {r}")
                        e_t = (srv.read({"protocol": 1, "op": "query", "document_id": doc2,
                                         "handles": [h_target], "detail": "full"}).get("entities") or [{}])[0]
                        if color_of(e_t) != baseline["target_color"]:
                            raise StepFailure(f"undo did not restore baseline: {json.dumps(color_of(e_t))}")
                        print(f"S10 PASS  undo restored original color {json.dumps(color_of(e_t))}")
                        rep.step("S10 undo restores original state", True, True,
                                 f"restored={json.dumps(color_of(e_t))}", t0)
                    except StepFailure as e:
                        print(f"S10 FAIL  {e}")
                        rep.step("S10 undo restores original state", True, False, str(e), t0)

                    # S11: redo — re-apply so the edited copy can be persisted
                    t0 = time.time()
                    try:
                        r = srv.mutate({"op": "redo"}, doc2, None)
                        if not r.get("ok"):
                            raise StepFailure(f"redo refused: {r}")
                        e_t = (srv.read({"protocol": 1, "op": "query", "document_id": doc2,
                                         "handles": [h_target], "detail": "full"}).get("entities") or [{}])[0]
                        if color_of(e_t) != {"Index": EDIT_COLOR}:
                            raise StepFailure(f"redo did not re-apply: {json.dumps(color_of(e_t))}")
                        print(f"S11 PASS  redo re-applied edit color {json.dumps(color_of(e_t))}")
                        rep.step("S11 redo re-applies the edit", True, True,
                                 f"color={json.dumps(color_of(e_t))}", t0)
                    except StepFailure as e:
                        print(f"S11 FAIL  {e}")
                        rep.step("S11 redo re-applies the edit", True, False, str(e), t0)

                    # S12: save the edited copy to its own path
                    t0 = time.time()
                    try:
                        hash_before = sha256_file(edited)
                        r = srv.mutate({"op": "save"}, doc2, None)
                        if not r.get("ok"):
                            raise StepFailure(f"save refused: {r}")
                        saved = (r.get("result") or {}).get("saved")
                        if saved != edited:
                            raise StepFailure(f"saved to {saved!r} instead of {edited!r}")
                        hash_after = sha256_file(edited)
                        if hash_after == hash_before:
                            raise StepFailure("file bytes unchanged after save — edit not persisted?")
                        print(f"S12 PASS  saved to {os.path.basename(edited)} "
                              f"(file bytes changed: {hash_before[:12]}.. -> {hash_after[:12]}..)")
                        rep.step("S12 save edited copy", True, True,
                                 f"saved={saved} target_format={(r.get('result') or {}).get('target_format')}", t0)
                    except StepFailure as e:
                        print(f"S12 FAIL  {e}")
                        rep.step("S12 save edited copy", True, False, str(e), t0)

                    # S13: close + reopen the edited copy — persistence proof
                    t0 = time.time()
                    try:
                        r = srv.mutate({"op": "close"}, doc2, None)
                        if not r.get("ok"):
                            raise StepFailure(f"close refused: {r}")
                        r = srv.mutate({"op": "open", "path": edited}, None, None)
                        if not r.get("ok"):
                            raise StepFailure(f"reopen refused: {r}")
                        state = srv.read({"protocol": 1, "op": "state"})
                        doc3 = state["document_id"]
                        e_t = (srv.read({"protocol": 1, "op": "query", "document_id": doc3,
                                         "handles": [h_target], "detail": "full"}).get("entities") or [{}])[0]
                        e_c = (srv.read({"protocol": 1, "op": "query", "document_id": doc3,
                                         "handles": [h_control], "detail": "full"}).get("entities") or [{}])[0]
                        if e_t.get("handle") != h_target:
                            raise StepFailure(f"handle changed across save/open: {e_t.get('handle')}")
                        if color_of(e_t) != {"Index": EDIT_COLOR}:
                            raise StepFailure(f"edit did not persist: {json.dumps(color_of(e_t))}")
                        if color_of(e_c) != baseline["control_color"]:
                            raise StepFailure(f"out-of-scope entity did not survive intact: {json.dumps(color_of(e_c))}")
                        txt = srv.read({"protocol": 1, "op": "query", "document_id": doc3,
                                        "type": "Text", "detail": "full"}).get("entities") or []
                        if len(txt) != 1 or txt[0].get("value") != FIXTURE_TEXT:
                            raise StepFailure(f"text lost or changed: {json.dumps(txt)[:200]}")
                        print(f"S13 PASS  reopened doc_id={doc3}: edit persisted (color "
                              f"{json.dumps(color_of(e_t))}), handle {h_target} stable, "
                              f"out-of-scope line and text intact")
                        rep.step("S13 save/reopen persistence verified", True, True,
                                 f"doc_id={doc3} color={json.dumps(color_of(e_t))}", t0)
                    except StepFailure as e:
                        print(f"S13 FAIL  {e}")
                        rep.step("S13 save/reopen persistence verified", True, False, str(e), t0)

                    # S14 (extra evidence): document audit for the DXF target
                    t0 = time.time()
                    try:
                        r = srv.read({"protocol": 1, "op": "audit", "document_id": doc3,
                                      "target_format": "dxf", "target_version": "2000"})
                        if not r.get("ok"):
                            raise StepFailure(f"audit refused: {r}")
                        if r.get("status") != "passed":
                            raise StepFailure(f"audit status {r.get('status')!r}: "
                                              f"issues={json.dumps(r.get('issues'))[:300]}")
                        if (r.get("summary") or {}).get("errors") or (r.get("summary") or {}).get("warnings"):
                            raise StepFailure(f"audit summary not clean: {json.dumps(r.get('summary'))}")
                        manifest = r.get("manifest") or {}
                        detail = (f"status=passed errors=0 warnings=0 "
                                  f"by_type={json.dumps(manifest.get('by_type'))} "
                                  f"lossless={(r.get('target') or {}).get('lossless')}")
                        print(f"S14 PASS  audit passed, manifest {json.dumps(manifest.get('by_type'))}, "
                              f"lossless={(r.get('target') or {}).get('lossless')}")
                        rep.step("S14 document audit (dxf/2000) — extra evidence",
                                 False, True, detail, t0)
                    except StepFailure as e:
                        print(f"S14 FAIL  {e}")
                        rep.step("S14 document audit (dxf/2000) — extra evidence",
                                 False, False, str(e), t0)

                    # S15 (extra evidence): verified save with reopen + hash
                    t0 = time.time()
                    try:
                        r = srv.mutate({"op": "save_verified", "path": verified,
                                        "target_format": "dxf", "target_version": "2000",
                                        "overwrite": False}, doc3, None)
                        if not r.get("ok"):
                            raise StepFailure(f"save_verified refused: {json.dumps(r)[:400]}")
                        res = r.get("result") or {}
                        hash_field = res.get("hash") or res.get("sha256")
                        if hash_field and os.path.isfile(verified):
                            actual = sha256_file(verified)
                            if actual != hash_field:
                                raise StepFailure(f"hash mismatch: reported {hash_field} actual {actual}")
                        if not os.path.isfile(verified):
                            raise StepFailure(f"verified file missing: {verified}")
                        print(f"S15 PASS  save_verified wrote {os.path.basename(verified)} "
                              f"hash={str(hash_field)[:16]}..")
                        rep.step("S15 save_verified (dxf/2000, reopen+hash) — extra evidence",
                                 False, True, json.dumps(res)[:300], t0)
                    except StepFailure as e:
                        print(f"S15 FAIL  {e}")
                        rep.step("S15 save_verified (dxf/2000, reopen+hash) — extra evidence",
                                 False, False, str(e), t0)
    finally:
        if srv:
            srv.close()
        rep.env_line("disk.free_after_run_bytes", disk_free(os.path.expanduser("~")))
        if os.path.isfile(stderr_log) and os.path.getsize(stderr_log) > 0:
            with open(stderr_log, "r", encoding="utf-8", errors="replace") as fh:
                head = fh.read(400).replace("\n", " | ")
            rep.env_line("ocs.stderr_excerpt", head)

    report_path = rep.write()
    all_ok, required_ok = rep.verdict()
    print(f"\nreport: {report_path}")
    print(f"verdict: required steps {'PASS' if required_ok else 'FAIL'}; "
          f"all steps {'PASS' if all_ok else 'INCLUDE FAILURES'}")
    return 0 if required_ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
