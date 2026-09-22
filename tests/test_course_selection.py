from __future__ import annotations

import pytest

from src.course_selection import (
    CourseSelectionError,
    filter_courses,
    parse_selection,
    select_courses_by_id,
    select_courses_by_term,
    select_courses_interactively,
)


COURSES = [
    {
        "id": 10,
        "course_code": "ENG1",
        "name": "Dynamics",
        "term": {"id": 1, "name": "Term A"},
        "account": {"id": 100, "name": "Engineering"},
        "current_user_roles": ["TeacherEnrollment"],
        "access_states": ["active"],
        "workflow_state": "available",
        "published": True,
        "is_favorite": True,
    },
    {
        "id": 20,
        "course_code": "ENG2",
        "name": "Statics",
        "term": {"id": 2, "name": "Term B"},
        "current_user_roles": ["TaEnrollment"],
        "access_states": ["completed"],
        "workflow_state": "completed",
        "concluded": True,
        "is_favorite": False,
    },
    {
        "id": 30,
        "course_code": "ENG3",
        "name": "Design",
        "term": {"id": 1, "name": "Term A"},
        "current_user_roles": ["TeacherEnrollment"],
        "access_states": ["active"],
        "workflow_state": "unpublished",
        "published": False,
        "is_favorite": True,
    },
    {
        "id": 40,
        "course_code": "ENG4",
        "name": "Labs",
        "term": None,
        "current_user_roles": ["DesignerEnrollment"],
        "access_states": ["active"],
        "is_favorite": False,
    },
]


def test_parse_selection_supports_lists_ranges_deduplication_and_all() -> None:
    assert parse_selection("1, 3-4; 3", 4) == [0, 2, 3]
    assert parse_selection("all", 4) == [0, 1, 2, 3]


@pytest.mark.parametrize("expression", ["", "0", "5", "two", "4-2", "1-x"])
def test_parse_selection_rejects_invalid_input(expression: str) -> None:
    with pytest.raises(CourseSelectionError):
        parse_selection(expression, 4)


def test_interactive_selection_reprompts_and_returns_only_chosen_courses() -> None:
    answers = iter(["99", "2,4"])
    output: list[str] = []

    selected = select_courses_interactively(
        COURSES,
        input_func=lambda _: next(answers),
        output_func=output.append,
    )

    assert selected == [COURSES[1], COURSES[3]]
    assert any("outside" in line for line in output)
    assert any("ENG1 - Dynamics" in line for line in output)


def test_filter_courses_searches_display_fields_case_insensitively() -> None:
    assert filter_courses(COURSES, search="dynamic") == [COURSES[0]]
    assert filter_courses(COURSES, search="engineering") == [COURSES[0]]
    assert filter_courses(COURSES, search="canvas-id-that-is-not-there") == []
    assert filter_courses(COURSES, search="40") == [COURSES[3]]


def test_filter_courses_combines_term_role_and_state() -> None:
    assert filter_courses(COURSES, term="term a") == [COURSES[0], COURSES[2]]
    assert filter_courses(COURSES, role="teacherenrollment") == [
        COURSES[0],
        COURSES[2],
    ]
    assert filter_courses(COURSES, state="completed") == [COURSES[1]]
    assert filter_courses(COURSES, term="a", state="unpublished") == [COURSES[2]]
    assert filter_courses(COURSES, state="published") == [COURSES[0]]
    assert filter_courses(COURSES, state="concluded") == [COURSES[1]]
    assert filter_courses(COURSES, favorite=True) == [COURSES[0], COURSES[2]]
    assert filter_courses(COURSES, term="term a", favorite=True) == [
        COURSES[0],
        COURSES[2],
    ]
    assert filter_courses(COURSES, term="term b", favorite=True) == []


def test_general_search_can_find_favorite_courses() -> None:
    assert filter_courses(COURSES, search="favorite") == [COURSES[0], COURSES[2]]
    assert filter_courses(COURSES, search="favourite") == [COURSES[0], COURSES[2]]


def test_interactive_filter_can_be_changed_without_restarting_command() -> None:
    answers = iter(["dynamic", "/filter labs", "1"])
    output: list[str] = []

    selected = select_courses_interactively(
        COURSES,
        prompt_for_filter=True,
        input_func=lambda _: next(answers),
        output_func=output.append,
    )

    assert selected == [COURSES[3]]
    rendered = "\n".join(output)
    assert "ENG1 - Dynamics" in rendered
    assert "ENG4 - Labs" in rendered
    assert "Showing 1 of 4" in rendered


def test_interactive_filter_recovers_from_no_matches() -> None:
    answers = iter(["nothing matches", "statics", "1"])
    output: list[str] = []

    selected = select_courses_interactively(
        COURSES,
        prompt_for_filter=True,
        input_func=lambda _: next(answers),
        output_func=output.append,
    )

    assert selected == [COURSES[1]]
    assert any("No courses matched" in line for line in output)


def test_direct_id_selection_preserves_requested_order() -> None:
    assert select_courses_by_id(COURSES, [30, "10", 30]) == [COURSES[2], COURSES[0]]
    with pytest.raises(CourseSelectionError, match="999"):
        select_courses_by_id(COURSES, [999])


def test_term_selection_uses_returned_name_or_canvas_id() -> None:
    assert select_courses_by_term(COURSES, "term a") == [COURSES[0], COURSES[2]]
    assert select_courses_by_term(COURSES, "2") == [COURSES[1]]
    with pytest.raises(CourseSelectionError):
        select_courses_by_term(COURSES, "Academic year invented by client")
