"""Shared benchmark harness for the small-model-fleet project.

Runs entirely on Kaggle. Provides:
  * tolerant parsing of the two tool-call serialisations we must support
    (JSON-in-<tool_call> used by Qwen, XML-in-<tool_call> used by LFM3)
  * planner JSON extraction and validation
  * per-check scoring for both suites
  * generation helpers that work for plain and reasoning chat templates
  * resource accounting (latency, tok/s, peak VRAM, peak RAM)

Design rule: scoring never inspects model-specific formatting, only the parsed
semantic content, so every model is graded by the same rules.
"""

from __future__ import annotations

import json
import re
import time

# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------

_TOOL_CALL_RE = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL)
_FUNCTION_RE = re.compile(r"<function=([^>\s]+)>(.*?)</function>", re.DOTALL)
_PARAM_RE = re.compile(r"<parameter=([^>\s]+)>(.*?)</parameter>", re.DOTALL)
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _maybe_json(v: str):
    v = v.strip()
    try:
        return json.loads(v)
    except Exception:
        return v


def _normalise_args(args):
    if isinstance(args, str):
        parsed = _maybe_json(args)
        return parsed if isinstance(parsed, dict) else {"_raw": args}
    if isinstance(args, dict):
        return args
    if args is None:
        return {}
    return {"_raw": args}


def parse_tool_calls(text: str) -> list[dict]:
    """Extract tool calls from either serialisation, or bare JSON."""
    if not text:
        return []
    calls: list[dict] = []
    for block in _TOOL_CALL_RE.findall(text):
        block = block.strip()
        # LFM3 / XML style
        fn = _FUNCTION_RE.search(block)
        if fn:
            name = fn.group(1).strip()
            args = {}
            for k, v in _PARAM_RE.findall(fn.group(2)):
                args[k.strip()] = _maybe_json(v)
            calls.append({"name": name, "arguments": args})
            continue
        # Qwen / JSON style
        obj = _maybe_json(block)
        if isinstance(obj, dict):
            name = obj.get("name") or obj.get("tool") or obj.get("function")
            args = obj.get("arguments", obj.get("parameters", obj.get("args", {})))
            if isinstance(name, dict):
                args = name.get("arguments", args)
                name = name.get("name")
            if name:
                calls.append({"name": str(name), "arguments": _normalise_args(args)})
    if calls:
        return calls
    # Fallback: model emitted a bare JSON call without tags
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        obj = _maybe_json(stripped)
        if isinstance(obj, dict):
            name = obj.get("name") or obj.get("tool")
            if name and ("arguments" in obj or "parameters" in obj or "args" in obj):
                args = obj.get("arguments", obj.get("parameters", obj.get("args", {})))
                return [{"name": str(name), "arguments": _normalise_args(args)}]
    return []


def extract_json_object(text: str):
    """Pull the first complete JSON object out of a model response."""
    if not text:
        return None, "empty"
    candidates = []
    fence = _FENCE_RE.search(text)
    if fence:
        candidates.append(fence.group(1))
    candidates.append(text)
    for cand in candidates:
        cand = cand.strip()
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj, "ok"
        except Exception:
            pass
        # brace scan
        start = cand.find("{")
        while start != -1:
            depth = 0
            in_str = False
            esc = False
            for i in range(start, len(cand)):
                ch = cand[i]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                    continue
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        chunk = cand[start : i + 1]
                        try:
                            obj = json.loads(chunk)
                            if isinstance(obj, dict):
                                return obj, "ok"
                        except Exception:
                            pass
                        break
            start = cand.find("{", start + 1)
    return None, "unparseable"


# --------------------------------------------------------------------------
# argument schema validation
# --------------------------------------------------------------------------

_TYPE_MAP = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


def validate_arguments(tool_schema: dict, args: dict) -> tuple[bool, list[str]]:
    """Validate parsed arguments against the tool's JSON schema (subset)."""
    errors: list[str] = []
    if not isinstance(args, dict):
        return False, ["arguments is not an object"]
    params = (tool_schema or {}).get("function", {}).get("parameters", {})
    props = params.get("properties", {})
    required = params.get("required", [])
    for key in required:
        if key not in args:
            errors.append(f"missing required '{key}'")
    for key, value in args.items():
        if key not in props:
            continue  # tolerate extra keys, but flag types when declared
        spec = props[key]
        jtype = spec.get("type")
        pytype = _TYPE_MAP.get(jtype)
        if pytype is None:
            continue
        if jtype == "integer" and isinstance(value, bool):
            errors.append(f"'{key}' should be integer, got bool")
            continue
        if jtype == "number" and isinstance(value, bool):
            errors.append(f"'{key}' should be number, got bool")
            continue
        if not isinstance(value, pytype):
            # allow numeric strings for integers only if they parse cleanly: still an error
            errors.append(f"'{key}' should be {jtype}, got {type(value).__name__}")
    return (len(errors) == 0), errors


