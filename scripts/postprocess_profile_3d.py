#!/usr/bin/env python3
"""Add a compact temporal frame to github-profile-3d-contrib SVG output.

The upstream action is intentionally kept untouched. This script post-processes
its local SVG deterministically using only Python's standard library.
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
import xml.etree.ElementTree as ET

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)

TOP_BAND = 72.0
PERSPECTIVE_SCALE = 0.94
PERSPECTIVE_PIVOT_Y = 420.0
MARKER = "v1"
MONTH_LABELS = ("Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez")
DAY_LABELS = ((1, "Seg"), (3, "Qua"), (5, "Sex"))  # GitHub: Sunday=0
PERIOD_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) / (\d{4}-\d{2}-\d{2})$")
TRANSLATE_RE = re.compile(r"^translate\(\s*(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s*\)")


def qname(local: str) -> str:
    return f"{{{SVG_NS}}}{local}"


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_viewbox(root: ET.Element) -> tuple[float, float, float, float]:
    values = [float(v) for v in root.attrib.get("viewBox", "").replace(",", " ").split()]
    if len(values) != 4:
        raise ValueError("SVG viewBox is missing or invalid")
    return values[0], values[1], values[2], values[3]


def fmt(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def find_calendar_group(root: ET.Element) -> ET.Element:
    for child in list(root):
        if local_name(child.tag) != "g":
            continue
        for elem in child.iter():
            cls = elem.attrib.get("class", "")
            if cls.startswith("cont-top-"):
                return child
    raise ValueError("3D contribution calendar group was not found")


def find_period(root: ET.Element, year: int) -> tuple[date, date]:
    for elem in root.iter(qname("text")):
        value = (elem.text or "").strip()
        match = PERIOD_RE.match(value)
        if match:
            return (
                datetime.strptime(match.group(1), "%Y-%m-%d").date(),
                datetime.strptime(match.group(2), "%Y-%m-%d").date(),
            )
    return date(year, 1, 1), date(year, 12, 31)


def github_weekday(value: date) -> int:
    return (value.weekday() + 1) % 7


def sunday_of_week(value: date) -> date:
    return value - timedelta(days=github_weekday(value))


def first_on_or_after(start: date, weekday: int) -> date:
    delta = (weekday - github_weekday(start)) % 7
    return start + timedelta(days=delta)


def infer_offset_y(
    calendar: ET.Element,
    start_date: date,
    dx: float,
    dy: float,
    canvas_height: float,
) -> float:
    sunday0 = sunday_of_week(start_date)
    bar_index = 0
    for bar in list(calendar):
        if local_name(bar.tag) != "g":
            continue
        transform = bar.attrib.get("transform", "")
        match = TRANSLATE_RE.match(transform)
        if not match:
            continue
        is_zero = any(
            elem.attrib.get("class") == "cont-top-0" for elem in bar.iter()
        )
        if is_zero:
            current_date = start_date + timedelta(days=bar_index)
            week = (current_date - sunday0).days // 7
            day = github_weekday(current_date)
            top_y = float(match.group(2))
            return top_y + 3.0 - (week + day) * dy
        bar_index += 1

    # Deterministic fallback for a full calendar-year grid in upstream v0.9.3.
    total_days = (date(start_date.year + 1, 1, 1) - date(start_date.year, 1, 1)).days
    first_day = github_weekday(date(start_date.year, 1, 1))
    week_count = math.ceil((total_days + first_day) / 7.0)
    return canvas_height - (week_count + 7) * dy


def append_style(root: ET.Element) -> None:
    style = root.find(qname("style"))
    if style is None:
        style = ET.Element(qname("style"))
        root.insert(0, style)
    extra = """
