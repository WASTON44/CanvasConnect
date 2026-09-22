# Contributing

Thank you for helping improve Canvas VLE Manager.

## Development setup

1. Create and activate a Python 3.11+ virtual environment.
2. Install dependencies with `python -m pip install -r requirements.txt`.
3. Run `python -m pytest` before opening a pull request.

## Contribution rules

- Never commit `.env`, API tokens, Canvas URLs belonging to another user, or
  Authorization headers.
- Do not add course archives, active working copies, quiz drafts, student data,
  or production teaching material to the repository.
- Preserve Version 0.1's read-only Canvas boundary. New code must not send
  `POST`, `PUT`, `PATCH`, or `DELETE` requests through `CanvasClient`.
- Add or update offline tests for behaviour changes. Tests must not require a
  live Canvas account or internet connection.
- Keep user-facing documentation accurate, especially privacy and data-handling
  statements.
