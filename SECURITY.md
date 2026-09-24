# Security policy

## Reporting a security issue

Do not open a public GitHub issue containing a Canvas URL, API token, course
content, student information, or a signed file URL. Instead, contact the
repository owner privately and include only the minimum information needed to
reproduce the issue.

## Local data

This application is designed for local use. Real credentials belong only in
`.env`, which is ignored by Git. Course archives, active working copies,
discovery data, logs, and quiz drafts are also ignored by default.

Before committing, run:

```text
git status
```

Review every staged file. If a token may have been committed or shared, revoke
it in Canvas immediately and remove it from the Git history before publishing.

## Canvas access boundary

The archive `CanvasClient` is permanently read-only: it blocks write methods
before they are sent. The optional direct-deployment command uses a distinct,
approval-gated write client and may only be run after the exact live changes
have been reviewed and explicitly approved under [`AGENTS.md`](AGENTS.md).
It uploads only from local `Active` working copies and records a local
deployment report. These controls are not substitutes for protecting local
course material and credentials.
