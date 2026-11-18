#!/usr/bin/env python3
"""Verification suite for the milestone-3A read-only QA layer.

Checks the `drawing_qa` module in three tiers:

- UNIT (U1–U35): no OCS process, no network, no API key. The index is built
  from canned `--serve` replies (the canned entities mirror the milestone-1
  synthetic fixture: 4 lines on TEST-BOUNDARY, a circle r=5.0 on
  TEST-SYMBOLS, one text — handles 86–8B) and the model is a fake client
  with a canned JSON reply. These checks are MOCKED model calls by
  definition; they prove the pipeline, not the provider. U27/U28 pin the
  Codex PR #4 review round-1 repros (deterministic display from index
  values, structural prose never displayed); U29–U31 pin the cumulative
  spend-cap semantics; U32/U33 pin the round-2 repros (displayed-fields
  reporting and refusal prose replaced by a local neutral message); U34/U35
  pin the round-3 repros (neutral parse failure without echoing response
  content; dotted layer names resolve in cited AND displayed paths).
- REAL BINARY (R1–R4): the pinned OpenCADStudio build, headless. The
  fixture is produced by the milestone-1 tooling, indexed through the real
  `--serve` channel, and the QA flow runs against the REAL index with a
  fake model (still no network). Skipped honestly when the binary is
  missing; SKIP is never proof.
- REAL MODEL (M1): ONE real OpenRouter call — only when the caller passes
  `--real-openrouter` AND `OPENROUTER_API_KEY` is set; otherwise SKIP.
  The call is Arabic, bounded by max_tokens, and inside the spending cap;
  the reported cost is printed as evidence. A skipped M1 proves nothing
  about the provider and is reported as NOT VERIFIED.

Exit code 0 iff no check FAILED. Run: `python tools/ocs/test_drawing_qa.py`
(add `--real-openrouter` to spend one real model call).
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ocs_smoke_test as ocs  # noqa: E402
import drawing_index as di    # noqa: E402
import drawing_qa as qa       # noqa: E402

results: list[tuple[str, str, str]] = []


class SkipCheck(Exception):
    """Raised by a check that cannot run (missing binary, no key, ...)."""


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


# ---- canned full-detail entity replies (milestone-1 synthetic fixture) ------
def _line(handle, start, end):
    return {"handle": handle, "type": "Line", "display_type": "Line",
            "layer": ocs.LAYER_BOUNDARY, "owner": "15",
            "space": "*Model_Space", "constraints": [],
            "start": start, "end": end,
            "bounds": {"min": list(start), "max": list(end)},
            "properties": {"common": {"color": "ByLayer",
                                      "layer": ocs.LAYER_BOUNDARY}}}


RAW_LINES = [
    _line("86", [0.0, 0.0, 0.0], [100.0, 0.0, 0.0]),
    _line("87", [100.0, 0.0, 0.0], [100.0, 60.0, 0.0]),
    _line("88", [100.0, 60.0, 0.0], [0.0, 60.0, 0.0]),
    _line("89", [0.0, 60.0, 0.0], [0.0, 0.0, 0.0]),
]
RAW_CIRCLE = {"handle": "8A", "type": "Circle", "display_type": "Circle",
              "layer": ocs.LAYER_SYMBOLS, "owner": "15",
              "space": "*Model_Space", "constraints": [],
              "center": [50.0, 30.0, 0.0], "radius": 5.0,
              "bounds": {"min": [45.0, 25.0, 0.0], "max": [55.0, 35.0, 0.0]},
              "properties": {"common": {"color": "ByLayer",
                                        "layer": ocs.LAYER_SYMBOLS}}}
RAW_TEXT = {"handle": "8B", "type": "Text", "display_type": "Text",
            "layer": ocs.LAYER_SYMBOLS, "owner": "15",
            "space": "*Model_Space", "constraints": [],
            "value": ocs.FIXTURE_TEXT, "text": ocs.FIXTURE_TEXT,
            "position": [50.0, 66.0, 0.0], "height": 2.5,
            "bounds": {"min": [50.0, 66.0, 0.0], "max": [66.5, 68.5, 0.0]},
            "properties": {"common": {"color": "ByLayer",
                                      "layer": ocs.LAYER_SYMBOLS}}}
CANNED = RAW_LINES + [RAW_CIRCLE, RAW_TEXT]


class FakeServe:
    """Canned --serve session (no OCS process); mutate() fails loudly."""

    def __init__(self, state, query_reply):
        self._state = state
        self._query = query_reply
        self.reads: list[dict] = []

    def read(self, req, timeout=120.0):
        self.reads.append(req)
        op = req.get("op")
        if op == "state":
            return self._state
        if op == "query":
            return self._query
        raise AssertionError(f"FakeServe.read unexpected op {op!r}")

    def send(self, req, timeout=120.0):
        self.reads.append(req)
        if req.get("op") == "file_identity":
            return {"ok": False, "error": "unknown op: file_identity"}
        raise AssertionError(f"FakeServe.send unexpected op {req.get('op')!r}")

    def mutate(self, req, document_id=None, revision=None, timeout=120.0):
        raise AssertionError(f"FakeServe.mutate unexpectedly called for {req.get('op')!r}")


def fake_state(path, revision=0, doc_id=1, dirty=False):
    return {"ok": True, "mode": "headless", "version": "2026.46",
            "session_id": "fake-session", "hand_seed": "99",
            "revision": revision, "document_id": doc_id,
            "documents": [{"dirty": dirty, "id": doc_id, "path": path,
                           "revision": revision, "title": "T", "start": False}]}


def make_tmp_drawing(tmpdir, content=b"0 SECTION\n  2 HEADER\n  0 ENDBLK\n") -> str:
    path = os.path.join(tmpdir, "fake_drawing.dxf")
    with open(path, "wb") as fh:
        fh.write(content)
    return path


def build_test_index(tmpdir):
    """A file-anchored index of the canned 6-entity fixture (offline)."""
    src = make_tmp_drawing(tmpdir)
    srv = FakeServe(fake_state(src), {"ok": True, "count": 6,
                                      "entities": CANNED, "next_offset": None})
    idx = di.build_index(srv, src)
    return idx, src


class FakeClient:
    """Canned model for offline tests; records every complete() call."""

    def __init__(self, reply=None, raw=None, error=None, on_call=None):
        self.calls: list[dict] = []
        self._reply = reply
        self._raw = raw
        self._error = error
        self._on_call = on_call

    def complete(self, system, user_content, meta=None):
        self.calls.append({"system": system, "user": user_content,
                           "meta": meta})
        if self._on_call:
            self._on_call()
        if self._error:
            raise qa.ModelFailure(self._error)
        if self._raw is not None:
            return self._raw, {"status": 200}
        return json.dumps(self._reply), {"status": 200}


def user_payload(client: FakeClient) -> dict:
    """The excerpt JSON exactly as it was sent to the (fake) model."""
    assert client.calls, "the model was never called"
    marker = "INDEX EXCERPT (JSON):\n"
    assert marker in client.calls[0]["user"], client.calls[0]["user"][:200]
    return json.loads(client.calls[0]["user"].split(marker, 1)[1])


AR_COUNT_Q = "كم عدد العناصر في الرسم؟"
EN_COUNT_Q = "How many entities are in this drawing?"


def reply_count_ok():
    return {"answerable": True,
            "answer": "The drawing contains 6 entities: 4 lines, 1 circle "
                      "and 1 text.",
            "citations": [{"fact": "entity_counts.total"},
                          {"fact": "entity_counts.by_type.Line"}]}


# ---- unit tier ---------------------------------------------------------------
def u1_english_count_answered() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        idx, _src = build_test_index(tmp)
        client = FakeClient(reply=reply_count_ok())
        ans = qa.answer_question(idx, EN_COUNT_Q, client)
        assert ans.status == "answered", (ans.status, ans.detail)
        assert "6" in ans.answer_text
        assert "Circle 1" in ans.answer_text, ans.answer_text  # composed from facts
        assert len(client.calls) == 1, "exactly one model call is allowed"
        assert ans.evidence["citations_validated"] is True
        assert ans.evidence["cited_handles"] == []  # count facts, no handles
        assert ans.evidence["cited_fact_paths"] == \
            ["entity_counts.total", "entity_counts.by_type.Line"]
        # deterministic render covers the whole counts facts; every rendered
        # field is reported (displayed ⊄ cited is expected and listed)
        assert ans.evidence["answer_source"] == "deterministic_from_index_facts"
        displayed = ans.evidence["displayed_fact_paths"]
        assert "entity_counts.total" in displayed
        assert "entity_counts.by_type.Circle" in displayed
        assert ans.evidence["displayed_handles"] == []
        ev = ans.evidence
        assert ev["source_path"].endswith("fake_drawing.dxf")
        assert len(ev["source_sha256"]) == 64
        assert ev["document_revision"] == 0
        payload = user_payload(client)
        # never send the whole drawing: the count excerpt has NO entity records
        assert "entity_records" not in payload["facts"], payload["facts"]
        # customer identity never leaves the machine
        assert "fake_drawing.dxf" not in client.calls[0]["user"]
        assert ev["source_sha256"] not in client.calls[0]["user"]
        return "EN count answered; 1 call; count excerpt has no records; path/sha withheld"


def u2_arabic_count_arabic_indic_digits() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        idx, _src = build_test_index(tmp)
        client = FakeClient(reply={
            "answerable": True,
            "answer": "يوجد في الرسم ٦ عناصر: ٤ خطوط ودائرة ونص.",
            "citations": [{"fact": "entity_counts.total"}]})
        ans = qa.answer_question(idx, AR_COUNT_Q, client)
        assert ans.status == "answered", (ans.status, ans.detail)
        assert ans.question_lang == "ar"
        runs = qa._digit_runs(ans.answer_text)
        assert "6" in runs and "4" in runs, runs  # ٦/٤ normalized, not invented
        return "AR count answered; Arabic-Indic digits normalized"


def u3_arabic_layers() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        idx, _src = build_test_index(tmp)
        client = FakeClient(reply={
            "answerable": True,
            "answer": "الرسم يحتوي على طبقتين: TEST-BOUNDARY (4 عناصر) "
                      "وTEST-SYMBOLS (2 عنصر).",
            "citations": [{"fact": "layers.TEST-BOUNDARY"},
                          {"fact": "layers.TEST-SYMBOLS"}]})
        ans = qa.answer_question(idx, "ما الطبقات الموجودة في الرسم؟", client)
        assert ans.status == "answered", (ans.status, ans.detail)
        payload = user_payload(client)
        assert payload["facts"]["layers"] == {ocs.LAYER_BOUNDARY: 4,
                                              ocs.LAYER_SYMBOLS: 2}
        return "AR layers answered; both layer counts cited from facts"


def u4_handle_lookup() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        idx, _src = build_test_index(tmp)
        client = FakeClient(reply={
            "answerable": True,
            "answer": "Entity 8A is a Circle on layer TEST-SYMBOLS with "
                      "radius 5.0.",
            "citations": [{"handle": "8A"}]})
        ans = qa.answer_question(idx, "Show me the details of handle 8A",
                                 client)
        assert ans.status == "answered", (ans.status, ans.detail)
        assert ans.requested_handle == "8A"
        payload = user_payload(client)
        recs = payload["facts"]["entity_records"]
        assert len(recs) == 1 and recs[0]["handle"] == "8A" \
            and recs[0]["geometry"]["radius"] == 5.0, recs
        assert recs[0]["classification"] is None
        assert "raw" not in recs[0]  # verbatim raw reply stays local
        # customer identity stays local even for record-bearing intents
        assert idx.data["source"]["sha256"] not in client.calls[0]["user"]
        assert "fake_drawing.dxf" not in client.calls[0]["user"]
        # displayed answer composed from the record's own values
        assert "8A" in ans.answer_text and "Circle" in ans.answer_text
        assert "5.0" in ans.answer_text, ans.answer_text
        assert ans.evidence["displayed_handles"] == ["8A"]
        assert ans.evidence["answer_source"] == "deterministic_from_index_facts"
        return "handle 8A excerpt: one slim record (radius 5.0, no raw, no path/sha)"


def u5_text_search() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        idx, _src = build_test_index(tmp)
        client = FakeClient(reply={
            "answerable": True,
            "answer": 'The drawing contains the text "SMOKE-TEST SYNTHETIC '
                      'FIXTURE".',
            "citations": [{"handle": "8B"}]})
        ans = qa.answer_question(idx, "find text SMOKE", client)
        assert ans.status == "answered", (ans.status, ans.detail)
        payload = user_payload(client)
        assert payload["facts"]["match_count"] == 1
        assert payload["facts"]["entity_records"][0]["handle"] == "8B"
        assert ans.evidence["cited_handles"] == ["8B"]
        assert "8B" in ans.answer_text \
            and "SMOKE-TEST SYNTHETIC FIXTURE" in ans.answer_text, ans.answer_text
        assert ans.evidence["displayed_handles"] == ["8B"]
        assert "query" in ans.evidence["displayed_fact_paths"] \
            and "match_count" in ans.evidence["displayed_fact_paths"]
        return "text search: 1 match, cited handle validated"


def u6_summary_excerpt_is_capped() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        idx, _src = build_test_index(tmp)
        client = FakeClient(reply={
            "answerable": True,
            "answer": "This drawing contains 6 entities on layers "
                      "TEST-BOUNDARY and TEST-SYMBOLS.",
            "citations": [{"fact": "entity_counts.total"}, {"handle": "86"}]})
        ans = qa.answer_question(idx, "What is in this drawing?", client,
                                 excerpt_max_records=2)
        assert ans.status == "answered", (ans.status, ans.detail)
        payload = user_payload(client)
        assert len(payload["facts"]["entity_records"]) == 2
        assert payload["facts"]["truncated"] is True
        assert payload["facts"]["entity_counts"]["total"] == 6
        # summary display = exactly the cited values, never model prose
        assert "entity_counts.total = 6" in ans.answer_text, ans.answer_text
        assert "86" in ans.answer_text
        assert ans.evidence["answer_source"] == "composed_from_cited_values"
        assert ans.evidence["cited_fact_paths"] == \
            ans.evidence["displayed_fact_paths"] == ["entity_counts.total"]
        assert ans.evidence["cited_handles"] == \
            ans.evidence["displayed_handles"] == ["86"]
        return "summary excerpt capped at 2 of 6 records; truncated=true"


def u7_missing_facts_model_declares_unsupported() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        idx, _src = build_test_index(tmp)
        client = FakeClient(reply={
            "answerable": False,
            "reason_code": "missing_facts",
            "reason": "The index does not record design dates."})
        ans = qa.answer_question(idx, "When was this drawing designed?",
                                 client)
        assert ans.status == "cannot_answer", (ans.status, ans.detail)
        # the DISPLAYED refusal text is the LOCAL neutral template; the
        # model's own prose never reaches the user
        assert "not present in the indexed excerpt" in ans.detail, ans.detail
        assert "design dates" not in ans.detail
        assert ans.evidence["model_reason_code"] == "missing_facts"
        assert len(client.calls) == 1  # one bounded call, then honest refusal
        payload = user_payload(client)
        assert "unresolved_gaps" in payload["facts"]
        return "missing fact -> answerable=false + reason_code -> LOCAL neutral CANNOT ANSWER (exit 4)"


def u8_structural_question_refused_without_call_en() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        idx, _src = build_test_index(tmp)
        client = FakeClient(reply=reply_count_ok())
        ans = qa.answer_question(idx, "Is this beam safe for the load?",
                                 client)
        assert ans.status == "refused_guard", (ans.status, ans.detail)
        assert client.calls == [], "no model call may be made"
        assert "structural" in ans.detail
        return "EN structural question refused, zero model calls"


def u9_structural_question_refused_without_call_ar() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        idx, _src = build_test_index(tmp)
        client = FakeClient(reply=reply_count_ok())
        ans = qa.answer_question(idx, "هل الأساس آمن؟", client)
        assert ans.status == "refused_guard", (ans.status, ans.detail)
        assert client.calls == []
        assert "structural" in ans.detail
        return "AR structural question refused, zero model calls"


def u10_edit_request_refused_without_call() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        idx, _src = build_test_index(tmp)
        client = FakeClient(reply=reply_count_ok())
        ans = qa.answer_question(idx, "Change the color of handle 8A", client)
        assert ans.status == "refused_guard", (ans.status, ans.detail)
        assert client.calls == []
        assert "read-only" in ans.detail
        return "edit request refused (read-only slice), zero model calls"


def u11_stale_index_refused_before_any_call() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        idx, src = build_test_index(tmp)
        old_sha = idx.data["source"]["sha256"]
        with open(src, "ab") as fh:
            fh.write(b"edited after indexing")
        new_sha = di.sha256_file(src)
        client = FakeClient(reply=reply_count_ok())
        ans = qa.answer_question(idx, EN_COUNT_Q, client)
        assert ans.status == "refused_stale", (ans.status, ans.detail)
        assert client.calls == [], "no model call may happen on a stale index"
        joined = " ".join(ans.reasons)
        assert old_sha in joined and new_sha in joined, joined
        return f"stale index refused before retrieval ({old_sha[:8]}.. -> {new_sha[:8]}..)"


def u12_unverifiable_index_refused() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        src = make_tmp_drawing(tmp)
        state = fake_state(src, revision=1, dirty=True)  # unsaved in-session doc
        idx = di.index_document(FakeServe(state, {"ok": True, "count": 6,
                                                  "entities": CANNED,
                                                  "next_offset": None}), 1)
        assert idx.data["ocs"]["document_dirty"] is True
        client = FakeClient(reply=reply_count_ok())
        ans = qa.answer_question(idx, EN_COUNT_Q, client)
        assert ans.status == "refused_stale", (ans.status, ans.detail)
        assert client.calls == []
        joined = " ".join(ans.reasons)
        assert "unsaved in-session document" in joined, joined
        return "dirty index refused (IndexUnverifiable): explicit re-index required"


def u13_change_during_model_call_discards_answer() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        idx, src = build_test_index(tmp)

        def tamper_during_call():
            with open(src, "ab") as fh:  # someone edits the drawing while the model answers
                fh.write(b"edited mid-answer")

        client = FakeClient(reply=reply_count_ok(), on_call=tamper_during_call)
        ans = qa.answer_question(idx, EN_COUNT_Q, client)
        assert ans.status == "refused_stale", (ans.status, ans.detail)
        assert ans.model_called is True and len(client.calls) == 1
        assert "discarded" in ans.detail, ans.detail
        return "gate #2 ran after the model call; stale answer discarded"


def u14_two_freshness_gates_in_order() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        idx, _src = build_test_index(tmp)
        events: list[str] = []
        original = di.DrawingIndex.check_fresh

        def spy(self, srv=None, document_id=None):
            events.append("gate")
            return original(self, srv, document_id)

        client = FakeClient(reply=reply_count_ok(),
                            on_call=lambda: events.append("call"))
        di.DrawingIndex.check_fresh = spy
        try:
            ans = qa.answer_question(idx, EN_COUNT_Q, client)
        finally:
            di.DrawingIndex.check_fresh = original
        assert ans.status == "answered", (ans.status, ans.detail)
        assert events.count("gate") >= 2, events
        assert events.count("call") == 1, events
        after_call = events[events.index("call") + 1:]
        assert after_call == ["gate"], events  # the LAST gate comes after the call
        return f"gate ordering proven: {events.count('gate')} gates, call in between, gate last"


def u15_invalid_handle_citation_discarded() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        idx, _src = build_test_index(tmp)
        client = FakeClient(reply={
            "answerable": True, "answer": "There are 6 entities.",
            "citations": [{"handle": "ZZ"}]})
        ans = qa.answer_question(idx, EN_COUNT_Q, client)
        assert ans.status == "model_failure", (ans.status, ans.detail)
        assert "INVALID MODEL CITATIONS" in ans.detail, ans.detail
        assert "ZZ" in ans.detail and "not present in the retrieved excerpt" in ans.detail
        return "cited handle ZZ not in excerpt -> answer discarded (exit 5)"


def u16_invalid_fact_citation_discarded() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        idx, _src = build_test_index(tmp)
        client = FakeClient(reply={
            "answerable": True, "answer": "There are 6 entities.",
            "citations": [{"fact": "entity_counts.by_type.Arc"}]})
        ans = qa.answer_question(idx, EN_COUNT_Q, client)
        assert ans.status == "model_failure", (ans.status, ans.detail)
        assert "does not resolve" in ans.detail, ans.detail
        return "cited fact path absent from the excerpt -> answer discarded"


def u17_invented_number_discarded() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        idx, _src = build_test_index(tmp)
        client = FakeClient(reply={
            "answerable": True, "answer": "There are 7 entities.",
            "citations": [{"fact": "entity_counts.total"}]})
        ans = qa.answer_question(idx, EN_COUNT_Q, client)
        assert ans.status == "model_failure", (ans.status, ans.detail)
        assert "7" in ans.detail and "does not exist" in ans.detail, ans.detail
        return "answer stating 7 (true count 6) discarded: number not in facts"


def u18_unparseable_output_discarded() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        idx, _src = build_test_index(tmp)
        client = FakeClient(raw="Sure! The drawing has 6 entities, happy to help.")
        ans = qa.answer_question(idx, EN_COUNT_Q, client)
        assert ans.status == "model_failure", (ans.status, ans.detail)
        assert "JSON" in ans.detail, ans.detail
        return "prose-only model output discarded (strict JSON contract)"


def u19_api_failure_no_answer() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        idx, _src = build_test_index(tmp)
        client = FakeClient(error="OpenRouter HTTP 503: upstream overloaded")
        ans = qa.answer_question(idx, EN_COUNT_Q, client)
        assert ans.status == "model_failure", (ans.status, ans.detail)
        assert "HTTP 503" in ans.detail, ans.detail
        return "API failure reported clearly; nothing answered, nothing displayed"


def u20_missing_key_fails_closed() -> str:
    client = qa.OpenRouterClient(model="x", api_key=None)
    try:
        client.complete("s", "u")
    except qa.ModelFailure as e:
        assert qa.ENV_KEY in str(e), e
        return "missing OPENROUTER_API_KEY -> clear failure before any request"
    raise AssertionError("missing key did not fail")


def u21_spend_cap_stops_at_the_cap() -> str:
    seen_headers: list[dict] = []
    calls = []

    def transport(url, body, headers, timeout):
        calls.append(url)
        seen_headers.append(headers)
        return 200, json.dumps({
            "choices": [{"message": {"content": "{}"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2,
                      "total_tokens": 7, "cost": 1.0}}).encode()

    key = "sk-or-v1-TESTKEY"
    client = qa.OpenRouterClient(model="m", api_key=key, spend_cap_usd=1.0,
                                 transport=transport)
    client.complete("s", "u")
    assert abs(client.tracked_spend_usd - 1.0) < 1e-9
    try:
        client.complete("s", "u")
    except qa.ModelFailure as e:
        assert "SPEND CAP REACHED" in str(e), e
    else:
        raise AssertionError("spend cap did not stop the second call")
    assert len(calls) == 1, "the capped call must never be sent"
    assert seen_headers[0]["Authorization"] == f"Bearer {key}"
    assert "Bearer" not in json.dumps(client.records)  # key never in records
    return "usage.cost 1.0 tracked; the next call refused by the $1.00 cap"


def u22_usage_log_has_no_sensitive_content() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        log_path = os.path.join(tmp, "usage.jsonl")
        key = "sk-or-v1-TESTKEY"

        def transport(url, body, headers, timeout):
            return 200, json.dumps({
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 3,
                          "total_tokens": 14, "cost": 0.0001}}).encode()

        client = qa.OpenRouterClient(model="m", api_key=key,
                                     usage_log_path=log_path,
                                     transport=transport)
        client.complete("s", "QUESTION: how many entities? secret plans",
                        meta={"question_sha256": "abc123", "intent": "count",
                              "question_lang": "en"})
        with open(log_path, "r", encoding="utf-8") as fh:
            text = fh.read()
        rec = json.loads(text.splitlines()[0])
        for field in ("ts_utc", "model", "status", "prompt_tokens",
                      "completion_tokens", "total_tokens", "cost_usd",
                      "tracked_spend_usd_after", "latency_ms",
                      "question_sha256", "intent", "question_lang"):
            assert field in rec, (field, rec)
        assert key not in text, "the key must never be logged"
        assert "secret plans" not in text, "prompt content must never be logged"
        assert "how many entities" not in text, "prompt content must never be logged"
        return "usage JSONL: tokens/cost/latency only; no key, no prompt text"


def u23_key_redaction_on_errors() -> str:
    key = "sk-or-v1-SECRETKVALUE"

    def boom(url, body, headers, timeout):
        raise RuntimeError(f"connection refused near token {key}")

    client = qa.OpenRouterClient(model="m", api_key=key, transport=boom)
    try:
        client.complete("s", "u")
    except qa.ModelFailure as e:
        assert "[REDACTED]" in str(e) and key not in str(e), e
    else:
        raise AssertionError("transport error was not converted")

    def unauthorized(url, body, headers, timeout):
        return 401, json.dumps({"error": {"message":
            f"invalid key {key}"}}).encode()

    client2 = qa.OpenRouterClient(model="m", api_key=key,
                                  transport=unauthorized)
    try:
        client2.complete("s", "u")
    except qa.ModelFailure as e:
        assert "[REDACTED]" in str(e) and key not in str(e), e
    else:
        raise AssertionError("HTTP 401 was not converted")
    return "key scrubbed from transport and HTTP error messages"


def u24_prompt_size_guard() -> str:
    original = qa.MAX_PROMPT_CHARS
    qa.MAX_PROMPT_CHARS = 10

    def must_not_be_called(url, body, headers, timeout):
        raise AssertionError("request must not be sent")

    client = qa.OpenRouterClient(model="m", api_key="k",
                                 transport=must_not_be_called)
    try:
        client.complete("s", "a prompt that is clearly longer than ten chars")
    except qa.ModelFailure as e:
        assert "refusing to send" in str(e), e
    else:
        raise AssertionError("oversized prompt was not refused")
    finally:
        qa.MAX_PROMPT_CHARS = original
    return "oversized prompt refused before any request"


def u25_cli_no_key_exit5() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        idx, _src = build_test_index(tmp)
        idx_json = os.path.join(tmp, "idx.json")
        idx.to_json(idx_json)
        env = dict(os.environ)
        env[qa.ENV_KEY] = ""  # present but empty: fail closed, no .env seeding
        proc = subprocess.run(
            [sys.executable, os.path.join(HERE, "drawing_qa.py"), "ask",
             "--index", idx_json, "--question", EN_COUNT_Q],
            capture_output=True, text=True, timeout=120, env=env)
        assert proc.returncode == qa.EXIT_MODEL, (proc.returncode, proc.stdout, proc.stderr)
        assert qa.ENV_KEY in proc.stderr, proc.stderr
        assert "MODEL / PROTOCOL FAILURE" in proc.stderr, proc.stderr
        return "CLI without a key: exit 5, clear message, no request"


def u26_cli_stale_exit3() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        idx, src = build_test_index(tmp)
        idx_json = os.path.join(tmp, "idx.json")
        idx.to_json(idx_json)
        old_sha = idx.data["source"]["sha256"]
        with open(src, "ab") as fh:
            fh.write(b"edited after indexing")
        env = dict(os.environ)
        env[qa.ENV_KEY] = ""
        proc = subprocess.run(
            [sys.executable, os.path.join(HERE, "drawing_qa.py"), "ask",
             "--index", idx_json, "--question", EN_COUNT_Q],
            capture_output=True, text=True, timeout=120, env=env)
        assert proc.returncode == qa.EXIT_STALE, (proc.returncode, proc.stdout, proc.stderr)
        assert "STALE" in proc.stderr and old_sha[:8] in proc.stderr, proc.stderr
        assert "drawing_index.py index" in proc.stderr, proc.stderr
        return "CLI on a stale index: exit 3 + rebuild hint, before any model call"


def u27_count_prose_mismatch_repro() -> str:
    """Codex PR #4 review repro 1: a VALID citation (entity_counts.by_type.
    Circle = 1) beside WRONG prose ("There are 4 circles." — 4 is the Line
    count). The displayed answer must come from the cited index values, not
    from the model's wording."""
    with tempfile.TemporaryDirectory() as tmp:
        idx, _src = build_test_index(tmp)
        client = FakeClient(reply={
            "answerable": True,
            "answer": "There are 4 circles.",
            "citations": [{"fact": "entity_counts.by_type.Circle"}]})
        ans = qa.answer_question(idx, "How many circles are in this drawing?",
                                 client)
        assert ans.status == "answered", (ans.status, ans.detail)
        assert ans.evidence["citations_validated"] is True
        text = ans.answer_text
        assert "Circle 1" in text, text          # the true cited value
        assert "4 circles" not in text, text     # the model's wrong prose
        assert "There are" not in text, text     # no model wording displayed
        # deterministic count render: every rendered field is reported
        assert ans.evidence["answer_source"] == "deterministic_from_index_facts"
        assert "entity_counts.by_type.Circle" in \
            ans.evidence["displayed_fact_paths"]
        return ("repro 1: valid citation + wrong prose -> deterministic "
                "count display (Circle 1)")


