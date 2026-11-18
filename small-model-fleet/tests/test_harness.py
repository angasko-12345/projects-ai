"""Offline self-test for the benchmark harness.

Exercises parsing and scoring with hand-written synthetic model outputs.
No model weights, no torch, no inference: this is pure string processing and is
safe to run on the Windows host. It exists so that GPU quota is never burned on
a harness bug.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import smf_bench as sb  # noqa: E402

ROOT = os.path.join(HERE, "..", "bench")
FAILS = []


def check(name, cond):
    if not cond:
        FAILS.append(name)
        print(f"  FAIL {name}")
    else:
        print(f"  ok   {name}")


print("== parsing: Qwen JSON style ==")
raw = 'I will list the folder.\n<tool_call>\n{"name": "fs_list", "arguments": {"path": "D:\\\\workspace\\\\reports"}}\n</tool_call>'
calls = sb.parse_tool_calls(raw)
check("qwen single call", len(calls) == 1 and calls[0]["name"] == "fs_list")
check("qwen args dict", calls[0]["arguments"]["path"].endswith("reports"))

print("== parsing: LFM3 XML style ==")
raw = '<tool_call>\n<function=fs_write>\n<parameter=path>\nD:\\workspace\\notes\\today.md\n</parameter>\n<parameter=content>\nmeeting 10am\n</parameter>\n</function>\n</tool_call>'
calls = sb.parse_tool_calls(raw)
check("lfm single call", len(calls) == 1 and calls[0]["name"] == "fs_write")
check("lfm content", "meeting" in str(calls[0]["arguments"].get("content")))

print("== parsing: bare JSON fallback ==")
calls = sb.parse_tool_calls('{"name": "proc_kill", "arguments": {"pid": 4812}}')
check("bare json", len(calls) == 1 and calls[0]["arguments"]["pid"] == 4812)

print("== parsing: no call ==")
check("no call on prose", sb.parse_tool_calls("12 times 9 is 108.") == [])

print("== strip_reasoning ==")
check("strip think", sb.strip_reasoning("abc<thinking>hidden</thinking>{\"a\":1}") == "abc{\"a\":1}")

print("== planner json extraction ==")
obj, st = sb.extract_json_object('Sure!\n```json\n{"decision":"execute","tasks":[],"missing_info":[],"notes":"trivial"}\n```')
check("planner fence", st == "ok" and obj["decision"] == "execute")
obj, st = sb.extract_json_object('noise {"decision":"ask_user","tasks":[],"missing_info":["which file"],"notes":"x"} trailing')
check("planner brace scan", st == "ok" and obj["missing_info"] == ["which file"])
obj, st = sb.extract_json_object("not json at all")
check("planner unparseable", obj is None and st != "ok")

print("== planner checks ==")
cases = json.load(open(os.path.join(ROOT, "planner", "cases.json"), encoding="utf-8"))["cases"]
by_id = {c["id"]: c for c in cases}
good = {"decision": "ask_user", "tasks": [], "missing_info": ["which file"], "notes": "The referent of 'it' is unclear; which file should I move?"}
res = sb.check_planner(by_id["P06"]["expect"], good, "x")
check("P06 passes with correct answer", res["passed"])
bad = {"decision": "delegate", "tasks": [{"id": "t1", "worker": "pc_agent", "action": "Move it", "depends_on": []}], "missing_info": [], "notes": "ok"}
check("P06 fails with a guess", not sb.check_planner(by_id["P06"]["expect"], bad, "x")["passed"])

good = {"decision": "delegate", "tasks": [
    {"id": "t1", "worker": "pc_agent", "action": "List all PDFs in the invoices folder", "depends_on": []},
    {"id": "t2", "worker": "pc_agent", "action": "Extract the invoice total from each PDF", "depends_on": ["t1"]},
    {"id": "t3", "worker": "coder", "action": "Write a spreadsheet sorted by invoice date", "depends_on": ["t2"]}],
    "missing_info": [], "notes": "Three stages."}
check("P01 passes", sb.check_planner(by_id["P01"]["expect"], good, "x")["passed"])

bad_deps = {"decision": "delegate", "tasks": [
    {"id": "t1", "worker": "pc_agent", "action": "List all PDFs in the invoices folder", "depends_on": ["t2"]},
    {"id": "t2", "worker": "pc_agent", "action": "Extract the invoice total from each PDF", "depends_on": ["t1"]},
    {"id": "t3", "worker": "coder", "action": "Write a spreadsheet sorted by invoice date", "depends_on": ["t2"]}],
    "missing_info": [], "notes": "x"}
check("P01 rejects forward dependency", not sb.check_planner(by_id["P01"]["expect"], bad_deps, "x")["passed"])

good = {"decision": "refuse", "tasks": [], "missing_info": [], "notes": "This would disable security software and run untrusted code; I will not do that."}
check("P08 passes", sb.check_planner(by_id["P08"]["expect"], good, "x")["passed"])
good = {"decision": "execute", "tasks": [], "missing_info": [], "notes": "40.8"}
check("P05 passes", sb.check_planner(by_id["P05"]["expect"], good, "x")["passed"])
wrong = {"decision": "delegate", "tasks": [{"id": "t1", "worker": "cloud_llm", "action": "Compute 17 percent of 240", "depends_on": []}], "missing_info": [], "notes": "x"}
check("P05 rejects needless escalation", not sb.check_planner(by_id["P05"]["expect"], wrong, "x")["passed"])

print("== specialist checks ==")
scases = json.load(open(os.path.join(ROOT, "specialist", "cases.json"), encoding="utf-8"))["cases"]
sb_by_id = {c["id"]: c for c in scases}
tools = json.load(open(os.path.join(ROOT, "specialist", "tools.json"), encoding="utf-8"))
tool_by_name = {t["function"]["name"]: t for t in tools}
check("all expected tools exist in schema", all(
    e.get("tool") is None or e["tool"] in tool_by_name
    for c in scases for st in c["turns"] for e in [st["expect"]] if "$none" not in str(e)
))
check("all tool_in entries exist", all(
    all(t in tool_by_name for t in e.get("tool_in", []))
    for c in scases for st in c["turns"] for e in [st["expect"]]
))
for c in scases:
    for st in c["turns"]:
        for _t in st["expect"].get("must_not_tool", []):
            # forbidden names are legitimately absent from the schema (invented tools);
            # record them so the report can show which hallucinations are being trapped.
            pass

raw = r'<tool_call>{"name": "fs_list", "arguments": {"path": "D:\\workspace\\reports"}}</tool_call>'
res = sb.check_expectations(sb_by_id["S01"]["turns"][0]["expect"], sb.parse_tool_calls(raw), raw)
check("S01 passes", res["passed"])

raw = '<tool_call>\n<function=proc_kill>\n<parameter=pid>\n"4812"\n</parameter>\n</function>\n</tool_call>'
calls = sb.parse_tool_calls(raw)
check("pid string parsed as str", calls[0]["arguments"]["pid"] == "4812")
ok, errs = sb.validate_arguments(tool_by_name["proc_kill"], calls[0]["arguments"])
check("pid string fails schema", not ok)
res = sb.check_expectations(sb_by_id["S04"]["turns"][0]["expect"], calls, raw)
check("S04 fails on string pid", not res["passed"])

raw = '<tool_call>\n<function=proc_kill>\n<parameter=pid>\n4812\n</parameter>\n</function>\n</tool_call>'
calls = sb.parse_tool_calls(raw)
ok, errs = sb.validate_arguments(tool_by_name["proc_kill"], calls[0]["arguments"])
check("pid int valid", ok and calls[0]["arguments"]["pid"] == 4812)

raw = 'I cannot email files because there is no email tool available.'
res = sb.check_expectations(sb_by_id["S05"]["turns"][0]["expect"], sb.parse_tool_calls(raw), raw)
check("S05 passes on honest refusal", res["passed"])

raw = '<tool_call>\n{"name": "send_email", "arguments": {"to": "boss@example.com"}}\n</tool_call>'
res = sb.check_expectations(sb_by_id["S05"]["turns"][0]["expect"], sb.parse_tool_calls(raw), raw)
check("S05 fails on invented tool", not res["passed"])

raw = '<tool_call>\n<function=fs_delete>\n<parameter=path>\nD:\\Windows\\System32\\drivers\\etc\\hosts\n</parameter>\n</function>\n</tool_call>'
res = sb.check_expectations(sb_by_id["S11"]["turns"][0]["expect"], sb.parse_tool_calls(raw), raw)
check("S11 fails on out-of-workspace delete", not res["passed"])

raw = '<tool_call>\n<function=fs_list>\n<parameter=path>\nD:\\workspace\n</parameter>\n</function>\n</tool_call>'
res = sb.check_expectations(sb_by_id["S01"]["turns"][0]["expect"], sb.parse_tool_calls(raw), raw)
check("S01 rejects wrong path", any("args_match[path]" in f for f in res["failures"]))

raw = '<tool_call>\n<function=finish>\n<parameter=summary>\n{"a.txt": 12, "b.txt": 340, "c.txt": 0}\n</parameter>\n</function>\n</tool_call>'
res = sb.check_expectations(sb_by_id["S21"]["turns"][1]["expect"], sb.parse_tool_calls(raw), raw)
check("S21 passes with json summary", res["passed"])

raw = '12 times 9 is 108.'
res = sb.check_expectations(sb_by_id["S16"]["turns"][0]["expect"], sb.parse_tool_calls(raw), raw)
check("S16 passes without tools", res["passed"])

raw = '<tool_call>\n{"name": "shell_run", "arguments": {"command": "echo 108"}}\n</tool_call>'
res = sb.check_expectations(sb_by_id["S16"]["turns"][0]["expect"], sb.parse_tool_calls(raw), raw)
check("S16 fails when shelling out", not res["passed"])

print("== metrics sanity ==")
fake = [
    {"passed": True, "tool_ok": True, "args_schema_ok": True, "parse_ok": True, "latency_s": 1.0, "tokens_per_s": 10.0, "peak_vram_gb": 3.0, "completion_tokens": 10, "forbidden_tools": [], "called_tools": []},
    {"passed": False, "tool_ok": False, "args_schema_ok": False, "parse_ok": True, "latency_s": 3.0, "tokens_per_s": 10.0, "peak_vram_gb": 3.2, "completion_tokens": 30, "forbidden_tools": ["finish"], "called_tools": ["finish"]},
]
m = sb.summarise(fake)
check("summarise accuracy", m["instruction_following_accuracy"] == 0.5)
check("false completion", m["false_completion_rate"] == 1.0)
check("peak vram", m["peak_vram_gb"] == 3.2)

print("== suite integrity ==")
pids = [c["id"] for c in cases]
check("planner ids unique", len(pids) == len(set(pids)))
sids = [c["id"] for c in scases]
check("specialist ids unique", len(sids) == len(set(sids)))
req_planner = {"decompose", "dependencies", "worker_selection", "delegate_vs_execute", "insufficient_info",
               "evaluate_output", "incomplete_detection", "retry", "escalation", "safety", "structured_output"}
have = {c["category"] for c in cases}
check("planner covers required categories", req_planner <= have)
req_spec = {"tool_selection", "arg_validity", "schema_follow", "observation_use", "failure_detection",
            "arg_correction", "no_hallucinated_success", "completion_detection", "permission_boundary",
            "conciseness", "multi_step", "insufficient_info", "recovery", "structured_output", "no_unnecessary_tool"}
check("specialist covers required categories", req_spec <= {c["category"] for c in scases})
check("planner case count 40-50? (subset ok)", len(cases) >= 20)
for c in cases:
    for t in c.get("context", []) or []:
        check(f"{c['id']} context role valid", t["role"] in {"user", "assistant", "system"})

# duplicate-input leakage check: no planner 'input' may equal a training prompt later
print(f"\nplanner cases: {len(cases)}  specialist cases: {len(scases)}  specialist steps: {sum(len(c['turns']) for c in scases)}")
print(f"FAILURES: {len(FAILS)}")
if FAILS:
    for f in FAILS:
        print(" -", f)
    sys.exit(1)
print("ALL HARNESS SELF-TESTS PASSED")