# --------------------------------------------------------------------------
# generic expectation checks
# --------------------------------------------------------------------------


def _word_count(s: str) -> int:
    return len(re.findall(r"\S+", s or ""))


def _as_text(args: dict) -> str:
    try:
        return json.dumps(args, ensure_ascii=False)
    except Exception:
        return str(args)


def _summary_json(summary):
    if isinstance(summary, (dict, list)):
        return summary
    if not isinstance(summary, str):
        return None
    cand = summary.strip()
    fence = _FENCE_RE.search(cand)
    if fence:
        cand = fence.group(1).strip()
    obj, _ = extract_json_object(cand)
    return obj


def check_expectations(expect: dict, calls: list[dict], raw_text: str) -> dict:
    """Evaluate one step's expectations. Returns {'passed': bool, 'checks': {...}, 'failures': [...]}"""
    checks: dict[str, bool] = {}
    failures: list[str] = []

    def record(name: str, ok: bool, detail: str = ""):
        checks[name] = bool(ok)
        if not ok:
            failures.append(f"{name}{(': ' + detail) if detail else ''}")

    first = calls[0] if calls else None
    first_name = first["name"] if first else None
    first_args = (first or {}).get("arguments") or {}

    if "tool" in expect:
        record("tool", first_name == expect["tool"], f"got {first_name!r} want {expect['tool']!r}")
    if "tool_in" in expect:
        record("tool_in", first_name in expect["tool_in"], f"got {first_name!r} want one of {expect['tool_in']}")
    if "must_not_tool" in expect:
        hit = [c["name"] for c in calls if c["name"] in expect["must_not_tool"]]
        record("must_not_tool", not hit, f"forbidden call(s) {hit}")
    if "no_tool_call" in expect:
        record("no_tool_call", len(calls) == 0, f"made {len(calls)} call(s)")

    for key, val in (expect.get("args_equal") or {}).items():
        ok = first_args.get(key) == val
        record(f"args_equal[{key}]", ok, f"got {first_args.get(key)!r} want {val!r}")
    for key, pattern in (expect.get("args_match") or {}).items():
        got = first_args.get(key)
        ok = got is not None and re.search(pattern, str(got)) is not None
        record(f"args_match[{key}]", ok, f"got {got!r} !~ {pattern!r}")
    for key, pattern in (expect.get("args_content_match") or {}).items():
        got = first_args.get(key)
        ok = got is not None and re.search(pattern, str(got), re.IGNORECASE) is not None
        record(f"args_content_match[{key}]", ok, f"got {str(got)[:120]!r} !~ {pattern!r}")
    for key, tname in (expect.get("args_type") or {}).items():
        val = first_args.get(key)
        pytype = _TYPE_MAP.get(tname)
        ok = isinstance(val, pytype) if pytype else val is not None
        if tname == "integer" and isinstance(val, bool):
            ok = False
        record(f"args_type[{key}]", ok, f"got {type(val).__name__} want {tname}")
    for key in (expect.get("args_required_keys") or []):
        record(f"args_required[{key}]", key in first_args)
    for key, val in (expect.get("args_optional") or {}).items():
        if key in first_args:
            record(f"args_optional[{key}]", first_args.get(key) == val, f"got {first_args.get(key)!r} want {val!r}")

    summary_raw = ""
    if first and first["name"] == "finish":
        summary_raw = first_args.get("summary", "")
    summary = summary_raw if isinstance(summary_raw, str) else json.dumps(summary_raw, ensure_ascii=False)
    if "summary_max_words" in expect:
        record("summary_max_words", _word_count(summary) <= expect["summary_max_words"], f"{_word_count(summary)} words")
    if "summary_contains_any" in expect:
        low = summary.lower()
        ok = any(k.lower() in low for k in expect["summary_contains_any"])
        record("summary_contains_any", ok, f"summary={summary[:160]!r}")
    if expect.get("summary_json_valid"):
        obj = _summary_json(summary_raw)
        record("summary_json_valid", isinstance(obj, (dict, list)), f"summary={summary[:160]!r}")
        if isinstance(obj, dict):
            for k, v in (expect.get("summary_assert") or {}).items():
                record(f"summary_assert[{k}]", obj.get(k) == v, f"got {obj.get(k)!r} want {v!r}")

    if "text_contains_any" in expect:
        low = (raw_text or "").lower()
        ok = any(k.lower() in low for k in expect["text_contains_any"])
        record("text_contains_any", ok, f"text={ (raw_text or '')[:160]!r}")
    if "text_or_args_contains_any" in expect:
        blob = ((raw_text or "") + " " + _as_text(first_args)).lower()
        ok = any(k.lower() in blob for k in expect["text_or_args_contains_any"])
        record("text_or_args_contains_any", ok, f"blob={(raw_text or '')[:160]!r}")

    return {"passed": len(failures) == 0, "checks": checks, "failures": failures}


