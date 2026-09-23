# Canvas VLE Manager

Canvas VLE Manager is a local Python command-line application that creates a
search-friendly reference archive of teaching content available through a
lecturer's Canvas LMS account. It runs from a terminal or VS Code and connects
directly to the user's own Canvas installation. There is no hosted service,
shared account, database, telemetry, or central token store.

## What it does

Version 0.1 can:

- configure and test a Canvas connection locally;
- discover all accessible active, pending, and concluded courses;
- let the user choose only the courses they want, using a numbered list,
  explicit Canvas IDs, or an exact Canvas term;
- filter displayed courses by text, term, role, state, or Canvas favourite;
- inspect course modules and module items;
- perform a dry run with content counts and an estimated file size;
- archive course metadata, syllabus HTML, modules, pages, files and Canvas
  folder structure;
- archive assignment definitions, assignment groups, rubrics, Classic Quiz and
  New Quiz definitions, discussion topics, and announcements where permitted;
- preserve external references without crawling external websites;
- calculate SHA-256 checksums and skip unchanged files on later runs;
- retain old local files if they disappear from the latest Canvas inventory;
- checkpoint file metadata so an interrupted backup can be resumed; and
- create per-course Markdown/JSON content indexes and global course indexes.

Version 0.1 is **read only against Canvas**. Its API client allows only `GET`
and `HEAD`. Attempts to use `POST`, `PUT`, `PATCH`, or `DELETE` are blocked
locally before a request can leave the computer.

## Requirements

- Python 3.11 or newer
- Internet access to the institution's Canvas site
- A Canvas personal access token that can read the required courses
- A terminal; the VS Code integrated terminal is suitable

Token creation and availability are controlled by each institution. Check the
Canvas account settings or ask local Canvas support. Never paste a token into a
chat, issue, screenshot, source file, or test fixture.

## Installation on Windows

Open PowerShell in the repository directory:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If PowerShell activation is restricted, use Command Prompt:

```bat
.venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Alternatively, call `.\.venv\Scripts\python.exe` directly for each command.

## Sharing this repository safely

The repository is safe to share as application source code, not as a copy of a
lecturer's local Canvas workspace. Before creating a GitHub repository or
inviting a collaborator:

1. Run `git status` and review every file that would be committed.
2. Confirm `.env` is not shown. It contains local credentials and must never be
   shared.
3. Leave `backups/`, `Active/`, `quiz_drafts/`, `user_data/`, and `logs/`
   untracked. They can contain teaching content and institution-specific data.
4. Ask each collaborator to run `python scripts/setup.py` and enter their own
   Canvas URL and token locally.

The project contains a GitHub Actions workflow that runs the offline test suite
on Python 3.11 and 3.12. It uses no Canvas credentials or course data.

## Installation on macOS or Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Setup

From the repository root, run:

```text
python scripts/setup.py
```

Enter the normal Canvas root URL, such as `https://canvas.example.edu`; do not
append `/api/v1`. Enter the API token at the hidden prompt. Setup stores both
values in the local, Git-ignored `.env` file and tests the connection. If
`CANVAS_BASE_URL` or `CANVAS_API_TOKEN` is set in the process environment, it
takes precedence over `.env` and setup reports that override without showing a
token.

Running setup again provides options to test, replace, or reset the local
configuration. The current token is never displayed.

## List accessible courses

```text
python scripts/list_courses.py
```

This prints every accessible course returned for active, pending, and completed
enrolment states and saves a privacy-conscious discovery index at
`user_data/courses.json`. Canvas term names are used as returned; the program
does not assume an academic-year format.

The displayed list can be narrowed without changing that full saved index:

```text
python scripts/list_courses.py --search dynamics
python scripts/list_courses.py --term "2025/26"
python scripts/list_courses.py --role TeacherEnrollment
python scripts/list_courses.py --state completed
python scripts/list_courses.py --favorite
```

`--search` checks the course name, code, Canvas ID, term, account, current-user
role, and state. Search and term filters are case-insensitive text matches;
role and state filters are case-insensitive exact matches. Filters can be
combined.

## List modules

```text
python scripts/list_modules.py --course 12345
```

The output includes module order, publish state, unlock information,
prerequisites, and accessible module items.

## Select and back up courses

Interactive mode is the simplest way to exclude irrelevant courses:

```text
python scripts/backup_courses.py
```

The command refreshes the accessible course list and displays entries such as:

```text
[1] ENG101 - Engineering Dynamics - Autumn 2026 (Canvas 12345)
[2] ENG102 - Engineering Statics - Autumn 2026 (Canvas 23456)
```

