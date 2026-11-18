"""Dataset preparation for the LFM3 PC-agent SFT experiment.

Runs on Kaggle. Downloads public sources, normalises them into one canonical
record shape, validates every tool call against its own schema, de-duplicates,
splits, and pre-tokenises prompt/completion pairs with the assistant span
masked so the loss is computed on the answer only.

Canonical record:
    {"source": str, "tools": [openai tool dicts], "messages": [
        {"role": "system"|"user"|"assistant"|"tool", "content": str}], "meta": {...}}

Transformations applied (all documented in experiments/datasets/dataset-audit.md):
  * assistant tool calls are re-rendered from JSON into LFM3's native XML form
  * `finish` and `ask_user` are added to every tool list
  * a trailing assistant text answer that follows a tool result is re-rendered
    as a `finish` call so the model learns completion detection
  * records whose tool call does not validate against its own schema are dropped
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
from collections import Counter

# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------

FINISH_TOOL = {
    "type": "function",
    "function": {
        "name": "finish",
        "description": "Declare the task complete and report the outcome. Call only when the task is actually done.",
        "parameters": {
            "type": "object",
            "properties": {"summary": {"type": "string", "description": "One or two sentences describing what was achieved"}},
            "required": ["summary"],
        },
    },
}

ASK_USER_TOOL = {
    "type": "function",
    "function": {
        "name": "ask_user",
        "description": "Ask the user for information you cannot obtain with another tool.",
        "parameters": {
            "type": "object",
            "properties": {"question": {"type": "string", "description": "A single specific question"}},
            "required": ["question"],
        },
    },
}

TRAIN_SYSTEM_PROMPT = (
    "You are pc_agent, a local worker of a small agent fleet.\n"
    "Every action goes through one of the provided tools. Never claim an action happened unless a tool result confirms it.\n"
    "Emit exactly one tool call per turn, with arguments that match the schema exactly.\n"
    "Read the tool result before deciding the next call. If a result is an error, do not pretend it succeeded: "
    "fix the cause, retry once, or ask the user.\n"
    "When the task is complete, call finish with a summary of at most two sentences.\n"
    "If the request is ambiguous or needs a fact you cannot obtain with a tool, call ask_user instead of guessing."
)

HF_BASE = "https://huggingface.co"


# --------------------------------------------------------------------------
# download
# --------------------------------------------------------------------------


def fetch_sources(cache_dir="/root/.cache/smf-data", sources=None):
    """Download the raw dataset files. Returns {name: local_path}."""
    from huggingface_hub import hf_hub_download

    sources = sources or {
        "xlam": ("lockon/xlam-function-calling-60k", "xlam_function_calling_60k.json"),
        "hermes": ("NousResearch/hermes-function-calling-v1", "func-calling.json"),
    }
    out = {}
    for name, (repo, filename) in sources.items():
        out[name] = hf_hub_download(repo_id=repo, filename=filename, repo_type="dataset", cache_dir=cache_dir)
        print(f"fetched {name}: {out[name]} ({os.path.getsize(out[name])} bytes)")
    return out


# --------------------------------------------------------------------------
# rendering helpers
# --------------------------------------------------------------------------


def _render_value(v):
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, (int, float)):
        return repr(v) if isinstance(v, float) else str(v)
    return json.dumps(v, ensure_ascii=False)


def render_tool_call(name: str, arguments: dict) -> str:
    """LFM3 native tool-call serialisation."""
    body = [f"<tool_call>\n<function={name}>\n"]
    for k, v in (arguments or {}).items():
        body.append(f"<parameter={k}>\n{_render_value(v)}\n</parameter>\n")
    body.append("</function>\n</tool_call>")
    return "".join(body)


def render_tool_calls(calls) -> str:
    return "\n".join(render_tool_call(c.get("name"), c.get("arguments") or {}) for c in calls)


_TAG_CALL = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL)
_FN = re.compile(r"<function=([^>\s]+)>(.*?)</function>", re.DOTALL)
_PARAM = re.compile(r"<parameter=([^>\s]+)>(.*?)</parameter>", re.DOTALL)


def parse_calls_any(text: str):
    """Parse either the JSON or the XML tool-call form into [{'name','arguments'}]."""
    calls = []
    for block in _TAG_CALL.findall(text or ""):
        fn = _FN.search(block)
        if fn:
            args = {}
            for k, v in _PARAM.findall(fn.group(2)):
                v = v.strip()
                try:
                    args[k.strip()] = json.loads(v)
                except Exception:
                    args[k.strip()] = v
            calls.append({"name": fn.group(1).strip(), "arguments": args})
            continue
        try:
            obj = json.loads(block.strip())
        except Exception:
            continue
        if isinstance(obj, dict) and obj.get("name"):
            a = obj.get("arguments", {})
            if isinstance(a, str):
                try:
                    a = json.loads(a)
                except Exception:
                    a = {"_raw": a}
            calls.append({"name": obj["name"], "arguments": a if isinstance(a, dict) else {}})
    return calls


def _type_ok(spec, value):
    t = spec.get("type")
    if t == "string":
        return isinstance(value, str)
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "boolean":
        return isinstance(value, bool)
    if t == "array":
        return isinstance(value, list)
    if t == "object":
        return isinstance(value, dict)
    return True


def validate_call(tools_by_name, call) -> list[str]:
    """Return a list of validation problems (empty means valid)."""
    problems = []
    name = call.get("name")
    if name not in tools_by_name:
        return [f"unknown tool {name}"]
    params = tools_by_name[name].get("function", {}).get("parameters", {})
    props = params.get("properties", {})
    args = call.get("arguments") or {}
    if not isinstance(args, dict):
        return ["arguments not an object"]
    for req in params.get("required", []):
        if req not in args:
            problems.append(f"missing {req}")
    for k, v in args.items():
        if k in props and not _type_ok(props[k], v):
            problems.append(f"bad type {k}")
    return problems


# --------------------------------------------------------------------------
# source normalisers
# --------------------------------------------------------------------------


def _dedupe_key(rec) -> str:
    user = next((m["content"] for m in rec["messages"] if m["role"] == "user"), "")
    tools = ",".join(sorted(t["function"]["name"] for t in rec["tools"]))
    return hashlib.sha256((user.strip() + "|" + tools).encode()).hexdigest()


def normalise_xlam(path, limit=None, rng=None):
    """xLAM: single-turn, OpenAI tool list, expected calls, no observations."""
    raw = json.load(open(path, encoding="utf-8"))
    rng = rng or random.Random(0)
    if limit and len(raw) > limit:
        raw = rng.sample(raw, limit)
    out, stats = [], Counter()
    for row in raw:
        try:
            tools = json.loads(row["tools"]) if isinstance(row.get("tools"), str) else row.get("tools")
            answers = json.loads(row["answers"]) if isinstance(row.get("answers"), str) else row.get("answers")
            query = row.get("query") or ""
        except Exception:
            stats["dropped_malformed"] += 1
            continue
        if not tools or not answers or not query:
            stats["dropped_empty"] += 1
            continue
        tools = [dict(t, type="function") if "function" not in t else t for t in tools]
        calls = [{"name": a.get("name"), "arguments": a.get("arguments") or {}} for a in answers if isinstance(a, dict)]
        if not calls:
            stats["dropped_no_calls"] += 1
            continue
        by_name = {t["function"]["name"]: t for t in tools}
        problems = [p for c in calls for p in validate_call(by_name, c)]
        if problems:
            stats["dropped_invalid_args"] += 1
            continue
        if len(tools) < 1 or len(calls) > 4:
            stats["dropped_shape"] += 1
            continue
        tools = tools + [FINISH_TOOL, ASK_USER_TOOL]
        out.append({
            "source": "xlam",
            "tools": tools,
            "messages": [
                {"role": "system", "content": TRAIN_SYSTEM_PROMPT},
                {"role": "user", "content": query},
                {"role": "assistant", "content": render_tool_calls(calls)},
            ],
            "meta": {"n_tools": len(tools), "n_calls": len(calls)},
        })
        stats["kept"] += 1
    return out, stats


def normalise_hermes(path, limit=None, rng=None):
    """Hermes FC: multi-turn with real observations and a closing text answer."""
    raw = json.load(open(path, encoding="utf-8"))
    rng = rng or random.Random(0)
    if limit and len(raw) > limit:
        raw = rng.sample(raw, limit)
    out, stats = [], Counter()
    for row in raw:
        try:
            tools = json.loads(row["tools"]) if isinstance(row.get("tools"), str) else row.get("tools")
        except Exception:
            stats["dropped_malformed_tools"] += 1
            continue
        tools = [dict(t, type="function") if "function" not in t else t for t in tools or []]
        if not tools:
            stats["dropped_no_tools"] += 1
            continue
        conv = row.get("conversations") or []
        # strip the dataset's own system turn, we use ours
        conv = [c for c in conv if c.get("from") != "system"]
        msgs, n_calls, n_obs = [], 0, 0
        for c in conv:
            frm, val = c.get("from"), (c.get("value") or "").strip()
            if frm == "human":
                msgs.append({"role": "user", "content": val})
            elif frm == "gpt":
                calls = parse_calls_any(val)
                if calls:
                    n_calls += len(calls)
                    msgs.append({"role": "assistant", "content": render_tool_calls(calls)})
                else:
                    if n_calls and msgs and msgs[-1]["role"] == "tool":
                        # closing answer after observations -> teach completion detection
                        msgs.append({"role": "assistant", "content": render_tool_call("finish", {"summary": val[:400]})})
                    else:
                        msgs.append({"role": "assistant", "content": val})
            elif frm == "tool":
                n_obs += 1
                msgs.append({"role": "tool", "content": val})
        if not msgs or msgs[0]["role"] != "user":
            stats["dropped_shape"] += 1
            continue
        if not any(m["role"] == "assistant" for m in msgs):
            stats["dropped_no_assistant"] += 1
            continue
        # a refusal / no-call answer is a useful negative example only if it is the last turn
        if n_calls == 0 and len(msgs) > 2:
            stats["dropped_no_calls_midconv"] += 1
            continue
        by_name = {t["function"]["name"]: t for t in tools}
        bad = False
        for m in msgs:
            if m["role"] == "assistant":
                for c in parse_calls_any(m["content"]):
                    if c["name"] in ("finish", "ask_user"):
                        continue
                    if validate_call(by_name, c):
                        bad = True
        if bad:
            stats["dropped_invalid_args"] += 1
            continue
        tools = tools + [FINISH_TOOL, ASK_USER_TOOL]
        out.append({
            "source": "hermes_func_calling",
            "tools": tools,
            "messages": [{"role": "system", "content": TRAIN_SYSTEM_PROMPT}] + msgs,
            "meta": {"n_calls": n_calls, "n_observations": n_obs},
        })
        stats["kept"] += 1
    return out, stats


# --------------------------------------------------------------------------
# assembled dataset
# --------------------------------------------------------------------------


def build_dataset(sources, xlam_limit=2600, hermes_limit=None, seed=7):
    rng = random.Random(seed)
    records, stats = [], {}
    if "xlam" in sources:
        recs, st = normalise_xlam(sources["xlam"], limit=xlam_limit, rng=rng)
        records += recs
        stats["xlam"] = dict(st)
    if "hermes" in sources:
        recs, st = normalise_hermes(sources["hermes"], limit=hermes_limit, rng=rng)
        records += recs
        stats["hermes"] = dict(st)
    # global de-duplication
    seen, deduped = set(), []
    for r in records:
        k = _dedupe_key(r)
        if k in seen:
            continue
        seen.add(k)
        deduped.append(r)
    stats["duplicates_removed"] = len(records) - len(deduped)

    # deterministic split, stratified by source
    by_src = {}
    for r in deduped:
        by_src.setdefault(r["source"], []).append(r)
    train, val = [], []
    for _src, rows in sorted(by_src.items()):
        rng.shuffle(rows)
        n_val = max(1, int(0.06 * len(rows)))
        val += rows[:n_val]
        train += rows[n_val:]
    rng.shuffle(train)
    rng.shuffle(val)
    return train, val, stats


def to_prompt_completion(rec, tokenizer, max_prompt_tokens=2560, max_completion_tokens=512):
    """Render one record into a masked (input_ids, labels) pair using the model's own template."""
    msgs = rec["messages"]
    prompt_msgs = msgs[:-1]
    completion_text = msgs[-1]["content"]
    try:
        prompt_text = tokenizer.apply_chat_template(
            prompt_msgs, tools=rec["tools"], tokenize=False, add_generation_prompt=True, enable_thinking=False
        )
    except TypeError:
        prompt_text = tokenizer.apply_chat_template(
            prompt_msgs, tools=rec["tools"], tokenize=False, add_generation_prompt=True
        )
    prompt_ids = tokenizer(prompt_text, add_special_tokens=False)["input_ids"]
    completion_ids = tokenizer(completion_text, add_special_tokens=False)["input_ids"]
    completion_ids = completion_ids + [tokenizer.eos_token_id]
    if len(prompt_ids) > max_prompt_tokens:
        return None, "prompt_too_long"
    if len(completion_ids) > max_completion_tokens:
        return None, "completion_too_long"
    input_ids = prompt_ids + completion_ids
    labels = [-100] * len(prompt_ids) + list(completion_ids)
    return {"input_ids": input_ids, "labels": labels, "source": rec["source"]}, "ok"


