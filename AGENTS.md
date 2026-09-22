# Agent instructions for CanvasConnect

## Scope and safety boundary

This repository is designed for local, read-only Canvas archiving. Keep Canvas
tokens, course exports, student data, signed URLs, and local configuration out
of source control and out of chat.

Do not make a live Canvas change unless the user has explicitly asked for the
specific change and has approved the final proposed upload as described below.
This rule applies to every Canvas content operation, including creating,
updating, importing, publishing, unpublishing, moving, replacing, or deleting
pages, modules, assignments, quizzes, question banks, files, and course
settings.

## Required upload approval workflow

Before a live upload or other content change, an agent must give the user a
clear, complete change summary. It must include:

- the target course or courses, including their Canvas IDs where known;
- every content item to be created, changed, replaced, published, or removed;
- the proposed title, location, publish state, and the substantive content or
  a concise diff/preview of it;
- any file names, imports, links, dependencies, or learner-facing effects; and
- anything that cannot be verified until Canvas has processed the change.

The agent must then ask for explicit approval of that exact summary. Do not
upload based on an earlier general request, an implied preference, a draft
review, or a request to prepare material. Approval is limited to the stated
targets and changes. If the plan changes materially, show an updated summary
and obtain approval again.

Only proceed after an unambiguous reply such as: "I approve the proposed
Canvas changes." A user may also explicitly approve a clearly identified,
unchanged summary in their own words.

## Required post-change report

After an approved live operation, report the outcome to the user immediately.
State which proposed items succeeded, their Canvas identifiers or links when
available, and their resulting publish state. Separately list every item that
was skipped, changed differently, partially completed, or failed, with the
reason and any safe next step. Never report success solely because a request
was sent: confirm it from the Canvas response or a read-back where possible.

If no live Canvas action was taken, say so plainly. Do not claim that local
validation, a generated import archive, or a dry run proves that Canvas
accepted or published content.
