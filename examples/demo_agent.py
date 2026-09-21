"""A small traced agent, used to prove the build gate end to end.

It answers a question by retrieving a fact, composing an answer, calling a tool and checking the
result with a guardrail - each step in its own span, the way a real agent's steps are traced. No
model is called, so it needs no key. Replace it with your own agent; keep ``setup()`` and the spans.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aigov.instrumentation import setup, span  # noqa: E402

setup("demo-agent")

FACTS = {
    "voltage": "The panel runs at 400 V three-phase.",
    "breaker": "The breaker trips at 63 A.",
}


def _lookup(question):
    for word, fact in FACTS.items():
        if word in question.lower():
            return fact
    return ""


def answer(question, skip_guardrail=None):
    """``skip_guardrail`` (or AIGOV_DEMO_SKIP_GUARDRAIL=1) leaves the guardrail step out, which is how
    a missing required span looks to the conformance check."""
    if skip_guardrail is None:
        skip_guardrail = os.environ.get("AIGOV_DEMO_SKIP_GUARDRAIL") == "1"
    with span("agent.answer", "agent", input=question) as root:
        with span("retrieve.facts", "retriever", input=question) as step:
            fact = _lookup(question)
            step.set_attribute("output.value", fact)
        with span("llm.compose", "llm", input=question) as step:
            draft = fact or "I do not know."
            step.set_attribute("output.value", draft)
        with span("tool.units", "tool", input=draft) as step:
            result = draft.replace(" V ", " volts ").replace(" A.", " amps.")
            step.set_attribute("output.value", result)
        if not skip_guardrail:
            with span("guardrail.pii", "guardrail", input=result) as step:
                step.set_attribute("output.value", "block" if "@" in result else "pass")
        root.set_attribute("output.value", result)
        return result


if __name__ == "__main__":
    for q in ("What is the voltage?", "When does the breaker trip?"):
        print(answer(q))


with span("retriever.policy_source", "retriever", input="which policy answered this"):
    # Cite which policy document the answer came from, so a reviewer can check it.
    POLICY_SOURCE = "support/refund-policy.md"