.profile-time-title { fill: #8bd49c; font-size: 18px; font-weight: 600; letter-spacing: .6px; }
.profile-time-label { fill: #9aa9a0; font-size: 14px; font-weight: 500; letter-spacing: .2px; }
.profile-time-day { fill: #83958a; font-size: 13px; font-weight: 500; }
.profile-time-legend { fill: #83958a; font-size: 12px; font-weight: 500; }
""".strip()
    current = style.text or ""
    if ".profile-time-title" not in current:
        style.text = f"{current}\n{extra}" if current else extra


def add_text(parent: ET.Element, text: str, **attrs: str) -> ET.Element:
    elem = ET.SubElement(parent, qname("text"), attrs)
    elem.text = text
    return elem


def process(svg_path: Path, year: int) -> None:
    tree = ET.parse(svg_path)
    root = tree.getroot()

    if root.attrib.get("data-profile-temporal-context") == MARKER:
        validate_root(root, year)
        print(f"Already post-processed: {svg_path}")
        return

    _, _, width, height = parse_viewbox(root)
    if width <= 0 or height <= 0:
        raise ValueError("SVG dimensions must be positive")

    calendar = find_calendar_group(root)
    start_date, _ = find_period(root, year)

    dx = width / 64.0
    dy = dx * math.tan(math.radians(30.0))
    offset_x = dx * 7.0
    offset_y = infer_offset_y(calendar, start_date, dx, dy, height)

    # Slightly flatten the 3D plane around its visual centre. This changes the
    # apparent angle from ~30° to ~28.5° without depending on upstream internals.
    perspective_shift = PERSPECTIVE_PIVOT_Y * (1.0 - PERSPECTIVE_SCALE)
    previous_transform = calendar.attrib.get("transform", "").strip()
    flattened = (
        f"translate(0 {fmt(perspective_shift)}) scale(1 {fmt(PERSPECTIVE_SCALE)})"
    )
    calendar.attrib["transform"] = (
        f"{flattened} {previous_transform}" if previous_transform else flattened
    )
    calendar.attrib["data-perspective-scale"] = fmt(PERSPECTIVE_SCALE)

    append_style(root)

    # Reserve a narrow header band and move the original generated composition
    # down as a single unit, preserving all local content and proportions.
    wrapper = ET.Element(qname("g"), {
        "id": "profile-original-content",
        "transform": f"translate(0 {fmt(TOP_BAND)})",
    })

    movable: list[ET.Element] = []
    for child in list(root):
        if child is calendar:
            movable.append(child)
        elif local_name(child.tag) == "g":
            movable.append(child)

    first_index = min((list(root).index(child) for child in movable), default=len(root))
    for child in movable:
        root.remove(child)
        wrapper.append(child)
    root.insert(first_index, wrapper)

    new_height = height + TOP_BAND
    root.attrib["height"] = fmt(new_height)
    root.attrib["viewBox"] = f"0 0 {fmt(width)} {fmt(new_height)}"
    root.attrib["data-profile-temporal-context"] = MARKER
    root.attrib["data-profile-year"] = str(year)

    for child in list(root):
        if local_name(child.tag) == "rect" and child.attrib.get("class") == "fill-bg":
            child.attrib["height"] = fmt(new_height)
            break

    overlay = ET.SubElement(root, qname("g"), {"id": "profile-temporal-context"})

    add_text(
        overlay,
        str(year),
        x="54",
        y="27",
        **{"class": "profile-time-title", "text-anchor": "start"},
    )

    # Intensity legend uses the upstream contribution classes, so the palette
    # always stays consistent with the night-green theme generated by the action.
    legend_y = 18.0
    add_text(
        overlay,
        "Menos",
        x=fmt(width - 242),
        y="27",
        **{"class": "profile-time-legend", "text-anchor": "start"},
    )
    square_x = width - 190
    for level in range(5):
        ET.SubElement(overlay, qname("rect"), {
            "x": fmt(square_x + level * 18),
            "y": fmt(legend_y),
            "width": "11",
            "height": "11",
            "rx": "2",
            "class": f"cont-top-{level}",
        })
    add_text(
        overlay,
        "Mais",
        x=fmt(width - 82),
        y="27",
        **{"class": "profile-time-legend", "text-anchor": "start"},
    )

    calendar_sunday = sunday_of_week(start_date)
    for month, label in enumerate(MONTH_LABELS, start=1):
        month_start = date(year, month, 1)
        week = (month_start - calendar_sunday).days // 7
        x = offset_x + week * dx
        add_text(
            overlay,
            label,
            x=fmt(x),
            y="60",
            **{"class": "profile-time-label", "text-anchor": "middle"},
        )

    for weekday, label in DAY_LABELS:
        target = first_on_or_after(start_date, weekday)
        week = (target - calendar_sunday).days // 7
        day = github_weekday(target)
        base_x = offset_x + (week - day) * dx
        base_y = offset_y + (week + day) * dy
        display_y = TOP_BAND + perspective_shift + PERSPECTIVE_SCALE * base_y + 4.0
        add_text(
            overlay,
            label,
            x=fmt(max(18.0, base_x - 12.0)),
            y=fmt(display_y),
            **{"class": "profile-time-day", "text-anchor": "end"},
        )

    validate_root(root, year)
    tree.write(svg_path, encoding="unicode", xml_declaration=False)
    print(f"Post-processed {svg_path} for {year}")


def validate_root(root: ET.Element, year: int) -> None:
    if root.attrib.get("data-profile-temporal-context") != MARKER:
        raise ValueError("temporal context marker is missing")
    if root.attrib.get("data-profile-year") != str(year):
        raise ValueError("SVG year marker does not match requested year")

    overlay = None
    wrapper = None
    for elem in root.iter(qname("g")):
        if elem.attrib.get("id") == "profile-temporal-context":
            overlay = elem
        elif elem.attrib.get("id") == "profile-original-content":
            wrapper = elem
    if overlay is None or wrapper is None:
        raise ValueError("expected post-processing groups are missing")

    texts = [(elem.text or "").strip() for elem in overlay.iter(qname("text"))]
    required = {str(year), *MONTH_LABELS, "Seg", "Qua", "Sex", "Menos", "Mais"}
    missing = sorted(required.difference(texts))
    if missing:
        raise ValueError(f"missing temporal labels: {', '.join(missing)}")

    calendar = find_calendar_group(wrapper)
    if calendar.attrib.get("data-perspective-scale") != fmt(PERSPECTIVE_SCALE):
        raise ValueError("perspective adjustment marker is missing")


def validate(svg_path: Path, year: int) -> None:
    root = ET.parse(svg_path).getroot()
    validate_root(root, year)
    print(f"Validation OK: {svg_path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--year", required=True, type=int)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    if not args.input.is_file():
        raise FileNotFoundError(args.input)
    if not 2008 <= args.year <= 2100:
        raise ValueError("year outside supported GitHub contribution range")

    if args.check:
        validate(args.input, args.year)
    else:
        process(args.input, args.year)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
