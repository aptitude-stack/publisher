---
name: test-driven-development
description: "Helps implement features, bug fixes, refactors, or behavior changes with a failing test first; use when a user asks to change code and needs confidence before implementation."
metadata:
  version: "0.1.1"
  intent: "publish_version"
  tags: ["testing","tdd","implementation"]
  inputs_schema: {"type":"object","additionalProperties":true}
  outputs_schema: {"type":"object","additionalProperties":true}
  relationships: {"depends_on":[],"extends":[],"conflicts_with":[],"overlaps_with":[]}
---
# Instructions

Use test-driven development when changing behavior that should be protected by an executable check.

## Core Loop

1. Define the smallest externally visible behavior that should change.
2. Write one focused test that fails for the right reason.
3. Run that test and confirm the failure is meaningful.
4. Implement the smallest code change that makes the test pass.
5. Run the focused test, then the relevant wider suite.
6. Refactor only after tests are green.

## Boundaries

- Keep this skill about the TDD workflow, not framework syntax.
- Use language- or tool-specific testing skills for assertions, fixtures, coverage, or runner commands.
- Do not keep production code written before the failing test as the implementation source; restart from the test-backed behavior.

## Quality Checks

- The test name describes behavior, not implementation.
- The assertion checks observable output, state, or interaction that matters to the caller.
- A passing test has been seen fail first.
- Refactoring does not broaden scope or add untested behavior.

## Example

For a bug where duplicate items appear in an export, first add a test showing the duplicate input and expected unique output. Confirm it fails because the export currently duplicates rows, then change only the export logic needed to pass that test.

## Troubleshooting

- If the test passes immediately, tighten it until it proves the missing or broken behavior.
- If the test fails because of setup noise, fix the setup before implementation.
- If the desired behavior is unclear, stop and clarify the contract before writing code.
