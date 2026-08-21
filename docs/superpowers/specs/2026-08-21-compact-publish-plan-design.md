# Compact Publish Plan Design

## Goal

Show the short, decision-useful metadata already available from the selected
`SKILL.md` before the Publisher runs inspection.

## Display

The Publish Plan shows:

- Action
- Name
- Version
- Intent, for publish workflows only
- Inspection depth
- Namespace
- License, only when declared

It omits Trust, Origin, description, tags, relations, schemas, compatibility,
and computed scores. Those values are either internal defaults, potentially
long, or unavailable until inspection.

All Publisher frame titles use the shared muted gray theme style.

## Data Flow

The existing frontmatter reader extracts name, version, intent, and optional
license when the skill is selected. `PublishPlan` stores those values, and the
renderer reads only from the plan. The pipeline still receives the existing
governance and execution values unchanged.

Missing required identity values use the existing menu defaults and remain
subject to pipeline validation. Missing license is simply not rendered.

## Verification

Focused unit tests prove that the plan renders the extracted name and version,
conditionally renders license, omits excluded rows, and uses gray frame titles.
