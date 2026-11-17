#!/usr/bin/env python3
"""Verification suite for the milestone-2 read/index layer.

Checks the `drawing_index` module in two tiers:

- UNIT (U1–U12): canned `--serve` replies (no OCS process) prove normalization
  (verbatim facts, explicit "unknown" sentinel, no invented fields), the
  deterministic queries, the file-hash staleness gate, the live-revision
  staleness gate, count integrity, JSON round-trip, the closed-document
  false-positive guard, freshness-gate coverage of EVERY public accessor,
  and the fail-closed rules for indexes that disk cannot validate (built
  from an unsaved/dirty document, or without a source file/hash): such
  indexes require the matching live session + document + revision and
  refuse otherwise; a supplied live `document_id` must equal the indexed
  one (no cross-document validation in the same session).
- REAL BINARY (R1–R11): the pinned OpenCADStudio build, headless. A synthetic
  fixture is produced by the milestone-1 tooling (`ocs_smoke_test.py`) and
  indexed through `--serve`; the index is cross-checked against a fresh live
  query of the same file; a copy is changed THROUGH OCS and the old index
  must refuse to answer (file-hash staleness, including the CLI `handles`
  form with exit 3); an unsaved in-session edit must be caught by the live
  revision check; unsupported API surface (`file_identity`) must be
  recorded; no structural classification may be invented; dirty and
  no-hash indexes must fail closed on the real binary too.

If the pinned binary is missing, every real-binary check reports SKIP and the
final verdict states that real integration was NOT verified. SKIP is never
presented as proof; only R1–R11 passing against the real binary is.

Exit code 0 iff no check FAILED. Run: `python3 tools/ocs/test_drawing_index.py`.
"""

import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ocs_smoke_test as ocs  # noqa: E402
import drawing_index as di    # noqa: E402

results: list[tuple[str, str, str]] = []


class SkipCheck(Exception):
    """Raised by a check that cannot run (e.g. real binary missing)."""


def check(name: str, fn) -> None:
    try:
        detail = fn() or ""
        results.append((name, "PASS", detail))
        print(f"PASS  {name}" + (f"  [{detail}]" if detail else ""))
    except SkipCheck as e:
        results.append((name, "SKIP", str(e)))
        print(f"SKIP  {name}  [{e}]")
    except Exception as e:  # noqa: BLE001 - the check reports any failure
        results.append((name, "FAIL", str(e)))
        print(f"FAIL  {name}: {e}")


# ---- canned full-detail entity replies (shape observed on the real build) ---
RAW_LINE = {"handle": "86", "type": "Line", "display_type": "Line", "layer": "A",
            "owner": "15", "space": "*Model_Space", "constraints": [],
            "start": [0.0, 0.0, 0.0], "end": [10.0, 0.0, 0.0],
            "bounds": {"min": [0.0, 0.0, 0.0], "max": [10.0, 0.0, 0.0]},
            "properties": {"common": {"color": "ByLayer", "layer": "A"}}}
RAW_CIRCLE = {"handle": "87", "type": "Circle", "display_type": "Circle", "layer": "B",
              "owner": "15", "space": "*Model_Space", "constraints": [],
              "center": [5.0, 5.0, 0.0], "radius": 2.0,
              "bounds": {"min": [3.0, 3.0, 0.0], "max": [7.0, 7.0, 0.0]},
              "properties": {"common": {"color": "ByLayer", "layer": "B"}}}
RAW_TEXT = {"handle": "88", "type": "Text", "display_type": "Text", "layer": "B",
            "owner": "15", "space": "*Model_Space", "constraints": [],
            "value": "HELLO WORLD", "text": "HELLO WORLD",
            "position": [5.0, 8.0, 0.0], "height": 2.5,
            "bounds": {"min": [5.0, 8.0, 0.0], "max": [21.5, 10.5, 0.0]},
            "properties": {"common": {"color": "ByLayer", "layer": "B"}}}
CANNED = [RAW_LINE, RAW_CIRCLE, RAW_TEXT]


class FakeServe:
    """Canned --serve session for unit checks (no OCS process).

    `read()` dispatches on op; `mutate()` fails loudly so unit checks prove
    no mutation is hidden inside the read/index path.
    """

    def __init__(self, state, query_reply, file_identity_reply=None):
        self._state = state
        self._query = query_reply
        self._fi = file_identity_reply or {"ok": False, "error": "unknown op: file_identity"}
        self.reads: list[dict] = []

    def read(self, req, timeout=120.0):
        self.reads.append(req)
        op = req.get("op")
        if op == "state":
            return self._state
        if op == "query":
            return self._query
        if op == "file_identity":
            return self._fi
        raise AssertionError(f"FakeServe.read unexpected op {op!r}")

    def send(self, req, timeout=120.0):
        self.reads.append(req)
        op = req.get("op")
        if op == "file_identity":
            return self._fi  # unit default: pinned-build-observed behavior
        raise AssertionError(f"FakeServe.send unexpected op {op!r}")

    def mutate(self, req, document_id=None, revision=None, timeout=120.0):
        raise AssertionError(f"FakeServe.mutate unexpectedly called for {req.get('op')!r}")


def fake_state(path, revision=0, doc_id=1, documents=None):
    return {"ok": True, "mode": "headless", "version": "2026.46",
            "session_id": "fake-session", "hand_seed": "99", "revision": revision,
            "document_id": doc_id,
            "documents": documents if documents is not None else
            [{"dirty": False, "id": doc_id, "path": path, "revision": revision,
              "title": "T", "start": False}]}


def make_tmp_drawing(tmpdir, content=b"0 SECTION\n  2 HEADER\n  0 ENDBLK\n") -> str:
    """A real file on disk standing in for a drawing (hash target only)."""
    path = os.path.join(tmpdir, "fake_drawing.dxf")
    with open(path, "wb") as fh:
        fh.write(content)
    return path


