"""Create new-format NTO1014 motion-modelling practice PDFs.

The activities use the constant-acceleration and projectile methods introduced
in ``Lecture 1 Introduction.pptx``. They deliberately use new contexts,
values and task formats rather than adapting the lecture examples.
"""

from __future__ import annotations

from math import cos, pi, sin, sqrt
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


OUTPUT = Path("output/pdf")
G = 9.81


def value(number: float, places: int = 3) -> str:
    return f"{number:.{places}f}".rstrip("0").rstrip(".")


def styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "PracticeTitle", parent=base["Title"], alignment=TA_CENTER,
            fontName="Helvetica-Bold", fontSize=18, leading=22,
            textColor=colors.HexColor("#17365D"), spaceAfter=6 * mm,
        ),
        "subtitle": ParagraphStyle(
            "PracticeSubtitle", parent=base["BodyText"], alignment=TA_CENTER,
            fontSize=9.5, leading=13, textColor=colors.HexColor("#4A5568"), spaceAfter=6 * mm,
        ),
        "heading": ParagraphStyle(
            "PracticeHeading", parent=base["Heading2"], fontName="Helvetica-Bold",
            fontSize=12, leading=15, textColor=colors.HexColor("#17365D"),
            spaceBefore=4 * mm, spaceAfter=2 * mm,
        ),
        "body": ParagraphStyle(
            "PracticeBody", parent=base["BodyText"], fontName="Helvetica",
            fontSize=10.3, leading=14.2, spaceAfter=2.2 * mm,
        ),
    }


def header_footer(canvas, doc) -> None:  # type: ignore[no-untyped-def]
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#B8C7D9"))
    canvas.line(20 * mm, 13 * mm, A4[0] - 20 * mm, 13 * mm)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#596A7A"))
    canvas.drawString(20 * mm, 8.5 * mm, "NTO1014 Dynamics - motion modelling")
    canvas.drawRightString(A4[0] - 20 * mm, 8.5 * mm, f"Page {doc.page}")
    canvas.restoreState()


def document(path: Path, title: str) -> SimpleDocTemplate:
    return SimpleDocTemplate(
        str(path), pagesize=A4, title=title, author="Canvas VLE Manager",
        leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
    )


def equations_box(style: dict[str, ParagraphStyle]) -> Table:
    table = Table(
        [[Paragraph(
            "<b>Use throughout:</b> g = 9.81 m/s<super>2</super>. Take upward as positive unless stated otherwise.",
            style["body"],
        )]],
        colWidths=[170 * mm],
    )
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#EAF1F8")),
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#9DB2CE")),
        ("LEFTPADDING", (0, 0), (-1, -1), 4 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4 * mm),
        ("TOPPADDING", (0, 0), (-1, -1), 3 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3 * mm),
    ]))
    return table


def activity_heading(style: dict[str, ParagraphStyle], title: str, marks: int) -> Paragraph:
    return Paragraph(f"<b>{title} ({marks} marks)</b>", style["heading"])


def activity(style: dict[str, ParagraphStyle], title: str, marks: int, text: str) -> KeepTogether:
    return KeepTogether([
        activity_heading(style, title, marks),
        Paragraph(text, style["body"]),
        Spacer(1, 2 * mm),
    ])


def solution(style: dict[str, ParagraphStyle], title: str, text: str) -> KeepTogether:
    return KeepTogether([
        Paragraph(f"<b>{title}</b>", style["heading"]),
        Paragraph(text, style["body"]),
        Spacer(1, 1.5 * mm),
    ])


def motion_log_table() -> Table:
    table = Table(
        [
            ["Time, t (s)", "0.0", "1.5", "3.0", "4.5"],
            ["Velocity, v (m/s)", "4.0", "7.3", "10.6", "13.9"],
        ],
        colWidths=[42 * mm, 31 * mm, 31 * mm, 31 * mm, 31 * mm],
    )
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#17365D")),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.white),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#9DB2CE")),
        ("BACKGROUND", (1, 0), (-1, -1), colors.HexColor("#F5F8FB")),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5 * mm),
    ]))
    return table


