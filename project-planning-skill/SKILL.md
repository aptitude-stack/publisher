---
name: project-planning-skill
description: Manages project planning workflows and creates structured planning output. Use when user asks for sprint planning, project planning, or roadmap setup.
compatibility: Works in Claude environments with markdown editing support.
metadata:
  author: Aptitude
  version: 1.0.0
  intent: create_skill
  tags: [planning, projects]
  headers: {"runtime": "markdown"}
  inputs_schema: {"type": "object"}
  outputs_schema: {"type": "object"}
  token_estimate: 128
  maturity_score: 0.9
  security_score: 0.95
---

# Project Planning Skill

# Instructions

## Step 1: Gather Context

Collect project requirements, constraints, and deadlines before drafting the plan.

## Step 2: Build the Plan

Create a structured project plan with milestones, owners, and dependencies.

## Examples

Example 1: Sprint planning

User says: "Help me plan the next sprint"

Result: A structured sprint plan with prioritized tasks and sequencing.

## Troubleshooting

Error: Missing project scope

Solution: Ask for the scope, timeline, and owners before continuing.
