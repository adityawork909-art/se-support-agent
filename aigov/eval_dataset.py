#!/usr/bin/env python3
"""AI-GOV eval dataset builder: real runs become a versioned regression suite.

    build     read this build's traces and write the agent runs as candidate cases
    promote   add the candidates to the suite; the version goes up only when the content changes
    run       re-run the suite against the agent and write evals/results.json for the build gate
    export    write the suite in DeepEval's golden format or Braintrust's dataset format

The suite lives in the repository (``evals/dataset.jsonl``), so it is versioned by git and reviewed
like code. AI-GOV only ever sees its name, version, case count, content hash and pass rate.
"""
import argparse
import hashlib
import importlib
import json
import os
import sys

TRACE_FILE = os.environ.get("AIGOV_TRACE_FILE", "aigov-spans.jsonl")
EVAL_DIR = os.environ.get("AIGOV_EVAL_DIR", "evals")
CANDIDATES = os.path.join(EVAL_DIR, "candidates.jsonl")
DATASET = os.path.join(EVAL_DIR, "dataset.jsonl")
META = os.path.join(EVAL_DIR, "dataset.meta.json")
RESULTS = os.path.join(EVAL_DIR, "results.json")


def read_jsonl(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as src:
        return [json.loads(line) for line in src if line.strip()]


def write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as out:
        for row in rows:
            out.write(json.dumps(row, sort_keys=True) + "\n")


def case_id(case):
    raw = case["input"] + "\x00" + case["expected_output"]
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def content_hash(rows):
    body = "\n".join(json.dumps(r, sort_keys=True) for r in rows)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def load_meta(default_name):
    if os.path.exists(META):
        with open(META, encoding="utf-8") as src:
            return json.load(src)
    return {"name": default_name, "version": 0, "cases": 0, "content_hash": None}


def build(args):
    """Every traced agent run with a question and an answer becomes a candidate case."""
    spans = read_jsonl(TRACE_FILE)
    roots = [s for s in spans if s.get("kind") == "agent"]
    cases = {}
    for s in roots:
        attrs = s.get("attributes", {})
        question, answer = attrs.get("input.value"), attrs.get("output.value")
        if question and answer:
            case = {"input": str(question), "expected_output": str(answer)}
            cases[case_id(case)] = case
    write_jsonl(CANDIDATES, [dict(id=k, **v) for k, v in sorted(cases.items())])
    print(f"{len(cases)} candidate case(s) from {len(roots)} traced agent run(s) -> {CANDIDATES}")


def promote(args):
    """A reviewed promotion: candidates join the suite, and the version moves only if content did."""
    existing = {c["id"]: c for c in read_jsonl(DATASET)}
    fresh = [c for c in read_jsonl(CANDIDATES) if c["id"] not in existing]
    rows = sorted([*existing.values(), *fresh], key=lambda c: c["id"])
    write_jsonl(DATASET, rows)
    meta = load_meta(args.name)
    digest = content_hash(rows)
    if meta.get("content_hash") != digest:
        meta["version"] = int(meta.get("version", 0)) + 1
    meta.update(name=meta.get("name") or args.name, cases=len(rows), content_hash=digest)
    with open(META, "w", encoding="utf-8") as out:
        json.dump(meta, out, indent=2, sort_keys=True)
    print(f"{len(fresh)} case(s) promoted; {meta['name']} v{meta['version']} holds {len(rows)} case(s)")


def _norm(text):
    return " ".join(str(text).lower().split())


def run(args):
    """Re-run every case through the agent. A case passes when the answer still carries what the
    promoted answer said. Teams that score with DeepEval or Braintrust write the same results file."""
    module_name, _, fn_name = args.entry.partition(":")
    sys.path.insert(0, os.getcwd())
    agent = getattr(importlib.import_module(module_name), fn_name)
    rows = read_jsonl(DATASET)
    meta = load_meta(args.name)
    passed = 0
    for case in rows:
        got = _norm(agent(case["input"]))
        want = _norm(case["expected_output"])
        passed += int(want in got or got in want)
    results = {
        "dataset": meta.get("name") or args.name,
        "version": f"v{meta.get('version', 0)}",
        "cases": len(rows),
        "passed": passed,
        "framework": "aigov-match",
        "content_hash": meta.get("content_hash"),
        "metrics": {"pass_rate": round(passed / len(rows), 4) if rows else 0.0},
    }
    os.makedirs(EVAL_DIR, exist_ok=True)
    with open(RESULTS, "w", encoding="utf-8") as out:
        json.dump(results, out, indent=2, sort_keys=True)
    print(f"{results['dataset']} {results['version']}: {passed}/{len(rows)} passed -> {RESULTS}")


def export(args):
    """The same suite, in the shape each eval tool loads."""
    rows = read_jsonl(DATASET)
    if args.format == "deepeval":
        path = os.path.join(EVAL_DIR, "deepeval_goldens.json")
        with open(path, "w", encoding="utf-8") as out:
            json.dump([{"input": r["input"], "expected_output": r["expected_output"]} for r in rows], out, indent=2)
    else:
        path = os.path.join(EVAL_DIR, "braintrust_dataset.jsonl")
        write_jsonl(path, [{"id": r["id"], "input": r["input"], "expected": r["expected_output"]} for r in rows])
    print(f"{len(rows)} case(s) -> {path}")


def main():
    ap = argparse.ArgumentParser(description="AI-GOV eval dataset builder")
    ap.add_argument("--name", default=os.environ.get("AIGOV_DATASET", "regression"))
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("build")
    sub.add_parser("promote")
    run_p = sub.add_parser("run")
    run_p.add_argument("--entry", default=os.environ.get("AIGOV_AGENT_ENTRY", "examples.demo_agent:answer"))
    exp = sub.add_parser("export")
    exp.add_argument("--format", choices=["deepeval", "braintrust"], default="deepeval")
    args = ap.parse_args()
    {"build": build, "promote": promote, "run": run, "export": export}[args.command](args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