# --------------------------------------------------------------------------
# planner-specific checks
# --------------------------------------------------------------------------


def check_planner(expect: dict, obj: dict | None, raw_text: str) -> dict:
    checks: dict[str, bool] = {}
    failures: list[str] = []

    def record(name: str, ok: bool, detail: str = ""):
        checks[name] = bool(ok)
        if not ok:
            failures.append(f"{name}{(': ' + detail) if detail else ''}")

    record("json_valid", isinstance(obj, dict), f"raw={raw_text[:160]!r}")
    if not isinstance(obj, dict):
        return {"passed": False, "checks": checks, "failures": failures}

    decision = obj.get("decision")
    tasks = obj.get("tasks")
    if not isinstance(tasks, list):
        tasks = []
    missing = obj.get("missing_info")
    if not isinstance(missing, list):
        missing = []
    notes = str(obj.get("notes") or "")

    if "decision_in" in expect:
        record("decision_in", decision in expect["decision_in"], f"got {decision!r} want {expect['decision_in']}")
    if "decision" in expect:
        record("decision", decision == expect["decision"], f"got {decision!r}")
    if "min_tasks" in expect:
        record("min_tasks", len(tasks) >= expect["min_tasks"], f"{len(tasks)} tasks")
    if "max_tasks" in expect:
        record("max_tasks", len(tasks) <= expect["max_tasks"], f"{len(tasks)} tasks")

    workers = [str(t.get("worker", "")).strip().lower() for t in tasks if isinstance(t, dict)]
    if "workers_all_of" in expect:
        for w in expect["workers_all_of"]:
            record(f"workers_all_of[{w}]", w.lower() in workers, f"workers={sorted(set(workers))}")
    if "workers_none_of" in expect:
        bad = [w for w in expect["workers_none_of"] if w.lower() in workers]
        record("workers_none_of", not bad, f"forbidden worker(s) {bad}")
    if "distinct_workers_min" in expect:
        record("distinct_workers_min", len(set(workers)) >= expect["distinct_workers_min"], f"{sorted(set(workers))}")

    ids = [str(t.get("id")) for t in tasks if isinstance(t, dict)]
    record("ids_unique", len(ids) == len(set(ids)), f"ids={ids}")

    dep_edges = []
    deps_ok = True
    dep_detail = ""
    for idx, t in enumerate(tasks):
        if not isinstance(t, dict):
            continue
        deps = t.get("depends_on") or []
        if not isinstance(deps, list):
            deps = [deps]
        for d in deps:
            dep_edges.append((str(d), str(t.get("id"))))
            if str(d) not in ids[:idx]:
                deps_ok = False
                dep_detail = f"{t.get('id')} depends on {d} which is not an earlier task id"
    if "has_deps" in expect:
        has = any((t.get("depends_on") or []) for t in tasks if isinstance(t, dict))
        record("has_deps", has == expect["has_deps"], f"got {has}")
    if expect.get("deps_acyclic"):
        record("deps_acyclic", deps_ok, dep_detail)
    if expect.get("last_task_has_deps"):
        last_ok = bool(tasks) and bool(isinstance(tasks[-1], dict) and (tasks[-1].get("depends_on") or []))
        record("last_task_has_deps", last_ok, "last task has no depends_on")

    if "actions_max_words" in expect:
        overflow = [t.get("id") for t in tasks if isinstance(t, dict) and _word_count(str(t.get("action", ""))) > expect["actions_max_words"]]
        record("actions_max_words", not overflow, f"too long: {overflow}")

    if expect.get("missing_info_nonempty"):
        record("missing_info_nonempty", len(missing) > 0, "missing_info empty")
    if expect.get("missing_info_empty"):
        record("missing_info_empty", len(missing) == 0, f"missing_info={missing}")

    if "notes_contains_any" in expect:
        low = notes.lower()
        ok = any(k.lower() in low for k in expect["notes_contains_any"])
        record("notes_contains_any", ok, f"notes={notes[:200]!r}")

    return {"passed": len(failures) == 0, "checks": checks, "failures": failures}


# --------------------------------------------------------------------------
# generation helper
# --------------------------------------------------------------------------

_THINK_BLOCK = re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>", re.DOTALL)


def strip_reasoning(text: str) -> str:
    if not text:
        return ""
    out = _THINK_BLOCK.sub("", text)
    # unterminated reasoning block
    if "<think" in out:
        out = out.split("<think")[0]
    return out.strip()


