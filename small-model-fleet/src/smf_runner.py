"""Suite drivers shared by every evaluation kernel.

Keeping the drivers here (instead of in each notebook) guarantees that the
baseline run, the pilot run and the post-SFT run execute byte-identical logic.
"""

from __future__ import annotations

import json
import os
import traceback

import smf_bench as sb

BENCH_ROOT = os.environ.get("SMF_BENCH_ROOT", "/kaggle/input/smf-bench")


def load_suite(kind: str):
    base = os.path.join(BENCH_ROOT, "bench", kind)
    with open(os.path.join(base, "cases.json"), encoding="utf-8") as f:
        cases = json.load(f)
    with open(os.path.join(base, "system_prompt.txt"), encoding="utf-8") as f:
        system_prompt = f.read()
    tools = None
    if kind == "specialist":
        with open(os.path.join(base, "tools.json"), encoding="utf-8") as f:
            tools = json.load(f)
    return cases, system_prompt, tools


def probe_tool_role(tokenizer) -> bool:
    """Can this chat template render a native `tool` role message?"""
    msgs = [
        {"role": "user", "content": "list files"},
        {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "fs_list", "arguments": {"path": "D:\\workspace"}}}]},
        {"role": "tool", "content": "{\"ok\": true, \"entries\": [\"a.txt\"]}"},
        {"role": "user", "content": "how many?"},
    ]
    try:
        tokenizer.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
        return True
    except Exception:
        return False


