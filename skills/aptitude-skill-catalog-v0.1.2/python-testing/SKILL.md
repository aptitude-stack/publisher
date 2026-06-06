---
name: python-testing
description: "Helps plan, organize, review, or debug Python test suites; use when users ask about Python tests, pytest usage, coverage decisions, or regression testing strategy."
metadata:
  version: "0.1.2"
  intent: "publish_version"
  tags: ["python","testing","pytest"]
  inputs_schema: {"type":"object","additionalProperties":true}
  outputs_schema: {"type":"object","additionalProperties":true}
  relationships: {"depends_on":[{"slug":"testing-coverage","version_constraint":">=0.0.0"},{"slug":"pytest","version_constraint":">=0.0.0"}],"extends":[{"slug":"test-driven-development","version":"0.1.1"}],"conflicts_with":[],"overlaps_with":[]}
---
# Instructions

Use this skill as the Python testing coordinator. Keep framework mechanics in the `pytest` skill, coverage policy in `testing-coverage`, and test-first workflow in `test-driven-development`.

## Process

1. Identify the behavior under test and the smallest useful regression or feature test.
2. Choose the test layer: unit for local behavior, integration for boundaries, end-to-end only for user-visible flows.
3. Prefer focused assertions over broad snapshots or incidental implementation checks.
4. Use pytest fixtures and parametrization only when they make tests clearer.
5. Check coverage after the behavior tests exist; do not write tests only to move a percentage.

## Python Test Shape

- Test public behavior through functions, classes, APIs, CLIs, or persisted effects.
- Keep one reason to fail per test.
- Name tests with the expected behavior, such as `test_rejects_expired_token`.
- Use temporary paths, monkeypatching, and fakes to isolate file system, environment, and network boundaries.
- Avoid asserting private helper calls unless the helper itself is the public contract.

## Example

For a parser bug, write `test_parser_preserves_quoted_commas` against the public parse function. Use a focused input string and assert the returned fields. After the test fails for the parser behavior, implement the smallest parser change and run the focused test plus nearby parser tests.

## Troubleshooting

- If tests are hard to write, the production boundary may be too coupled; introduce a small seam only when it clarifies behavior.
- If many tests fail after a small change, run the smallest failing subset and classify failures before editing more code.
- If coverage is high but bugs keep escaping, inspect missing branches, boundary cases, and assertions instead of raising the threshold.
