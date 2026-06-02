---
name: email-drafter
description: "Generates short email drafts; use when the user asks for help writing an email."
metadata:
  version: 1.0.0
  intent: create_skill
  tags: [email, writing]
  inputs_schema: {"type":"object"}
  outputs_schema: {"type":"object"}
---

# Instructions

Draft a clear email from the user's requested purpose and tone.

# Example

Input: Email request.
Output: Email draft.

# Troubleshooting

If the recipient or purpose is missing, ask for it.
