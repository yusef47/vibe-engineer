#!/usr/bin/env python3
"""Read-only question answering over a saved drawing index (milestone 3A).

Answers simple Arabic or English questions about an ALREADY-INDEXED DXF
(entity counts, layers, handles, recorded text) by sending a SMALL relevant
index excerpt — never the whole drawing — to a configurable OpenRouter model,
then validating the model's answer and citations locally before displaying
anything. This module is READ-ONLY: it never calls a CAD mutation operation
and never edits anything.

Pipeline (one question = at most ONE model call; there are no tool loops and
no model loops of any kind — unbounded repair loops are deliberately absent):

  1. classify the question locally (bilingual keyword map) into:
     count | layers | text | handle | summary | unmapped, plus two
     deterministic out-of-scope guards (structural-safety questions and edit
     requests) which refuse WITHOUT any model call;
  2. freshness gate #1: `DrawingIndex.check_fresh()` re-hashes the source
     file (and refuses fail-closed indexes without their matching live
     session — `IndexUnverifiableError`). A stale or unverifiable index is
     refused before any fact leaves the index; recovery is ONLY an explicit
     re-indexing operation (`drawing_index.py index`);
  3. build the excerpt for the classified intent (counts only for count
     questions; matched records for text/handle questions; a capped slice
     for summary/unmapped questions). The excerpt NEVER contains the source
     path or file hash (they stay local evidence; the model does not need
     customer file paths);
  4. ONE OpenRouter call (configurable model, timeout, max_tokens, spending
     cap) with a strict JSON contract: answer + citations (handles and/or
     dotted fact paths into the excerpt);
  5. freshness gate #2: `check_fresh()` runs AGAIN before the answer is
     displayed. If the drawing changed while the model was thinking, the
     answer is discarded and refused (exit 3);
  6. citation validation against the retrieved excerpt: every cited handle
     must be present among the returned records, every fact path must
     resolve inside the excerpt, and every number stated in the answer must
     exist in the excerpt facts (Arabic-Indic digits are normalized).
     Invalid citations or unsupported numbers -> the answer is discarded
     (exit 5), never displayed;
  7. a factual answer is displayed only with its evidence block: source
     path, source SHA-256, document revision, indexed-at timestamp, and the
     cited handles. "Cannot answer" is stated clearly (exit 4) when the
     facts do not support the question — nothing structural, no loads, no
     units, nothing not in the index is ever inferred.

Trust contract carried over from milestone 2 (`drawing_index.py`):

- every index read is freshness-gated (twice here: before retrieval and
  before display);
- stale or unverifiable indexes require an explicit re-index; no accessor
  serves stale data;
- classification stays null; type/layer are verbatim CAD strings, never
  structural claims;
- values the index does not record stay `unknown`; the model is told this
  in the excerpt caveats.

OpenRouter usage rules (budget safety):

- the API key is read from the `OPENROUTER_API_KEY` environment variable
  (or an ignored `.env` file that only seeds it) — it is never printed,
  logged, or committed; every error message is redacted;
- the model is configurable: `--model` > `$OPENROUTER_MODEL` > the PLAN.md
  candidate `z-ai/glm-5.3-flash` (a model id, never a price);
- one request per question; `max_tokens` bounds each reply; the excerpt
  bounds the prompt;
- a spending cap (default $5.00 = PLAN.md's initial evaluation budget;
  `--spend-cap-usd` / `$OPENROUTER_SPEND_CAP_USD`) refuses any further call
  once tracked spend reaches it. Tracked spend comes from the `usage.cost`
  OpenRouter itself reports (requested via `usage: {"include": true}`);
  when a call reports no cost, the call is logged with `cost_usd: null` so
  the operator can reconcile against the OpenRouter dashboard — the cap can
  only count reported costs, which is documented as a limitation;
- usage logging prints token counts, cost, and latency per call (stdout,
  plus optional `--usage-log` JSONL). Logs contain NO prompt content and NO
  key material.

CLI (see tools/ocs/README.md):

    python tools/ocs/drawing_qa.py ask --index idx.json --question "How many circles?"
    python tools/ocs/drawing_qa.py ask --index idx.json --question "كم عدد العناصر في الرسم؟"
    python tools/ocs/drawing_qa.py excerpt --index idx.json --question "..."   # what would be sent

Exit codes for `ask`: 0 answered (with evidence); 2 usage; 3 stale or
unverifiable index refusal; 4 cannot answer from the available drawing
facts (deterministic guard or model-declared unsupported); 5 model/API
failure (missing key, HTTP error, timeout, unparseable output, invalid
citations, spend cap reached); 1 unexpected; 130 interrupted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from ocs_smoke_test import utcnow  # noqa: E402 (proven milestone-1 helper)
import drawing_index as di  # noqa: E402

SCHEMA_QA = "vibe-engineer/drawing-qa@1"

DEFAULT_MODEL = "z-ai/glm-5.3-flash"   # PLAN.md candidate; configurable, never a price
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MAX_TOKENS = 500               # bounds each completion
DEFAULT_TIMEOUT_S = 60.0               # per-request timeout
DEFAULT_SPEND_CAP_USD = 5.00           # PLAN.md initial evaluation budget
DEFAULT_EXCERPT_MAX_RECORDS = 20       # bounds entity records sent to the model
MAX_PROMPT_CHARS = 200_000             # hard cap on the serialized prompt

ENV_KEY = "OPENROUTER_API_KEY"
ENV_MODEL = "OPENROUTER_MODEL"
ENV_SPEND_CAP = "OPENROUTER_SPEND_CAP_USD"

EXIT_OK = 0
EXIT_OTHER = 1
EXIT_USAGE = 2
EXIT_STALE = 3
EXIT_CANNOT = 4
EXIT_MODEL = 5

# ---- intents ----------------------------------------------------------------
INTENT_COUNT = "count"
INTENT_LAYERS = "layers"
INTENT_TEXT = "text"
INTENT_HANDLE = "handle"
INTENT_SUMMARY = "summary"
INTENT_UNMAPPED = "unmapped"             # falls back to the capped summary excerpt
INTENT_STRUCTURAL = "structural_out_of_scope"
INTENT_EDIT = "edit_request"

# Deterministic out-of-scope guards. English cues are matched on lowercase
# word tokens; Arabic cues are matched by substring because Arabic words
# carry prefixes/suffixes (ال، و، بال…) that break token equality. The guard
# refuses clearly and costs no model call; refusing such questions is the
# honest outcome — the index contains no confirmed structural data at all.
_STRUCTURAL_EN = {"safe", "safety", "load", "loads", "loading", "capacity",
                  "strength", "stability", "reinforce", "reinforcement",
                  "structural", "bending", "stress"}
_STRUCTURAL_AR = ("حمل", "أحمال", "احمال", "حمولة", "تحميل", "تحمل", "آمن",
                  "أمان", "امان", "سلامة", "تسليح", "إنشائي", "انشائي",
                  "إنشائية", "انشائية", "مقاومة", "أمان")
_EDIT_EN = {"change", "modify", "set", "move", "delete", "remove", "rename",
            "edit", "update", "erase", "offset", "trim", "rotate", "scale",
            "mirror", "copy", "add", "create", "draw"}
_EDIT_AR = ("عدّل", "عدل", "غيّر", "غير", "احذف", "امسح", "انقل", "أضف",
            "اضف", "انسخ", "أنشئ", "ارسم", "حدّث", "حدث")

_COUNT_EN = ("how many", "how much", "count", "number of", "total number",
             "quantity", "totals")
_LAYERS_EN = ("layer", "layers")
_TEXT_EN = ("text", "texts", "label", "labels", "annotation", "annotations",
            "search", "find", "look for")
_SUMMARY_EN = ("summary", "summarize", "summarise", "overview", "describe",
               "what is", "what's", "what are", "tell me", "list all",
               "this drawing", "contents", "inventory")
_COUNT_AR = ("كم", "عدد", "عدّ", "احصاء", "إحصاء", "اجمالي", "إجمالي",
             "كمية", "العدد")
_LAYERS_AR = ("طبقة", "طبقات", "الطبقات")
_TEXT_AR = ("نص", "نصوص", "كلمة", "كلمات", "بحث", "ابحث", "تسمية", "ملاحظة",
            "ملصق")
_SUMMARY_AR = ("ملخص", "لخص", "ما هذا", "اوصف", "وصف", "عن الرسم", "محتويات",
               "ماذا", "ما هي", "ما هو", "اذكر", "استعرض", "قائمة")


class ModelFailure(Exception):
    """The model/protocol path failed: missing key, HTTP error, timeout,
    unparseable output, invalid citations, or spend cap reached. No answer
    is displayed (CLI exit 5)."""


class Answer:
    """The outcome of `answer_question()`; the CLI maps status -> exit code.

    statuses: "answered" | "cannot_answer" | "refused_stale" |
    "refused_guard" | "model_failure".
    """

    def __init__(self, status: str, *, answer_text=None, cited_handles=None,
                 evidence=None, reasons=None, detail=None, excerpt=None,
                 model_called=False, intent=None, question_lang=None,
                 requested_handle=None):
        self.status = status
        self.answer_text = answer_text
        self.cited_handles = list(cited_handles or [])
        self.evidence = evidence
        self.reasons = list(reasons or [])
        self.detail = detail
        self.excerpt = excerpt
        self.model_called = model_called
        self.intent = intent
        self.question_lang = question_lang
        self.requested_handle = requested_handle


def redact(text: str, secret: str | None) -> str:
    """Scrub the API key (and any sk-or-… pattern) out of a message before it
    is ever printed or logged."""
    text = str(text)
    if secret:
        text = text.replace(secret, "[REDACTED]")
    return re.sub(r"sk-or-[A-Za-z0-9_\-]+", "[REDACTED]", text)


# ---- question classification (bilingual, deterministic) ----------------------
def detect_lang(question: str) -> str:
    return "ar" if any("\u0600" <= ch <= "\u06FF" for ch in question) else "en"


def _find_handle_token(question: str) -> str | None:
    m = re.search(r"handle\s*[:=]?\s*([0-9A-Fa-f]{1,8})\b", question,
                  re.IGNORECASE)
    if m:
        return m.group(1)
    if any(c in question for c in ("معرف", "مقبض", "المعرف", "المقبض")):
        m = re.search(r"\b([0-9A-Fa-f]{1,8})\b", question)
        if m:
            return m.group(1)
    return None


def classify(question: str) -> str:
    q = question.strip()
    ql = q.lower()
    tokens = set(re.findall(r"[a-z]+", ql))
    # out-of-scope guards first: they refuse without any model call
    if tokens & _STRUCTURAL_EN or any(c in q for c in _STRUCTURAL_AR):
        return INTENT_STRUCTURAL
    if tokens & _EDIT_EN or any(c in q for c in _EDIT_AR):
        return INTENT_EDIT
    if _find_handle_token(q):
        return INTENT_HANDLE
    if any(c in ql for c in _COUNT_EN) or any(c in q for c in _COUNT_AR):
        return INTENT_COUNT
    if any(c in ql for c in _LAYERS_EN) or any(c in q for c in _LAYERS_AR):
        return INTENT_LAYERS
    if any(c in ql for c in _TEXT_EN) or any(c in q for c in _TEXT_AR):
        return INTENT_TEXT
    if any(c in ql for c in _SUMMARY_EN) or any(c in q for c in _SUMMARY_AR):
        return INTENT_SUMMARY
    return INTENT_UNMAPPED


# ---- excerpt building (small relevant slice; never the whole drawing) -------
def _slim_record(rec: dict) -> dict:
    """One entity record for the model: facts + provenance, minus the bulky
    verbatim `raw` reply, internal bookkeeping, and the source path/hash
    (customer identity stays in the LOCAL evidence block; the model never
    sees it). `classification` is kept (always null) so the model can SEE
    that nothing structural is confirmed."""
    return {
        "handle": rec["handle"],
        "type": rec["type"],
        "layer": rec["layer"],
        "space": rec["space"],
        "geometry": rec.get("geometry") or {},
        "text": rec.get("text"),
        "bounds": rec.get("bounds"),
        "color": rec.get("color"),
        "classification": rec.get("classification"),
        "provenance": {
            "document_revision": rec["provenance"]["document_revision"],
            "document_id": rec["provenance"]["document_id"],
        },
    }


def _all_text_values(idx, cap: int) -> list[str]:
    values = []
    for rec in idx.entities():
        value = (rec.get("text") or {}).get("value")
        if isinstance(value, str):
            values.append(value)
            if len(values) >= cap:
                break
    return values


def _text_records(idx, cap: int) -> list[dict]:
    out = []
    for rec in idx.entities():
        if rec.get("text"):
            out.append(_slim_record(rec))
            if len(out) >= cap:
                break
    return out


_TEXT_CUE_TOKENS = {"text", "texts", "label", "labels", "search", "find",
                    "look", "in", "on", "of", "for", "about", "the",
                    "نص", "نصوص", "كلمة", "كلمات", "بحث", "ابحث", "عن",
                    "في", "من", "ال"}


def _extract_text_needle(question: str) -> str | None:
    m = re.search(r"[\"']([^\"']{2,80})[\"']", question)
    if m:
        return m.group(1).strip()
    cues = list(re.finditer(
        r"(search(?:\s+for)?|find|look\s+for|text|texts|label|labels|"
        r"نص|نصوص|كلمة|كلمات|ابحث|بحث)", question, re.IGNORECASE))
    if not cues:
        return None
    # take the token after the LAST cue, skipping connectors like for/about/the
    tail = question[cues[-1].end():]
    tail = re.sub(r"^\s*(?:for\s+|about\s+|in\s+|on\s+|the\s+|عن\s+|في\s+)?"
                  r"[\"']?", "", tail)
    m2 = re.match(r"([A-Za-z0-9_\-.]{2,40})", tail)
    if m2 and m2.group(1).lower() not in _TEXT_CUE_TOKENS:
        return m2.group(1)
    return None


def _build_excerpt(idx: "di.DrawingIndex", question: str, intent: str,
                   max_records: int) -> tuple[list[dict], dict, str | None]:
    """Facts + slim records for one intent. Every index read is through the
    gated accessors (each re-verifies freshness; cheap, read-only). Returns
    (records, facts, requested_handle)."""
    requested = None
    facts: dict = {}
    records: list[dict] = []
    if intent == INTENT_COUNT:
        facts["entity_counts"] = {"total": idx.count(),
                                  "by_type": idx.count_by_type(),
                                  "by_layer": idx.count_by_layer()}
    elif intent == INTENT_LAYERS:
        facts["layers"] = idx.count_by_layer()
    elif intent == INTENT_TEXT:
        needle = _extract_text_needle(question)
        if needle:
            hits = idx.search_text(needle)
            if hits:
                records = [_slim_record(r) for r in hits[:max_records]]
                facts = {"query": needle, "match_count": len(hits),
                         "truncated": len(hits) > max_records,
                         "entity_records": records}
            else:
                facts = {"query": needle, "match_count": 0,
                         "all_recorded_text_values":
                             _all_text_values(idx, max_records)}
                records = _text_records(idx, max_records)
        else:
            facts = {"all_recorded_text_values":
                         _all_text_values(idx, max_records)}
            records = _text_records(idx, max_records)
    elif intent == INTENT_HANDLE:
        requested = _find_handle_token(question)
        try:
            rec = idx.entity(requested)
        except KeyError:
            facts = {"entity_records": [],
                     "note": "no entity with this handle is present in the index"}
        else:
            records = [_slim_record(rec)]
            facts = {"entity_records": records}
    else:  # summary / unmapped: capped overview slice
        total = idx.count()
        facts = {
            "entity_counts": {"total": total, "by_type": idx.count_by_type(),
                              "by_layer": idx.count_by_layer()},
            "all_recorded_text_values": _all_text_values(idx, max_records),
            "unresolved_gaps": idx.summary()["unknown"],
            "truncated": total > max_records,
            "entity_records": [_slim_record(r)
                               for r in idx.entities()[:max_records]],
        }
        records = facts["entity_records"]
    return records, facts, requested


CAVEATS = [
    "classification is null for every entity: nothing in this index confirms "
    "structural meaning (beams, columns, loads); do not claim any",
    "units are not recorded in this index; never state units (mm, cm, m) for "
    "any measurement",
    "answer ONLY from the facts in this excerpt; if they do not contain what "
    "is needed, set answerable=false and say briefly what is missing",
    "every number you state must come from these facts; cite the entity "
    "handles and/or fact paths you actually used",
]

SYSTEM_PROMPT = """You answer questions about ONE CAD drawing strictly from an excerpt of its local index (JSON).