def unit_normalize_verbatim() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        src = make_tmp_drawing(tmp)
        srv = FakeServe(fake_state(src), {"ok": True, "count": 3, "entities": CANNED,
                                          "next_offset": None})
        idx = di.build_index(srv, src)
        recs = {r["handle"]: r for r in idx.entities()}
        assert set(recs) == {"86", "87", "88"}, recs.keys()
        line, circ, text = recs["86"], recs["87"], recs["88"]
        # verbatim slots
        assert (line["type"], line["layer"], line["space"], line["owner"]) == \
            ("Line", "A", "*Model_Space", "15")
        assert line["geometry"] == {"start": [0.0, 0.0, 0.0], "end": [10.0, 0.0, 0.0]}
        assert circ["geometry"] == {"center": [5.0, 5.0, 0.0], "radius": 2.0}
        assert text["text"] == {"value": "HELLO WORLD"}
        assert text["geometry"] == {"position": [5.0, 8.0, 0.0], "height": 2.5}
        assert line["text"] is None and circ["text"] is None
        assert all(r["color"] == "ByLayer" for r in (line, circ, text))
        assert all(r["bounds"] != di.UNKNOWN for r in (line, circ, text))
        # explicit unknowns, nothing invented
        assert all(r["classification"] is None for r in (line, circ, text))
        assert all(r["unmapped_fields"] == ["constraints"] for r in (line, circ, text))
        assert line["raw"] == RAW_LINE and text["raw"] == RAW_TEXT
        # counts recorded from the records, sorted
        assert idx.count() == 3
        assert idx.count_by_type() == {"Circle": 1, "Line": 1, "Text": 1}
        assert idx.count_by_layer() == {"A": 1, "B": 2}
        # unknown ledger carries the file_identity evidence of this fake build
        facts = {u["fact"] for u in idx.data["unknown"]}
        assert "ocs_file_identity" in facts, facts
        return "slots verbatim; classification null; constraints listed as unmapped"


def unit_search_text() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        src = make_tmp_drawing(tmp)
        srv = FakeServe(fake_state(src), {"ok": True, "count": 3, "entities": CANNED,
                                          "next_offset": None})
        idx = di.build_index(srv, src)
        hits = idx.search_text("hello")           # case-insensitive default
        assert [h["handle"] for h in hits] == ["88"], hits
        assert hits[0]["text"]["value"] == "HELLO WORLD"
        assert [h["handle"] for h in idx.search_text("WORLD")] == ["88"]
        assert idx.search_text("ZZZNOMATCH") == []
        assert idx.search_text("hello", case_sensitive=True) == []  # stored as uppercase
        assert idx.search_text("8") == []  # handles/numbers are not text
        return "substring, case-insensitive default, no match -> [], text-only"


def unit_entity_lookup_provenance() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        src = make_tmp_drawing(tmp)
        sha = di.sha256_file(src)
        srv = FakeServe(fake_state(src), {"ok": True, "count": 3, "entities": CANNED,
                                          "next_offset": None})
        idx = di.build_index(srv, src)
        rec = idx.entity("87")
        assert rec["type"] == "Circle" and rec["geometry"]["radius"] == 2.0
        p = rec["provenance"]
        assert p["source_path"] == src and p["source_sha256"] == sha
        assert p["document_id"] == 1 and p["document_revision"] == 0
        assert p["handle"] == "87" and "query" in p["supplied_by"]
        assert p["ocs_version"] == "2026.46"
        # caller mutation must not corrupt the index
        rec["geometry"]["radius"] = 999
        assert idx.entity("87")["geometry"]["radius"] == 2.0
        try:
            idx.entity("ZZ")
        except KeyError as e:
            assert "not in this index" in str(e), e
        else:
            raise AssertionError("unknown handle did not raise KeyError")
        return "handle lookup with file+revision+handle provenance; copy isolated"


def unit_file_hash_staleness() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        src = make_tmp_drawing(tmp, b"original bytes")
        srv = FakeServe(fake_state(src), {"ok": True, "count": 3, "entities": CANNED,
                                          "next_offset": None})
        idx = di.build_index(srv, src)
        assert idx.count() == 3
        old_sha = di.sha256_file(src)
        with open(src, "wb") as fh:
            fh.write(b"changed bytes - someone edited the drawing")
        new_sha = di.sha256_file(src)
        for call in (lambda: idx.count(),
                     lambda: idx.entity("87"),
                     lambda: idx.search_text("hello"),
                     lambda: idx.count_by_layer()):
            try:
                call()
            except di.IndexStaleError as e:
                assert old_sha in str(e) and new_sha in str(e), e
                assert "explicit new indexing operation" in str(e), e
            else:
                raise AssertionError("stale index answered a query")
        # restoring the exact bytes un-stales the index (content-based, not mtime)
        with open(src, "wb") as fh:
            fh.write(b"original bytes")
        assert idx.count() == 3
        return f"hash {old_sha[:12]}.. -> {new_sha[:12]}.. refused; exact restore accepted"


def unit_live_revision_staleness() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        src = make_tmp_drawing(tmp)
        canned = {"ok": True, "count": 3, "entities": CANNED, "next_offset": None}
        idx = di.build_index(FakeServe(fake_state(src), canned), src)
        stale_srv = FakeServe(fake_state(src, revision=1), canned)  # session moved on
        try:
            idx.count(srv=stale_srv, document_id=1)
        except di.IndexStaleError as e:
            assert "live document revision 1 != indexed revision 0" in str(e), e
        else:
            raise AssertionError("live revision divergence did not refuse")
        try:
            idx.count(srv=FakeServe(fake_state(src), canned))
        except ValueError as e:
            assert "both srv and document_id" in str(e), e
        else:
            raise AssertionError("half-specified live context did not raise ValueError")
        return "live revision 1 vs indexed 0 refused; half-context rejected"