The interactive command first asks for an optional filter, so a lecturer can
search by course name, code, Canvas ID, term, account, role, or state before a
long list is displayed. Enter a selection such as `1,3-5`, or enter `all`.
Enter `/filter dynamics` at the selection prompt to change the displayed list
without restarting. The selected courses are shown again, and no archive starts
until the user answers the final `Proceed with backup? [y/N]` prompt.

Filters can also be supplied on the command line before interactive selection:

```text
python scripts/backup_courses.py --search dynamics
python scripts/backup_courses.py --filter-term "2025/26" --role TeacherEnrollment
python scripts/backup_courses.py --state completed
python scripts/backup_courses.py --favorite
```

Select by Canvas ID instead:

```text
python scripts/backup_courses.py --course 12345
python scripts/backup_courses.py --course 12345 23456 34567
```

Or select all accessible courses whose returned Canvas term name or ID exactly
matches:

```text
python scripts/backup_courses.py --term "Autumn 2026"
```

For deliberate non-interactive use, `--yes` skips only the final confirmation;
it does not broaden Canvas access or change the selected courses.

## Dry run

Inspect a course before downloading it:

```text
python scripts/backup_courses.py --course 12345 --dry-run
```

Dry run reports accessible modules, pages, files, assignments, rubrics,
quizzes, discussions, announcements, and total Canvas-reported file size. If
any file lacks a reliable size, the estimate is shown as `Unknown`. It creates
no course archive and downloads no file; it does refresh local course discovery
and write a diagnostic log.

## Resume and incremental backup

```text
python scripts/backup_courses.py --course 12345 --resume
```

Every normal backup is incremental for course files. A file is skipped only
when its Canvas ID, reported size, change timestamp, local size, and prior
checksum record support that decision. New or changed files are downloaded to a
temporary sibling file and moved into place only after a successful transfer.
File metadata is checkpointed after each item, so `--resume` can continue an
interrupted archive. The flag is also recorded in the manifest.

The deletion policy is deliberately conservative. A local file missing from a
later Canvas inventory is retained and marked as such in `files/files.json`;
Version 0.1 never prunes it automatically.

## Backup structure

A course is stored under its real Canvas term when available, with a sanitized
course code, name, and Canvas ID:

```text
backups/
|-- COURSE_INDEX.md
|-- course_index.json
`-- Autumn 2026/
    `-- ENG101_Dynamics__12345/
        |-- course.json
        |-- manifest.json
        |-- syllabus/
        |-- modules/modules.json
        |-- pages/
        |-- files/
        |   |-- files.json
        |   `-- folders.json
        |-- assignments/
        |-- assignment_groups/
        |-- rubrics/
        |-- quizzes/
        |   |-- classic/
        |   `-- new/
        |-- discussions/
        |-- announcements/
        `-- indexes/
            |-- content_index.json
            |-- content_index.md
            `-- external_links.json
```

Canvas page, syllabus, assignment, discussion, and announcement HTML is kept as
plain HTML. Metadata and structure are JSON. `indexes/content_index.md` is the
best human-readable entry point for one course, while `backups/COURSE_INDEX.md`
is the entry point for the entire local archive. Paths in indexes are relative.

Filenames are made Windows-safe. Different Canvas files that resolve to the
same local name receive a deterministic `__canvas_ID` suffix rather than
silently overwriting each other. Original filenames remain in file metadata.

## Manifest and partial backups

Every course contains `manifest.json`, recording the application version,
course identity, timestamps, status, counts, local paths, warnings, inaccessible
endpoint categories, file checksums, skipped content, and per-item errors.

Canvas features and permissions vary. If an optional endpoint such as New
Quizzes is disabled or inaccessible, the backup records a warning and continues
with other categories. A completed archive can therefore have status `partial`.
One failed file is recorded without discarding the rest of the course.

## Privacy and local data

- Credentials remain in local `.env` or process environment values.
- Tokens and Authorization headers are not intentionally printed, logged, or
  stored in backups and indexes.
- Signed file download URLs are used transiently and excluded from file
  metadata; known signature/token query parameters are removed from stored
  links.
- Course discovery, downloaded content, backups, and logs remain local and are
  excluded from Git by default.
- Teaching material is sent only between this program and the configured Canvas
  or Canvas-provided file host. External links are recorded but never crawled.
- There is no Canvas VLE Manager server, telemetry, or analytics integration.
- Every user runs an independent installation with their own Canvas access.

`.gitignore` is not encryption. Anyone able to read the working directory may
be able to read `.env` and archived course material. Protect the computer and
follow institutional data-handling policy. Revoke a token if it may have been
exposed.

## Student-data exclusions