def u28_structural_prose_never_displayed() -> str:
    """Codex PR #4 review repro 2: a summary answer claiming structural
    meaning ("safe reinforced columns") that the index does not contain
    (classification is null everywhere). The displayed answer must contain
    only cited index values."""
    with tempfile.TemporaryDirectory() as tmp:
        idx, _src = build_test_index(tmp)
        client = FakeClient(reply={
            "answerable": True,
            "answer": "The drawing contains safe reinforced columns.",
            "citations": [{"fact": "entity_counts.total"}]})
        ans = qa.answer_question(idx, "Summarize this drawing.", client)
        assert ans.status == "answered", (ans.status, ans.detail)
        lowered = ans.answer_text.lower()
        for word in ("safe", "reinforced", "column", "structural", "beam"):
            assert word not in lowered, (word, ans.answer_text)
        assert "entity_counts.total = 6" in ans.answer_text, ans.answer_text
        assert ans.evidence["answer_source"] == "composed_from_cited_values"
        # summary render = exactly the cited fields/handles
        assert ans.evidence["cited_fact_paths"] == \
            ans.evidence["displayed_fact_paths"] == ["entity_counts.total"]
        return "repro 2: structural prose never displayed; only cited values"


def u29_cumulative_spend_cap_across_invocations() -> str:
    """Codex PR #4 review point 2: the cap must NOT reset per CLI run. Three
    invocations share one usage log; the third must be refused BEFORE any
    request is sent."""
    with tempfile.TemporaryDirectory() as tmp:
        log_path = os.path.join(tmp, "usage.jsonl")
        calls = []

        def transport(url, body, headers, timeout):
            calls.append(1)
            return 200, json.dumps({
                "choices": [{"message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                          "total_tokens": 2, "cost": 0.5}}).encode()

        key = "sk-or-v1-TESTKEY"
        c1 = qa.OpenRouterClient(model="m", api_key=key, spend_cap_usd=1.0,
                                 usage_log_path=log_path, transport=transport)
        c1.complete("s", "u")
        assert abs(c1.tracked_spend_usd - 0.5) < 1e-9
        c2 = qa.OpenRouterClient(model="m", api_key=key, spend_cap_usd=1.0,
                                 usage_log_path=log_path, transport=transport)
        assert abs(c2.tracked_spend_usd - 0.5) < 1e-9, \
            "a new invocation must seed its tracked spend from the shared log"
        c2.complete("s", "u")
        assert abs(c2.tracked_spend_usd - 1.0) < 1e-9
        c3 = qa.OpenRouterClient(model="m", api_key=key, spend_cap_usd=1.0,
                                 usage_log_path=log_path, transport=transport)
        assert abs(c3.tracked_spend_usd - 1.0) < 1e-9
        try:
            c3.complete("s", "u")
        except qa.ModelFailure as e:
            msg = str(e)
            for needle in ("SPEND CAP REACHED", "cumulative", "post-call",
                           "OpenRouter dashboard"):
                assert needle in msg, (needle, msg)
        else:
            raise AssertionError(
                "cumulative cap did not stop the third invocation")
        assert len(calls) == 2, "the capped invocation must never send a request"
        return ("cap is cumulative across invocations sharing the usage "
                "log; 3rd invocation refused before any request")


def u30_unreported_cost_flagged_and_malformed_log_tolerated() -> str:
    """Honest accounting: calls whose cost the provider does not report count
    as zero toward the cap but are FLAGGED; malformed log lines are skipped
    without crashing; a $0.00 cap refuses before any request."""
    with tempfile.TemporaryDirectory() as tmp:
        log_path = os.path.join(tmp, "usage.jsonl")
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts_utc": "t", "model": "m", "status": 200,
                                 "cost_usd": None}) + "\n")
            fh.write("not json at all\n")  # malformed line must be skipped

        calls = []

        def transport(url, body, headers, timeout):
            calls.append(1)
            return 200, json.dumps({
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                          "total_tokens": 2}}).encode()  # no cost reported

        key = "sk-or-v1-TESTKEY"
        c = qa.OpenRouterClient(model="m", api_key=key, spend_cap_usd=10.0,
                                usage_log_path=log_path, transport=transport)
        assert c.unreported_cost_calls == 1, c.unreported_cost_calls
        assert abs(c.tracked_spend_usd) < 1e-12
        c.complete("s", "u")
        assert c.unreported_cost_calls == 2
        assert len(calls) == 1
        capped = qa.OpenRouterClient(model="m", api_key=key, spend_cap_usd=0.0,
                                     usage_log_path=log_path,
                                     transport=transport)
        try:
            capped.complete("s", "u")
        except qa.ModelFailure as e:
            assert "SPEND CAP REACHED" in str(e), e
            assert "reported no cost" in str(e), e
        else:
            raise AssertionError("a $0.00 cap did not stop the call")
        assert len(calls) == 1, "the capped call must never be sent"
        return ("unreported-cost calls flagged; malformed log lines skipped; "
                "$0 cap refuses before any request")