Rules:
- Use ONLY the facts in the excerpt. Never invent or infer anything not present.
- Never claim structural meaning: the index records CAD type/layer strings verbatim and every classification is null. Do not identify beams, columns, slabs, loads, or safety.
- Never state units; the index does not record units.
- If the excerpt does not contain the answer, set "answerable" to false and explain briefly what is missing, in the question's language.
- If it does, set "answerable" to true, write a concise answer in the SAME language as the question (an Arabic question gets an Arabic answer; an English question gets an English answer), and list "citations": the entity handles and/or fact paths you used.
- Use Western digits (0-9) for all numbers in the answer.
- "fact" citations are dotted paths into the excerpt, e.g. "entity_counts.by_type.Circle" or "layers.TEST-SYMBOLS".
- Output EXACTLY ONE JSON object and nothing else:
  {"answerable": true, "answer": "...", "citations": [{"handle": "8A"}, {"fact": "entity_counts.total"}]}
  or
  {"answerable": false, "reason": "..."}"""


def _user_content(question: str, facts: dict) -> str:
    excerpt = {"excerpt_version": 1, "facts": facts, "caveats": CAVEATS}
    payload = json.dumps(excerpt, ensure_ascii=False, sort_keys=True)
    return f"QUESTION: {question}\n\nINDEX EXCERPT (JSON):\n{payload}"


def _excerpt_only_json(facts: dict) -> str:
    return json.dumps({"excerpt_version": 1, "facts": facts,
                       "caveats": CAVEATS},
                      ensure_ascii=False, sort_keys=True, indent=1)


# ---- reply parsing and validation -------------------------------------------
def _parse_model_json(text: str) -> dict:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z0-9_\-]*\s*|\s*```$", "", raw).strip()
    try:
        reply = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            raise ModelFailure(
                "the model did not return a JSON object; answer discarded "
                f"(output began: {raw[:120]!r})")
        try:
            reply = json.loads(m.group(0))
        except json.JSONDecodeError as e:
            raise ModelFailure(
                f"the model returned malformed JSON ({e}); answer discarded")
    if not isinstance(reply, dict):
        raise ModelFailure("the model did not return a JSON object; "
                           "answer discarded")
    return reply


def _resolve_fact_path(facts: dict, path: str):
    """Resolve a dotted path inside the excerpt facts. Returns (found, value).
    Layer names may themselves contain dots, so a failing 'layers.<rest>'
    path is retried with the remainder as one literal key."""
    if not isinstance(path, str) or not path.strip():
        return False, None
    path = path.strip().lstrip("$.")
    node = facts
    for part in path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            if path.startswith("layers."):
                tail = path[len("layers."):]
                layers = facts.get("layers") or {}
                if isinstance(layers, dict) and tail in layers:
                    return True, layers[tail]
            return False, None
    return True, node


def _fact_target_exists(value) -> bool:
    if value is None or value == di.UNKNOWN:
        return False
    if isinstance(value, str):
        return bool(value) and value != di.UNKNOWN
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return True
    if isinstance(value, (list, dict)):
        return len(value) > 0
    return False


def _digit_runs(text: str) -> list[str]:
    table = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
    return re.findall(r"\d+", str(text).translate(table))


def _collect_numbers(node, out: set) -> None:
    if isinstance(node, bool):
        return
    if isinstance(node, (int, float)):
        out.update(_digit_runs(str(node)))
    elif isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str):
                out.update(_digit_runs(key))
            _collect_numbers(value, out)
    elif isinstance(node, list):
        for value in node:
            _collect_numbers(value, out)


def _validate_citations(reply: dict, facts: dict,
                        records_by_handle_ci: dict) -> tuple[list, list, list]:
    """Every citation must resolve inside the retrieved excerpt. Returns
    (errors, cited_canonical_handles, valid_fact_paths)."""
    citations = reply.get("citations")
    if not isinstance(citations, list) or not citations:
        return ["citations must be a non-empty list of "
                '{"handle": ...} or {"fact": ...} objects'], [], []
    errors: list[str] = []
    cited_handles: list[str] = []
    fact_paths: list[str] = []
    for citation in citations:
        if not isinstance(citation, dict) or \
                not ({"handle", "fact"} & set(citation)):
            errors.append(f"unrecognized citation shape: {citation!r}")
            continue
        if "handle" in citation:
            handle = str(citation["handle"])
            rec = records_by_handle_ci.get(handle.lower())
            if rec is None:
                errors.append(f"cited handle {handle!r} is not present in the "
                              "retrieved excerpt")
            else:
                cited_handles.append(rec["handle"])
        else:
            path = citation["fact"]
            found, value = _resolve_fact_path(facts, path)
            if not found or not _fact_target_exists(value):
                errors.append(f"cited fact path {str(path)!r} does not resolve "
                              "to a value in the retrieved excerpt")
            else:
                fact_paths.append(str(path.strip().lstrip("$.")))
    return errors, cited_handles, fact_paths


def _validate_intent_binding(intent: str, reply: dict, answer: str, facts: dict,
                             cited_handles: list[str], fact_paths: list[str],
                             records: list[dict],
                             requested_handle: str | None) -> list[str]:
    """Intent-specific consistency checks beyond citation existence."""
    errors = []
    if intent == INTENT_COUNT:
        if not any(p.startswith("entity_counts") for p in fact_paths):
            errors.append("a count answer must cite an entity_counts fact "
                          "path (e.g. entity_counts.total)")
        allowed: set = set()
        _collect_numbers(facts, allowed)
        for run in _digit_runs(answer):
            if run not in allowed:
                errors.append(f"the answer states the number {run!r}, which "
                              "does not exist in the retrieved excerpt facts")
        return errors
    if intent == INTENT_HANDLE:
        if requested_handle and requested_handle.lower() not in \
                {h.lower() for h in cited_handles}:
            errors.append(f"a handle answer must cite the requested handle "
                          f"{requested_handle!r}")
        if requested_handle and requested_handle.lower() not in answer.lower():
            errors.append(f"the answer does not state the requested handle "
                          f"{requested_handle!r}")
        return errors
    if intent == INTENT_TEXT and facts.get("match_count"):
        matched = {r["handle"].lower() for r in records}
        if not matched & {h.lower() for h in cited_handles}:
            errors.append("a text answer must cite at least one matched "
                          "entity handle")
        return errors
    return errors


def _validate_numbers_general(answer: str, facts: dict,
                              records: list[dict]) -> list[str]:
    """Any number stated in an answer must exist in the excerpt facts (or as
    part of a record handle). Prevents invented values such as dates, sizes,
    or loads that the index does not contain."""
    allowed: set = set()
    _collect_numbers(facts, allowed)
    for rec in records:
        allowed.update(_digit_runs(rec.get("handle", "")))
    errors = []
    for run in _digit_runs(answer):
        if run not in allowed:
            errors.append(f"the answer states the number {run!r}, which does "
                          "not exist in the retrieved excerpt facts")
    return errors


# ---- OpenRouter client -------------------------------------------------------
class OpenRouterClient:
    """One-question-one-call OpenRouter client (stdlib urllib only).

    `transport` is injectable for tests: callable(url, body_bytes, headers,
    timeout) -> (status, body_bytes)."""

    def __init__(self, *, model: str, api_key: str | None = None,
                 base_url: str = DEFAULT_BASE_URL,
                 timeout_s: float = DEFAULT_TIMEOUT_S,
                 max_tokens: int = DEFAULT_MAX_TOKENS,
                 spend_cap_usd: float = DEFAULT_SPEND_CAP_USD,
                 usage_log_path: str | None = None, transport=None):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.timeout_s = float(timeout_s)
        self.max_tokens = int(max_tokens)
        self.spend_cap_usd = float(spend_cap_usd)
        self.usage_log_path = usage_log_path
        self._transport = transport or self._http_transport
        self.tracked_spend_usd = 0.0
        self.records: list[dict] = []

    def _http_transport(self, url, body, headers, timeout):
        req = urllib.request.Request(url, data=body, headers=headers,
                                     method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def _log(self, record: dict) -> None:
        self.records.append(record)
        if not self.usage_log_path:
            return
        try:
            with open(self.usage_log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False,
                                    sort_keys=True) + "\n")
        except OSError as e:
            # a failed usage log must never break or leak the call itself
            print(f"warning: could not write usage log: {e}", file=sys.stderr)

    def complete(self, system: str, user_content: str,
                 meta: dict | None = None) -> tuple[str, dict]:
        if not self.api_key:
            raise ModelFailure(
                f"{ENV_KEY} is not set — set the environment variable "
                f"(or an ignored .env file seeding it); the key is never "
                "passed on the command line")
        if len(user_content) > MAX_PROMPT_CHARS:
            raise ModelFailure(
                f"the prepared prompt is {len(user_content)} characters "
                f"(limit {MAX_PROMPT_CHARS}); refusing to send — reduce the "
                "excerpt (lower --excerpt-max-records)")
        if self.tracked_spend_usd >= self.spend_cap_usd - 1e-9:
            raise ModelFailure(
                f"SPEND CAP REACHED — tracked spend ${self.tracked_spend_usd:.6f} "
                f">= cap ${self.spend_cap_usd:.2f}; no request sent. Raise the "
                "cap consciously (--spend-cap-usd / $OPENROUTER_SPEND_CAP_USD) "
                "or stop")
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user_content}],
            "temperature": 0,
            "max_tokens": self.max_tokens,
            "usage": {"include": True},  # ask OpenRouter to report cost
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/json"}
        started = time.monotonic()
        record = {"ts_utc": utcnow(), "model": self.model, "status": None,
                  **(meta or {})}
        try:
            status, resp_bytes = self._transport(self.base_url, body, headers,
                                                 self.timeout_s)
        except Exception as exc:  # noqa: BLE001 - reported redacted, no traceback
            record.update({"status": "transport_error",
                           "error_class": type(exc).__name__,
                           "latency_ms": round((time.monotonic() - started) * 1000)})
            self._log(record)
            raise ModelFailure("OpenRouter request failed: "
                               f"{redact(exc, self.api_key)}") from None
        record["latency_ms"] = round((time.monotonic() - started) * 1000)
        record["status"] = status
        if status != 200:
            message = _error_message(resp_bytes)
            self._log(record)
            raise ModelFailure(redact(f"OpenRouter HTTP {status}: {message}",
                                      self.api_key))
        try:
            data = json.loads(resp_bytes.decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError, UnicodeDecodeError):
            record["error_class"] = "unexpected_reply_shape"
            self._log(record)
            raise ModelFailure("OpenRouter reply had an unexpected shape "
                               "(no choices[0].message.content); answer "
                               "discarded") from None
        usage = data.get("usage") or {}
        cost = usage.get("cost")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            self.tracked_spend_usd += float(cost)
        record.update({
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "cost_usd": float(cost) if isinstance(cost, (int, float))
            and not isinstance(cost, bool) else None,
            "tracked_spend_usd_after": round(self.tracked_spend_usd, 6),
        })
        self._log(record)
        return content, record


def _error_message(body_bytes) -> str:
    try:
        data = json.loads(body_bytes.decode("utf-8"))
        error = data.get("error") or {}
        return str(error.get("message") or data)
    except Exception:  # noqa: BLE001 - best-effort only
        return (body_bytes or b"")[:200].decode("utf-8", errors="replace")


def _seed_env_file() -> None:
    """Seed OPENROUTER_* variables from an ignored `.env` at the repo root.

    Only OPENROUTER_* keys are read, existing environment variables always
    win, values are never printed or logged."""
    root = os.path.dirname(os.path.dirname(HERE))
    path = os.path.join(root, ".env")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r"\s*(OPENROUTER_[A-Z_]+)\s*=\s*(.*)\s*$", line)
                if not m:
                    continue
                value = m.group(2).strip().strip('"').strip("'")
                os.environ.setdefault(m.group(1), value)
    except OSError:
        pass  # no .env file is the normal case


# ---- the QA flow -------------------------------------------------------------
def answer_question(idx: "di.DrawingIndex", question: str, client,
                    *, excerpt_max_records: int = DEFAULT_EXCERPT_MAX_RECORDS,
                    keep_excerpt: bool = False) -> Answer:
    """Full read-only QA flow (see module docstring). `client` only needs
    `complete(system, user_content, meta) -> (content, usage_record)`."""
    question = (question or "").strip()
    if not question:
        raise ValueError("empty question")
    intent = classify(question)
    lang = detect_lang(question)
    meta = {"question_sha256": hashlib.sha256(
        question.encode("utf-8")).hexdigest()[:12],
        "question_lang": lang, "intent": intent}

    # deterministic out-of-scope guards: no model call, no facts leave the index
    if intent == INTENT_STRUCTURAL:
        return Answer(
            "refused_guard", intent=intent, question_lang=lang,
            detail="structural-safety questions are out of scope: this index "
                   "records CAD data only, holds no confirmed structural "
                   "classification (classification is null everywhere), and "
                   "no loads, materials, or safety state. This prototype does "
                   "not infer any of them.")
    if intent == INTENT_EDIT:
        return Answer(
            "refused_guard", intent=intent, question_lang=lang,
            detail="this slice is strictly read-only: it cannot and will not "
                   "change the drawing. Use the CAD editor for edits; edit "
                   "support comes only through the later reviewed "
                   "plan/verify/undo workflow.")

    data = idx.data
    # freshness gate #1 — before any fact is retrieved
    try:
        checks1 = idx.check_fresh()
    except di.IndexStaleError as exc:
        return Answer("refused_stale", intent=intent, question_lang=lang,
                      reasons=exc.reasons, detail="checked before retrieval")

    try:
        records, facts, requested = _build_excerpt(idx, question, intent,
                                                   excerpt_max_records)
    except di.IndexStaleError as exc:
        return Answer("refused_stale", intent=intent, question_lang=lang,
                      reasons=exc.reasons, detail="while retrieving facts")
    user_content = _user_content(question, facts)

    # exactly one model call — there is no retry and no loop
    try:
        content, _usage = client.complete(SYSTEM_PROMPT, user_content, meta)
    except ModelFailure as exc:
        return Answer("model_failure", intent=intent, question_lang=lang,
                      detail=str(exc))
    except Exception as exc:  # noqa: BLE001 - client misuse, still fail closed
        return Answer("model_failure", intent=intent, question_lang=lang,
                      detail=f"unexpected client failure: {exc}")

    # freshness gate #2 — before the answer is displayed
    try:
        checks2 = idx.check_fresh()
    except di.IndexStaleError as exc:
        return Answer("refused_stale", intent=intent, question_lang=lang,
                      reasons=exc.reasons, model_called=True,
                      detail="the drawing changed while the model was "
                             "answering; the answer was discarded "
                             "(checked again before display)")
    evidence_base = {
        "source_path": data["source"]["path"],
        "source_sha256": data["source"]["sha256"],
        "document_revision": data["ocs"]["document_revision"],
        "document_id": data["ocs"]["document_id"],
        "indexed_utc": data["created_utc"],
        "freshness_checks_before_retrieval": checks1,
        "freshness_checks_before_display": checks2,
        "citations_validated": False,
    }

    try:
        reply = _parse_model_json(content)
    except ModelFailure as exc:
        return Answer("model_failure", intent=intent, question_lang=lang,
                      detail=str(exc), model_called=True)

    if reply.get("answerable") is False:
        reason = reply.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            reason = "the model did not state a reason"
        return Answer("cannot_answer", intent=intent, question_lang=lang,
                      detail=reason.strip(), model_called=True,
                      evidence=dict(evidence_base),
                      requested_handle=requested,
                      excerpt=_excerpt_only_json(facts) if keep_excerpt else None)

    if reply.get("answerable") is not True:
        return Answer("model_failure", intent=intent, question_lang=lang,
                      detail='"answerable" must be true or false; answer '
                             "discarded", model_called=True)

    answer_text = reply.get("answer")
    if not isinstance(answer_text, str) or not answer_text.strip():
        return Answer("model_failure", intent=intent, question_lang=lang,
                      detail='"answer" must be a non-empty string; answer '
                             "discarded", model_called=True)

    records_by_handle_ci = {r["handle"].lower(): r for r in records}
    errors, cited_handles, fact_paths = _validate_citations(
        reply, facts, records_by_handle_ci)
    errors += _validate_intent_binding(intent, reply, answer_text, facts,
                                       cited_handles, fact_paths, records,
                                       requested)
    if intent != INTENT_COUNT:
        # count already enforces this with the strict count-facts rule
        errors += _validate_numbers_general(answer_text, facts, records)
    if errors:
        return Answer("model_failure", intent=intent, question_lang=lang,
                      detail="INVALID MODEL CITATIONS — answer discarded: "
                             + "; ".join(errors),
                      model_called=True)

    evidence = dict(evidence_base)
    evidence.update({
        "cited_handles": cited_handles,
        "citations_validated": True,
    })
    return Answer("answered", answer_text=answer_text.strip(),
                  cited_handles=cited_handles, evidence=evidence,
                  intent=intent, question_lang=lang, model_called=True,
                  requested_handle=requested,
                  excerpt=_excerpt_only_json(facts) if keep_excerpt else None)


# ---- CLI ---------------------------------------------------------------------
def _fmt_handles(handles: list[str]) -> str:
    return ", ".join(handles) if handles else "(none)"


def _print_rebuild_hint() -> None:
    interpreter = f'"{sys.executable}"' if " " in sys.executable else sys.executable
    print("  recovery: re-index ONLY through an explicit new indexing "
          "operation:", file=sys.stderr)
    print(f"    {interpreter} tools/ocs/drawing_index.py index "
          "--file <drawing> --json <index>", file=sys.stderr)


def _usage_line(client: "OpenRouterClient") -> str | None:
    if not client.records:
        return None
    record = client.records[-1]
    if record.get("status") != 200:
        return (f"usage: call FAILED (status={record.get('status')!r}, "
                f"latency_ms={record.get('latency_ms')}); no tokens/cost "
                "reported by the provider for a failed call")
    cost = ("not reported by provider (cap counts reported costs only)"
            if record.get("cost_usd") is None
            else f"${record['cost_usd']:.6f}")
    return (f"usage: model={record['model']} "
            f"prompt_tokens={record.get('prompt_tokens')} "
            f"completion_tokens={record.get('completion_tokens')} "
            f"total_tokens={record.get('total_tokens')} cost={cost} "
            f"tracked_spend=${client.tracked_spend_usd:.6f} "
            f"(cap ${client.spend_cap_usd:.2f})")


def _print_evidence(answer: Answer) -> None:
    ev = answer.evidence
    print("")
    print("EVIDENCE (every fact above comes from this index and nothing else):")
    print(f"  source:            {ev['source_path']}")
    print(f"  sha256:            {ev['source_sha256']}")
    print(f"  revision:          {ev['document_revision']} "
          f"(document id {ev['document_id']}, indexed {ev['indexed_utc']})")
    print(f"  freshness:         checked before retrieval "
          f"({len(ev['freshness_checks_before_retrieval'])} checks) and again "
          f"before display ({len(ev['freshness_checks_before_display'])} checks)")
    print(f"  cited handles:     {_fmt_handles(ev['cited_handles'])} "
          "(validated against the retrieved excerpt)")


def _index_consulted_line(answer: Answer) -> None:
    ev = answer.evidence or {}
    if ev.get("source_path"):
        print(f"index consulted: {ev['source_path']} sha256 "
              f"{str(ev.get('source_sha256'))[:12]}.. revision "
              f"{ev.get('document_revision')}")


def cmd_ask(args, parser) -> int:
    _seed_env_file()
    if not args.question.strip():
        parser.error("--question must not be empty")
    model = args.model or os.environ.get(ENV_MODEL) or DEFAULT_MODEL
    if args.spend_cap_usd is not None:
        cap = args.spend_cap_usd
    else:
        env_cap = os.environ.get(ENV_SPEND_CAP)
        try:
            cap = float(env_cap) if env_cap else DEFAULT_SPEND_CAP_USD
        except ValueError:
            parser.error(f"${ENV_SPEND_CAP} must be a number, got {env_cap!r}")
    try:
        idx = di.DrawingIndex.from_json(args.index)
    except (OSError, json.JSONDecodeError) as e:
        parser.error(f"cannot load index {args.index!r}: {e}")
    client = OpenRouterClient(
        model=model, api_key=os.environ.get(ENV_KEY),
        timeout_s=args.timeout_s, max_tokens=args.max_tokens,
        spend_cap_usd=cap, usage_log_path=args.usage_log)
    answer = answer_question(idx, args.question, client,
                             excerpt_max_records=args.excerpt_max_records,
                             keep_excerpt=args.show_excerpt)
    print(f"question intent: {answer.intent}   language: {answer.question_lang}   "
          f"model: {model}")
    usage = _usage_line(client)
    if answer.status == "answered":
        print("")
        print("ANSWER:")
        print(f"  {answer.answer_text}")
        _print_evidence(answer)
        if args.show_excerpt and answer.excerpt:
            print("")
            print("EXCERPT SENT TO THE MODEL (facts only; never the whole drawing):")
            print(answer.excerpt)
        if usage:
            print(usage)
        return EXIT_OK
    if answer.status == "cannot_answer":
        print("")
        print("CANNOT ANSWER FROM THE AVAILABLE DRAWING FACTS")
        print(f"  reason: {answer.detail}")
        print("  (the index facts do not support this question; nothing "
              "was inferred)")
        _index_consulted_line(answer)
        if usage:
            print(usage)
        return EXIT_CANNOT
    if answer.status == "refused_guard":
        print("")
        print("REFUSED — OUT OF SCOPE FOR THIS READ-ONLY PROTOTYPE")
        print(f"  reason: {answer.detail}")
        print("  no model call was made and no facts left the index")
        return EXIT_CANNOT
    if answer.status == "refused_stale":
        print("", file=sys.stderr)
        print("STALE / UNVERIFIABLE INDEX — refusing to answer from "
              f"{args.index}", file=sys.stderr)
        if answer.detail:
            print(f"  when: {answer.detail}", file=sys.stderr)
        for reason in answer.reasons:
            print(f"  reason: {reason}", file=sys.stderr)
        _print_rebuild_hint()
        return EXIT_STALE
    # model_failure
    print("", file=sys.stderr)
    print("MODEL / PROTOCOL FAILURE — no answer displayed", file=sys.stderr)
    print(f"  reason: {answer.detail}", file=sys.stderr)
    if usage:
        print(usage, file=sys.stderr)
    return EXIT_MODEL


def cmd_excerpt(args, parser) -> int:
    """Show exactly what WOULD be sent to the model (no network, no key)."""
    try:
        idx = di.DrawingIndex.from_json(args.index)
    except (OSError, json.JSONDecodeError) as e:
        parser.error(f"cannot load index {args.index!r}: {e}")
    question = (args.question or "").strip()
    if not question:
        parser.error("--question is required")
    intent = classify(question)
    if intent in (INTENT_STRUCTURAL, INTENT_EDIT):
        print(f"intent: {intent} — refused deterministically; nothing is sent")
        return EXIT_CANNOT
    try:
        idx.check_fresh()
        records, facts, requested = _build_excerpt(idx, question, intent,
                                                   args.excerpt_max_records)
    except di.IndexStaleError as exc:
        print("STALE / UNVERIFIABLE INDEX — refusing", file=sys.stderr)
        for reason in exc.reasons:
            print(f"  reason: {reason}", file=sys.stderr)
        _print_rebuild_hint()
        return EXIT_STALE
    print(f"intent: {intent}   excerpt entities sent: {len(records)}")
    print(_excerpt_only_json(facts))
    return EXIT_OK


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Read-only QA over a saved drawing index via a "
                    "configurable OpenRouter model (milestone 3A)",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_ask = sub.add_parser("ask", help="answer one question from an index")
    p_ask.add_argument("--index", required=True,
                       help="index JSON produced by drawing_index.py index")
    p_ask.add_argument("--question", "-q", required=True,
                       help="the question (Arabic or English)")
    p_ask.add_argument("--model", default=None,
                       help=f"OpenRouter model id (default: ${ENV_MODEL}, "
                            f"then {DEFAULT_MODEL})")
    p_ask.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS,
                       help=f"completion token limit (default {DEFAULT_MAX_TOKENS})")
    p_ask.add_argument("--timeout-s", type=float, default=DEFAULT_TIMEOUT_S,
                       help=f"per-request timeout in seconds (default {DEFAULT_TIMEOUT_S:g})")
    p_ask.add_argument("--spend-cap-usd", type=float, default=None,
                       help=f"tracked-spend cap in USD (default: ${ENV_SPEND_CAP}, "
                            f"then {DEFAULT_SPEND_CAP_USD:.2f})")
    p_ask.add_argument("--excerpt-max-records", type=int,
                       default=DEFAULT_EXCERPT_MAX_RECORDS,
                       help="max entity records included in the excerpt "
                            f"(default {DEFAULT_EXCERPT_MAX_RECORDS})")
    p_ask.add_argument("--usage-log", default=None,
                       help="append per-call usage records (JSONL, no key, "
                            "no prompt content) to this file")
    p_ask.add_argument("--show-excerpt", action="store_true",
                       help="also print the exact excerpt sent to the model")
    p_ask.set_defaults(fn=cmd_ask)

    p_ex = sub.add_parser("excerpt",
                          help="print the excerpt that would be sent "
                               "(no network, no key)")
    p_ex.add_argument("--index", required=True)
    p_ex.add_argument("--question", "-q", required=True)
    p_ex.add_argument("--excerpt-max-records", type=int,
                      default=DEFAULT_EXCERPT_MAX_RECORDS)
    p_ex.set_defaults(fn=cmd_excerpt)

    args = ap.parse_args(argv)
    try:
        return args.fn(args, ap)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
