---
name: changelog-writer
description: "Use when documenting delivered implementation milestones, architecture changes, schema updates, verification evidence, or release notes. Helps apply the appropriate Aptitude skill workflow."
metadata:
  version: "0.1.0"
  intent: "create_skill"
  tags: ["documentation","changelog","release-notes"]
  inputs_schema: {"type":"object","additionalProperties":true}
  outputs_schema: {"type":"object","additionalProperties":true}
  relationships: {"depends_on":[],"extends":[{"slug":"documentation-writer","version":"0.1.0"}],"conflicts_with":[],"overlaps_with":[{"slug":"architect-review","version":"0.1.0"},{"slug":"verification-before-completion","version":"0.1.0"}]}
---
# Changelog Writer

Write changelogs in the same style as project milestone changelogs under `docs/changelog/`.

## Workflow

1. Read the target plan/spec and implementation files before drafting.
2. Map each delivered behavior to concrete code links.
3. Draft a concise changelog using the template in `references/changelog-template.md`.
4. Verify every major claim has at least one file link.

## Output Rules

- Keep writing concise and technical; prefer bullets over long paragraphs.
- Use Markdown links to concrete repo-relative paths for routes, services, models, migrations, tests, and plans.
- Prefer repo-relative links by default; GitHub links are acceptable when a hosted deep link is more useful.
- Never use machine-specific absolute filesystem paths such as `/path/to/repo/...`.
- Include at least one Mermaid diagram (`flowchart` or `sequenceDiagram`) that reflects the real implementation.
- Add a schema section with Markdown tables. Each table row must explain field purpose, not only type.
- Call out key tradeoffs and limits without repeating obvious code details.
- Avoid invented behavior; only document what is implemented.

## Minimum Sections

1. Title + short intro linking the milestone plan/spec.
2. Scope Delivered.
3. Architecture / Flow (Mermaid).
4. Design Notes (major decisions and rationale).
5. Schema Reference (table format with clear explanations).
6. Verification Notes (tests or checks).

## Link Quality

- Prefer deep links to the most specific file that proves the point.
- When useful, include multiple links per bullet (API + core + persistence + test).
- Keep link lists short and relevant.
- Keep every link portable across machines by using repo-relative paths or GitHub URLs only.

## Reusable Template

Use `references/changelog-template.md` as the starting structure and adapt headings to the milestone.
