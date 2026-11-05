"""CLI: `python -m histyt <command>`. Exit codes 0 ok, 2 gate/reject, 1 error."""
import argparse
import json
import sys

from . import commons, pipeline
from . import qa as qa_mod
from . import scenes as scenes_mod
from . import shorts as shorts_mod
from . import state as state_mod
from . import topics, upload
from .assemble import assemble_stage
from .config import get_runs_dir, load_config
from .doctor import doctor, format_doctor
from .research import (SOURCE_TIERS, add_source, extract_text, fetch_url,
                       fetch_wikipedia)
from .script_stage import format_review, generate_script
from .thumbnail import make_thumbnail
from .variation import format_variation, variation_check_stage
from .visuals import visuals_stage
from .voice_stage import voice_stage


def _parser():
    p = argparse.ArgumentParser(prog="histyt", description="History video pipeline")
    p.add_argument("--config", default=None, help="path to channel.yaml")
    p.add_argument("--runs", default=None, help="runs directory")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor")

    st = sub.add_parser("status")
    st.add_argument("id", nargs="?")
    st.add_argument("--json", action="store_true", help="emit JSON instead of table")

    tl = sub.add_parser("topic-list")
    tl.add_argument("--json", action="store_true", help="emit JSON instead of table")

    ta = sub.add_parser("topic-add")
    ta.add_argument("title")
    ta.add_argument("--angle", default="")

    tc = sub.add_parser("topic-import-csv")
    tc.add_argument("path")

    r = sub.add_parser("research")
    r.add_argument("id")
    r.add_argument("--url", default=None)
    r.add_argument("--title", default=None)
    r.add_argument("--note", default=None)
    r.add_argument("--tier", choices=SOURCE_TIERS, default="secondary",
                   help="source evidence tier (default: secondary)")
    r.add_argument("--fetch-wikipedia", default=None)
    r.add_argument("--fetch-url", default=None)

    for name in ("script", "voice", "visuals", "assemble", "thumbnail",
                 "variation-check", "run", "scenes"):
        sp = sub.add_parser(name)
        sp.add_argument("id")

    qa_parser = sub.add_parser("qa")
    qa_parser.add_argument("id")

    vf = sub.add_parser("visuals-fetch")
    vf.add_argument("id")
    vf.add_argument("--query", required=True)
    vf.add_argument("--limit", type=int, default=4)

    up = sub.add_parser("upload")
    up.add_argument("id")
    up.add_argument("--mock", action="store_true",
                    help="use the in-memory mock client (no network, no credentials)")

    sp_plan = sub.add_parser("shorts-plan")
    sp_plan.add_argument("id")
    sp_plan.add_argument("--max-clips", type=int, default=None,
                         help="override shorts.max_clips from config")

    sp_ext = sub.add_parser("shorts-extract")
    sp_ext.add_argument("id")

    asg = sub.add_parser("approve-script")
    asg.add_argument("id")
    ar = sub.add_parser("approve-render")
    ar.add_argument("id")
    return p