def unit_counts_integrity_and_roundtrip() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        src = make_tmp_drawing(tmp)
        idx = di.build_index(FakeServe(fake_state(src),
                                       {"ok": True, "count": 3, "entities": CANNED,
                                        "next_offset": None}), src)
        t: dict = {}
        l: dict = {}
        for r in idx.entities():
            t[r["type"]] = t.get(r["type"], 0) + 1
            l[r["layer"]] = l.get(r["layer"], 0) + 1
        assert idx.count_by_type() == dict(sorted(t.items()))
        assert idx.count_by_layer() == dict(sorted(l.items()))
        path = os.path.join(tmp, "index.json")
        idx.to_json(path)
        idx2 = di.DrawingIndex.from_json(path)
        assert idx2.count() == idx.count()
        assert idx2.count_by_type() == idx.count_by_type()
        assert idx2.entity("88")["text"]["value"] == "HELLO WORLD"
        assert idx2.data["created_utc"] == idx.data["created_utc"]
        # staleness survives a round-trip
        with open(src, "wb") as fh:
            fh.write(b"rewritten after the index was saved")
        try:
            idx2.count()
        except di.IndexStaleError:
            pass
        else:
            raise AssertionError("reloaded stale index answered")
        return "recomputed counts == stored; JSON round-trip; staleness persists"


def unit_closed_document_no_false_stale() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        src = make_tmp_drawing(tmp)
        canned = {"ok": True, "count": 3, "entities": CANNED, "next_offset": None}
        idx = di.build_index(FakeServe(fake_state(src), canned), src)
        closed_srv = FakeServe(fake_state(src, documents=[]), canned)  # doc no longer open
        performed = idx.check_fresh(srv=closed_srv, document_id=1)  # must NOT raise
        assert any("unavailable" in p for p in performed), performed
        assert idx.count(srv=closed_srv, document_id=1) == 3
        return f"closed doc -> live check skipped honestly, file hash still checked {performed}"


class GateSentinel(Exception):
    """Raised by a patched check_fresh to prove accessors route through it."""


def unit_all_accessors_gated() -> str:
    """No public accessor may answer stale entity data: every fact-reading
    method must route through `check_fresh`. Persistence helpers (`to_json`,
    `to_json_dict`, `from_json`) and the raw `data` container are exempt —
    they move the snapshot, they do not interpret it; the persisted file is
    freshness-checked when loaded for queries."""
    with tempfile.TemporaryDirectory() as tmp:
        src = make_tmp_drawing(tmp)
        canned = {"ok": True, "count": 3, "entities": CANNED, "next_offset": None}
        idx = di.build_index(FakeServe(fake_state(src), canned), src)
        calls = {
            "count": lambda: idx.count(),
            "count_by_type": lambda: idx.count_by_type(),
            "count_by_layer": lambda: idx.count_by_layer(),
            "entity": lambda: idx.entity("86"),
            "search_text": lambda: idx.search_text("hello"),
            "entities": lambda: idx.entities(),
            "summary": lambda: idx.summary(),
        }
        exempt = {"check_fresh", "to_json", "to_json_dict", "from_json", "data"}
        public = {m for m in dir(type(idx))
                  if not m.startswith("_") and m not in exempt
                  and callable(getattr(type(idx), m, None))}
        assert public == set(calls), \
            f"public accessor list changed; gate every fact-reading method: " \
            f"uncovered={sorted(public - set(calls))} extra={sorted(set(calls) - public)}"
        original = di.DrawingIndex.check_fresh

        def boom(self, srv=None, document_id=None):
            raise GateSentinel("gate bypassed")

        try:
            di.DrawingIndex.check_fresh = boom
            for name, call in calls.items():
                try:
                    call()
                except GateSentinel:
                    pass
                else:
                    raise AssertionError(f"{name} answered without the freshness gate")
        finally:
            di.DrawingIndex.check_fresh = original
        # sanity: with the gate restored, the same calls answer again
        assert idx.count() == 3 and len(idx.entities()) == 3 and idx.summary()["counts"]
        return f"all {len(calls)} public accessors route through check_fresh " \
               f"(exempt: {sorted(exempt)})"


def build_dirty_index() -> tuple:
    """Index built from a document the fake session reports as dirty."""
    tmp = tempfile.mkdtemp()
    src = make_tmp_drawing(tmp)
    canned = {"ok": True, "count": 3, "entities": CANNED, "next_offset": None}
    state = fake_state(src, revision=1)
    state["documents"][0]["dirty"] = True
    idx = di.build_index(FakeServe(state, canned), src)
    assert idx.data["ocs"]["document_dirty"] is True
    assert idx.data["source"]["sha256"] is not None
    return idx, src, tmp


def build_nofile_index() -> tuple:
    """Index built from an unsaved document (no path, no hash)."""
    tmp = tempfile.mkdtemp()
    canned = {"ok": True, "count": 3, "entities": CANNED, "next_offset": None}
    state = fake_state(None, revision=0)  # documents[0].path=None
    idx = di.index_document(FakeServe(state, canned), 1)
    assert idx.data["source"]["sha256"] is None
    return idx, tmp


