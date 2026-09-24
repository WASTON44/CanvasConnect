"""Upload the user-approved Dynamics PDFs and local lecture-quiz drafts.

The command remains a dry run unless both ``--apply`` and the exact
confirmation phrase are supplied. It uploads only local artifacts and creates
only the explicitly approved Canvas records.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
import json
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401 - adjusts sys.path for direct script execution

from src.config import load_config
from src.course_discovery import write_json_atomic
from src.exceptions import CanvasError
from src.local_upload import CanvasWriteClient, UploadError


CONFIRMATION = "UPLOAD APPROVED PDFS AND LECTURE QUIZZES"
DYNAMICS_COURSE_ID = 38829
PROGRAMMING_COURSE_ID = 38826
CIRCUITS_COURSE_ID = 37937
DYNAMICS_FOLDER_ID = 1460347
QUESTIONS_PDF = Path("output/pdf/NTO1014_intro_projectiles_constant_acceleration_questions.pdf")
SOLUTIONS_PDF = Path("output/pdf/NTO1014_intro_projectiles_constant_acceleration_worked_solutions.pdf")
QUESTIONS_TITLE = "Motion Modelling Challenges - Questions"
SOLUTIONS_TITLE = "Motion Modelling Challenges - Worked Solutions"


@dataclass(frozen=True)
class QuizPlacement:
    course_id: int
    draft_path: Path
    module_name: str
    powerpoint_title: str
    position_offset: int = 1


@dataclass(frozen=True)
class ResolvedQuiz:
    placement: QuizPlacement
    draft: dict[str, Any]
    module_id: int
    module_name: str
    anchor_position: int
    existing_quiz_id: int | None = None
    existing_assignment_id: int | None = None
    existing_module_item_id: int | None = None


def _programming_placements() -> list[QuizPlacement]:
    values = [
        ("lecture_01.json", "1. Getting Started", "1. Getting Started.pptx"),
        ("lecture_02.json", "2. Basic Input and Output", "2. Basic Input and Output.pptx"),
        ("lecture_03.json", "3. Conditional Statements", "3. Conditional Statements.pptx"),
        ("lecture_04.json", "4. Loops", "4. Loops.pptx"),
        ("lecture_05.json", "5. Software Development Methodology", "5. Software Development Methodology.pptx"),
        ("lecture_06.json", "5. Types and Casting", "5. Types and Casting.pptx"),
        ("lecture_07.json", "6. Arrays", "6. Arrays.pptx"),
        ("lecture_08.json", "7. Strings", "7. Strings.pptx"),
        ("lecture_09.json", "8. Functions", "8. Functions.pptx"),
        ("lecture_10.json", "10. Structures", "10. Structures.pptx"),
        ("lecture_11.json", "11. File Input and Output", "11. File Input and Output.pptx"),
        ("lecture_12.json", "9. Pointers", "9. Pointers.pptx"),
    ]
    root = Path("quiz_drafts/NTO1012_2261_AUT")
    return [QuizPlacement(PROGRAMMING_COURSE_ID, root / name, module, deck) for name, module, deck in values]


def _dynamics_placements() -> list[QuizPlacement]:
    values = [
        ("lecture_01.json", "Intro", "Introduction.pptx", 3),
        ("lecture_02.json", "Kinetics", "Kinetics.pptx", 1),
        ("lecture_03.json", "Velocity and Accerleration", "Displacement Velocity Acceleration.pptx", 1),
        ("lecture_04.json", "Circular Motion", "Circular motion new.pptx", 1),
        ("lecture_05.json", "Torque", "Lecture 5 torqueinertia.pptx", 1),
        ("lecture_06.json", "Energy", "Lecture 6 Energy.pptx", 1),
        ("lecture_07.json", "Linear Momentum", "Lecture 9 Linear Momentum.pptx", 1),
        ("lecture_08.json", "Angular Momentum", "Lecture 10 Angular Momentum v2.pptx", 1),
        ("lecture_09.json", "Balancing", "Lecture 8 Balancing.pptx", 1),
        ("lecture_10.json", "Rotating Mechanisms", "Lecture 7 Dynamics of rotating systems(v2).pptx", 1),
    ]
    root = Path("quiz_drafts/NTO1014_2261_AUT")
    return [QuizPlacement(DYNAMICS_COURSE_ID, root / name, module, deck, offset) for name, module, deck, offset in values]


def _load_draft(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise UploadError(f"Unable to load quiz draft {path}.") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("title"), str):
        raise UploadError(f"Quiz draft {path} does not have a title.")
    questions = payload.get("questions")
    if not isinstance(questions, list) or len(questions) != 10:
        raise UploadError(f"Quiz draft {path} must contain exactly 10 questions.")
    for number, question in enumerate(questions, start=1):
        if not isinstance(question, dict):
            raise UploadError(f"Question {number} in {path} is invalid.")
        choices = question.get("choices")
        answer_index = question.get("answer_index")
        if (
            not isinstance(question.get("stem"), str)
            or not isinstance(question.get("topic"), str)
            or not isinstance(question.get("explanation"), str)
            or not isinstance(choices, list)
            or len(choices) != 4
            or not all(isinstance(choice, str) and choice for choice in choices)
            or not isinstance(answer_index, int)
            or answer_index not in range(4)
        ):
            raise UploadError(f"Question {number} in {path} is not a valid four-option quiz question.")
    return payload


def _lookup_unique(records: list[dict[str, Any]], name: str, label: str) -> dict[str, Any]:
    matches = [
        record for record in records
        if isinstance(record.get("name") or record.get("title"), str)
        and str(record.get("name") or record.get("title")).strip().casefold() == name.strip().casefold()
    ]
    if len(matches) != 1:
        raise UploadError(f"Expected exactly one {label} named {name!r}; found {len(matches)}.")
    return matches[0]


def _item_title(item: dict[str, Any]) -> str:
    title = item.get("title")
    if not isinstance(title, str) or not title:
        raise UploadError("Canvas returned a module item with no title.")
    return title


def _question_form(question: dict[str, Any], number: int) -> list[tuple[str, Any]]:
    stem = escape(str(question["stem"])).replace("\n", "<br/>")
    explanation = escape(str(question["explanation"])).replace("\n", "<br/>")
    values: list[tuple[str, Any]] = [
        ("question[question_name]", f"{number}. {question['topic']}"),
        ("question[question_text]", stem),
        ("question[question_type]", "multiple_choice_question"),
        ("question[points_possible]", "1"),
        ("question[correct_comments]", explanation),
        ("question[incorrect_comments]", explanation),
    ]
    for index, choice in enumerate(question["choices"]):
        values.extend(
            [
                (f"question[answers][{index}][answer_text]", escape(choice)),
                (f"question[answers][{index}][answer_weight]", "100" if index == question["answer_index"] else "0"),
            ]
        )
    return values


def _quiz_form(title: str) -> list[tuple[str, Any]]:
    return [
        ("quiz[title]", title),
        ("quiz[description]", ""),
        ("quiz[quiz_type]", "assignment"),
        ("quiz[points_possible]", "10"),
        ("quiz[published]", "false"),
    ]


def _module_item_form(item_type: str, content_id: int, title: str, position: int, published: bool) -> dict[str, Any]:
    return {
        "module_item[type]": item_type,
        "module_item[content_id]": content_id,
        "module_item[title]": title,
        "module_item[position]": position,
        "module_item[published]": "true" if published else "false",
    }


def _existing_titles(
    client: CanvasWriteClient, course_id: int, *, include_files: bool = True
) -> tuple[dict[str, dict[str, Any]], set[str]]:
    quiz_titles = {
        str(quiz.get("title")).strip().casefold(): quiz
        for quiz in client.get_list(f"courses/{course_id}/quizzes")
        if isinstance(quiz.get("title"), str)
    }
    file_titles = set()
    if include_files:
        file_titles = {
            str(file.get("display_name") or file.get("filename") or file.get("name")).strip().casefold()
            for file in client.get_list(f"courses/{course_id}/files")
            if isinstance(file.get("display_name") or file.get("filename") or file.get("name"), str)
        }
    return quiz_titles, file_titles


def _preflight_quizzes(
    client: CanvasWriteClient, placements: list[QuizPlacement], *, resume: bool
) -> list[ResolvedQuiz]:
    by_course: dict[int, list[QuizPlacement]] = {}
    for placement in placements:
        by_course.setdefault(placement.course_id, []).append(placement)
    resolved: list[ResolvedQuiz] = []
    for course_id, course_placements in by_course.items():
        quiz_titles, _ = _existing_titles(client, course_id, include_files=False)
        modules = client.get_list(f"courses/{course_id}/modules")
        for placement in course_placements:
            draft = _load_draft(placement.draft_path)
            title = draft["title"]
            existing_quiz = quiz_titles.get(title.strip().casefold())
            if existing_quiz is not None and not resume:
                raise UploadError(f"Canvas already contains a Classic Quiz named {title!r} in course {course_id}.")
            module = _lookup_unique(modules, placement.module_name, "module")
            module_id = module.get("id")
            if not isinstance(module_id, int):
                raise UploadError(f"Target module {placement.module_name!r} has no numeric Canvas ID.")
            items = client.get_list(f"courses/{course_id}/modules/{module_id}/items")
            anchor = _lookup_unique(items, placement.powerpoint_title, "source PowerPoint module item")
            if anchor.get("type") != "File" or not isinstance(anchor.get("position"), int):
                raise UploadError(f"Source PowerPoint {placement.powerpoint_title!r} is not a positioned file item.")
            matching_items = [item for item in items if _item_title(item).strip().casefold() == title.strip().casefold()]
            if existing_quiz is None and matching_items:
                raise UploadError(f"Module {placement.module_name!r} already contains an item named {title!r}.")
            existing_quiz_id: int | None = None
            existing_assignment_id: int | None = None
            existing_module_item_id: int | None = None
            if existing_quiz is not None:
                existing_quiz_id = existing_quiz.get("id")
                existing_assignment_id = existing_quiz.get("assignment_id")
                if not isinstance(existing_quiz_id, int) or existing_quiz.get("published") is not False:
                    raise UploadError(f"Existing quiz {title!r} cannot safely be resumed.")
                quiz_details = client.get_json(f"courses/{course_id}/quizzes/{existing_quiz_id}")
                if quiz_details.get("question_count") != 10:
                    raise UploadError(f"Existing quiz {title!r} does not contain the expected 10 questions.")
                if len(matching_items) != 1:
                    raise UploadError(f"Existing quiz {title!r} does not have exactly one module item in {placement.module_name!r}.")
                existing_item = matching_items[0]
                if (
                    existing_item.get("type") != "Quiz"
                    or existing_item.get("content_id") != existing_quiz_id
                    or existing_item.get("published") is not False
                    or not isinstance(existing_item.get("id"), int)
                ):
                    raise UploadError(f"Existing module item for quiz {title!r} cannot safely be resumed.")
                existing_module_item_id = int(existing_item["id"])
                if isinstance(existing_assignment_id, int):
                    assignment = client.get_json(f"courses/{course_id}/assignments/{existing_assignment_id}")
                    if assignment.get("published") is not False:
                        raise UploadError(f"Existing quiz assignment for {title!r} is not unpublished.")
            resolved.append(
                ResolvedQuiz(
                    placement, draft, module_id, str(module.get("name")), int(anchor["position"]),
                    existing_quiz_id, existing_assignment_id, existing_module_item_id,
                )
            )
    return resolved


def _preflight_pdfs(client: CanvasWriteClient, *, resume: bool) -> dict[str, Any]:
    for path in (QUESTIONS_PDF, SOLUTIONS_PDF):
        if not path.is_file() or path.stat().st_size == 0:
            raise UploadError(f"Approved PDF is missing or empty: {path}")
    _, file_titles = _existing_titles(client, DYNAMICS_COURSE_ID, include_files=True)
    modules = client.get_list(f"courses/{DYNAMICS_COURSE_ID}/modules")
    intro = _lookup_unique(modules, "Intro", "Dynamics Intro module")
    module_id = intro.get("id")
    if not isinstance(module_id, int):
        raise UploadError("Dynamics Intro module has no numeric Canvas ID.")
    items = client.get_list(f"courses/{DYNAMICS_COURSE_ID}/modules/{module_id}/items")
    anchor = _lookup_unique(items, "Introduction.pptx", "Introduction PowerPoint module item")
    if anchor.get("type") != "File" or anchor.get("position") != 1:
        raise UploadError("Introduction PowerPoint is no longer the first file item in the Intro module.")
    question_items = [item for item in items if _item_title(item).strip().casefold() == QUESTIONS_TITLE.casefold()]
    solution_items = [item for item in items if _item_title(item).strip().casefold() == SOLUTIONS_TITLE.casefold()]
    if question_items or solution_items:
        if not resume or len(question_items) != 1 or len(solution_items) != 1:
            raise UploadError("Dynamics PDF module items already exist and cannot safely be resumed.")
        question_item, solution_item = question_items[0], solution_items[0]
        questions_id = question_item.get("content_id")
        solutions_id = solution_item.get("content_id")
        if (
            question_item.get("type") != "File"
            or solution_item.get("type") != "File"
            or not isinstance(questions_id, int)
            or not isinstance(solutions_id, int)
        ):
            raise UploadError("Existing Dynamics PDF module items do not identify their files.")
        questions_file = client.get_json(f"files/{questions_id}")
        solutions_file = client.get_json(f"files/{solutions_id}")
        if (
            questions_file.get("hidden") is not False
            or questions_file.get("locked") is not False
            or question_item.get("published") is not True
            or solutions_file.get("hidden") is not True
            or solutions_file.get("locked") is not True
            or solution_item.get("published") is not False
        ):
            raise UploadError("Existing Dynamics PDFs do not have the approved visibility settings.")
        return {
            "module_id": module_id,
            "folder_id": DYNAMICS_FOLDER_ID,
            "folder_name": "course files",
            "already_uploaded": True,
            "existing_result": {
                "questions_file_id": questions_id,
                "questions_module_item_id": question_item.get("id"),
                "solutions_file_id": solutions_id,
                "solutions_module_item_id": solution_item.get("id"),
            },
        }
    for path in (QUESTIONS_PDF, SOLUTIONS_PDF):
        if path.name.casefold() in file_titles:
            raise UploadError(f"Canvas already contains a file named {path.name!r}; refusing to upload a duplicate.")
    folder = client.get_json(f"folders/{DYNAMICS_FOLDER_ID}")
    return {
        "module_id": module_id,
        "folder_id": DYNAMICS_FOLDER_ID,
        "folder_name": folder.get("full_name"),
        "already_uploaded": False,
    }


def _create_pdf_items(client: CanvasWriteClient, preflight: dict[str, Any]) -> dict[str, Any]:
    if preflight.get("already_uploaded") is True:
        return dict(preflight["existing_result"])
    questions_file = client.upload_file(DYNAMICS_COURSE_ID, preflight["folder_id"], QUESTIONS_PDF, "application/pdf")
    questions_id = questions_file.get("id")
    if not isinstance(questions_id, int):
        raise UploadError("Canvas did not return an ID for the questions PDF.")
    client.put_form(f"files/{questions_id}", {"hidden": "false", "locked": "false"})
    questions_item = client.post_form(
        f"courses/{DYNAMICS_COURSE_ID}/modules/{preflight['module_id']}/items",
        _module_item_form("File", questions_id, QUESTIONS_TITLE, 2, True),
    )

    solutions_file = client.upload_file(DYNAMICS_COURSE_ID, preflight["folder_id"], SOLUTIONS_PDF, "application/pdf")
    solutions_id = solutions_file.get("id")
    if not isinstance(solutions_id, int):
        raise UploadError("Canvas did not return an ID for the worked-solutions PDF.")
    client.put_form(f"files/{solutions_id}", {"hidden": "true", "locked": "true"})
    solutions_item = client.post_form(
        f"courses/{DYNAMICS_COURSE_ID}/modules/{preflight['module_id']}/items",
        _module_item_form("File", solutions_id, SOLUTIONS_TITLE, 3, False),
    )
    return {
        "questions_file_id": questions_id,
        "questions_module_item_id": questions_item.get("id"),
        "solutions_file_id": solutions_id,
        "solutions_module_item_id": solutions_item.get("id"),
    }


def _create_quiz(client: CanvasWriteClient, item: ResolvedQuiz) -> dict[str, Any]:
    if item.existing_quiz_id is not None:
        return {
            "course_id": item.placement.course_id,
            "title": item.draft["title"],
            "quiz_id": item.existing_quiz_id,
            "assignment_id": item.existing_assignment_id,
            "module_id": item.module_id,
            "module_name": item.module_name,
            "module_item_id": item.existing_module_item_id,
            "question_count": len(item.draft["questions"]),
            "status": "reused",
        }
    created = client.post_form(f"courses/{item.placement.course_id}/quizzes", _quiz_form(item.draft["title"]))
    quiz_id = created.get("id")
    if not isinstance(quiz_id, int):
        raise UploadError(f"Canvas did not return an ID for quiz {item.draft['title']!r}.")
    for number, question in enumerate(item.draft["questions"], start=1):
        client.post_form(
            f"courses/{item.placement.course_id}/quizzes/{quiz_id}/questions",
            _question_form(question, number),
        )
    client.put_form(
        f"courses/{item.placement.course_id}/quizzes/{quiz_id}",
        {"quiz[published]": "false", "quiz[notify_of_update]": "false"},
    )
    assignment_id = created.get("assignment_id")
    if isinstance(assignment_id, int):
        client.put_form(
            f"courses/{item.placement.course_id}/assignments/{assignment_id}",
            {"assignment[published]": "false"},
        )
    module_item = client.post_form(
        f"courses/{item.placement.course_id}/modules/{item.module_id}/items",
        _module_item_form(
            "Quiz", quiz_id, item.draft["title"], item.anchor_position + item.placement.position_offset, False
        ),
    )
    return {
        "course_id": item.placement.course_id,
        "title": item.draft["title"],
        "quiz_id": quiz_id,
        "assignment_id": assignment_id,
        "module_id": item.module_id,
        "module_name": item.module_name,
        "module_item_id": module_item.get("id"),
        "question_count": len(item.draft["questions"]),
        "status": "created",
    }


def _verify_pdf_items(client: CanvasWriteClient, preflight: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    questions_file = client.get_json(f"files/{result['questions_file_id']}")
    solutions_file = client.get_json(f"files/{result['solutions_file_id']}")
    items = client.get_list(f"courses/{DYNAMICS_COURSE_ID}/modules/{preflight['module_id']}/items")
    questions_item = _lookup_unique(items, QUESTIONS_TITLE, "questions PDF module item")
    solutions_item = _lookup_unique(items, SOLUTIONS_TITLE, "worked-solutions PDF module item")
    if (
        questions_file.get("hidden") is not False
        or questions_file.get("locked") is not False
        or questions_item.get("published") is not True
        or solutions_file.get("hidden") is not True
        or solutions_file.get("locked") is not True
        or solutions_item.get("published") is not False
    ):
        raise UploadError("Canvas read-back did not confirm the approved PDF visibility settings.")
    return {
        "questions": {"file_id": questions_file.get("id"), "module_item_id": questions_item.get("id"), "published": questions_item.get("published")},
        "solutions": {"file_id": solutions_file.get("id"), "module_item_id": solutions_item.get("id"), "published": solutions_item.get("published")},
    }


def _verify_quiz(client: CanvasWriteClient, result: dict[str, Any]) -> dict[str, Any]:
    course_id = int(result["course_id"])
    quiz_id = int(result["quiz_id"])
    quiz = client.get_json(f"courses/{course_id}/quizzes/{quiz_id}")
    question_count = quiz.get("question_count")
    items = client.get_list(f"courses/{course_id}/modules/{result['module_id']}/items")
    item = _lookup_unique(items, str(result["title"]), "quiz module item")
    if quiz.get("published") is not False or question_count != 10 or item.get("published") is not False:
        raise UploadError(f"Canvas read-back did not confirm quiz {result['title']!r} as an unpublished 10-question draft.")
    assignment_id = result.get("assignment_id")
    assignment_published = None
    if isinstance(assignment_id, int):
        assignment = client.get_json(f"courses/{course_id}/assignments/{assignment_id}")
        assignment_published = assignment.get("published")
        if assignment_published is not False:
            raise UploadError(f"Canvas read-back did not confirm quiz assignment {assignment_id} as unpublished.")
    return {
        "course_id": course_id,
        "quiz_id": quiz.get("id"),
        "title": quiz.get("title"),
        "question_count": question_count,
        "published": quiz.get("published"),
        "module_item_id": item.get("id"),
        "module_item_published": item.get("published"),
        "assignment_id": assignment_id,
        "assignment_published": assignment_published,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="perform the approved Canvas writes")
    parser.add_argument("--confirm", default="", help=f"must exactly equal {CONFIRMATION!r} with --apply")
    parser.add_argument("--resume", action="store_true", help="reuse only verified matching items from an interrupted upload")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    placements = _programming_placements() + _dynamics_placements()
    if args.apply and args.confirm != CONFIRMATION:
        print("Upload stopped: --confirm must use the exact approved confirmation phrase.")
        return 2
    config = load_config()
    report: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "source": "local PDFs and quiz_drafts JSON only",
        "status": "dry_run" if not args.apply else "running",
        "quiz_count": len(placements),
        "skipped": [{"course_id": CIRCUITS_COURSE_ID, "quiz_count": 19, "reason": "target course has no modules or lecture PowerPoints"}],
    }
    with CanvasWriteClient(config.base_url, config.api_token) as client:
        pdf_preflight = _preflight_pdfs(client, resume=args.resume)
        quizzes = _preflight_quizzes(client, placements, resume=args.resume)
        report["courses"] = {
            str(course_id): client.get_json(f"courses/{course_id}").get("workflow_state")
            for course_id in (PROGRAMMING_COURSE_ID, DYNAMICS_COURSE_ID, CIRCUITS_COURSE_ID)
        }
        report["pdf_destination"] = pdf_preflight
        report["quiz_destinations"] = [
            {"course_id": item.placement.course_id, "title": item.draft["title"], "module": item.module_name, "after": item.placement.powerpoint_title}
            for item in quizzes
        ]
        if not args.apply:
            print(json.dumps(report, ensure_ascii=True, indent=2))
            return 0
        pdf_result = _create_pdf_items(client, pdf_preflight)
        quiz_results = [_create_quiz(client, item) for item in quizzes]
        report["pdfs"] = _verify_pdf_items(client, pdf_preflight, pdf_result)
        report["quizzes"] = [_verify_quiz(client, result) for result in quiz_results]
        report["status"] = "succeeded"
    report["completed_at"] = datetime.now(UTC).isoformat()
    report_path = Path("Active/deployment_reports") / ("approved_quiz_pdf_upload_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S") + ".json")
    write_json_atomic(report_path, report)
    print(json.dumps({"status": report["status"], "report_path": str(report_path), "pdfs": report.get("pdfs"), "quiz_count": len(report.get("quizzes", [])), "skipped": report["skipped"]}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (UploadError, CanvasError) as error:
        print(f"Upload stopped: {error}")
        raise SystemExit(1)
