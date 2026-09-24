"""Deploy approved local Active course copies to their matched Canvas courses.

This command is intentionally inert unless both ``--apply`` and the exact
confirmation phrase are provided.  It never copies from another Canvas course;
the only content source is the local ``Active`` directory.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import _bootstrap  # noqa: F401 - adjusts sys.path for direct script execution

from src.config import load_config
from src.course_discovery import write_json_atomic
from src.exceptions import CanvasError
from src.local_upload import ActiveCourse, CanvasWriteClient, CourseDeployment, UploadError


CONFIRMATION = "UPLOAD APPROVED ACTIVE COURSES"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--active-root", type=Path, default=Path("Active"))
    parser.add_argument(
        "--course",
        action="append",
        default=[],
        metavar="CODE",
        help="deploy only this course code; may be supplied more than once",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="perform the Canvas upload after all local safety checks",
    )
    parser.add_argument(
        "--confirm",
        default="",
        help=f"must exactly equal {CONFIRMATION!r} when --apply is used",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="resume one interrupted deployment by matching only exact local source names",
    )
    parser.add_argument(
        "--omit-missing-quiz-media",
        action="store_true",
        help="for the approved Dynamics recovery only, omit unavailable image tags from New Quiz HTML",
    )
    parser.add_argument(
        "--omit-empty-files",
        action="store_true",
        help="for the approved Dynamics recovery only, omit archived zero-byte files Canvas rejects",
    )
    return parser


def discover_active_courses(root: Path, selected_codes: list[str]) -> list[ActiveCourse]:
    if not root.is_dir():
        raise UploadError(f"Active working-copy directory was not found: {root}")
    selected = {value.strip().casefold() for value in selected_codes if value.strip()}
    courses = [
        ActiveCourse.load(path)
        for path in sorted(root.iterdir())
        if path.is_dir() and (path / "course.json").is_file()
    ]
    if selected:
        courses = [course for course in courses if course.course_code.casefold() in selected]
    if not courses:
        raise UploadError("No Active courses matched the requested selection.")
    missing = selected - {course.course_code.casefold() for course in courses}
    if missing:
        raise UploadError("No Active course was found for: " + ", ".join(sorted(missing)))
    return courses


def preview(courses: list[ActiveCourse]) -> None:
    print("Approved local upload preview")
    print("=============================")
    for course in courses:
        manifest = json.loads((course.root / "manifest.json").read_text(encoding="utf-8"))
        counts = manifest.get("counts", {})
        print(f"\n{course.course_code}: Canvas target {course.target_course_id}")
        print(
            "  "
            + ", ".join(
                f"{key}={counts.get(key, 0)}"
                for key in (
                    "modules",
                    "module_items",
                    "files",
                    "pages",
                    "assignments",
                    "classic_quizzes",
                    "new_quizzes",
                    "discussions",
                )
            )
        )
        print(f"  announcements excluded={counts.get('announcements', 0)}")
    print("\nCourse state remains unpublished. Only each first module is published.")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    courses = discover_active_courses(args.active_root, args.course)
    preview(courses)
    if not args.apply:
        print("\nDry run only. No Canvas request was made.")
        return 0
    if args.confirm != CONFIRMATION:
        print("\nUpload stopped: --confirm must use the exact approved confirmation phrase.")
        return 2
    if args.resume and len(courses) != 1:
        print("\nUpload stopped: --resume requires exactly one --course selection.")
        return 2
    if (
        args.omit_missing_quiz_media or args.omit_empty_files
    ) and [course.course_code for course in courses] != ["NTO1014"]:
        print("\nUpload stopped: recovery omission options are limited to the approved NTO1014 recovery.")
        return 2

    config = load_config()
    report: dict[str, object] = {
        "started_at": datetime.now(UTC).isoformat(),
        "confirmation": CONFIRMATION,
        "source": "local Active working copies",
        "courses": [],
    }
    failed = False
    with CanvasWriteClient(config.base_url, config.api_token) as client:
        for course in courses:
            try:
                result = CourseDeployment(
                    client,
                    course,
                    resume=args.resume,
                    omit_missing_quiz_media=args.omit_missing_quiz_media,
                    omit_empty_files=args.omit_empty_files,
                ).run()
                report["courses"].append({"status": "succeeded", **result})  # type: ignore[index]
                print(f"\nCompleted {course.course_code} (Canvas {course.target_course_id}).")
            except CanvasError as error:
                failed = True
                report["courses"].append(  # type: ignore[index]
                    {
                        "status": "failed",
                        "target_course_id": course.target_course_id,
                        "course_code": course.course_code,
                        "error": str(error),
                    }
                )
                print(f"\nStopped at {course.course_code}: {error}")
                break
    report["completed_at"] = datetime.now(UTC).isoformat()
    report_path = args.active_root / "deployment_reports" / (
        "upload_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S") + ".json"
    )
    write_json_atomic(report_path, report)
    print(f"Deployment report: {report_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (UploadError, CanvasError) as error:
        print(error)
        raise SystemExit(1)