The application is intended for lecturer-created teaching material, not student
records. It does not intentionally request rosters, submissions, grades,
student files, quiz attempts or responses, attendance, analytics, messages, or
discussion entries/replies. It avoids Canvas API include parameters that would
request assessment or submission data and removes common incidental user,
submission, assessment, author, entry, and reply fields before metadata is
saved.

Some topic or institution-specific API responses may still contain unexpected
fields. In particular, Canvas does not provide a universal, reliable way for
this local tool to determine whether every discussion topic was authored by a
lecturer without retrieving additional people/enrolment data. Version 0.1
therefore saves accessible topic definitions, strips author and reply data, and
never calls discussion-entry endpoints. Review an archive before sharing it.

## Read-only safety

The safety boundary is enforced in the reusable `CanvasClient`, not just in the
CLI. Only `GET` and `HEAD` requests are accepted. File transfers use normal
`GET` requests. A bearer token is never copied into the separate session used
for cross-origin Canvas file hosts, and normal TLS certificate verification
remains enabled.

Read-only API access can still expose sensitive course material and appears in
Canvas logs like other account activity. Use only an account and courses you
are authorised to access.

## Command reference

```text
python scripts/setup.py
python scripts/list_courses.py
python scripts/list_courses.py --search dynamics
python scripts/list_courses.py --favorite
python scripts/list_modules.py --course 12345
python scripts/backup_courses.py
python scripts/backup_courses.py --search dynamics
python scripts/backup_courses.py --favorite
python scripts/backup_courses.py --course 12345
python scripts/backup_courses.py --course 12345 23456
python scripts/backup_courses.py --term "Autumn 2026"
python scripts/backup_courses.py --course 12345 --dry-run
python scripts/backup_courses.py --course 12345 --resume
python -m pytest
```

Run commands from the repository root.

## Project layout

```text
.
|-- .env.example
|-- README.md
|-- requirements.txt
|-- scripts/                 User-facing commands
|-- src/                     Application code
|-- tests/                   Offline test suite
|-- user_data/               Local discovery data (created, Git-ignored)
|-- backups/                 Local archives (created, Git-ignored)
`-- logs/                    Timestamped logs (created, Git-ignored)
```

## Testing

```text
python -m pytest
```

The automated suite uses fake clients and responses. It does not use `.env`, a
live Canvas token, an internet connection, or real course data.

## Troubleshooting

### The application says it has not been configured

Run `python scripts/setup.py` from the repository root. Do not post the contents
of `.env` when asking for help.

### Canvas authentication fails

Check that the token is complete, active, and belongs to the intended account.
Run setup again to replace it. Some institutions disable personal access tokens
or apply expiry/scope rules; ask local Canvas support if needed.

### Expected courses or content are missing

Canvas course state, enrolment state, dates, feature flags, institutional
permissions, and token policy affect what the API returns. The program records
inaccessible content categories but cannot bypass Canvas permissions. Compare
with the same account in the Canvas web interface.

### A backup is partial

Open the course `manifest.json` and the timestamped log shown by the command.
Look at `inaccessible_endpoints`, `warnings`, and `errors`. Fix a transient
network or permission issue, then rerun with `--resume`; successfully archived
unchanged files will be skipped.

### A transfer is interrupted

Rerun the same selection with `--resume`. Temporary `.part` files are removed
after handled failures, completed file records are checkpointed, and no
automatic deletion occurs.

### PowerShell cannot activate `.venv`

Use Command Prompt activation or invoke `.\.venv\Scripts\python.exe` directly.
Changing the machine-wide execution policy is not required.

## Current limitations

- Version 0.1 uses personal access tokens. Instructure's OAuth guidance should
  be reviewed before distributing an integration broadly to other users.
- It is a content reference archive, not a complete Canvas export or a browser-
  perfect offline copy. Original HTML is preserved but internal links are not
  rewritten for offline browsing.
- External websites and embedded third-party media are not downloaded.
- New Quiz and question access varies by Canvas installation, feature state,
  permissions, and API support; unavailable endpoints are recorded.
- The conservative deletion policy means the archive can retain obsolete files
  until the user removes them manually.
- No local archive encryption is provided.
- This is an independent project, not an official Instructure product.

## Future roadmap

Potential Version 0.2+ work includes OAuth, archive comparison reports, offline
link reconstruction, an optional interface, and deliberately previewed and
approved Canvas write workflows. None of those write capabilities exist in
Version 0.1.

## License

Canvas VLE Manager is available under the
[Canvas VLE Manager Non-Commercial Licence](LICENSE). Non-commercial use and
derivative works are permitted under its terms. Any commercial use of this
software or a derivative work requires the copyright holder's prior written
permission.