def u31_cli_help_labels_cap_honestly() -> str:
    """The CLI must not promise a hard per-call guarantee (Codex point 2):
    the help text states the cap is a cumulative post-call stop and names
    the external hard limit."""
    proc = subprocess.run(
        [sys.executable, os.path.join(HERE, "drawing_qa.py"), "ask", "--help"],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, (proc.returncode, proc.stderr)
    text = proc.stdout.lower()  # case-insensitive: wrapping/italics may vary
    for needle in ("cumulative", "post-call", "dashboard", "max-tokens",
                   "guarantee"):
        assert needle in text, (needle, proc.stdout)
    return ("CLI help states the cap is a cumulative post-call stop and "
            "names the dashboard as the hard limit")


def u32_displayed_fields_reported_count() -> str:
    """Codex PR #4 review round 2, repro 1: the model cites ONLY
    entity_counts.total for a count question. The deterministic render
    still shows the whole counts facts, so EVERY rendered field must be
    listed in the evidence (displayed_fact_paths) and every listed field
    must resolve in the excerpt — the citation/display correspondence is
    checkable and the "cited values only" claim is no longer made."""
    with tempfile.TemporaryDirectory() as tmp:
        idx, _src = build_test_index(tmp)
        client = FakeClient(reply={
            "answerable": True,
            "answer": "The drawing contains 6 entities.",
            "citations": [{"fact": "entity_counts.total"}]})
        ans = qa.answer_question(idx, EN_COUNT_Q, client)
        assert ans.status == "answered", (ans.status, ans.detail)
        ev = ans.evidence
        assert ev["cited_fact_paths"] == ["entity_counts.total"]
        displayed = ev["displayed_fact_paths"]
        # the full deterministic render is reported, not claimed as cited
        assert "entity_counts.total" in displayed
        assert "entity_counts.by_type.Circle" in displayed
        assert "entity_counts.by_type.Line" in displayed
        assert any(p.startswith("entity_counts.by_layer.")
                   for p in displayed), displayed
        # correspondence: every DISPLAYED path resolves inside the excerpt
        # facts (fresh accessors, exactly what the model saw)
        payload = user_payload(client)
        for path in displayed:
            found, _value = qa._resolve_fact_path(payload["facts"], path)
            assert found, (path, displayed)
        # ...and every path whose VALUE appears in the text is listed
        assert "Circle 1" in ans.answer_text \
            and "entity_counts.by_type.Circle" in displayed
        assert ev["answer_source"] == "deterministic_from_index_facts"
        return ("repro: count display beyond the cited field -> every "
                "rendered path listed and resolvable")


def u33_refusal_prose_never_displayed() -> str:
    """Codex PR #4 review round 2, repro 2: a structural-hallucinating
    refusal reason ("This structure is unsafe and requires reinforcement.")
    must NOT reach the user; the displayed cannot-answer text is the local,
    neutral message."""
    with tempfile.TemporaryDirectory() as tmp:
        idx, _src = build_test_index(tmp)
        client = FakeClient(reply={
            "answerable": False,
            "reason": "This structure is unsafe and requires "
                      "reinforcement."})
        ans = qa.answer_question(idx, "Summarize this drawing.", client)
        assert ans.status == "cannot_answer", (ans.status, ans.detail)
        lowered = ans.detail.lower()
        for word in ("unsafe", "reinforced", "structure", "safety"):
            assert word not in lowered, (word, ans.detail)
        # neutral local fallback (no reason_code supplied)
        assert "could not answer this question from the index facts" \
            in ans.detail, ans.detail
        assert ans.evidence["model_reason_code"] is None
        # validated reason_code routes to its own local template
        client2 = FakeClient(reply={
            "answerable": False,
            "reason_code": "question_unsupported",
            "reason": "structural nonsense"})
        ans2 = qa.answer_question(idx, "Summarize this drawing.", client2)
        assert ans2.status == "cannot_answer", (ans2.status, ans2.detail)
        assert "cannot be answered from CAD index facts" in ans2.detail
        assert "nonsense" not in ans2.detail
        return ("repro: structural refusal prose never displayed; local "
                "neutral message by reason_code")


def u34_parse_failure_neutral_no_echo() -> str:
    """Codex PR #4 review round 3, repro 1: malformed model output (plain
    prose, no JSON) must produce a NEUTRAL model_failure detail — the
    response content is never echoed, so the model's free wording cannot
    reach the display through the error path either."""
    with tempfile.TemporaryDirectory() as tmp:
        idx, _src = build_test_index(tmp)
        client = FakeClient(
            raw="This structure is unsafe and requires reinforcement.")
        ans = qa.answer_question(idx, "Summarize this drawing.", client)
        assert ans.status == "model_failure", (ans.status, ans.detail)
        lowered = ans.detail.lower()
        for word in ("unsafe", "reinforced", "structure", "safety"):
            assert word not in lowered, (word, ans.detail)
        # the old echo marker is gone and no response content is quoted
        assert "output began" not in lowered, ans.detail
        assert "the model response did not contain a json object" in lowered, \
            ans.detail
        assert ans.answer_text is None
        assert len(client.calls) == 1  # exactly one bounded call, no retry
        # malformed JSON WITH braces is still a neutral failure too (the
        # JSONDecodeError message reports a position, never content)
        client2 = FakeClient(raw='{"answerable": true, "answer": "oops",}')
        ans2 = qa.answer_question(idx, "Summarize this drawing.", client2)
        assert ans2.status == "model_failure", (ans2.status, ans2.detail)
        assert "malformed json" in ans2.detail.lower(), ans2.detail
        assert "oops" not in ans2.detail, ans2.detail
        return ("repro: malformed model output -> NEUTRAL model_failure, "
                "response content never echoed")


def build_dotted_layer_index(tmpdir):
    """Fixture with a layer name containing a dot: 4 lines on TEST-BOUNDARY
    plus one circle on layer 'A.B' (offline, canned --serve replies)."""
    src = make_tmp_drawing(tmpdir)
    circle = dict(RAW_CIRCLE, layer="A.B")
    circle["properties"] = {"common": {"color": "ByLayer", "layer": "A.B"}}
    canned = RAW_LINES + [circle]
    srv = FakeServe(fake_state(src), {"ok": True, "count": 5,
                                      "entities": canned,
                                      "next_offset": None})
    idx = di.build_index(srv, src)
    return idx, src


def u35_dotted_layer_paths_resolve() -> str:
    """Codex PR #4 review round 3, repro 2: layer names containing a dot.
    entity_counts.by_layer.<name> display paths (and model citations) must
    resolve like layers.<name> does, and the U32-style correspondence check
    must hold for a dotted layer name end to end."""
    # 1. the exact repro: a by_layer path with a dotted name now resolves
    facts = {"entity_counts": {"by_layer": {"A.B": 2}}}
    assert qa._resolve_fact_path(facts, "entity_counts.by_layer.A.B") == \
        (True, 2)
    # 2. unambiguity: 'A' and 'A.B' both present -> each path picks its own
    facts2 = {"entity_counts": {"by_layer": {"A": 3, "A.B": 1}}}
    assert qa._resolve_fact_path(facts2, "entity_counts.by_layer.A") == \
        (True, 3)
    assert qa._resolve_fact_path(facts2, "entity_counts.by_layer.A.B") == \
        (True, 1)
    # 3. absent layer still fails closed
    assert qa._resolve_fact_path(facts2, "entity_counts.by_layer.B.C") == \
        (False, None)
    # 4. the layers.<name> fallback keeps working for dotted names
    assert qa._resolve_fact_path({"layers": {"A.B": 1}}, "layers.A.B") == \
        (True, 1)
    # 5. end to end: count question, model cites the DOTTED layer path,
    #    every displayed path (including the dotted one) resolves
    with tempfile.TemporaryDirectory() as tmp:
        idx, _src = build_dotted_layer_index(tmp)
        client = FakeClient(reply={
            "answerable": True,
            "answer": "The drawing contains 5 entities.",
            "citations": [{"fact": "entity_counts.by_layer.A.B"}]})
        ans = qa.answer_question(idx, EN_COUNT_Q, client)
        assert ans.status == "answered", (ans.status, ans.detail)
        ev = ans.evidence
        assert ev["cited_fact_paths"] == ["entity_counts.by_layer.A.B"], \
            ev["cited_fact_paths"]
        displayed = ev["displayed_fact_paths"]
        assert "entity_counts.by_layer.A.B" in displayed, displayed
        # the count answer shows the dotted layer's own value
        assert "A.B 1" in ans.answer_text, ans.answer_text
        # U32-style correspondence, now with a dotted name in the mix
        payload = user_payload(client)
        for path in displayed:
            found, _value = qa._resolve_fact_path(payload["facts"], path)
            assert found, (path, displayed)
        return ("repro: dotted layer name end to end — cited/displayed "
                "path resolves, count shown")


# ---- real-binary tier (SKIP when the pinned OCS binary is missing) -----------
EXE = ocs.resolve_exe()
REAL_OK = os.path.isfile(EXE)


def require_real() -> None:
    if not REAL_OK:
        raise SkipCheck(f"OpenCADStudio binary not installed at {EXE} "
                        "(run tools/ocs/setup.ps1 on Windows or tools/ocs/setup.sh "
                        "on Linux); this check is NOT executed")


def real_pinned_banner() -> str:
    require_real()
    out = subprocess.run([EXE, "--version"], capture_output=True, text=True,
                         timeout=60)
    banner = (out.stdout or "").strip()
    assert out.returncode == 0 and ocs.OCS_VERSION in banner, banner
    return (f"{ocs.OCS_VERSION} present at {EXE} (SHA-256 pin asserted by "
            "test_drawing_index.py R1)")


def run_smoke_tool(workdir_base: str) -> str:
    proc = subprocess.run([sys.executable, os.path.join(HERE, "ocs_smoke_test.py"),
                           "--workdir", workdir_base],
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, f"milestone-1 smoke test failed:\n{proc.stdout[-2000:]}"
    import glob
    hits = sorted(glob.glob(os.path.join(workdir_base, "run-*", "fixture_base.dxf")),
                  key=os.path.getmtime)
    assert hits, "smoke test produced no fixture_base.dxf"
    return hits[-1]


def real_qa_offline(base: str, fixture: str) -> str:
    """Real OCS index + fake model: the QA flow over REAL indexed facts."""
    require_real()
    stderr_log = os.path.join(base, "ocs_stderr.log")
    srv = ocs.Serve(EXE, stderr_path=stderr_log)
    try:
        idx = di.build_index(srv, fixture)
    finally:
        srv.close()
    client = FakeClient(reply={
        "answerable": True,
        "answer": "The drawing contains 6 entities: 4 lines, 1 circle and "
                  "1 text.",
        "citations": [{"fact": "entity_counts.total"},
                      {"fact": "entity_counts.by_type.Line"},
                      {"fact": "entity_counts.by_layer.TEST-BOUNDARY"}]})
    ans = qa.answer_question(idx, EN_COUNT_Q, client)
    assert ans.status == "answered", (ans.status, ans.detail)
    assert ans.evidence["source_sha256"] == di.sha256_file(fixture)
    payload = user_payload(client)
    assert payload["facts"]["entity_counts"]["by_type"] == \
        {"Line": 4, "Circle": 1, "Text": 1}, payload["facts"]
    assert "6" in qa._digit_runs(ans.answer_text)
    return (f"real index ({idx.count()} entities) -> QA answered with "
            "validated citations")


def real_qa_stale(base: str, fixture: str) -> str:
    require_real()
    copy1 = os.path.join(base, "qa_stale_copy.dxf")
    import shutil
    shutil.copy2(fixture, copy1)
    srv = ocs.Serve(EXE, stderr_path=os.path.join(base, "ocs_stderr.log"))
    try:
        idx = di.build_index(srv, copy1)
    finally:
        srv.close()
    old_sha = idx.data["source"]["sha256"]
    with open(copy1, "ab") as fh:  # any byte change must stale the index
        fh.write(b"changed")
    new_sha = di.sha256_file(copy1)
    client = FakeClient(reply=reply_count_ok())
    ans = qa.answer_question(idx, EN_COUNT_Q, client)
    assert ans.status == "refused_stale", (ans.status, ans.detail)
    assert client.calls == []
    joined = " ".join(ans.reasons)
    assert old_sha in joined and new_sha in joined, joined
    return f"real stale index refused before any model call ({old_sha[:8]}.. -> {new_sha[:8]}..)"


# ---- real-model tier (ONE paid call, opt-in) ----------------------------------
def real_model_qa(base: str, fixture: str, args) -> str:
    key = os.environ.get(qa.ENV_KEY)
    if not key:
        raise SkipCheck(f"{qa.ENV_KEY} is not set — the real-model check is NOT "
                        "executed (set the key and pass --real-openrouter)")
    require_real()
    stderr_log = os.path.join(base, "ocs_stderr.log")
    srv = ocs.Serve(EXE, stderr_path=stderr_log)
    try:
        idx = di.build_index(srv, fixture)
    finally:
        srv.close()
    log_path = os.path.join(base, "openrouter_usage.jsonl")
    model = args.model or os.environ.get(qa.ENV_MODEL) or qa.DEFAULT_MODEL
    client = qa.OpenRouterClient(
        model=model, api_key=key, max_tokens=300,
        spend_cap_usd=(args.spend_cap_usd if args.spend_cap_usd is not None
                       else qa.DEFAULT_SPEND_CAP_USD),
        usage_log_path=log_path)
    question = "كم عدد العناصر في هذا الرسم؟"
    ans = qa.answer_question(idx, question, client)
    if ans.status != "answered":
        raise AssertionError(f"real model did not answer: {ans.status}: {ans.detail}")
    runs = qa._digit_runs(ans.answer_text)
    assert "6" in runs, f"answer lacks the true count 6: {ans.answer_text!r}"
    assert ans.evidence["citations_validated"] is True
    rec = client.records[-1]
    cost = "not reported" if rec.get("cost_usd") is None else f"${rec['cost_usd']:.6f}"
    return (f"REAL OpenRouter call: model={model} answer={ans.answer_text!r} "
            f"tokens={rec.get('total_tokens')} cost={cost} "
            f"tracked=${client.tracked_spend_usd:.6f} log={log_path}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--real-openrouter", action="store_true",
                    help="allow ONE real (possibly paid) OpenRouter call, "
                         "requires OPENROUTER_API_KEY; without the flag the "
                         "check is SKIPped, never silently run")
    ap.add_argument("--model", default=None, help="model id for the real call")
    ap.add_argument("--spend-cap-usd", type=float, default=None,
                    help="spending cap for the real call (default "
                         f"{qa.DEFAULT_SPEND_CAP_USD:.2f})")
    args = ap.parse_args()

    print(f"real binary: {EXE} -> "
          f"{'PRESENT' if REAL_OK else 'MISSING (real checks will SKIP)'}")
    print("model tier: mocked (FakeClient) unless --real-openrouter is passed")
    repo_root = os.path.dirname(os.path.dirname(HERE))
    base = os.path.join(repo_root, "tools", "ocs", "out")
    os.makedirs(base, exist_ok=True)

    check("U1 unit: EN count answered; one call; count excerpt has no entity "
          "records; path/sha never sent", u1_english_count_answered)
    check("U2 unit: AR count answered; Arabic-Indic digits normalized", 
          u2_arabic_count_arabic_indic_digits)
    check("U3 unit: AR layers answered from layer facts", u3_arabic_layers)
    check("U4 unit: handle lookup sends ONE slim record; classification null; "
          "no raw", u4_handle_lookup)
    check("U5 unit: text search matches recorded text only; cited handle "
          "validated", u5_text_search)
    check("U6 unit: summary excerpt capped + truncated flag", 
          u6_summary_excerpt_is_capped)
    check("U7 unit: missing fact -> model answerable=false -> CANNOT ANSWER",
          u7_missing_facts_model_declares_unsupported)
    check("U8 unit: EN structural question refused with zero model calls",
          u8_structural_question_refused_without_call_en)
    check("U9 unit: AR structural question refused with zero model calls",
          u9_structural_question_refused_without_call_ar)
    check("U10 unit: edit request refused (read-only), zero model calls",
          u10_edit_request_refused_without_call)
    check("U11 unit: stale index refused before retrieval; no model call",
          u11_stale_index_refused_before_any_call)
    check("U12 unit: dirty index refused (unverifiable); explicit re-index "
          "required", u12_unverifiable_index_refused)
    check("U13 unit: drawing changed during the model call -> answer "
          "discarded by gate #2", u13_change_during_model_call_discards_answer)
    check("U14 unit: two freshness gates in order (gate, ..., call, gate)",
          u14_two_freshness_gates_in_order)
    check("U15 unit: handle citation outside the excerpt -> discarded",
          u15_invalid_handle_citation_discarded)
    check("U16 unit: fact citation that does not resolve -> discarded",
          u16_invalid_fact_citation_discarded)
    check("U17 unit: invented number (7 vs true 6) -> discarded",
          u17_invented_number_discarded)
    check("U18 unit: non-JSON model output -> discarded", 
          u18_unparseable_output_discarded)
    check("U19 unit: API failure -> clear model failure, nothing displayed",
          u19_api_failure_no_answer)
    check("U20 unit: missing API key fails closed before any request",
          u20_missing_key_fails_closed)
    check("U21 unit: spending cap stops the next call once tracked spend "
          "reaches it; key only in the Authorization header",
          u21_spend_cap_stops_at_the_cap)
    check("U22 unit: usage log carries tokens/cost only — no key, no prompt "
          "content", u22_usage_log_has_no_sensitive_content)
    check("U23 unit: API key redacted from error messages",
          u23_key_redaction_on_errors)
    check("U24 unit: oversized prompt refused before sending",
          u24_prompt_size_guard)
    check("U25 cli: no key -> exit 5 with a clear message",
          u25_cli_no_key_exit5)
    check("U26 cli: stale index -> exit 3 + rebuild hint before any model "
          "call", u26_cli_stale_exit3)
    check("U27 unit: Codex repro 1 — valid citation + wrong prose; display "
          "composed from cited values", u27_count_prose_mismatch_repro)
    check("U28 unit: Codex repro 2 — structural prose never displayed; only "
          "cited index values", u28_structural_prose_never_displayed)
    check("U29 unit: spend cap is CUMULATIVE across invocations sharing the "
          "usage log", u29_cumulative_spend_cap_across_invocations)
    check("U30 unit: unreported-cost calls flagged; malformed log lines "
          "skipped; $0 cap refuses pre-send",
          u30_unreported_cost_flagged_and_malformed_log_tolerated)
    check("U31 cli: --help labels the cap honestly (cumulative post-call "
          "stop, dashboard hard limit)", u31_cli_help_labels_cap_honestly)
    check("U32 unit: Codex round-2 repro 1 — count display beyond the cited "
          "field; every rendered path reported and resolvable",
          u32_displayed_fields_reported_count)
    check("U33 unit: Codex round-2 repro 2 — refusal prose never displayed; "
          "local neutral message by reason_code",
          u33_refusal_prose_never_displayed)
    check("U34 unit: Codex round-3 repro 1 — malformed model output -> "
          "NEUTRAL parse failure, response content never echoed",
          u34_parse_failure_neutral_no_echo)
    check("U35 unit: Codex round-3 repro 2 — dotted layer name: "
          "by_layer paths resolve, correspondence holds end to end",
          u35_dotted_layer_paths_resolve)

    ctx: dict = {}

    def r1() -> str:
        return real_pinned_banner()

    def r2() -> str:
        require_real()
        ctx["fixture"] = run_smoke_tool(base)
        return (f"fixture from milestone-1 tooling: "
                f"{os.path.basename(ctx['fixture'])} sha256 "
                f"{di.sha256_file(ctx['fixture'])[:12]}..")

    def r3() -> str:
        require_real()
        if "fixture" not in ctx:
            raise SkipCheck("depends on R2 (which failed or was skipped)")
        return real_qa_offline(base, ctx["fixture"])

    def r4() -> str:
        require_real()
        if "fixture" not in ctx:
            raise SkipCheck("depends on R2 (which failed or was skipped)")
        return real_qa_stale(base, ctx["fixture"])

    def m1() -> str:
        if "fixture" not in ctx:
            raise SkipCheck("depends on R2 (which failed or was skipped)")
        return real_model_qa(base, ctx["fixture"], args)

    check("R1 real: pinned OpenCADStudio binary answers --version", r1)
    check("R2 real: synthetic fixture produced by milestone-1 tooling", r2)
    check("R3 real: QA flow over the REAL OCS-built index (mock model): "
          "answered + citations validated", r3)
    check("R4 real: stale REAL index refused before any model call", r4)
    check("M1 real-model: ONE Arabic question answered by the configured "
          "OpenRouter model, citations validated, cost reported", m1)

    def stats(prefix: str) -> tuple[int, int, int]:
        rows = [(n, ok) for n, ok, _ in results if n.startswith(prefix)]
        return (sum(1 for _, ok in rows if ok == "PASS"), len(rows),
                sum(1 for _, ok in rows if ok == "SKIP"))

    for prefix, label in (("U", "unit"), ("R", "real binary"),
                          ("M", "real model")):
        p, n, s = stats(prefix)
        extra = f", {s} skipped" if s else ""
        print(f"{label}: {p}/{n} passed{extra}")
    if stats("M")[2]:
        print("REAL-MODEL INTEGRATION: NOT VERIFIED (SKIP) — a skipped M1 is "
              "not proof; run with --real-openrouter and the key set")
    if stats("R")[2] == stats("R")[1] and stats("R")[1]:
        print("REAL-BINARY INTEGRATION: NOT VERIFIED (SKIP)")
    elif not stats("R")[2]:
        print(f"REAL-BINARY INTEGRATION: VERIFIED (pinned OpenCADStudio "
              f"{ocs.OCS_VERSION} at {EXE})" if REAL_OK else "")
    failed = [n for n, ok, _ in results if ok == "FAIL"]
    print(f"verdict: {'PASS' if not failed else 'FAIL'} "
          f"({len(results) - len(failed)}/{len(results)} checks passed)")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