def unit_dirty_index_requires_live() -> str:
    idx, src, tmp = build_dirty_index()
    canned = {"ok": True, "count": 3, "entities": CANNED, "next_offset": None}

    def dirty_state(revision=1, session="fake-session", documents=None):
        st = fake_state(src, revision=revision, documents=documents)
        st["session_id"] = session
        if documents is None:
            st["documents"][0]["dirty"] = True
        return st

    try:
        # 1) no live context -> refuse, clearly
        try:
            idx.count()
        except di.IndexUnverifiableError as e:
            assert "unsaved in-session document" in str(e), e
            assert "no live session context" in str(e), e
        else:
            raise AssertionError("dirty index answered without a live session")
        # 2) matching live session + document + revision -> answer
        ok_srv = FakeServe(dirty_state(revision=1), canned)
        assert idx.count(srv=ok_srv, document_id=1) == 3
        assert idx.entities(srv=ok_srv, document_id=1)[0]["type"] == "Line"
        # 3) different session -> refuse (identity, not just revision)
        try:
            idx.count(srv=FakeServe(dirty_state(session="someone-elses-session"), canned),
                      document_id=1)
        except di.IndexStaleError as e:
            assert "live session 'someone-elses-session' != indexed session" in str(e), e
        else:
            raise AssertionError("dirty index accepted a different session")
        # 4) document closed -> refuse (nothing can validate the facts)
        try:
            idx.count(srv=FakeServe(dirty_state(documents=[]), canned), document_id=1)
        except di.IndexUnverifiableError as e:
            assert "no longer open" in str(e), e
        else:
            raise AssertionError("dirty index answered with its document closed")
        # 5) revision diverged -> refuse
        try:
            idx.count(srv=FakeServe(dirty_state(revision=2), canned), document_id=1)
        except di.IndexStaleError as e:
            assert "live document revision 2 != indexed revision 1" in str(e), e
        else:
            raise AssertionError("dirty index answered after a further edit")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)  # test-owned temp dir only
    return ("no ctx / other session / closed doc / diverged rev all refuse; "
            "matching live session answers")


