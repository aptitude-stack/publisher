---
name: simple-note-summarizer-skill
description: Helps summarize short notes into concise bullets with clear next actions. Use when notes need a compact summary and follow-up list.
compatibility: Works in markdown-first assistant environments.
metadata:
  author: Aptitude
  version: 0.1.0
  intent: create_skill
  tags: [notes, summary]
  inputs_schema: {"type": "object"}
  outputs_schema: {"type": "object"}
  token_estimate: 96
  maturity_score: 0.8
  security_score: 0.9
---

# Simple Note Summarizer Skill

## Instructions

Read the provided notes and return a concise summary with the most important facts,
decisions, and next actions.

## Steps

1. Identify the main topic of the notes.
2. Extract concrete facts and decisions.
3. List open questions or follow-up actions.
4. Keep the response short and easy to scan.

## Examples

User says: "Summarize these meeting notes."

Result:

- Main topic: release planning.
- Decision: ship the demo build first.
- Next action: confirm database verification steps.

## Troubleshooting

If the notes are too vague, ask for the missing context before summarizing.
