# se-support-agent

A support agent instrumented for **AI-GOV**. Every push runs the build gate:

1. the agent's tests run with tracing on,
2. the trace is checked against the control catalogue with OPA/Rego,
3. traced runs become candidate eval cases and the versioned suite is re-run,

and the result is reported to AI-GOV while the run is still going.

The receiver address and the ingest key are configuration, not code: the key is the repository
secret `AIGOV_INGEST_KEY` and never appears in this repository.