def unit_nofile_index_requires_live() -> str:
    idx, tmp = build_nofile_index()
    try:
        canned = {"ok": True, "count": 3, "entities": CANNED, "next_offset": None}
        # 1) no live context -> refuse, clearly
        try:
            idx.summary()
        except di.IndexUnverifiableError as e:
            assert "no readable source file/hash" in str(e), e
        else:
            raise AssertionError("no-file index answered without a live session")
        # 2) matching live session -> answer
        ok_srv = FakeServe(fake_state(None, revision=0), canned)
        assert idx.count(srv=ok_srv, document_id=1) == 3
        # 3) different session -> refuse
        other = fake_state(None, revision=0)
        other["session_id"] = "someone-elses-session"
        try:
            idx.entities(srv=FakeServe(other, canned), document_id=1)
        except di.IndexStaleError as e:
            assert "live session 'someone-elses-session' != indexed session" in str(e), e
        else:
            raise AssertionError("no-file index accepted a different session")
        # 4) document closed -> refuse
        closed = fake_state(None, revision=0, documents=[])
        try:
            idx.count(srv=FakeServe(closed, canned), document_id=1)
        except di.IndexUnverifiableError as e:
            assert "no longer open" in str(e), e
        else:
            raise AssertionError("no-file index answered with its document closed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)  # test-owned temp dir only
    return "no ctx / other session / closed doc refuse; matching live session answers"


def unit_clean_index_live_checks_session_identity() -> str:
    """Even for a file-anchored index, a requested live check that lands on a
    different session is refused: document ids and revision counters are
    per-session, so the comparison would be meaningless. The file-hash-only
    path still answers (the index describes the file on disk)."""
    with tempfile.TemporaryDirectory() as tmp:
        src = make_tmp_drawing(tmp)
        canned = {"ok": True, "count": 3, "entities": CANNED, "next_offset": None}
        idx = di.build_index(FakeServe(fake_state(src), canned), src)
        other = fake_state(src)
        other["session_id"] = "someone-elses-session"
        try:
            idx.count(srv=FakeServe(other, canned), document_id=1)
        except di.IndexStaleError as e:
            assert "live session 'someone-elses-session' != indexed session" in str(e), e
        else:
            raise AssertionError("live check accepted a different session")
        assert idx.count() == 3  # file-hash-only answer is unaffected
        return "session identity verified on live checks; file-only path unaffected"


def unit_live_document_id_must_equal_indexed() -> str:
    """Regression (review of 3afb678): a live check must land on the document
    the index was built from. A session can hold TWO documents at the SAME
    revision; validating against the other document's id proves nothing.
    Both documents here have no file (source_sha256=null, live-required),
    same session, same revision — the old code answered the wrong-document
    live check because entry lookup + revision comparison alone matched."""
    canned = {"ok": True, "count": 3, "entities": CANNED, "next_offset": None}
    docs = [{"dirty": False, "id": 1, "path": None, "revision": 0,
             "title": "A", "start": False},
            {"dirty": False, "id": 2, "path": None, "revision": 0,
             "title": "B", "start": False}]
    state = fake_state(None, revision=0, doc_id=1, documents=docs)
    srv = FakeServe(state, canned)
    idx = di.index_document(srv, 1)
    assert idx.data["source"]["sha256"] is None        # live-required index
    assert idx.data["ocs"]["document_id"] == 1
    # the indexed document itself: live-verified answer still works
    assert idx.count(srv=srv, document_id=1) == 3
    assert idx.summary(srv=srv, document_id=1)["counts"]["entities"] == 3
    # the OTHER document, SAME session, SAME revision: refuse
    try:
        idx.count(srv=srv, document_id=2)
    except di.IndexStaleError as e:
        assert "is not the indexed document" in str(e), e
        assert "1" in str(e) and "2" in str(e), e  # both ids named
    else:
        raise AssertionError("live check accepted a different document id "
                             "in the same session at the same revision")
    return ("two no-file documents, same session+revision: live check on "
            "document 2 refused, on the indexed document 1 answers")


# ---- real-binary tier -------------------------------------------------------
EXE = ocs.resolve_exe()
REAL_OK = os.path.isfile(EXE)


def require_real() -> None:
    if not REAL_OK:
        raise SkipCheck(f"OpenCADStudio binary not installed at {EXE} "
                        "(run tools/ocs/setup.ps1 on Windows or tools/ocs/setup.sh "
                        "on Linux); this check is NOT executed")


def real_pinned_binary() -> str:
    require_real()
    out = subprocess.run([EXE, "--version"], capture_output=True, text=True, timeout=60)
    banner = (out.stdout or "").strip()
    assert out.returncode == 0, f"--version exit={out.returncode}"
    assert ocs.OCS_VERSION in banner and ocs.OCS_REVISION in banner, banner
    digest = di.sha256_file(EXE)
    # Pinned asset digests (official GitHub release-asset digests for tag
    # v2026.46 — https://api.github.com/repos/HakanSeven12/OpenCADStudio/releases/tags/v2026.46),
    # one per tested platform, matching the assets installed by
    # tools/ocs/setup.ps1 (win32) and tools/ocs/setup.sh (linux).
    PINNED_SHA256 = {
        "win32": ("OpenCADStudio-v2026.46-windows-x86_64-portable.exe",
                  "9492830b7d2b8d6e4f2cedf1e8ac47c6025757e6e30fdad687df6f5209064989"),
        "linux": ("OpenCADStudio-v2026.46-linux-x86_64.AppImage",
                  "267adab0d5aea632e07da16404d7d3255bd0b05fecda60f6a71630b5fab0061c"),
    }
    pinned = PINNED_SHA256.get(sys.platform)
    assert pinned is not None, f"no pinned asset digest recorded for platform {sys.platform!r}"
    pinned_asset, pinned_digest = pinned
    assert digest == pinned_digest, \
        (f"binary is not the pinned release asset {pinned_asset}: "
         f"sha256 {digest} != {pinned_digest}")
    return (f"{ocs.OCS_VERSION} rev {ocs.OCS_REVISION}; sha256 {digest[:12]}.. "
            f"matches pinned {pinned_asset}")


def run_smoke_tool(workdir_base: str) -> str:
    """Generate the fixture with the milestone-1 tooling and return its path."""
    proc = subprocess.run([sys.executable, os.path.join(HERE, "ocs_smoke_test.py"),
                           "--workdir", workdir_base],
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, f"milestone-1 smoke test failed:\n{proc.stdout[-2000:]}"
    hits = sorted(glob.glob(os.path.join(workdir_base, "run-*", "fixture_base.dxf")),
                  key=os.path.getmtime)
    assert hits, "smoke test produced no fixture_base.dxf"
    return hits[-1]


def real_fixture_from_milestone1_tooling(base: str) -> tuple[str, str]:
    require_real()
    fixture = run_smoke_tool(base)
    sha = di.sha256_file(fixture)
    return fixture, sha


def norm_fact(src: dict) -> dict:
    """Comparable normalized facts from a raw (or indexed) entity payload."""
    common = (src.get("properties") or {}).get("common") or {}
    text_value = src.get("value", src.get("text"))
    return {"type": src.get("type"), "layer": src.get("layer"),
            "space": src.get("space"), "owner": src.get("owner"),
            "color": common.get("color"),
            "start": src.get("start"), "end": src.get("end"),
            "center": src.get("center"), "radius": src.get("radius"),
            "position": src.get("position"), "height": src.get("height"),
            "value": text_value, "bounds": src.get("bounds")}


def real_build_and_crosscheck(base: str) -> tuple:
    require_real()
    fixture, fixture_sha = real_fixture_from_milestone1_tooling(base)
    stderr_log = os.path.join(base, "ocs_stderr.log")
    srv = ocs.Serve(EXE, stderr_path=stderr_log)
    try:
        idx = di.build_index(srv, fixture)
        assert idx.count() == 6, idx.count()
        assert idx.count_by_type() == {"Line": 4, "Circle": 1, "Text": 1}, \
            idx.count_by_type()
        assert idx.count_by_layer() == {ocs.LAYER_BOUNDARY: 4, ocs.LAYER_SYMBOLS: 2}, \
            idx.count_by_layer()
        circ = [r for r in idx.entities() if r["type"] == "Circle"]
        assert len(circ) == 1 and circ[0]["geometry"]["radius"] == 5.0 \
            and circ[0]["geometry"]["center"] == [50.0, 30.0, 0.0], circ
        lines = [r for r in idx.entities() if r["type"] == "Line"]
        got = {(tuple(l["geometry"]["start"]), tuple(l["geometry"]["end"])) for l in lines}
        want = {((0.0, 0.0, 0.0), (100.0, 0.0, 0.0)), ((100.0, 0.0, 0.0), (100.0, 60.0, 0.0)),
                ((100.0, 60.0, 0.0), (0.0, 60.0, 0.0)), ((0.0, 60.0, 0.0), (0.0, 0.0, 0.0))}
        assert got == want, sorted(got)
        texts = [r for r in idx.entities() if r["type"] == "Text"]
        assert len(texts) == 1 and texts[0]["text"]["value"] == ocs.FIXTURE_TEXT
        # provenance on every record
        for r in idx.entities():
            p = r["provenance"]
            assert p["source_path"] == fixture and p["source_sha256"] == fixture_sha
            assert p["document_revision"] == 0 and "query" in p["supplied_by"]
        # cross-check against a fresh live query of the same file
        live = ocs.Serve(EXE, stderr_path=stderr_log)
        try:
            live.mutate({"op": "open", "path": fixture}, None, None)
            st = live.read({"protocol": 1, "op": "state"})
            doc = st["document_id"]
            reply = live.read({"protocol": 1, "op": "query", "document_id": doc,
                               "detail": "full"})
            live_ents = {e["handle"]: e for e in reply["entities"]}
            idx_ents = {r["handle"]: r for r in idx.entities()}
            assert set(live_ents) == set(idx_ents), \
                f"handle sets differ: {set(live_ents) ^ set(idx_ents)}"
            for h, raw in live_ents.items():
                assert norm_fact(idx_ents[h]["raw"]) == norm_fact(raw), \
                    f"facts differ for handle {h}"
        finally:
            live.close()
        return idx, fixture, fixture_sha
    finally:
        srv.close()


def real_source_not_modified(idx, fixture, fixture_sha) -> str:
    require_real()
    assert di.sha256_file(fixture) == fixture_sha, "source changed by indexing"
    assert idx.data["source"]["sha256"] == fixture_sha
    return f"fixture sha256 unchanged through indexing ({fixture_sha[:12]}..)"


def real_file_staleness_refused(base: str, fixture: str, fixture_sha: str) -> str:
    require_real()
    copy1 = os.path.join(base, "stale_copy.dxf")
    shutil.copy2(fixture, copy1)
    assert di.sha256_file(copy1) == fixture_sha
    srv = ocs.Serve(EXE, stderr_path=os.path.join(base, "ocs_stderr.log"))
    try:
        idx = di.build_index(srv, copy1)   # opens + closes; hash proven unchanged
        n_before = idx.count()             # fresh answer, captured before the edit
        circ = next(r for r in idx.entities() if r["type"] == "Circle")
        handle = circ["handle"]
        # change the COPY through OCS itself: bounded edit + save (on the copy only)
        srv.mutate({"op": "open", "path": copy1}, None, None)
        st = srv.read({"protocol": 1, "op": "state"})
        doc, rev = st["document_id"], st["revision"]
        r = srv.mutate({"op": "entities_set", "handles": [handle], "color": 1},
                       doc, rev)
        assert r.get("ok") and (r.get("result") or {}).get("written") == 1, r
        r = srv.mutate({"op": "save"}, doc, None)
        assert r.get("ok"), r
        srv.mutate({"op": "close"}, doc, None)
        old_sha = idx.data["source"]["sha256"]
        new_sha = di.sha256_file(copy1)
        assert new_sha != old_sha, "OCS edit+save did not change the copy"
        for call in (lambda: idx.count(),
                     lambda: idx.entity(handle),
                     lambda: idx.search_text(ocs.FIXTURE_TEXT[:8]),
                     lambda: idx.count_by_type()):
            try:
                call()
            except di.IndexStaleError as e:
                assert old_sha in str(e) and new_sha in str(e), e
                assert "explicit new indexing operation" in str(e), e
            else:
                raise AssertionError("stale index answered after an OCS edit+save")
        # explicit rebuild is the only way forward
        idx2 = di.build_index(srv, copy1)
        assert idx2.count() == n_before
        assert idx2.data["source"]["sha256"] == new_sha
        circ2 = idx2.entity(handle)
        assert circ2["color"] == {"Index": 1}, circ2["color"]
        # the pristine fixture was never touched
        assert di.sha256_file(fixture) == fixture_sha
        return (f"index sha {old_sha[:12]}.. vs file {new_sha[:12]}.. refused on all 4 "
                f"queries; rebuild sees color {json.dumps(circ2['color'])}")
    finally:
        srv.close()


def real_live_revision_refused(base: str, fixture: str, fixture_sha: str) -> str:
    require_real()
    copy2 = os.path.join(base, "live_copy.dxf")
    shutil.copy2(fixture, copy2)
    srv = ocs.Serve(EXE, stderr_path=os.path.join(base, "ocs_stderr.log"))
    try:
        srv.mutate({"op": "open", "path": copy2}, None, None)
        st = srv.read({"protocol": 1, "op": "state"})
        doc = st["document_id"]
        idx = di.index_document(srv, doc)      # caller-owned doc; we never close it
        assert idx.data["ocs"]["document_revision"] == 0
        assert idx.count(srv=srv, document_id=doc) == 6
        circ = next(r for r in idx.entities() if r["type"] == "Circle")
        r = srv.mutate({"op": "entities_set", "handles": [circ["handle"]], "color": 3},
                       doc, 0)
        assert r.get("ok"), r
        live_rev = (r.get("state") or {}).get("revision")
        assert live_rev == 1, live_rev
        # the file on disk is unchanged (edit not saved): the hash check alone
        # cannot see this divergence, the live revision check must.
        assert di.sha256_file(copy2) == fixture_sha
        try:
            idx.count(srv=srv, document_id=doc)
        except di.IndexStaleError as e:
            assert "live document revision 1 != indexed revision 0" in str(e), e
        else:
            raise AssertionError("unsaved in-session edit was not caught by live check")
        assert idx.count() == 6  # file-hash-only answer still true (file unchanged)
        idx2 = di.index_document(srv, doc)     # explicit rebuild
        assert idx2.data["ocs"]["document_revision"] == 1
        assert idx2.count(srv=srv, document_id=doc) == 6
        srv.mutate({"op": "close"}, doc, None)  # we opened it, we close it
        return ("unsaved edit: live revision 1 vs indexed 0 refused; hash-only "
                "answer still correct (file unchanged); rebuild accepted")
    finally:
        srv.close()


def real_unsupported_recorded(idx, fixture) -> str:
    require_real()
    fi = idx.data["ocs_file_identity"]
    assert fi["status"] == "unsupported_by_pinned_build", fi
    assert fi["advertised"] is True
    assert fi["reply"].get("error") == "unknown op: file_identity", fi
    # the probe was a plain passive send: the document must be untouched
    assert idx.data["ocs"]["document_dirty"] is False
    assert idx.data["ocs"]["document_revision"] == 0
    assert any(u["fact"] == "ocs_file_identity" for u in idx.data["unknown"])
    return ("capabilities advertises file_identity; pinned build answers "
            "'unknown op: file_identity' (plain send, doc untouched) — recorded")


def real_no_structural_claims(idx, fixture) -> str:
    require_real()
    recs = idx.entities()
    assert all(r["classification"] is None for r in recs)
    assert all(r["type"] == r["raw"]["type"] and r["layer"] == r["raw"]["layer"]
               for r in recs)  # verbatim, no remapping into engineering labels
    blob = json.dumps(idx.data).lower()
    assert "column" not in blob and "beam" not in blob  # fixture has no such data anyway
    notes = " ".join(idx.data["notes"]).lower()
    assert "not structural claims" in notes and "engineer confirms" in notes
    return f"{len(recs)} records: classification null, type/layer verbatim, policy noted"


def ocs_change_color(base: str, path: str, color: int) -> str:
    """Change one entity through OCS (bounded edit + save) and return its handle."""
    srv = ocs.Serve(EXE, stderr_path=os.path.join(base, "ocs_stderr.log"))
    try:
        srv.mutate({"op": "open", "path": path}, None, None)
        st = srv.read({"protocol": 1, "op": "state"})
        doc, rev = st["document_id"], st["revision"]
        idx = di.index_document(srv, doc)
        circ = next(r for r in idx.entities() if r["type"] == "Circle")
        r = srv.mutate({"op": "entities_set", "handles": [circ["handle"]], "color": color},
                       doc, rev)
        assert r.get("ok") and (r.get("result") or {}).get("written") == 1, r
        r = srv.mutate({"op": "save"}, doc, None)
        assert r.get("ok"), r
        srv.mutate({"op": "close"}, doc, None)
        return circ["handle"]
    finally:
        srv.close()


def real_cli_handles_refusal(base: str, fixture: str, fixture_sha: str) -> str:
    """Regression for review item 1: the CLI `handles` form must pass the
    freshness gate — after the source changed, exit 3 with the reason."""
    require_real()
    copy3 = os.path.join(base, "cli_handles.dxf")
    shutil.copy2(fixture, copy3)
    assert di.sha256_file(copy3) == fixture_sha
    idx_json = os.path.join(base, "cli_handles_index.json")
    cli = [sys.executable, os.path.join(HERE, "drawing_index.py")]
    r = subprocess.run(cli + ["index", "--file", copy3, "--json", idx_json],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout[-500:] + r.stderr[-500:]
    r = subprocess.run(cli + ["query", "--index", idx_json, "--query", "handles"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout[-500:] + r.stderr[-500:]
    handles_fresh = r.stdout.split()
    assert len(handles_fresh) == 6, r.stdout

    handle = ocs_change_color(base, copy3, 2)     # change the COPY through OCS
    new_sha = di.sha256_file(copy3)
    assert new_sha != fixture_sha, "OCS edit+save did not change the copy"

    r = subprocess.run(cli + ["query", "--index", idx_json, "--query", "handles"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 3, (r.returncode, r.stdout[-300:], r.stderr[-300:])
    assert "STALE INDEX" in r.stderr, r.stderr
    assert fixture_sha in r.stderr and new_sha in r.stderr, r.stderr
    # and the equivalent python accessors refuse the same way
    idx = di.DrawingIndex.from_json(idx_json)
    calls = [idx.entities, idx.summary, idx.count, idx.count_by_type,
             idx.count_by_layer, lambda: idx.search_text("SMOKE"),
             lambda: idx.entity(handle)]
    for call in calls:
        try:
            call()
        except di.IndexStaleError:
            pass
        else:
            raise AssertionError("a public accessor answered after the file changed")
    return (f"CLI handles: 6 answers fresh -> exit 3 after OCS change "
            f"({fixture_sha[:8]}.. -> {new_sha[:8]}..); all 7 accessors refuse")


def real_dirty_index_fail_closed(base: str, fixture: str, fixture_sha: str) -> str:
    """Regression for review item 2 (dirty index) on the real binary."""
    require_real()
    copy4 = os.path.join(base, "dirty_failclosed.dxf")
    shutil.copy2(fixture, copy4)
    srv = ocs.Serve(EXE, stderr_path=os.path.join(base, "ocs_stderr.log"))
    try:
        srv.mutate({"op": "open", "path": copy4}, None, None)
        st = srv.read({"protocol": 1, "op": "state"})
        doc = st["document_id"]
        idx0 = di.index_document(srv, doc)
        circ = next(r["handle"] for r in idx0.entities() if r["type"] == "Circle")
        r = srv.mutate({"op": "entities_set", "handles": [circ], "color": 4}, doc, 0)
        assert r.get("ok"), r                      # unsaved edit: dirty, revision 1
        idx = di.index_document(srv, doc)          # index the DIRTY document
        assert idx.data["ocs"]["document_dirty"] is True
        assert idx.data["ocs"]["document_revision"] == 1
        assert di.sha256_file(copy4) == fixture_sha  # disk cannot validate the facts
        assert idx.count(srv=srv, document_id=doc) == 6  # live-verified answer works
        try:
            idx.count()
        except di.IndexUnverifiableError as e:
            assert "unsaved in-session document" in str(e), e
        else:
            raise AssertionError("dirty index answered without a live session")
        srv.mutate({"op": "close", "discard": True}, doc, None)  # drop OUR unsaved edit
        assert di.sha256_file(copy4) == fixture_sha  # the file was never touched
        try:
            idx.count(srv=srv, document_id=doc)
        except di.IndexUnverifiableError as e:
            assert "no longer open" in str(e), e
        else:
            raise AssertionError("dirty index answered after its document was closed")
        return ("dirty index: live-verified 6; no-context refused; closed-document "
                "refused (file hash never changed)")
    finally:
        srv.close()


def real_nofile_index_fail_closed(base: str) -> str:
    """Regression for review item 2 (no source hash) on the real binary."""
    require_real()
    srv = ocs.Serve(EXE, stderr_path=os.path.join(base, "ocs_stderr.log"))
    try:
        srv.send({"op": "new"})
        st = srv.read({"protocol": 1, "op": "state"})
        doc = st["document_id"]            # empty in-memory doc: not dirty, path None
        idx = di.index_document(srv, doc)
        assert idx.data["source"]["sha256"] is None
        assert idx.data["ocs"]["document_dirty"] is False  # isolates the no-hash rule
        assert idx.count(srv=srv, document_id=doc) == 0    # live-verified answer works
        try:
            idx.count()
        except di.IndexUnverifiableError as e:
            assert "no readable source file/hash" in str(e), e
        else:
            raise AssertionError("no-hash index answered without a live session")
        srv.mutate({"op": "close"}, doc, None)   # clean doc: plain close accepted
        try:
            idx.count(srv=srv, document_id=doc)
        except di.IndexUnverifiableError as e:
            assert "no longer open" in str(e), e
        else:
            raise AssertionError("no-hash index answered after its document was closed")
        return ("no-hash index: live-verified answer ok; no-context and "
                "closed-document refused")
    finally:
        srv.close()


def main() -> int:
    print(f"real binary: {EXE} -> "
          f"{'PRESENT' if REAL_OK else 'MISSING (real checks will SKIP)'}")
    repo_root = os.path.dirname(os.path.dirname(HERE))
    base = os.path.join(repo_root, "tools", "ocs", "out")
    os.makedirs(base, exist_ok=True)

    check("U1 unit: normalization verbatim + explicit unknowns + no invented fields",
          unit_normalize_verbatim)
    check("U2 unit: text search deterministic (case-insensitive default, text-only)",
          unit_search_text)
    check("U3 unit: entity lookup returns recorded provenance; unknown handle honest",
          unit_entity_lookup_provenance)
    check("U4 unit: file-hash staleness refuses every query, with old/new hashes",
          unit_file_hash_staleness)
    check("U5 unit: live-revision staleness refuses; half-specified context rejected",
          unit_live_revision_staleness)
    check("U6 unit: stored counts == recomputed; JSON round-trip keeps staleness",
          unit_counts_integrity_and_roundtrip)
    check("U7 unit: closed document is not false staleness (live check unavailable, "
          "not stale)", unit_closed_document_no_false_stale)
    check("U8 unit: every public accessor routes through the freshness gate",
          unit_all_accessors_gated)
    check("U9 unit: dirty index requires matching live session (no ctx / other "
          "session / closed doc / diverged rev refuse)", unit_dirty_index_requires_live)
    check("U10 unit: no-source-hash index requires matching live session",
          unit_nofile_index_requires_live)
    check("U11 unit: live checks verify session identity even for file-anchored "
          "indexes", unit_clean_index_live_checks_session_identity)
    check("U12 unit: live check must land on the indexed document (two no-file "
          "documents, same session, same revision)",
          unit_live_document_id_must_equal_indexed)

    ctx: dict = {}

    def r1() -> str:
        return real_pinned_binary()

    def r2() -> str:
        ctx["fixture"], ctx["sha"] = real_fixture_from_milestone1_tooling(base)
        return (f"fixture from milestone-1 tooling: {os.path.basename(ctx['fixture'])} "
                f"sha256 {ctx['sha'][:12]}..")

    def r3() -> str:
        ctx["idx"], ctx["fixture"], ctx["sha"] = real_build_and_crosscheck(base)
        n = ctx["idx"].count()
        return (f"index==live cross-check passed: {n} entities, "
                f"handles {sorted(r['handle'] for r in ctx['idx'].entities())}")

    def need_r3() -> None:
        if "idx" not in ctx:
            raise SkipCheck("depends on R3 (which failed or was skipped)")

    def r4() -> str:
        require_real()
        need_r3()
        return real_source_not_modified(ctx["idx"], ctx["fixture"], ctx["sha"])

    def r5() -> str:
        require_real()
        need_r3()
        return real_file_staleness_refused(base, ctx["fixture"], ctx["sha"])

    def r6() -> str:
        require_real()
        need_r3()
        return real_live_revision_refused(base, ctx["fixture"], ctx["sha"])

    def r7() -> str:
        require_real()
        need_r3()
        return real_unsupported_recorded(ctx["idx"], ctx["fixture"])

    def r8() -> str:
        require_real()
        need_r3()
        return real_no_structural_claims(ctx["idx"], ctx["fixture"])

    def r9() -> str:
        need_r3()
        return real_cli_handles_refusal(base, ctx["fixture"], ctx["sha"])

    def r10() -> str:
        need_r3()
        return real_dirty_index_fail_closed(base, ctx["fixture"], ctx["sha"])

    def r11() -> str:
        return real_nofile_index_fail_closed(base)

    check("R1 real: pinned OpenCADStudio binary answers --version", r1)
    check("R2 real: synthetic fixture produced by milestone-1 tooling (smoke test PASS)",
          r2)
    check("R3 real: index built through --serve; contents cross-checked vs live query",
          r3)
    check("R4 real: source file not modified by indexing", r4)
    check("R5 real: OCS edit+save on a copy -> old index refuses all queries; "
          "explicit rebuild accepted", r5)
    check("R6 real: unsaved in-session edit -> live revision check refuses; "
          "rebuild accepted", r6)
    check("R7 real: advertised-but-unimplemented op recorded as unsupported", r7)
    check("R8 real: no structural classification invented; type/layer stay CAD data", r8)
    check("R9 real: CLI handles refuses after an OCS change (exit 3); "
          "all public accessors refuse", r9)
    check("R10 real: dirty (unsaved) index fails closed on the real binary", r10)
    check("R11 real: no-source-hash index fails closed on the real binary", r11)

    unit = [(n, ok) for n, ok, _ in results if n.startswith("U")]
    real = [(n, ok) for n, ok, _ in results if n.startswith("R")]
    failed = [n for n, ok, _ in results if ok == "FAIL"]
    print(f"\nunit: {sum(1 for _, ok in unit if ok == 'PASS')}/{len(unit)} passed")
    print(f"real binary: {sum(1 for _, ok in real if ok == 'PASS')}/{len(real)} passed, "
          f"{sum(1 for _, ok in real if ok == 'SKIP')} skipped")
    if all(ok != "FAIL" for _, ok in real):
        if all(ok == "PASS" for _, ok in real):
            print("REAL-BINARY INTEGRATION: VERIFIED "
                  f"(real pinned OpenCADStudio {ocs.OCS_VERSION} at {EXE})")
        else:
            print("REAL-BINARY INTEGRATION: NOT VERIFIED (SKIP) — skipped checks are "
                  "not proof of integration")
    print(f"verdict: {'PASS' if not failed else 'FAIL'} "
          f"({len(results) - len(failed)}/{len(results)} checks passed)")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
