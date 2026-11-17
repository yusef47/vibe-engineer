#!/usr/bin/env python3
"""Deterministic read/index layer for an existing DXF via OCS `--serve` (milestone 2).

Reads an EXISTING synthetic (or any) drawing through the real pinned Open CAD
Studio build — the same proven `Serve` JSON-line channel as
`tools/ocs/ocs_smoke_test.py` — and builds a local, self-describing index of
what OCS actually returned. This module only READS. It never deletes caller
data (no rmtree anywhere), never rewrites caller paths, and inherits
`Serve.mutate()`'s stop-on-stale rule (a refused mutation is sent exactly once
and never replayed with a refreshed revision).

What the index records (all facts exactly as returned by OCS):

- the drawing source path and its SHA-256 (and size) at index time;
- OCS version, session id, document id, document revision, hand_seed, mode,
  and whether the document was dirty (unsaved changes) at index time;
- per entity: handle, type, layer, space, owner, geometry/text fields
  actually returned, bounds, color (verbatim shape: string "ByLayer" or
  {"Index": N}), plus the verbatim raw reply and any unmapped fields;
- explicit provenance per fact: source file + sha256, document revision,
  document id, entity handle, OCS version, and the OCS op that supplied it;
- explicit unknowns: anything OCS did not supply is recorded as the sentinel
  "unknown"; nothing is ever invented. Unsupported API surface observed on
  the pinned build (e.g. `file_identity` is advertised by `capabilities` but
  answers `unknown op`) is recorded with the exact reply.

Deterministic queries over the index (all freshness-gated; optional live
check that compares session id, document identity and revision):

- `count()` — total indexed entity count;
- `count_by_type()` / `count_by_layer()` — counts as recorded at index time;
- `entity(handle)` — one entity with its recorded source/provenance;
- `entities()` — all records (deep copies);
- `summary()` — identity/counts/unknown summary;
- `search_text(needle)` — substring search over recorded text values.

Staleness and safety (the trust contract):

- Every public query path passes the freshness gate before answering:
  `count`, `count_by_type`, `count_by_layer`, `entity`, `search_text`,
  `entities`, `summary`, and every CLI query form (including `handles`).
  No accessor serves stale entity data.
- File gate: the source file is re-hashed first; when the hash no longer
  matches the indexed one the answer is refused (`IndexStaleError` naming
  the old and new hashes). Re-reading/rebuilding happens ONLY through an
  explicit new indexing operation (`build_index` / `index_document` /
  CLI `index`).
- Live gate: with a live session context (`srv=..., document_id=...`) the
  supplied `document_id` must equal the index's stored `ocs.document_id`
  (validating against a different document proves nothing about these
  facts), and the live session id, the document identity (registered path)
  and the document revision are all compared with the indexed ones; any
  mismatch refuses. This catches unsaved in-session changes that a file
  hash cannot see.
- Fail-closed indexes: an index built from an unsaved (dirty) OCS document
  or one without a readable source file/hash (`source_sha256` null) carries
  in-session facts that the disk cannot validate. Such indexes REQUIRE the
  matching live session, document and revision for EVERY query and refuse
  to answer otherwise (`IndexUnverifiableError`): no session context, a
  different session, a closed document, or an unreadable session all
  refuse with the reason. Only an explicit new indexing operation changes
  what an index is anchored to.
- The source is not modified while indexing: the file is hashed before and
  after the read pass and an index is refused if the hash changed. If the
  document was already open in the session (caller-owned), it is indexed but
  never closed by this module; a document opened for indexing is closed
  afterwards (OCS reuses the document id when the same path is already open,
  so the pre-open state check is what keeps caller documents open).
- No structural claims: the index records `type`/`layer` verbatim as CAD
  data and sets `classification: null` on every entity. Labels such as
  "column" are never inferred from geometry or layer names; they stay
  unconfirmed until an engineer confirms them (a later, separate mechanism).

The pinned-build behavior this was verified against (v2026.46, Cloud Shell):

- `query` without `type` returns all entities with `detail:"geometry"|"full"`;
  `next_offset` was always null at fixture scale (pagination loop is
  defensive and its offset path is unverified — see README limits).
- unknown entity type filters answer `ok:true, count:0` (not an error);
- a document revision advances on mutations (e.g. entities_set), NOT on save;
- reopening an already-open path keeps the same document id;
- `file_identity` is advertised but unimplemented in this build.

CLI (see tools/ocs/README.md for the reproduction guide):

    python3 tools/ocs/drawing_index.py index --file PATH [--json OUT] [--exe PATH]
    python3 tools/ocs/drawing_index.py query --index IDX.json --query count|types|layers|handles|handle:<H>|text:<STR>

Exit codes: 0 ok; 1 failure; 2 usage; 3 stale index refusal (query command).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from ocs_smoke_test import (  # noqa: E402  (proven milestone-1 pieces, reused as-is)
    DEFAULT_EXE,
    Serve,
    StepFailure,
    StaleStateError,
    prepare_run_dir,
    sha256_file,
    utcnow,
)

SCHEMA = "vibe-engineer/drawing-index@1"
UNKNOWN = "unknown"  # explicit sentinel: OCS did not supply this value; never invent one

# Top-level entity reply fields the index maps into named slots. Everything
# else returned by OCS is kept verbatim in `raw` and listed by name in
# `unmapped_fields` so nothing is silently dropped or invented.
_KNOWN_IDENTITY = {"handle", "type", "layer", "space", "owner", "display_type"}
_KNOWN_TEXT = {"value", "text"}
_KNOWN_GEOMETRY = {"start", "end", "center", "radius", "position", "height"}
_KNOWN_FULL = {"bounds", "properties"}
_KNOWN_KEYS = _KNOWN_IDENTITY | _KNOWN_TEXT | _KNOWN_GEOMETRY | _KNOWN_FULL


class IndexIntegrityError(Exception):
    """The index cannot be built truthfully from OCS replies (e.g. a reply
    lacks a handle, the source changed during indexing, pagination stalls).
    The caller gets no index rather than a partially invented one."""


class IndexStaleError(Exception):
    """A query was refused because the index no longer matches reality.

    Carries `reasons` (human-readable, with measured old/new values) and
    `detail`. Answers are never served from a stale index; rebuilding is an
    explicit new indexing operation.
    """

    def __init__(self, reasons, detail=None):
        self.reasons = list(reasons)
        self.detail = detail or {}
        super().__init__(
            "STALE INDEX — refusing to answer from it. "
            + " ".join(self.reasons)
            + " Rebuild only through an explicit new indexing operation."
        )


class IndexUnverifiableError(IndexStaleError):
    """A query was refused because the index's facts CANNOT be verified.

    Raised for fail-closed indexes (built from an unsaved/dirty OCS
    document, or without a readable source file/hash) when the matching
    live session + document + revision required to validate them is
    missing, closed, different, or unreadable. The CLI exits 3 for this
    like any other refusal; programmatic callers can distinguish it from
    positive staleness evidence."""


class LiveCheckUnavailable(RuntimeError):
    """The requested live revision check could not be performed at all
    (e.g. the OCS process failed to answer `state`). This is different from
    staleness: it means 'cannot verify', not 'verified stale'."""


def _realpath(path) -> str | None:
    if path is None:
        return None
    return os.path.realpath(os.path.abspath(os.path.expanduser(str(path))))


def _same_path(a, b) -> bool:
    ra, rb = _realpath(a), _realpath(b)
    if ra is None or rb is None:
        return False
    return ra == rb


def _color_of(raw: dict):
    """Color as returned by OCS (string "ByLayer" or {"Index": N}), verbatim.

    Returns UNKNOWN when the full-detail reply did not carry the field; a
    literal null OCS returned is kept as None (it is what OCS said)."""
    common = ((raw.get("properties") or {}).get("common")) or {}
    if "color" in common:
        return common["color"]
    return UNKNOWN


def _normalize_entity(raw: dict, provenance: dict, doc_dirty: bool) -> dict:
    """One index record per entity: named slots + verbatim raw + provenance."""
    handle = raw.get("handle")
    etype = raw.get("type")
    if not handle or not etype:
        raise IndexIntegrityError(
            f"entity reply without handle/type; refusing to invent one: {json.dumps(raw)[:300]}")
    text_value = None
    for key in ("value", "text"):  # OCS returns both for Text; `value` is the slot
        if key in raw and isinstance(raw[key], str):
            text_value = raw[key]
            break
    unmapped = sorted(k for k in raw if k not in _KNOWN_KEYS)
    return {
        "handle": handle,
        "type": etype,                                   # verbatim, CAD data
        "layer": raw["layer"] if "layer" in raw else UNKNOWN,
        "space": raw["space"] if "space" in raw else UNKNOWN,
        "owner": raw["owner"] if "owner" in raw else UNKNOWN,
        "geometry": {k: raw[k] for k in sorted(_KNOWN_GEOMETRY) if k in raw},
        "text": {"value": text_value} if text_value is not None else None,
        "bounds": raw["bounds"] if "bounds" in raw else UNKNOWN,
        "color": _color_of(raw),
        "classification": None,  # never inferred; engineer-confirmed labels only
        "unmapped_fields": unmapped,
        "raw": raw,                                      # verbatim OCS reply fields
        "provenance": dict(provenance, handle=handle),
        "document_dirty_at_index": doc_dirty,
    }


def _query_all_entities(srv: Serve, document_id) -> list[dict]:
    """All entities of a document, full detail, following next_offset pages."""
    entities: list[dict] = []
    offset = None
    pages = 0
    while True:
        pages += 1
        if pages > 1000:  # never truncate silently; refuse instead
            raise IndexIntegrityError(
                f"pagination did not terminate after {pages - 1} pages "
                f"({len(entities)} entities so far); refusing to index partially")
        req = {"protocol": 1, "op": "query", "document_id": document_id, "detail": "full"}
        if offset is not None:
            req["offset"] = offset
        reply = srv.read(req)
        if not reply.get("ok"):
            raise IndexIntegrityError(f"query refused: {json.dumps(reply)[:400]}")
        batch = reply.get("entities")
        if not isinstance(batch, list):
            raise IndexIntegrityError(f"query reply has no entities list: {json.dumps(reply)[:400]}")
        entities.extend(batch)
        nxt = reply.get("next_offset")
        if nxt is None:
            return entities
        if nxt == offset:  # no progress
            raise IndexIntegrityError(f"pagination stuck at offset {offset!r}")
        offset = nxt


def _file_identity_probe(srv: Serve, document_id) -> dict:
    """Record what the pinned build says about file identity — including
    'unsupported' when it does not implement the advertised op.

    IMPORTANT (observed on the pinned v2026.46 build, see probe evidence):
    sending ANY op — including an unimplemented one — as a protocol-1
    mutation envelope can advance the document revision and mark the
    document dirty (`file_identity` with a valid envelope moved rev 0->1 and
    set dirty=true even though the op is not implemented; a bogus op name in
    an envelope answered `unknown_operation` with no side effect). The read
    layer therefore probes with a PLAIN passive send only, which keeps the
    document untouched (rev/dirty unchanged, observed) and answers
    `unknown op: file_identity`."""
    reply = srv.send({"op": "file_identity", "document_id": document_id})
    if reply.get("ok"):
        return {"status": "supported", "reply": reply}
    return {"status": "unsupported_by_pinned_build",
            "advertised": True, "reply": reply}


def _collect_document(srv: Serve, document_id, doc_entry: dict, state: dict,
                      src_path: str | None, sha_before: str | None,
                      size_bytes: int | None) -> dict:
    """Read everything for one open document and assemble the index dict."""
    session_id = state.get("session_id")
    version = state.get("version")
    mode = state.get("mode")
    revision = doc_entry.get("revision")
    dirty = bool(doc_entry.get("dirty"))
    doc_path = doc_entry.get("path")
    if src_path is not None and not _same_path(doc_path, src_path):
        raise IndexIntegrityError(
            f"document {document_id} is registered as {doc_path!r}, not {src_path!r}")

    file_identity = _file_identity_probe(srv, document_id)
    raw_entities = _query_all_entities(srv, document_id)

    provenance = {
        "source_path": src_path,
        "source_sha256": sha_before,
        "document_id": document_id,
        "document_revision": revision,
        "ocs_version": version,
        "supplied_by": 'ocs-cad-automation "query" op, detail="full"',
    }
    records = [_normalize_entity(e, provenance, dirty) for e in raw_entities]
    records.sort(key=lambda r: str(r["handle"]))

    type_counts: dict[str, int] = {}
    layer_counts: dict[str, int] = {}
    for rec in records:
        type_counts[rec["type"]] = type_counts.get(rec["type"], 0) + 1
        layer_counts[rec["layer"]] = layer_counts.get(rec["layer"], 0) + 1

    unknown = []
    if sha_before is None:
        unknown.append({"fact": "source_file_sha256", "value": UNKNOWN,
                        "why": "document is not backed by a readable file on disk"})
    if file_identity["status"] != "supported":
        unknown.append({"fact": "ocs_file_identity",
                        "value": "unsupported_by_pinned_build",
                        "why": ('capabilities advertises file_identity, but the pinned '
                                f"build replies {json.dumps(file_identity['reply'], sort_keys=True)}")})
    if dirty:
        unknown.append({"fact": "unsaved_changes_at_index_time", "value": True,
                        "why": "document had unsaved in-session changes when indexed; "
                               "entity facts describe the in-session document while the "
                               "file hash describes the file on disk"})
    if version is None or session_id is None or revision is None:
        unknown.append({"fact": "ocs_identity_fields", "value": UNKNOWN,
                        "why": "state reply did not carry version/session_id/revision"})

    return {
        "schema": SCHEMA,
        "created_utc": utcnow(),
        "source": {"path": src_path, "sha256": sha_before, "size_bytes": size_bytes},
        "ocs": {
            "version": version,
            "session_id": session_id,
            "mode": mode,
            "document_id": document_id,
            "document_revision": revision,
            "document_dirty": dirty,
            "document_path": doc_path,
            "hand_seed": state.get("hand_seed"),
        },
        "counts": {
            "entities": len(records),
            "by_type": dict(sorted(type_counts.items())),
            "by_layer": dict(sorted(layer_counts.items())),
        },
        "entities": records,
        "unknown": unknown,
        "notes": [
            "type/layer are recorded verbatim as CAD data; they are NOT structural claims",
            "classification is null on every entity until an engineer confirms a label",
            "values OCS did not return are recorded as 'unknown'; nothing is invented",
        ] + (["this index requires the matching live session for every query "
              "(built from an unsaved document or without a source file hash)"]
             if (dirty or sha_before is None) else []),
        "ocs_file_identity": file_identity,
    }


def build_index(srv: Serve, path, *, close_opened: bool = True) -> "DrawingIndex":
    """Explicit indexing operation for a file on disk, through the live `srv`.

    Opens the file in the OCS session unless it is already open there (an
    already-open document is caller-owned: it is indexed but never closed).
    The file is hashed before and after the read pass; if it changed during
    indexing, no index is returned. `close_opened=False` keeps a document we
    opened (for callers that continue working in the same session).
    """
    src = _realpath(path)
    if src is None or not os.path.isfile(src):
        raise FileNotFoundError(f"drawing source file not found: {path!r}")
    sha0 = sha256_file(src)
    size0 = os.path.getsize(src)

    state = srv.read({"protocol": 1, "op": "state"})
    if not state.get("ok"):
        raise IndexIntegrityError(f"state read failed: {json.dumps(state)[:300]}")
    doc_entry = next((d for d in state.get("documents") or []
                      if _same_path(d.get("path"), src)), None)
    owned = False
    if doc_entry is None:
        opened = srv.mutate({"op": "open", "path": src}, None, None)
        if not opened.get("ok"):
            raise IndexIntegrityError(f"open refused: {json.dumps(opened)[:300]}")
        owned = True
        state = srv.read({"protocol": 1, "op": "state"})
        doc_entry = next((d for d in state.get("documents") or []
                          if _same_path(d.get("path"), src)), None)
        if doc_entry is None:
            raise IndexIntegrityError("opened document not visible in state; refusing to index")
    collect_error = None
    try:
        data = _collect_document(srv, doc_entry["id"], doc_entry, state, src, sha0, size0)
    except Exception as exc:  # noqa: BLE001 - re-raised after our document is closed
        collect_error = exc
    finally:
        if owned and close_opened:
            closed = srv.mutate({"op": "close"}, doc_entry["id"], None)
            if not closed.get("ok"):
                raise IndexIntegrityError(f"close of our own document refused: "
                                          f"{json.dumps(closed)[:300]}")
    if collect_error is not None:
        raise collect_error  # original failure, not the cleanup outcome
    sha1 = sha256_file(src)
    if sha1 != sha0:
        raise IndexIntegrityError(
            f"source file changed while indexing (before {sha0} after {sha1}); "
            "no index produced — re-run the explicit indexing operation")
    return DrawingIndex(data)


def index_document(srv: Serve, document_id, *, source_path=None) -> "DrawingIndex":
    """Explicit indexing operation for a document already open in `srv`.

    The caller owns the document lifecycle (it is never closed here). The
    source path comes from OCS `state` unless overridden. An unsaved document
    has no file: sha256 stays None and the gap is recorded in `unknown`
    (staleness then rests on the live revision check).
    """
    state = srv.read({"protocol": 1, "op": "state"})
    if not state.get("ok"):
        raise IndexIntegrityError(f"state read failed: {json.dumps(state)[:300]}")
    doc_entry = next((d for d in state.get("documents") or []
                      if d.get("id") == document_id), None)
    if doc_entry is None:
        raise IndexIntegrityError(
            f"document {document_id} is not open in this session; nothing indexed")
    src = _realpath(source_path if source_path is not None else doc_entry.get("path"))
    sha0 = sha256_file(src) if (src and os.path.isfile(src)) else None
    size0 = os.path.getsize(src) if (src and os.path.isfile(src)) else None
    data = _collect_document(srv, document_id, doc_entry, state, src, sha0, size0)
    if src and os.path.isfile(src):
        sha1 = sha256_file(src)
        if sha1 != sha0:
            raise IndexIntegrityError(
                f"source file changed while indexing (before {sha0} after {sha1}); "
                "no index produced — re-run the explicit indexing operation")
    return DrawingIndex(data)


class DrawingIndex:
    """Immutable snapshot + deterministic queries with staleness checks."""

    def __init__(self, data: dict):
        if data.get("schema") != SCHEMA:
            raise IndexIntegrityError(f"unsupported index schema {data.get('schema')!r}")
        self._data = data

    @property
    def data(self) -> dict:
        """Raw index container — for persistence/inspection only.

        NOT a freshness-checked query: it exposes the snapshot exactly as
        stored (the persisted JSON is the same artifact `from_json` loads).
        Reading drawing facts through it bypasses the gate deliberately not
        supported; use the query methods."""
        return self._data

    # ---- persistence -----------------------------------------------------
    def to_json_dict(self) -> dict:
        return self._data

    def to_json(self, path: str) -> str:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh, ensure_ascii=False, sort_keys=True, indent=1)
            fh.write("\n")
        return path

    @classmethod
    def from_json(cls, path: str) -> "DrawingIndex":
        with open(path, "r", encoding="utf-8") as fh:
            return cls(json.load(fh))

    # ---- staleness gate --------------------------------------------------
    def _live_required_reason(self) -> str | None:
        """Why this index can only be validated by a matching live session."""
        ocs_meta = self._data["ocs"]
        if ocs_meta.get("document_dirty"):
            return (f"this index was built from an unsaved in-session document "
                    f"(document_dirty=true): its facts are anchored to live session "
                    f"{ocs_meta.get('session_id')!r} / document {ocs_meta.get('document_id')} "
                    f"at revision {ocs_meta.get('document_revision')}, which no disk "
                    f"hash can validate")
        if self._data["source"]["sha256"] is None:
            return ("this index has no readable source file/hash (source_sha256=null): "
                    f"its facts are anchored to live session "
                    f"{ocs_meta.get('session_id')!r} / document {ocs_meta.get('document_id')}, "
                    "which no disk artifact can validate")
        return None

    def check_fresh(self, srv: Serve | None = None, document_id=None) -> list[str]:
        """Refuse (raise `IndexStaleError`) if the index no longer matches.

        Always: re-hash the source file when the index has one.
        Fail-closed indexes (built from an unsaved/dirty document, or with
        `source_sha256` null) additionally REQUIRE a live session context
        (`srv` + `document_id`, both or neither) whose supplied document id
        equals the indexed one and whose session id, document identity and
        revision all match; anything else refuses with
        `IndexUnverifiableError`.
        For file-anchored indexes the live context is optional: when given,
        the same session/document/revision comparison applies; a closed
        document is not staleness (the index describes the file on disk,
        whose hash was just verified) and is recorded as unavailable.

        Returns the list of checks actually performed (for honest reporting).
        """
        performed = []
        live_required = self._live_required_reason()
        src = self._data["source"]
        if src["sha256"] is None:
            performed.append("source_file_hash:unavailable (document has no file on disk)")
        else:
            performed.append("source_file_hash")
            if not os.path.isfile(src["path"]):
                raise IndexStaleError(
                    [f"source file is missing: {src['path']!r} (indexed sha256 "
                     f"{src['sha256']})"], {"indexed_sha256": src["sha256"]})
            now = sha256_file(src["path"])
            if now != src["sha256"]:
                raise IndexStaleError(
                    [f"source file changed since indexing: path={src['path']!r} "
                     f"indexed_sha256={src['sha256']} current_sha256={now}"],
                    {"indexed_sha256": src["sha256"], "current_sha256": now})
        if (srv is None) != (document_id is None):
            raise ValueError("pass both srv and document_id (or neither)")
        if srv is None:
            if live_required:
                raise IndexUnverifiableError(
                    [f"no live session context was provided, but {live_required} — "
                     "refusing to answer; re-index in the live session or pass "
                     "srv=.../document_id=..."])
            return performed
        performed.append("live_session_identity")
        state = srv.read({"protocol": 1, "op": "state"})
        if not state.get("ok"):
            message = f"live session could not be verified: state read failed: " \
                      f"{json.dumps(state)[:300]}"
            if live_required:
                raise IndexUnverifiableError([message])
            raise LiveCheckUnavailable(message)
        ocs_meta = self._data["ocs"]
        live_sid = state.get("session_id")
        if live_sid != ocs_meta.get("session_id"):
            raise IndexStaleError(
                [f"live session {live_sid!r} != indexed session "
                 f"{ocs_meta.get('session_id')!r}: the live context is not the "
                 "session this index was built in (document ids and revision "
                 "counters are per-session, so a cross-session comparison would "
                 "be meaningless)"],
                {"indexed_session_id": ocs_meta.get("session_id"),
                 "live_session_id": live_sid})
        if document_id != ocs_meta.get("document_id"):
            raise IndexStaleError(
                [f"the supplied live document {document_id!r} is not the indexed "
                 f"document {ocs_meta.get('document_id')!r}: a live check only "
                 "validates facts of the document this index was built from "
                 "(document ids are per-session identities, and another "
                 "document in the same session can carry the same revision)"],
                {"indexed_document_id": ocs_meta.get("document_id"),
                 "live_document_id": document_id})
        entry = next((d for d in state.get("documents") or []
                      if d.get("id") == document_id), None)
        if entry is None:
            if live_required:
                raise IndexUnverifiableError(
                    [f"the indexed document {document_id} is no longer open in the "
                     f"live session, and {live_required} — there is nothing left "
                     "that can validate this index's facts; refusing"])
            performed.append("live_document_revision:unavailable (document closed; "
                             "file hash check still applied)")
            return performed
        performed.append("live_document_revision")
        if src["path"] is not None and not _same_path(entry.get("path"), src["path"]):
            raise IndexStaleError(
                [f"live document {document_id} points at {entry.get('path')!r}, "
                 f"not the indexed file {src['path']!r}"])
        live_rev = entry.get("revision")
        idx_rev = ocs_meta["document_revision"]
        if live_rev != idx_rev:
            raise IndexStaleError(
                [f"live document revision {live_rev} != indexed revision "
                 f"{idx_rev}: the session changed the drawing after this "
                 f"index was built"],
                {"indexed_revision": idx_rev, "live_revision": live_rev})
        return performed

    # ---- deterministic queries -------------------------------------------
    def count(self, srv=None, document_id=None) -> int:
        self.check_fresh(srv, document_id)
        return len(self._data["entities"])

    def count_by_type(self, srv=None, document_id=None) -> dict:
        self.check_fresh(srv, document_id)
        return dict(self._data["counts"]["by_type"])

    def count_by_layer(self, srv=None, document_id=None) -> dict:
        self.check_fresh(srv, document_id)
        return dict(self._data["counts"]["by_layer"])

    def entity(self, handle, srv=None, document_id=None) -> dict:
        self.check_fresh(srv, document_id)
        for rec in self._data["entities"]:
            if str(rec["handle"]) == str(handle):
                return json.loads(json.dumps(rec))  # deep copy: caller can't mutate the index
        raise KeyError(
            f"handle {handle!r} is not in this index (built {self._data['created_utc']} "
            f"from {self._data['source']['path']!r} sha256 {self._data['source']['sha256']}); "
            "it may exist in the drawing but was not indexed — rebuild explicitly")

    def search_text(self, needle: str, *, case_sensitive: bool = False,
                    srv=None, document_id=None) -> list[dict]:
        """Substring search over recorded text values only (never invented)."""
        if not needle:
            raise ValueError("empty search needle")
        self.check_fresh(srv, document_id)
        hits = []
        for rec in self._data["entities"]:
            value = (rec.get("text") or {}).get("value")
            if not isinstance(value, str):
                continue  # no text recorded -> never matches, never guessed
            hay, pin = (value, needle) if case_sensitive else (value.lower(), needle.lower())
            if pin in hay:
                hits.append(json.loads(json.dumps(rec)))
        hits.sort(key=lambda r: str(r["handle"]))
        return hits

    def entities(self, srv=None, document_id=None) -> list[dict]:
        """All indexed entity records (deep copies), freshness-gated."""
        self.check_fresh(srv, document_id)
        return json.loads(json.dumps(self._data["entities"]))

    def summary(self, srv=None, document_id=None) -> dict:
        """Index summary (counts and identity), freshness-gated."""
        self.check_fresh(srv, document_id)
        return json.loads(json.dumps({
            "schema": self._data["schema"],
            "created_utc": self._data["created_utc"],
            "source": self._data["source"],
            "ocs": self._data["ocs"],
            "counts": self._data["counts"],
            "unknown": self._data["unknown"],
        }))


# ---- CLI -------------------------------------------------------------------
def _fmt_counts(counts: dict) -> str:
    return ", ".join(f"{k} {v}" for k, v in counts.items()) or "(none)"


def cmd_index(args, parser) -> int:
    from ocs_smoke_test import OCS_VERSION, OCS_REVISION  # pinned expectations for the warning
    exe = os.path.abspath(os.path.expanduser(args.exe))
    if not os.path.isfile(exe):
        parser.error(f"OpenCADStudio executable not found: {exe} (run tools/ocs/setup.sh)")
    repo_root = os.path.dirname(os.path.dirname(HERE))
    base = args.workdir or os.path.join(repo_root, "tools", "ocs", "out")
    try:
        run_dir, _ = prepare_run_dir(base)
    except ValueError as e:
        parser.error(str(e))
    stderr_log = os.path.join(run_dir, "ocs_stderr.log")
    srv = Serve(exe, stderr_path=stderr_log)
    srv.transcript_path = os.path.join(run_dir, "requests.jsonl")
    try:
        idx = build_index(srv, args.file)
    finally:
        srv.close()
    s = idx.summary()
    print(f"drawing index built ({s['schema']})")
    print(f"  source:       {s['source']['path']}")
    print(f"  sha256:       {s['source']['sha256']} ({s['source']['size_bytes']} bytes)")
    o = s["ocs"]
    print(f"  ocs:          {o['version']} session {o['session_id']} mode {o['mode']}")
    print(f"  document:     id {o['document_id']} revision {o['document_revision']} "
          f"dirty={o['document_dirty']} path {o['document_path']}")
    c = s["counts"]
    print(f"  entities:     {c['entities']} ({_fmt_counts(c['by_type'])})")
    print(f"  layers:       {_fmt_counts(c['by_layer'])}")
    print(f"  unknowns:     {len(s['unknown'])} recorded")
    for u in s["unknown"]:
        print(f"    - {u['fact']}: {u['value']}")
    if o["version"] != OCS_VERSION:
        print(f"  NOTE: OCS version {o['version']!r} != pinned {OCS_VERSION!r} "
              f"(rev {OCS_REVISION}) — recorded in the index")
    if args.json:
        idx.to_json(args.json)
        print(f"  index json:   {args.json}")
    print(f"  run evidence: {run_dir} (requests.jsonl, ocs_stderr.log)")
    return 0


def cmd_query(args, parser) -> int:
    try:
        idx = DrawingIndex.from_json(args.index)
    except (OSError, json.JSONDecodeError) as e:
        parser.error(f"cannot load index {args.index!r}: {e}")
    q = args.query
    try:
        if q == "count":
            print(idx.count())
        elif q == "types":
            for k, v in idx.count_by_type().items():
                print(f"{k} {v}")
        elif q == "layers":
            for k, v in idx.count_by_layer().items():
                print(f"{k} {v}")
        elif q == "handles":
            for rec in idx.entities():
                print(rec["handle"])
        elif q.startswith("handle:"):
            print(json.dumps(idx.entity(q.split(":", 1)[1]),
                             ensure_ascii=False, sort_keys=True, indent=1))
        elif q.startswith("text:"):
            needle = q.split(":", 1)[1]
            hits = idx.search_text(needle)
            print(f"{len(hits)} match(es) for text {needle!r}:")
            for rec in hits:
                print(f"  handle {rec['handle']} type {rec['type']} layer {rec['layer']} "
                      f"value {json.dumps(rec['text']['value'])}")
        else:
            parser.error("--query must be count | types | layers | handles | "
                         "handle:<H> | text:<STR>")
    except IndexUnverifiableError as e:
        print(f"UNVERIFIABLE INDEX — refusing to answer from {args.index}", file=sys.stderr)
        for r in e.reasons:
            print(f"  reason: {r}", file=sys.stderr)
        print("  rebuild only through an explicit new indexing operation:", file=sys.stderr)
        print("    python3 tools/ocs/drawing_index.py index --file <drawing> --json <index>",
              file=sys.stderr)
        return 3
    except IndexStaleError as e:
        print(f"STALE INDEX — refusing to answer from {args.index}", file=sys.stderr)
        for r in e.reasons:
            print(f"  reason: {r}", file=sys.stderr)
        print("  rebuild only through an explicit new indexing operation:", file=sys.stderr)
        print("    python3 tools/ocs/drawing_index.py index --file <drawing> --json <index>",
              file=sys.stderr)
        return 3
    except KeyError as e:
        print(str(e))
        return 0  # "not in this index" is a legitimate deterministic answer
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Drawing read/index layer over OCS --serve (milestone 2)",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_index = sub.add_parser("index", help="build a drawing index (explicit indexing op)")
    p_index.add_argument("--file", required=True, help="existing drawing file to index")
    p_index.add_argument("--exe", default=os.environ.get("OCS_APPIMAGE", DEFAULT_EXE),
                         help="OpenCADStudio AppImage path")
    p_index.add_argument("--json", default=None, help="write the index JSON here")
    p_index.add_argument("--workdir", default=None,
                         help="base dir for run evidence (fresh subdir; nothing deleted)")
    p_index.set_defaults(fn=cmd_index)

    p_query = sub.add_parser("query",
                             help="answer deterministic queries from a saved index "
                                  "(file-hash freshness checked; never starts OCS)")
    p_query.add_argument("--index", required=True, help="index JSON produced by `index`")
    p_query.add_argument("--query", required=True,
                         help="count | types | layers | handles | handle:<H> | text:<STR>")
    p_query.set_defaults(fn=cmd_query)

    args = ap.parse_args(argv)
    try:
        return args.fn(args, ap)
    except (IndexIntegrityError, IndexStaleError, LiveCheckUnavailable,
            StaleStateError, StepFailure, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