def build_questions(path: Path) -> None:
    s = styles()
    story = [
        Paragraph("Motion Modelling Challenges", s["title"]),
        Paragraph(
            "NTO1014 Dynamics - additional practice<br/>"
            "New tasks on constant acceleration and projectiles, using data, decisions and explanation as well as calculation.",
            s["subtitle"],
        ),
        equations_box(s),
        Spacer(1, 4 * mm),
        Paragraph("Instructions", s["heading"]),
        Paragraph(
            "Show enough working for a reader to follow your reasoning. Include units, make directions clear, "
            "and neglect air resistance unless an activity says otherwise.",
            s["body"],
        ),
        activity(
            s, "Activity 1: Plan a safe stopping distance", 8,
            "A battery-powered trolley starts from rest and accelerates uniformly at 1.80 m/s<super>2</super> for 5.00 s. "
            "It then brakes uniformly at 2.40 m/s<super>2</super> until it stops. A safety line is 35.0 m from the starting point. "
            "Use a two-stage motion model to decide whether the trolley stops before the line. Give its total stopping distance and, "
            "if it crosses the line, the distance by which it overruns it.",
        ),
        activity(
            s, "Activity 2: Test a braking-zone design", 7,
            "During a braking test, a vehicle passes marker A at 14.0 m/s. It passes marker B, 24.0 m later along the track, at 10.0 m/s. "
            "Assume its deceleration is uniform. A safety bay begins 12.0 m beyond marker B. Find the deceleration and decide whether "
            "the vehicle stops within the safety bay. State the distance by which it stops short of, or passes beyond, the bay.",
        ),
        activity(
            s, "Activity 3: Check a detector-window design", 8,
            "A launcher 1.50 m above the floor fires a test ball at 19.5 m/s. Its launch angle theta satisfies tan(theta) = 5/12. "
            "A vertical detector plane is 18.0 m from the launcher and has an opening from 3.80 m to 4.30 m above the floor. "
            "Calculate the ball's velocity components, its height at the detector plane, and its vertical velocity there. "
            "Decide whether the ball passes through the opening and state whether it is rising or falling.",
        ),
        PageBreak(),
        Paragraph("Projectile Modelling", s["title"]),
        activity(
            s, "Activity 4: Build a model from components", 8,
            "A computer-controlled launcher gives a foam ball initial velocity components u<sub>x</sub> = 12.0 m/s and "
            "u<sub>y</sub> = 16.0 m/s. Take the launch point as x = 0, y = 0. Write equations for x and y in terms of time. "
            "Use them to give the ball's position and vertical velocity at t = 1.20 s. State whether it is rising or falling at that "
            "moment, and find its greatest height.",
        ),
        activity(
            s, "Activity 5: Make a clearance decision", 7,
            "A ball leaves a testing machine at ground level at 18.0 m/s and 40 degrees above the horizontal. "
            "A 3.50 m high mesh barrier is 22.0 m from the machine. Calculate the ball's height when it reaches the barrier, "
            "then decide whether it clears it. State the vertical direction of travel at the barrier.",
        ),
        activity(
            s, "Activity 6: Specify a safe conveyor speed", 7,
            "A component leaves a horizontal conveyor 1.80 m above the floor. A collection bin starts 0.95 m from the conveyor edge "
            "and ends 1.25 m from the edge. Determine the range of horizontal conveyor speeds that land the component in the bin. "
            "Give the lower and upper speed limits.",
        ),
    ]
    document(path, "NTO1014 Motion Modelling Challenges").build(
        story, onFirstPage=header_footer, onLaterPages=header_footer
    )


