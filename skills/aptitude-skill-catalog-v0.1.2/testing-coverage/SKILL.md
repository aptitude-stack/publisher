---
name: testing-coverage
description: "Helps assess meaningful automated test coverage, identify untested critical paths, and set coverage thresholds; use when users ask whether tests cover enough behavior."
metadata:
  version: "0.1.0"
  intent: "create_skill"
  tags: ["testing","coverage","quality"]
  inputs_schema: {"type":"object","additionalProperties":true}
  outputs_schema: {"type":"object","additionalProperties":true}
  relationships: {"depends_on":[],"extends":[],"conflicts_with":[],"overlaps_with":[]}
---
# Instructions

Use coverage as a risk signal, not a scoreboard. The goal is confidence that important behavior fails when broken.

## Coverage Review

1. Identify critical paths: money movement, authorization, persistence, migrations, external integrations, and user-visible workflows.
2. Map each critical path to tests that would fail if the behavior regressed.
3. Inspect missing branches and exception paths before raising global thresholds.
4. Treat uncovered glue code differently from uncovered domain decisions.
5. Recommend the smallest test additions that improve risk coverage.

## Threshold Guidance

- Use project thresholds to prevent backsliding, not to claim quality.
- Require stronger coverage for critical paths than for incidental adapters.
- Prefer branch coverage when conditionals carry business or security meaning.
- Do not add low-value tests that assert implementation details only to increase a number.

## Example

If coverage reports miss an authorization denial branch, add a test proving unauthorized callers are rejected. That test is higher value than covering a trivial property or constant to improve the same percentage.

## Troubleshooting

- High coverage with repeated regressions means assertions are weak or the wrong behavior is covered.
- Low coverage in generated or declarative code may be acceptable if meaningful behavior is tested elsewhere.
- If a threshold blocks urgent work, add a targeted regression test or explicitly lower the threshold with a tracked reason.
