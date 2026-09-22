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
    "warranty": "The panel carries a 24 month warranty from the install date.",
    "ingress": "The enclosure is rated IP54 for indoor use.",
    "firmware": "The controller ships on firmware 3.8.2.",
    "torque": "Terminal screws are tightened to 2.5 Nm.",
    "standby": "The unit draws 12 W in standby.",
    "temperature": "The rated ambient range is -10 C to 45 C.",
}


def _lookup(question):
    """Returns the fact and the reason it was chosen, so the choice can be recorded."""
    for word, fact in FACTS.items():
        if word in question.lower():
            return fact, "matched the keyword %r" % word
    return "", "no keyword in the question matched any known fact"


def answer(question, skip_guardrail=None):
    """``skip_guardrail`` (or AIGOV_DEMO_SKIP_GUARDRAIL=1) leaves the guardrail step out, which is how
    a missing required span looks to the conformance check."""
    if skip_guardrail is None:
        skip_guardrail = os.environ.get("AIGOV_DEMO_SKIP_GUARDRAIL") == "1"
    with span("agent.answer", "agent", input=question) as root:
        with span("retrieve.facts", "retriever", input=question) as step:
            fact, why = _lookup(question)
            step.set_attribute("output.value", fact)
            step.set_attribute("aigov.decision", why)
            step.set_attribute("aigov.candidates", len(FACTS))
        with span("llm.compose", "llm", input=question) as step:
            draft = fact or "I do not know."
            step.set_attribute("output.value", draft)
            step.set_attribute(
                "aigov.decision",
                "answered from the retrieved fact" if fact else "refused: nothing retrieved to answer from",
            )
        with span("tool.units", "tool", input=draft) as step:
            result = draft.replace(" V ", " volts ").replace(" A.", " amps.")
            step.set_attribute("output.value", result)
            step.set_attribute(
                "aigov.decision",
                "rewrote unit symbols to words" if result != draft else "no unit symbols to rewrite",
            )
        if not skip_guardrail:
            with span("guardrail.pii", "guardrail", input=result) as step:
                blocked = "@" in result
                step.set_attribute("output.value", "block" if blocked else "pass")
                step.set_attribute(
                    "aigov.decision",
                    "blocked: the answer contains an e-mail address" if blocked
                    else "passed: no e-mail address in the answer",
                )
        root.set_attribute("output.value", result)
        return result


QUESTIONS = (
    "What is the voltage?",
    "When does the breaker trip?",
    "How long is the warranty?",
    "What is the ingress rating?",
    "Which firmware does it ship with?",
    "What torque for the terminal screws?",
    "What does it draw in standby?",
    "What ambient temperature is it rated for?",
)


if __name__ == "__main__":
    for q in QUESTIONS:
        print(answer(q))
