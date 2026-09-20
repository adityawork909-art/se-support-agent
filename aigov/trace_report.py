#!/usr/bin/env python3
"""AI-GOV build gate: check this build's trace against the control catalogue and report it.

1. Fetches the current policy from AI-GOV. The policy is the catalogue's, so every repository is held
   to the same rule, and a change in the console reaches every pipeline on its next run.
2. Evaluates the trace with OPA when ``opa`` is on the path.
3. Posts the report to AI-GOV, which re-checks it and answers with the authoritative verdict.
4. Writes a job summary and fails the build when the verdict fails.

What leaves the build machine: span names, kinds, statuses and durations, the tracing packages the
repository declares, the OPA result, and the eval results file. No prompt, completion or attribute
value is sent.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

KINDS = {"agent", "llm", "tool", "retriever", "guardrail", "chain", "embedding", "reranker", "evaluator", "prompt"}
MAX_SPANS = 8000  # AI-GOV accepts at most 8000 spans in a report, and a report of at most 1 MB
BODY_BUDGET = 950_000  # bytes; measured: 8000 spans with 60-character names alone come to 1.1 MB
TELEMETRY = re.compile(
    r"^(arize-phoenix(-otel)?|openinference-instrumentation[\w-]*|opentelemetry-(sdk|api|distro)|langfuse|langsmith|traceloop-sdk|logfire)$",
    re.I,
)


def env(name, default=None):
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def load_spans(path):
    spans = []
    if not os.path.exists(path):
        return spans
    with open(path, encoding="utf-8") as src:
        for line in src:
            line = line.strip()
            if not line:
                continue
            try:
                s = json.loads(line)
            except ValueError:
                continue
            kind = str(s.get("kind", "")).lower()
            spans.append({
                "name": str(s.get("name", ""))[:120],
                "kind": kind if kind in KINDS else "other",
                "status": "error" if s.get("status") == "error" else "ok",
                "duration_ms": s.get("duration_ms", 0),
            })
    return spans


def request(method, url, key, body=None):
    # The key goes in a header AND in the body: some proxies (here.now among them) strip every request
    # header, and the body is what reaches AI-GOV. It is never put in the URL.
    payload = dict(body or {})
    payload["aigov_key"] = key
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers={
        "X-AIGOV-Key": key,
        "Content-Type": "application/json",
        "User-Agent": "aigov-build-gate/1.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as err:
        try:
            payload = json.loads(err.read() or b"{}")
        except ValueError:
            payload = {}
        return err.code, payload


def fit_names(report):
    """Shorten span names until the report fits the receiver's body limit.

    The verdict is judged on span kinds and statuses, never on names, so a long trace keeps every
    span and only loses name characters. Returns the name length that was kept."""
    spans = report["trace"]["spans"]
    for cap in (120, 60, 24, 0):
        for s in spans:
            s["name"] = s["name"][:cap]
        if len(json.dumps(report, separators=(",", ":")).encode("utf-8")) <= BODY_BUDGET:
            return cap
    return 0


def opa_check(rego, spans):
    """The policy as OPA evaluates it, or None when OPA is not installed."""
    opa = shutil.which("opa")
    if not opa:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        policy_path = os.path.join(tmp, "policy.rego")
        input_path = os.path.join(tmp, "input.json")
        with open(policy_path, "w", encoding="utf-8") as out:
            out.write(rego)
        with open(input_path, "w", encoding="utf-8") as out:
            json.dump({"spans": spans}, out)
        run = subprocess.run(
            [opa, "eval", "--format", "json", "--data", policy_path, "--input", input_path, "data.aigov.trace"],
            capture_output=True, text=True, timeout=120,
        )
    if run.returncode != 0:
        return {"engine": "opa", "error": run.stderr.strip()[:300]}
    value = json.loads(run.stdout)["result"][0]["expressions"][0]["value"]
    return {"engine": "opa", "allow": bool(value.get("allow")), "deny": sorted(value.get("deny", []))}


def pull_request():
    """The pull request this run belongs to, or {} when there is not one.

    GITHUB_REF_NAME is "123/merge" on a pull_request event, so it cannot be used for this. The
    event payload at GITHUB_EVENT_PATH is written by the runner and is the only reliable source.
    Everything is optional: a push build simply reports no pull request.
    """
    path = env("GITHUB_EVENT_PATH")
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as src:
            payload = json.load(src)
    except (OSError, ValueError):
        return {}
    node = payload.get("pull_request") or {}
    if not node:
        return {}
    user = node.get("user") or {}
    return {
        "number": node.get("number"),
        "title": (node.get("title") or "")[:300] or None,
        "url": node.get("html_url"),
        "author": user.get("login"),
    }


def declared_telemetry():
    """Tracing packages the repository declares, read from its own manifests."""
    found = set()
    for name in ("requirements.txt", "requirements-dev.txt", "pyproject.toml"):
        if not os.path.exists(name):
            continue
        with open(name, encoding="utf-8", errors="ignore") as src:
            for line in src:
                token = re.split(r"[\s=<>~!\[;,\"']", line.strip().lstrip("\"'"), maxsplit=1)[0]
                if token and TELEMETRY.match(token):
                    found.add(token.lower())
    return sorted(found)


def main():
    ap = argparse.ArgumentParser(description="Check the trace against the AI-GOV policy and report it.")
    ap.add_argument("--trace", default=env("AIGOV_TRACE_FILE", "aigov-spans.jsonl"))
    ap.add_argument("--evals", default=os.path.join(env("AIGOV_EVAL_DIR", "evals"), "results.json"))
    ap.add_argument("--receiver", default=env("AIGOV_RECEIVER"))
    ap.add_argument("--repository", default=env("GITHUB_REPOSITORY"))
    ap.add_argument("--no-fail", action="store_true", help="report, but never fail the build")
    args = ap.parse_args()

    key = env("AIGOV_INGEST_KEY")
    if not args.receiver or not key:
        print("AIGOV_RECEIVER and AIGOV_INGEST_KEY must both be set.", file=sys.stderr)
        return 2
    if not args.repository:
        print("No repository name: run inside GitHub Actions or pass --repository owner/name.", file=sys.stderr)
        return 2
    base = args.receiver.rstrip("/")

    status, policy = request("POST", base + "/policy", key)
    if status != 200:
        print(f"Could not read the AI-GOV policy ({status}): {policy.get('error')}", file=sys.stderr)
        return 2

    spans = load_spans(args.trace)[:MAX_SPANS]
    opa = opa_check(policy["rego"], spans)
    evals = None
    if os.path.exists(args.evals):
        with open(args.evals, encoding="utf-8") as src:
            evals = json.load(src)

    pr = pull_request()
    run_id = env("GITHUB_RUN_ID")
    report = {
        "repository": args.repository,
        "commit": env("GITHUB_SHA"),
        "branch": env("GITHUB_REF_NAME"),
        "run_id": f"{run_id}-{env('GITHUB_RUN_ATTEMPT', '1')}" if run_id else f"local-{int(time.time() * 1000)}",
        "run_url": f"{env('GITHUB_SERVER_URL', 'https://github.com')}/{args.repository}/actions/runs/{run_id}" if run_id else None,
        "workflow": env("GITHUB_WORKFLOW"),
        "actor": env("GITHUB_ACTOR"),
        "event": env("GITHUB_EVENT_NAME"),
        "pr_number": pr.get("number"),
        "pr_title": pr.get("title"),
        "pr_url": pr.get("url"),
        "pr_author": pr.get("author"),
        "instrumentation": {"declared": declared_telemetry(), "trace_file": bool(spans)},
        "trace": {"spans": spans},
        "conformance": {
            "engine": opa["engine"] if opa else "none",
            "passed": opa.get("allow") if opa else None,
            "deny": opa.get("deny", []) if opa else [],
            "error": opa.get("error") if opa else None,
            "policy_version": policy.get("version"),
        },
        "evals": evals,
    }
    name_cap = fit_names(report)
    if name_cap < 120:
        print(f"Large trace: span names shortened to {name_cap} characters to fit the report limit.")

    status, res = request("POST", base + "/build", key, report)
    if status != 200:
        print(f"AI-GOV did not accept the report ({status}): {res.get('error')}", file=sys.stderr)
        return 2
    verdict = res["verdict"]
    kinds = ", ".join(f"{k} {n}" for k, n in sorted(verdict["counts"].items())) or "none"
    lines = [
        f"AI-GOV trace conformance: {'PASS' if verdict['passed'] else 'FAIL'} (policy v{verdict['policy_version']})",
        f"  spans: {len(spans)} ({kinds})",
        f"  OPA: {'not installed' if not opa else ('error: ' + opa['error']) if opa.get('error') else ('allow' if opa['allow'] else 'deny')}",
    ]
    lines += [f"  - {r}" for r in verdict["reasons"]]
    if res.get("parity") is False:
        lines.append("  ! OPA and AI-GOV disagree: this build evaluated an older policy.")
    if evals:
        lines.append(f"  evals: {evals.get('dataset')} {evals.get('version')} - {evals.get('passed')}/{evals.get('cases')} passed")
    gate = res.get("gate") or {}
    lines.append(f"  promotion gate: {'open' if gate.get('promote') else 'blocked'}" + (f" ({'; '.join(gate.get('reasons', []))})" if gate.get("reasons") else ""))
    print("\n".join(lines))

    summary = env("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as out:
            out.write("### AI-GOV build gate\n\n```\n" + "\n".join(lines) + "\n```\n")

    if not verdict["passed"] and not args.no_fail:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