def build_solutions(path: Path) -> None:
    s = styles()
    speed1_after_acceleration = 1.80 * 5.00
    distance1_acceleration = 0.5 * 1.80 * 5.00**2
    distance1_braking = speed1_after_acceleration**2 / (2 * 2.40)
    distance1_total = distance1_acceleration + distance1_braking
    overrun1 = distance1_total - 35.0
    acceleration2 = (10.0**2 - 14.0**2) / (2 * 24.0)
    stopping_distance2 = 10.0**2 / (2 * abs(acceleration2))
    overrun2 = stopping_distance2 - 12.0
    ux3 = 19.5 * 12.0 / 13.0
    uy3 = 19.5 * 5.0 / 13.0
    time3 = 18.0 / ux3
    height3 = 1.50 + uy3 * time3 - 0.5 * G * time3**2
    vy3 = uy3 - G * time3
    time4 = 1.20
    x4 = 12.0 * time4
    y4 = 16.0 * time4 - 0.5 * G * time4**2
    vy4 = 16.0 - G * time4
    highest4 = 16.0**2 / (2 * G)
    ux5 = 18.0 * cos(40.0 * pi / 180)
    uy5 = 18.0 * sin(40.0 * pi / 180)
    time5 = 22.0 / ux5
    y5 = uy5 * time5 - 0.5 * G * time5**2
    vy5 = uy5 - G * time5
    time6 = sqrt(2 * 1.80 / G)
    speed6_low = 0.95 / time6
    speed6_high = 1.25 / time6

    story = [
        Paragraph("Worked Solutions", s["title"]),
        Paragraph(
            "NTO1014 Dynamics - Motion Modelling Challenges<br/>"
            "Use alongside the separate activity sheet. Values are rounded sensibly at the final step.",
            s["subtitle"],
        ),
        equations_box(s),
        solution(
            s, "Activity 1: Plan a safe stopping distance",
            f"Stage 1, acceleration: v = u + at = 0 + 1.80(5.00) = <b>{value(speed1_after_acceleration)} m/s</b>. "
            f"The distance is s = 1/2 at<super>2</super> = 1/2(1.80)(5.00)<super>2</super> = {value(distance1_acceleration)} m.<br/>"
            f"Stage 2, braking: 0 = {value(speed1_after_acceleration)}<super>2</super> - 2(2.40)s, so the braking distance is "
            f"{value(distance1_braking)} m. The total stopping distance is {value(distance1_acceleration)} + {value(distance1_braking)} "
            f"= <b>{value(distance1_total)} m</b>.<br/>"
            f"The trolley does not stop before the 35.0 m line. It overruns it by <b>{value(overrun1)} m</b>.",
        ),
        solution(
            s, "Activity 2: Test a braking-zone design",
            f"Using v<super>2</super> = u<super>2</super> + 2as between the markers: "
            f"10.0<super>2</super> = 14.0<super>2</super> + 2a(24.0). Therefore a = <b>{value(acceleration2)} m/s<super>2</super></b>.<br/>"
            f"From marker B, 0 = 10.0<super>2</super> + 2({value(acceleration2)})s. The stopping distance is "
            f"<b>{value(stopping_distance2)} m</b> beyond marker B.<br/>"
            f"The safety bay is only 12.0 m long, so the vehicle passes beyond it by <b>{value(overrun2)} m</b>.",
        ),
        solution(
            s, "Activity 3: Check a detector-window design",
            f"tan(theta) = 5/12 gives a 5-12-13 velocity triangle. Thus u<sub>x</sub> = 19.5(12/13) = "
            f"<b>{value(ux3)} m/s</b> and u<sub>y</sub> = 19.5(5/13) = <b>{value(uy3)} m/s</b>.<br/>"
            f"At the detector plane, t = x/u<sub>x</sub> = 18.0/{value(ux3)} = {value(time3)} s. "
            f"The height is y = 1.50 + {value(uy3)}({value(time3)}) - 4.905({value(time3)})<super>2</super> "
            f"= <b>{value(height3)} m</b>, which lies within the 3.80 m to 4.30 m opening.<br/>"
            f"v<sub>y</sub> = {value(uy3)} - 9.81({value(time3)}) = <b>{value(vy3)} m/s</b>. "
            f"The negative value means the ball passes through the opening while falling.",
        ),
        PageBreak(),
        Paragraph("Worked Solutions", s["title"]),
        solution(
            s, "Activity 4: Build a model from components",
            f"Horizontal motion: x = 12.0t. Vertical motion: y = 16.0t - 4.905t<super>2</super>.<br/>"
            f"At t = 1.20 s, x = <b>{value(x4)} m</b> and y = 16.0(1.20) - 4.905(1.20)<super>2</super> = <b>{value(y4)} m</b>. "
            f"v<sub>y</sub> = 16.0 - 9.81(1.20) = <b>{value(vy4)} m/s upward</b>, so the ball is still rising.<br/>"
            f"At the highest point v<sub>y</sub> = 0, giving y = u<sub>y</sub><super>2</super>/(2g) = "
            f"16.0<super>2</super>/(2(9.81)) = <b>{value(highest4)} m</b>.",
        ),
        solution(
            s, "Activity 5: Make a clearance decision",
            f"u<sub>x</sub> = 18.0 cos 40 degrees = {value(ux5)} m/s and u<sub>y</sub> = 18.0 sin 40 degrees = {value(uy5)} m/s.<br/>"
            f"The time to reach the barrier is t = 22.0/{value(ux5)} = {value(time5)} s. "
            f"Its height is y = {value(uy5)}({value(time5)}) - 4.905({value(time5)})<super>2</super> = <b>{value(y5)} m</b>.<br/>"
            f"It clears the 3.50 m barrier by {value(y5 - 3.5)} m. "
            f"v<sub>y</sub> = {value(uy5)} - 9.81({value(time5)}) = <b>{value(vy5)} m/s</b>, so it is travelling downward.",
        ),
        solution(
            s, "Activity 6: Specify a safe conveyor speed",
            f"The flight time depends only on the 1.80 m vertical drop: 1.80 = 1/2 gt<super>2</super>, so t = <b>{value(time6)} s</b>.<br/>"
            f"For the near edge of the bin, u<sub>x</sub> = 0.95/{value(time6)} = <b>{value(speed6_low)} m/s</b>. "
            f"For the far edge, u<sub>x</sub> = 1.25/{value(time6)} = <b>{value(speed6_high)} m/s</b>.<br/>"
            f"The conveyor must therefore run from <b>{value(speed6_low)} m/s to {value(speed6_high)} m/s</b>, inclusive.",
        ),
    ]
    document(path, "NTO1014 Motion Modelling Challenge Worked Solutions").build(
        story, onFirstPage=header_footer, onLaterPages=header_footer
    )


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    build_questions(OUTPUT / "NTO1014_intro_projectiles_constant_acceleration_questions.pdf")
    build_solutions(OUTPUT / "NTO1014_intro_projectiles_constant_acceleration_worked_solutions.pdf")


if __name__ == "__main__":
    main()