def prepare_tokenised(records, tokenizer, **kw):
    out, stats = [], Counter()
    for r in records:
        ex, why = to_prompt_completion(r, tokenizer, **kw)
        stats[why] += 1
        if ex:
            out.append(ex)
    return out, stats


def check_bench_overlap(records, bench_root=None, ngram=6, threshold=0.6):
    """Lexical leakage check between training prompts and the held-out bench.

    Reports, for every bench prompt, the highest fraction of its word n-grams that
    also appear in any training user message. Anything above `threshold` is a leak
    signal and is printed loudly.
    """

    bench_root = bench_root or os.environ.get("SMF_BENCH_ROOT", "/kaggle/input/smf-bench")
    bench_texts = []
    for kind in ("planner", "specialist"):
        path = os.path.join(bench_root, "bench", kind, "cases.json")
        if not os.path.exists(path):
            continue
        for case in json.load(open(path, encoding="utf-8"))["cases"]:
            if case.get("input"):
                bench_texts.append((f"{case['id']}:input", case["input"]))
            for t in case.get("turns", []):
                if t.get("user"):
                    bench_texts.append((f"{case['id']}:user", t["user"]))
            for m in case.get("context", []) or []:
                if m.get("role") == "user":
                    bench_texts.append((f"{case['id']}:ctx", m["content"]))

    def grams(text):
        words = re.findall(r"[a-z0-9]+", text.lower())
        return {" ".join(words[i:i + ngram]) for i in range(max(0, len(words) - ngram + 1))}

    train_grams = set()
    for r in records:
        for m in r["messages"]:
            if m["role"] == "user":
                train_grams |= grams(m["content"])

    findings = []
    for name, text in bench_texts:
        g = grams(text)
        if not g:
            continue
        overlap = len(g & train_grams) / len(g)
        if overlap >= threshold:
            findings.append({"bench_item": name, "overlap": round(overlap, 3), "text": text[:200]})
    findings.sort(key=lambda x: -x["overlap"])
    return {
        "bench_prompts_checked": len(bench_texts),
        "train_user_ngrams": len(train_grams),
        "n_over_threshold": len(findings),
        "threshold": threshold,
        "findings": findings[:20],
    }


def manifest(train, val, stats, sources_meta):
    def h(rows):
        m = hashlib.sha256()
        for r in rows:
            m.update(_dedupe_key(r).encode())
        return m.hexdigest()

    return {
        "n_train": len(train),
        "n_val": len(val),
        "source_counts_train": dict(Counter(r["source"] for r in train)),
        "source_counts_val": dict(Counter(r["source"] for r in val)),
        "split_hash_train": h(train),
        "split_hash_val": h(val),
        "prep_stats": stats,
        "sources": sources_meta,
        "seed": 7,
        "transformations": [
            "assistant tool calls re-rendered into LFM3 native XML <tool_call><function=...> form",
            "finish and ask_user appended to every tool list",
            "trailing assistant answer after a tool result re-rendered as a finish call",
            "records with schema-invalid tool arguments dropped",
            "conversation-level de-duplication on (user text, tool-name set)",
            "prompt tokens masked with -100; loss on completion only",
        ],
    }
