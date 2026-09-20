# AI-GOV build gate

Every commit runs the three build-phase steps and reports to AI-GOV as it happens:

| Step | What it does | File |
|---|---|---|
| Scaffold & instrument | Every agent step is traced in one format: OpenTelemetry spans carrying OpenInference kinds, and Arize Phoenix when a collector is set | `aigov/instrumentation.py` |
| Trace conformance | The trace is checked against the control catalogue's required span kinds with OPA / Rego. A missing kind fails the build | `aigov/trace_report.py` |
| Eval dataset builder | Traced runs become candidate cases. Reviewed candidates are promoted into a versioned suite, which is re-run on every commit | `aigov/eval_dataset.py` |

Receiver: `https://129-159-231-86.sslip.io/gov/v1/ingest`

## Set up

1. **AI-GOV → Receiver → Create key.** Copy the key, which is shown once.
2. **GitHub → Settings → Secrets and variables → Actions → New repository secret.**
   Name it `AIGOV_INGEST_KEY` and paste the key.
3. **Copy the kit into the repository root.** The workflow goes in `.github/workflows/`, the scripts in `aigov/`.
4. **Call `setup()` once when your agent starts,** and wrap the steps auto-instrumentation does not see:

   ```python
   from aigov.instrumentation import setup, span
   setup("my-agent")

   with span("guardrail.pii", "guardrail", input=text):
       ...
   ```

   Then set `AIGOV_AGENT_ENTRY` in the workflow to your agent's `module:function`.

Push. The run appears in AI-GOV while it is still running.

## Building the regression suite

```bash
python -m pytest -q                        # run the agent with tracing on
python aigov/eval_dataset.py build         # traced runs -> evals/candidates.jsonl
python aigov/eval_dataset.py promote       # review, then promote -> evals/dataset.jsonl (versioned)
git add evals && git commit -m "Promote eval cases"
python aigov/eval_dataset.py export --format deepeval     # evals/deepeval_goldens.json
python aigov/eval_dataset.py export --format braintrust   # evals/braintrust_dataset.jsonl
```

The version goes up only when the suite's content changes.

The built-in scorer passes a case when the agent's answer still carries the promoted answer. If you
score with DeepEval or Braintrust instead, write `evals/results.json` in the same shape: `dataset`,
`version`, `cases`, `passed`, `framework`, `content_hash`, `metrics`. The build gate reports whatever
that file says.

## What leaves the build

**Sent to AI-GOV:**

- span names, kinds, statuses and durations;
- the tracing packages the repository declares;
- the OPA result;
- the eval results file.

The ingest key goes in the `X-AIGOV-Key` header and in the request body, because some proxies strip every header. It is never put in a URL.

**Never sent:** prompts, completions and attribute values. Those stay in `aigov-spans.jsonl` on the
build machine, and the suite stays in the repository.
