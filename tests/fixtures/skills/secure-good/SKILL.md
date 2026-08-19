---
name: secure-good
description: Helps create a clear, actionable checklist from a short request; use when planning a small task.
metadata:
  version: 0.0.1
  intent: create_skill
  tags: [fixture, quality]
  inputs_schema: {"type": "object", "properties": {"request": {"type": "string"}}, "required": ["request"]}
  outputs_schema: {"type": "object", "properties": {"checklist": {"type": "array", "items": {"type": "string"}}}, "required": ["checklist"]}
---

# Instructions

1. Extract the requested outcome and any constraints from the request.
2. Return the smallest ordered checklist that reaches that outcome.
3. State one concrete assumption only when the request omits information needed to act.

# Example

Input: "Prepare a release checklist for version 1.2.0."

Output:

1. Confirm the version is 1.2.0 in the release metadata.
2. Run the project test suite.
3. Review the release notes.

# Troubleshooting

If the request has no outcome, ask for the desired result before making a checklist.
