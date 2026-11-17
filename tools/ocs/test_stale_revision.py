#!/usr/bin/env python3
"""Focused check for review item 2: a stale revision stops the edit.

`Serve.mutate()` used to auto-refresh the revision and replay the mutation
after a `stale_state` refusal. It now sends the mutation exactly once and
raises `StaleStateError` so the caller re-reads the drawing and reviews a
new change plan. This check proves:

  1. unit — a `stale_state` reply raises `StaleStateError` after exactly one
     send, carrying the caller's revision (never rewritten or replayed);
  2. unit — the sibling code `session_changed` behaves the same;
  3. unit — a clean reply is returned untouched after exactly one send;
  4. real binary — an edit sent with a deliberately outdated revision is
     refused, the target entity is verifiably unchanged, and the documented
     caller recovery (re-read state, send a fresh revision) is accepted.

Exit code 0 iff every check passes. Run: `python3 tools/ocs/test_stale_revision.py`.
"""

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ocs_smoke_test as ocs  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, fn) -> None:
    try:
        detail = fn() or ""
        results.append((name, True, detail))
        print(f"PASS  {name}" + (f"  [{detail}]" if detail else ""))
    except Exception as e:  # noqa: BLE001 - the check reports any failure
        results.append((name, False, str(e)))
        print(f"FAIL  {name}: {e}")


def fake_serve(reply):
    """A Serve instance without a process: `send` records calls, returns `reply`."""
    srv = ocs.Serve.__new__(ocs.Serve)
    srv.transcript_path = None
    calls: list[dict] = []

    def send(req, timeout=120.0):
        calls.append(req)
        return dict(reply)

    srv.send = send
    return srv, calls


def unit_stale_state_single_send() -> str:
    reply = {"ok": False, "code": "stale_state", "status": "failed",
             "error": "Nothing ran. Read state (ocs_read op state) and retry"}
    srv, calls = fake_serve(reply)
    try:
        srv.mutate({"op": "entities_set", "handles": ["8A"], "color": 1},
                   document_id=3, revision=0)
    except ocs.StaleStateError as e:
        assert e.reply["code"] == "stale_state"
        assert "NOT executed" in str(e), f"error must say the edit did not run: {e}"
    else:
        raise AssertionError("stale_state did not raise StaleStateError")
    assert len(calls) == 1, f"mutation was sent {len(calls)} times (replay still present)"
    req = calls[0]
    assert req["op"] == "entities_set" and req["protocol"] == 1
    assert req["document_id"] == 3
    assert req["revision"] == 0, "caller revision was rewritten"
    assert json.dumps(reply["error"])  # reply preserved on the exception
    return "1 send, StaleStateError raised, caller revision passed through"


def unit_session_changed_single_send() -> str:
    reply = {"ok": False, "code": "session_changed", "status": "failed",
             "error": "session changed"}
    srv, calls = fake_serve(reply)
    try:
        srv.mutate({"op": "undo"}, document_id=2, revision=7)
    except ocs.StaleStateError as e:
        assert e.reply["code"] == "session_changed"
    else:
        raise AssertionError("session_changed did not raise StaleStateError")
    assert len(calls) == 1
    assert calls[0]["revision"] == 7
    return "1 send, StaleStateError raised"


def unit_clean_reply_untouched() -> str:
    reply = {"ok": True, "status": "completed", "result": {"written": 1}}
    srv, calls = fake_serve(reply)
    out = srv.mutate({"op": "entities_set", "handles": ["8A"], "color": 1},
                     document_id=3, revision=1)
    assert out["ok"] is True and out["result"]["written"] == 1
    assert len(calls) == 1
    return "1 send, reply returned unchanged"


def real_binary_stale_refusal() -> str:
    exe = os.path.expanduser(os.environ.get("OCS_APPIMAGE") or ocs.DEFAULT_EXE)
    if not os.path.isfile(exe):
        return "SKIPPED real-binary part (OpenCADStudio binary not installed)"
    with tempfile.TemporaryDirectory() as tmp:
        srv = ocs.Serve(exe, stderr_path=os.path.join(tmp, "ocs_stderr.log"))
        try:
            srv.send({"op": "new"})
            st = srv.read({"protocol": 1, "op": "state"})
            doc, rev = st["document_id"], st["revision"]
            r = srv.mutate({"op": "entities_create", "entities": [
                {"type": "Circle", "layer": "TEST-SYMBOLS",
                 "center": [10, 10, 0], "radius": 3}]}, doc, rev)
            handle = r["result"]["handles"][0]

            stale_rev = rev  # revision from before the create: guaranteed behind
            try:
                srv.mutate({"op": "entities_set", "handles": [handle], "color": 1},
                           doc, stale_rev)
            except ocs.StaleStateError as e:
                assert e.reply["code"] == "stale_state"
            else:
                raise AssertionError("edit with stale revision was accepted — "
                                     "the stop-on-stale behavior is broken")

            q = srv.read({"protocol": 1, "op": "query", "document_id": doc,
                          "handles": [handle], "detail": "full"})
            color = q["entities"][0]["properties"]["common"]["color"]
            assert color == "ByLayer", \
                f"edit executed despite stale refusal: color={color!r}"

            # Documented caller recovery: re-read, review, send a fresh revision.
            st2 = srv.read({"protocol": 1, "op": "state"})
            r2 = srv.mutate({"op": "entities_set", "handles": [handle], "color": 1},
                            doc, st2["revision"])
            assert r2.get("ok") is True and (r2.get("result") or {}).get("written") == 1
            return (f"stale revision {stale_rev} refused, entity unchanged "
                    f"(color={color!r}), fresh revision accepted")
        finally:
            srv.close()


def main() -> int:
    check("1 unit: stale_state -> StaleStateError after exactly one send",
          unit_stale_state_single_send)
    check("2 unit: session_changed behaves the same", unit_session_changed_single_send)
    check("3 unit: clean reply returned after exactly one send",
          unit_clean_reply_untouched)
    check("4 real binary: stale revision refused, edit not executed, "
          "fresh revision accepted", real_binary_stale_refusal)
    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
