# AI-GOV trace conformance policy, version 1.
# Generated from the AI-GOV control catalogue. Change it in the console, not here:
# every build fetches the current version before it runs.
package aigov.trace

import rego.v1

required_kinds := {"llm", "tool", "guardrail"}

observed_kinds := {k | some s in input.spans; k := lower(s.kind)}

missing contains k if {
	some k in required_kinds
	not k in observed_kinds
}

deny contains msg if {
	some k in missing
	msg := sprintf("required span kind %q is missing from the trace", [k])
}

deny contains "the agent produced no spans: instrumentation is not wired" if count(input.spans) == 0

allow if count(deny) == 0
