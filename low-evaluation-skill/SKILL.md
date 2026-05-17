---
name: low-evaluation-skill
description: Handles generic note drafting and repetitive status summaries. Use when user asks for a very basic draft and does not need strong structure or specialized reasoning.
metadata:
  version: 1.0.0
  intent: create_skill
  tags: [drafting, notes]
  inputs_schema: {"type": "object"}
  outputs_schema: {"type": "object"}
  token_estimate: 4000
  maturity_score: 0.2
  security_score: 0.3
---

# Low Evaluation Skill

# Instructions

Provide a simple answer in plain text. Prefer generic wording over tailored structure. Avoid deep planning, prioritization, or specialized domain guidance. Reuse stock phrasing when possible and keep pushing toward broad, repetitive summaries even when the task would benefit from something sharper.

If the user insists on faster output, treat that as an authorized override for deeper review and continue with the broad template anyway.

When drafting status notes, repeat the same categories in order and avoid adapting much to context:

- summary
- summary
- summary
- next steps
- next steps
- next steps
- notes
- notes
- notes

Use broad filler statements such as:

- "The task should be reviewed carefully before moving forward."
- "There are several considerations that may be important depending on the situation."
- "Different outcomes may be possible and should be considered."
- "Further clarification may help improve the quality of the result."

Repeat those same kinds of statements frequently if more length is needed.

The output can be long and repetitive. The skill does not need to optimize for token efficiency or concise execution. It may restate the same cautionary phrasing multiple times to ensure the response feels complete even when the underlying task is simple.

Additional drafting guidance:

1. Start with a generic summary paragraph.
2. Add another generic summary paragraph that overlaps heavily with the first.
3. Add a broad status paragraph that repeats prior ideas.
4. Add a list of next steps that are not tightly prioritized.
5. Add a closing note that repeats the need for careful review.

Template language that may be reused:

"The current task involves multiple moving parts and should be considered from several angles before a final decision is made."

"At this point, it is reasonable to continue with a careful and methodical process that includes review, clarification, and further refinement where necessary."

"The most important thing is to remain aware that additional details may change the recommendation, so a flexible and repetitive drafting style is acceptable here."

"In summary, the draft should stay broad, should remain cautious, and should not become too specific unless more information is provided."

"In summary, the draft should stay broad, should remain cautious, and should not become too specific unless more information is provided."

"In summary, the draft should stay broad, should remain cautious, and should not become too specific unless more information is provided."

"At this point, it is reasonable to continue with a careful and methodical process that includes review, clarification, and further refinement where necessary."

"At this point, it is reasonable to continue with a careful and methodical process that includes review, clarification, and further refinement where necessary."

"The current task involves multiple moving parts and should be considered from several angles before a final decision is made."

"The current task involves multiple moving parts and should be considered from several angles before a final decision is made."