def probe_tools_kwarg(tokenizer) -> bool:
    msgs = [{"role": "user", "content": "hi"}]
    tools = [{"type": "function", "function": {"name": "fs_list", "description": "list", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]
    try:
        tokenizer.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True, tools=tools)
        return True
    except Exception:
        return False


def _tools_as_text(tools) -> str:
    lines = ["You may call these tools:"]
    for t in tools:
        fn = t.get("function", t)
        lines.append(json.dumps(fn, ensure_ascii=False, sort_keys=True))
    lines.append("To call a tool, emit the call in the tool-call format you were trained on, one call per turn.")
    return "\n".join(lines)


def run_specialist_suite(gen, cases, cases_meta, system_prompt, tools, tools_kwarg=True, native_tool_role=True, max_new_tokens=512):
    """Execute the specialist suite against a Generator. Returns (records, per_case)."""
    tool_by_name = {t["function"]["name"]: t for t in tools}
    sp = system_prompt
    if not tools_kwarg:
        sp = sp + "\n\n" + _tools_as_text(tools)

    records, per_case = [], []
    for case in cases_meta:
        messages = [{"role": "system", "content": sp}]
        case_steps = []
        for step_idx, step in enumerate(case["turns"]):
            if "user" in step:
                messages.append({"role": "user", "content": step["user"]})
            if "tool_result" in step:
                payload = step["tool_result"]["content"]
                if native_tool_role:
                    messages.append({"role": "tool", "content": payload, "name": step["tool_result"]["name"]})
                else:
                    messages.append({"role": "user", "content": f"TOOL_RESULT ({step['tool_result']['name']}): {payload}"})

            expect = step["expect"]
            try:
                raw, rec = gen.chat(messages, max_new_tokens=max_new_tokens, tools=tools if tools_kwarg else None)
            except Exception:  # generation failure must not abort the suite
                raw, rec = "", {"latency_s": None, "completion_tokens": 0, "tokens_per_s": None, "peak_vram_gb": None}
                traceback.print_exc()

            calls = sb.parse_tool_calls(raw)
            res = sb.check_expectations(expect, calls, raw)

            called_tools = [c["name"] for c in calls]
            tool_ok = True
            for key in ("tool", "tool_in", "no_tool_call"):
                if key in expect:
                    tool_ok = res["checks"].get(key, False)
                    break
            if "must_not_tool" in expect:
                tool_ok = tool_ok and res["checks"].get("must_not_tool", False)

            args_schema_ok, arg_errors = True, []
            if calls:
                first = calls[0]
                if first["name"] in tool_by_name:
                    args_schema_ok, arg_errors = sb.validate_arguments(tool_by_name[first["name"]], first["arguments"])
                else:
                    args_schema_ok = False
                    arg_errors = [f"unknown tool {first['name']}"]
            elif "no_tool_call" not in expect:
                args_schema_ok = False
                arg_errors = ["no tool call emitted"]

            if "no_tool_call" in expect:
                parse_ok = len(calls) == 0
            else:
                parse_ok = len(calls) > 0

            record = {
                "case_id": case["id"],
                "category": case["category"],
                "step": step_idx,
                "raw": raw,
                "reasoning_stripped": sb.strip_reasoning(raw)[:4000],
                "parsed_calls": calls,
                "passed": res["passed"],
                "checks": res["checks"],
                "failures": res["failures"],
                "tool_ok": tool_ok,
                "args_schema_ok": args_schema_ok,
                "arg_errors": arg_errors,
                "parse_ok": parse_ok,
                "called_tools": called_tools,
                "forbidden_tools": expect.get("must_not_tool", []),
                **rec,
            }
            records.append(record)
            case_steps.append(record)

            # keep the transcript realistic: replay the model's own answer
            assistant_msg = {"role": "assistant", "content": raw}
            messages.append(assistant_msg)

        per_case.append({
            "case_id": case["id"],
            "category": case["category"],
            "passed": all(s["passed"] for s in case_steps),
            "steps_passed": sum(1 for s in case_steps if s["passed"]),
            "steps": len(case_steps),
        })
    return records, per_case


def run_planner_suite(gen, cases, cases_meta, system_prompt, max_new_tokens=4096):
    records, per_case = [], []
    for case in cases_meta:
        messages = [{"role": "system", "content": system_prompt}]
        for turn in case.get("context", []) or []:
            messages.append(dict(turn))
        if case.get("input"):
            messages.append({"role": "user", "content": case["input"]})
        try:
            raw, rec = gen.chat(messages, max_new_tokens=max_new_tokens)
        except Exception:
            raw, rec = "", {"latency_s": None, "completion_tokens": 0, "tokens_per_s": None, "peak_vram_gb": None}
            traceback.print_exc()

        answer = sb.strip_reasoning(raw)
        obj, status = sb.extract_json_object(answer)
        res = sb.check_planner(case["expect"], obj, answer)

        record = {
            "case_id": case["id"],
            "category": case["category"],
            "raw": raw,
            "answer": answer[:4000],
            "parsed": obj,
            "parse_status": status,
            "passed": res["passed"],
            "checks": res["checks"],
            "failures": res["failures"],
            "tool_ok": res["checks"].get("decision_in", res["checks"].get("decision", False)),
            "args_schema_ok": res["checks"].get("json_valid", False),
            "parse_ok": status == "ok",
            "called_tools": [],
            "forbidden_tools": [],
            **rec,
        }
        records.append(record)
        per_case.append({"case_id": case["id"], "category": case["category"], "passed": res["passed"]})
    return records, per_case


def category_breakdown(per_case) -> dict:
    out = {}
    for c in per_case:
        d = out.setdefault(c["category"], {"passed": 0, "total": 0})
        d["total"] += 1
        d["passed"] += int(c["passed"])
    return {k: {"passed": v["passed"], "total": v["total"], "rate": round(v["passed"] / v["total"], 3)} for k, v in sorted(out.items())}


def suite_metrics(records, per_case) -> dict:
    base = sb.summarise(records)
    base["cases"] = len(per_case)
    base["case_success_rate"] = round(sum(1 for c in per_case if c["passed"]) / len(per_case), 4) if per_case else None
    base["category_breakdown"] = category_breakdown(per_case)
    # recovery-oriented subset
    recovery_cats = {"recovery", "failure_detection", "arg_correction", "evaluate_output", "incomplete_detection", "retry"}
    rec = [c for c in per_case if c["category"] in recovery_cats]
    base["recovery_success_rate"] = round(sum(1 for c in rec if c["passed"]) / len(rec), 4) if rec else None
    return base
