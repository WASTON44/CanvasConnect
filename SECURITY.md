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

## Supported boundary

Version 0.1 is read-only against Canvas. The client blocks write methods before
they are sent. This is a safety control, not a substitute for protecting local
course material and credentials.
