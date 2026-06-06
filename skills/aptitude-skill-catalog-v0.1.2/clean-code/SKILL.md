---
name: clean-code
description: "Helps refactor and simplify code while preserving tested behavior; use when users ask to improve maintainability, naming, structure, or readability."
metadata:
  version: "0.1.0"
  intent: "create_skill"
  tags: ["refactoring","maintainability","quality"]
  inputs_schema: {"type":"object","additionalProperties":true}
  outputs_schema: {"type":"object","additionalProperties":true}
  relationships: {"depends_on":[{"slug":"test-driven-development","version_constraint":">=0.0.0"}],"extends":[],"conflicts_with":[],"overlaps_with":[]}
---
# Instructions

Use this skill after behavior is protected by tests. Clean code work should preserve behavior while reducing local complexity.

## Refactoring Loop

1. Confirm the relevant tests pass before editing.
2. Choose one improvement: name, shape, duplication, dependency direction, or dead code.
3. Make the smallest coherent change.
4. Run focused tests after each meaningful step.
5. Stop when the code is clearer enough for the current goal.

## Priorities

- Prefer clear names over comments that explain unclear names.
- Prefer small functions when they separate concepts, not when they merely chop code into fragments.
- Remove duplication when the shared abstraction has a real shared meaning.
- Keep module boundaries aligned with behavior and ownership.
- Avoid broad rewrites that are not required by the current change.

## Example

After a parser bug fix is green, rename ambiguous variables, extract repeated normalization into a small helper, and run the parser tests again. Do not redesign unrelated parser features during the cleanup.

## Troubleshooting

- If refactoring changes behavior, revert the refactor or add the missing behavior test before continuing.
- If an abstraction needs a long explanation, keep the simpler code until a clearer shape emerges.
- If many files need edits for a small cleanup, the change is probably too broad for this pass.
