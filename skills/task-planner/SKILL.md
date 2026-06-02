---
name: task-planner
description: "Creates simple task plans; use when the user asks to turn goals into ordered steps."
metadata:
  version: 1.0.0
  intent: create_skill
  tags: [planning, tasks]
  inputs_schema: {"type":"object"}
  outputs_schema: {"type":"object"}
---

# Instructions

Convert the user's goal into a short ordered task plan.

# Example

Input: Project goal.
Output: Ordered next steps.

# Troubleshooting

If the goal is too broad, ask one clarifying question.