def template_supports(tokenizer, kwarg: str) -> bool:
    tmpl = getattr(tokenizer, "chat_template", None) or ""
    if isinstance(tmpl, dict):
        tmpl = " ".join(str(v) for v in tmpl.values())
    return kwarg in tmpl


class Generator:
    """Thin wrapper that records latency, token counts and peak memory."""

    def __init__(self, model, tokenizer, device="cuda", max_new_tokens=1024, name="model",
                 enable_thinking=True):
        self.model = model
        self.tokenizer = tokenizer
        self.device = device
        self.max_new_tokens = max_new_tokens
        self.name = name
        self.enable_thinking = enable_thinking
        self.records = []
        self._enable_thinking = template_supports(tokenizer, "enable_thinking")
        self._supports_tools = template_supports(tokenizer, "tools")

    def render(self, messages, tools=None):
        kwargs = dict(tokenize=False, add_generation_prompt=True)
        if tools and self._supports_tools:
            kwargs["tools"] = tools
        if self._enable_thinking:
            kwargs["enable_thinking"] = self.enable_thinking
        try:
            return self.tokenizer.apply_chat_template(messages, **kwargs)
        except TypeError:
            kwargs.pop("enable_thinking", None)
            return self.tokenizer.apply_chat_template(messages, **kwargs)

    def chat(self, messages, max_new_tokens=None, tools=None):
        import torch

        text = self.render(messages, tools=tools)
        inputs = self.tokenizer(text, return_tensors="pt", add_special_tokens=False).to(self.device)
        prompt_len = inputs["input_ids"].shape[1]
        n_new = max_new_tokens or self.max_new_tokens
        if torch.cuda.is_available():
            torch.cuda.synchronize()
            torch.cuda.reset_peak_memory_stats()
        gen_kwargs = dict(
            max_new_tokens=n_new,
            do_sample=False,
            pad_token_id=self.tokenizer.pad_token_id or self.tokenizer.eos_token_id,
        )
        t0 = time.time()
        with torch.inference_mode():
            try:
                out = self.model.generate(**inputs, **gen_kwargs)
            except Exception:
                # older/newer transformers reject some combinations; fall back to the minimum
                out = self.model.generate(**inputs, max_new_tokens=n_new, do_sample=False)
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        dt = time.time() - t0
        gen_ids = out[0][prompt_len:]
        completion = self.tokenizer.decode(gen_ids, skip_special_tokens=True)
        n_tok = int(gen_ids.shape[0])
        rec = {
            "prompt_tokens": int(prompt_len),
            "completion_tokens": n_tok,
            "latency_s": round(dt, 3),
            "tokens_per_s": round(n_tok / dt, 2) if dt > 0 else None,
            "peak_vram_gb": round(torch.cuda.max_memory_allocated() / 1e9, 3) if torch.cuda.is_available() else None,
        }
        self.records.append(rec)
        return completion, rec


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------


def summarise(step_records: list[dict]) -> dict:
    """Aggregate step-level records into suite metrics."""
    if not step_records:
        return {}
    n = len(step_records)
    lat = [r["latency_s"] for r in step_records if r.get("latency_s")]
    tps = [r["tokens_per_s"] for r in step_records if r.get("tokens_per_s")]
    vram = [r["peak_vram_gb"] for r in step_records if r.get("peak_vram_gb")]
    forbidden = [r for r in step_records if r.get("forbidden_tools")]
    finish_forbidden = [r for r in forbidden if "finish" in r["forbidden_tools"]]
    false_completion = [r for r in finish_forbidden if "finish" in (r.get("called_tools") or [])]
    return {
        "steps": n,
        "instruction_following_accuracy": round(sum(1 for r in step_records if r["passed"]) / n, 4),
        "tool_selection_accuracy": round(sum(1 for r in step_records if r.get("tool_ok")) / n, 4),
        "argument_validity": round(sum(1 for r in step_records if r.get("args_schema_ok")) / n, 4),
        "parse_validity": round(sum(1 for r in step_records if r.get("parse_ok")) / n, 4),
        "false_completion_rate": round(len(false_completion) / len(finish_forbidden), 4) if finish_forbidden else 0.0,
        "permission_violation_rate": round(
            sum(1 for r in forbidden if set(r.get("called_tools") or []) & set(r["forbidden_tools"])) / n, 4
        ),
        "avg_latency_s": round(sum(lat) / len(lat), 3) if lat else None,
        "p95_latency_s": round(sorted(lat)[int(0.95 * (len(lat) - 1))], 3) if lat else None,
        "avg_tokens_per_s": round(sum(tps) / len(tps), 2) if tps else None,
        "peak_vram_gb": max(vram) if vram else None,
        "total_completion_tokens": sum(r.get("completion_tokens") or 0 for r in step_records),
    }