def _script_meta(runs, video_id):
    import os
    path = os.path.join(runs, video_id, "script_meta.json")
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        cfg = load_config(args.config) if args.command != "doctor" or args.config else load_config()
        runs = get_runs_dir(cfg, args.runs)
        if args.command == "doctor":
            print(format_doctor(doctor(cfg, runs)))
            return 0
        if args.command == "status":
            rows = pipeline.status(runs, args.id)
            if args.json:
                print(json.dumps(rows, indent=2, ensure_ascii=False))
            else:
                rows_list = rows if isinstance(rows, list) else [rows]
                if not rows_list:
                    print("(no videos)")
                else:
                    gate_stages = ("awaiting_script_approval", "awaiting_render_approval")
                    print(f"{'VIDEO':<42}{'STAGE':<28}{'THUMB':<20}{'SEED':>8}")
                    for r in rows_list:
                        vid = r.get("video_id", "?")
                        stage = r.get("stage", "?")
                        flag = " [GATE]" if stage in gate_stages else ""
                        thumb = r.get("thumbnail_layout") or "-"
                        seed = r.get("thumbnail_seed")
                        print(f"{vid:<42}{stage + flag:<28}{thumb:<20}{str(seed if seed is not None else '-'):>8}")
            return 0
        if args.command == "topic-list":
            rows = topics.list_topics(runs)
            if args.json:
                print(json.dumps(rows, indent=2, ensure_ascii=False))
            else:
                for t in rows:
                    print(f"{t.get('video_id','?')}\t{t.get('title','')}\t{t.get('angle','')}")
            return 0
        if args.command == "topic-add":
            vid = topics.add_topic(runs, args.title, args.angle)
            print(f"added topic: {vid}")
            return 0
        if args.command == "topic-import-csv":
            print(f"imported {topics.import_csv(runs, args.path)} topics")
            return 0
        if args.command == "research":
            vid = args.id
            if args.fetch_wikipedia:
                page = fetch_wikipedia(args.fetch_wikipedia)
                title = page["title"]
                url = "https://en.wikipedia.org/wiki/" + title.replace(" ", "_")
                sid = add_source(runs, vid, url, title, "",
                                 tier=args.tier, text=page["extract"])
                print(f"fetched wikipedia -> {sid} ({len(page['extract'])} chars, tier {args.tier})")
            if args.fetch_url:
                html = fetch_url(args.fetch_url)
                import re as _re
                m = _re.search(r"<title[^>]*>(.*?)</title>", html, _re.I | _re.S)
                title = m.group(1).strip() if m else args.fetch_url
                text = extract_text(html)
                sid = add_source(runs, vid, args.fetch_url, title, "",
                                 tier=args.tier, text=text)
                print(f"fetched url -> {sid} ({len(text)} chars, tier {args.tier})")
            if args.url:
                sid = add_source(runs, vid, args.url, args.title or args.url,
                                 args.note or "", tier=args.tier)
                print(f"added source {sid} (tier {args.tier})")
            return 0
        if args.command == "script":
            meta = generate_script(runs, args.id, cfg)
            print(f"script generated ({meta.get('template')}); awaiting approval")
            print(format_review(meta))
            return 0
        if args.command == "approve-script":
            meta = _script_meta(runs, args.id)
            if meta:
                print(format_review(meta))
            pipeline.approve_script(runs, args.id)
            print("script approved")
            return 0
        if args.command == "voice":
            print(f"voice: {len(voice_stage(runs, args.id, cfg))} chunks")
            return 0
        if args.command == "scenes":
            manifest = scenes_mod.build_manifest(runs, args.id, cfg)
            print(f"scenes: {len(manifest['scenes'])} scene(s), "
                  f"{manifest['total_duration']:.1f}s -> scene_manifest.json")
            return 0
        if args.command == "visuals":
            print(f"visuals: {len(visuals_stage(runs, args.id, cfg))} assets")
            return 0
        if args.command == "visuals-fetch":
            added = commons.fetch_images(runs, args.id, args.query, limit=args.limit)
            for asset in added:
                print(f"added {asset['path']} [{asset['license']}]")
            return 0
        if args.command == "assemble":
            print(f"assembled -> {assemble_stage(runs, args.id, cfg)['final']}")
            return 0
        if args.command == "thumbnail":
            print(f"thumbnail: {make_thumbnail(runs, args.id)}")
            return 0
        if args.command == "variation-check":
            rows, passed = variation_check_stage(runs, args.id, cfg)
            print(format_variation(rows, passed))
            return 0
        if args.command == "qa":
            report = qa_mod.qa_stage(runs, args.id, cfg)
            print(qa_mod.format_report(report))
            return 2 if report.get("verdict") == "reject" else 0
        if args.command == "shorts-plan":
            plan = shorts_mod.plan_shorts(runs, args.id, cfg,
                                          max_clips=args.max_clips)
            print(f"shorts plan: {len(plan['clips'])} clip(s) "
                  f"(relaxed={plan['relaxed_min_seconds']}) -> shorts/shorts_plan.json")
            for c in plan["clips"]:
                print(f"  #{c['index']} {c['start']:.1f}s-{c['end']:.1f}s "
                      f"({c['duration']:.1f}s) score={c['score']} hook={bool(c['hook'])}")
            return 0
        if args.command == "shorts-extract":
            manifest = shorts_mod.extract_shorts(runs, args.id, cfg)
            print(f"extracted {len(manifest['shorts'])} landscape short(s) "
                  "-> shorts/ (manifest: shorts/shorts_manifest.json)")
            return 0
        if args.command == "approve-render":
            pipeline.approve_render(runs, args.id)
            print("render approved")
            return 0
        if args.command == "upload":
            if args.mock:
                client = upload.MockYouTubeClient(video_id=f"mock-{args.id}")
                record = upload.upload_stage(runs, args.id, cfg, client=client)
                print(f"uploaded (mock): {record}")
            else:
                print(f"uploaded: {upload.upload_stage(runs, args.id, cfg)}")
            return 0
        if args.command == "run":
            state = pipeline.run_until_gate(runs, args.id, cfg)
            report = qa_mod.load_report(runs, args.id)
            if report:
                print(f"QA verdict: {report.get('verdict')} "
                      f"(see qa_report.json)")
            print(f"stopped at stage: {state.get('stage')}")
            gate_stages = ("awaiting_script_approval", "awaiting_render_approval")
            if state.get("stage") in gate_stages:
                return 2
            if report and report.get("verdict") == "reject":
                return 2
            return 0
        return 1
    except state_mod.GateError as exc:
        print(f"GATE: {exc}", file=sys.stderr)
        return 2
    except (state_mod.PipelineError, Exception) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
