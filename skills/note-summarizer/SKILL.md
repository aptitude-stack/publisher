---
name: note-summarizer
description: "Generates concise note summaries; use when the user asks for a short summary of notes or meeting text."
metadata:
  version: 1.0.0
  intent: create_skill
  tags: [summary, notes]
  inputs_schema: {"type":"object"}
  outputs_schema: {"type":"object"}
---

# Instructions

Summarize the provided notes in a concise bullet list.

# Example

Input: Meeting notes.
Output: Short summary bullets.

# Troubleshooting

If the notes are unclear, ask for the missing context.
