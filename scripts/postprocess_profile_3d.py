#!/usr/bin/env python3
"""Post-process github-profile-3d-contrib SVGs for the profile README.

Adds temporal labels, a subtle contribution beat, a curated language donut,
and a transparent light-mode background while keeping the pinned upstream
3D renderer untouched. Uses only Python's standard library.
"""

from __future__ import annotations

import argparse
import json
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
MARKER = "v2"
BEAT_MARKER = "v1"
MAX_LANGUAGES = 10
MONTH_LABELS = ("Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez")
DAY_LABELS = ((1, "Seg"), (3, "Qua"), (5, "Sex"))  # GitHub: Sunday=0
PERIOD_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) / (\d{4}-\d{2}-\d{2})$")
TRANSLATE_RE = re.compile(r"^translate\(\s*(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s*\)")
TRANSLATE_PAIR_RE = re.compile(r"^translate\(\s*(-?\d+(?:\.\d+)?)\s*(?:,|\s)\s*(-?\d+(?:\.\d+)?)\s*\)")
LEVEL_RE = re.compile(r"^cont-top-([0-4])$")

# Deliberately varied palette: no cluster of near-identical blues.
LANGUAGE_PALETTE = (
    "#2EA043",
    "#F0883E",
    "#A371F7",
    "#E3B341",
    "#DB61A2",
    "#39C5CF",
    "#FF7B72",
    "#56D364",
    "#D2A8FF",
    "#FFA657",
    "#7EE787",
)
OTHER_COLOR = "#8B949E"


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


def infer_offset_y(calendar: ET.Element, start_date: date, dx: float, dy: float, canvas_height: float) -> float:
    sunday0 = sunday_of_week(start_date)
    bar_index = 0
    for bar in list(calendar):
        if local_name(bar.tag) != "g":
            continue
        transform = bar.attrib.get("transform", "")
        match = TRANSLATE_RE.match(transform)
        if not match:
            continue
        is_zero = any(elem.attrib.get("class") == "cont-top-0" for elem in bar.iter())
        if is_zero:
            current_date = start_date + timedelta(days=bar_index)
            week = (current_date - sunday0).days // 7
            day = github_weekday(current_date)
            top_y = float(match.group(2))
            return top_y + 3.0 - (week + day) * dy
        bar_index += 1

    total_days = (date(start_date.year + 1, 1, 1) - date(start_date.year, 1, 1)).days
    first_day = github_weekday(date(start_date.year, 1, 1))
    week_count = math.ceil((total_days + first_day) / 7.0)
    return canvas_height - (week_count + 7) * dy


def append_style(root: ET.Element, theme: str) -> None:
    style = root.find(qname("style"))
    if style is None:
        style = ET.Element(qname("style"))
        root.insert(0, style)
    if theme == "light":
        title, label, day, legend = "#1f6f43", "#57606a", "#6e7781", "#6e7781"
    else:
        title, label, day, legend = "#8bd49c", "#9aa9a0", "#83958a", "#83958a"
    extra = f"""
.profile-time-title {{ fill: {title}; font-size: 18px; font-weight: 600; letter-spacing: .6px; }}
.profile-time-label {{ fill: {label}; font-size: 14px; font-weight: 500; letter-spacing: .2px; }}
.profile-time-day {{ fill: {day}; font-size: 13px; font-weight: 500; }}
.profile-time-legend {{ fill: {legend}; font-size: 12px; font-weight: 500; }}
.profile-language-label {{ font-size: 14px; font-weight: 500; }}
""".strip()
    current = style.text or ""
    if ".profile-time-title" not in current:
        style.text = f"{current}\n{extra}" if current else extra


def add_text(parent: ET.Element, text: str, **attrs: str) -> ET.Element:
    elem = ET.SubElement(parent, qname("text"), attrs)
    elem.text = text
    return elem


def find_level(bar: ET.Element) -> int:
    for elem in bar.iter():
        match = LEVEL_RE.match(elem.attrib.get("class", ""))
        if match:
            return int(match.group(1))
    return 0


def add_beat(calendar: ET.Element) -> int:
    animated = 0
    contribution_index = 0
    for bar in list(calendar):
        if local_name(bar.tag) != "g" or not TRANSLATE_RE.match(bar.attrib.get("transform", "")):
            continue
        level = find_level(bar)
        if level <= 0:
            contribution_index += 1
            continue
        rest_opacity = {1: 0.93, 2: 0.90, 3: 0.87, 4: 0.84}[level]
        phase = (contribution_index % 18) * 0.045
        ET.SubElement(
            bar,
            qname("animate"),
            {
                "attributeName": "opacity",
                "values": f"{rest_opacity:.2f};1;0.96;1;{rest_opacity:.2f}",
                "keyTimes": "0;0.07;0.15;0.25;1",
                "dur": "2.6s",
                "begin": f"{3.0 + phase:.3f}s",
                "repeatCount": "indefinite",
                "data-profile-beat": BEAT_MARKER,
            },
        )
        animated += 1
        contribution_index += 1
    calendar.attrib["data-profile-beat"] = BEAT_MARKER
    calendar.attrib["data-profile-beat-bars"] = str(animated)
    return animated


def load_languages(path: Path, year: int) -> list[dict[str, int | str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if int(data.get("year", 0)) != year:
        raise ValueError("language data year does not match requested year")
    raw = data.get("languages", [])
    languages: list[dict[str, int | str]] = []
    for item in raw:
        name = str(item.get("language", "")).strip()
        count = int(item.get("contributions", 0) or 0)
        if name and count > 0:
            languages.append({"language": name, "contributions": count})
    languages.sort(key=lambda item: (-int(item["contributions"]), str(item["language"]).lower()))
    if not languages:
        raise ValueError("language data contains no languages")
    return languages


def find_language_group(wrapper: ET.Element, original_height: float) -> tuple[int, ET.Element]:
    expected_y = original_height - 260.0 - 70.0
    for index, child in enumerate(list(wrapper)):
        if local_name(child.tag) != "g":
            continue
        match = TRANSLATE_PAIR_RE.match(child.attrib.get("transform", ""))
        if not match:
            continue
        x, y = float(match.group(1)), float(match.group(2))
        if abs(x - 40.0) < 0.5 and abs(y - expected_y) < 0.5:
            return index, child
    raise ValueError("upstream language chart group was not found")


def polar(cx: float, cy: float, radius: float, angle: float) -> tuple[float, float]:
    return cx + radius * math.cos(angle), cy + radius * math.sin(angle)


def donut_path(cx: float, cy: float, outer: float, inner: float, start: float, end: float) -> str:
    delta = max(0.0, end - start)
    if delta >= 2 * math.pi - 1e-6:
        mid = start + math.pi
        o1, om, o2 = polar(cx, cy, outer, start), polar(cx, cy, outer, mid), polar(cx, cy, outer, end)
        i2, im, i1 = polar(cx, cy, inner, end), polar(cx, cy, inner, mid), polar(cx, cy, inner, start)
        return (
            f"M {fmt(o1[0])} {fmt(o1[1])} "
            f"A {fmt(outer)} {fmt(outer)} 0 0 1 {fmt(om[0])} {fmt(om[1])} "
            f"A {fmt(outer)} {fmt(outer)} 0 0 1 {fmt(o2[0])} {fmt(o2[1])} "
            f"L {fmt(i2[0])} {fmt(i2[1])} "
            f"A {fmt(inner)} {fmt(inner)} 0 0 0 {fmt(im[0])} {fmt(im[1])} "
            f"A {fmt(inner)} {fmt(inner)} 0 0 0 {fmt(i1[0])} {fmt(i1[1])} Z"
        )
    large = 1 if delta > math.pi else 0
    o1, o2 = polar(cx, cy, outer, start), polar(cx, cy, outer, end)
    i2, i1 = polar(cx, cy, inner, end), polar(cx, cy, inner, start)
    return (
        f"M {fmt(o1[0])} {fmt(o1[1])} "
        f"A {fmt(outer)} {fmt(outer)} 0 {large} 1 {fmt(o2[0])} {fmt(o2[1])} "
        f"L {fmt(i2[0])} {fmt(i2[1])} "
        f"A {fmt(inner)} {fmt(inner)} 0 {large} 0 {fmt(i1[0])} {fmt(i1[1])} Z"
    )


def build_language_chart(languages: list[dict[str, int | str]], theme: str, x: float, y: float) -> ET.Element:
    visible = [dict(item) for item in languages[:MAX_LANGUAGES]]
    residual = sum(int(item["contributions"]) for item in languages[MAX_LANGUAGES:])
    if residual > 0:
        visible.append({"language": "Outras", "contributions": residual})

    total = sum(int(item["contributions"]) for item in visible)
    group = ET.Element(
        qname("g"),
        {
            "id": "profile-language-chart",
            "transform": f"translate({fmt(x)} {fmt(y)})",
            "data-language-count": str(len(visible)),
            "data-language-visible": str(min(len(languages), MAX_LANGUAGES)),
        },
    )
    cx, cy, outer, inner = 130.0, 130.0, 116.0, 66.0
    stroke = "#ffffff" if theme == "light" else "#00000f"
    start = -math.pi / 2

    label_x = 276.0
    row_h = 260.0 / max(11.0, float(len(visible) + 1))
    block_h = row_h * len(visible)
    first_y = (260.0 - block_h) / 2.0 + row_h / 2.0

    for index, item in enumerate(visible):
        count = int(item["contributions"])
        fraction = count / total if total else 0.0
        end = start + 2 * math.pi * fraction
        color = OTHER_COLOR if item["language"] == "Outras" else LANGUAGE_PALETTE[index % len(LANGUAGE_PALETTE)]
        path = ET.SubElement(
            group,
            qname("path"),
            {
                "d": donut_path(cx, cy, outer, inner, start, end),
                "fill": color,
                "stroke": stroke,
                "stroke-width": "2",
                "data-language": str(item["language"]),
            },
        )
        title = ET.SubElement(path, qname("title"))
        title.text = f"{item['language']} · {count} commits · {fraction * 100:.1f}%"

        ly = first_y + index * row_h
        ET.SubElement(
            group,
            qname("rect"),
            {
                "x": fmt(label_x),
                "y": fmt(ly - 6.5),
                "width": "13",
                "height": "13",
                "rx": "3",
                "fill": color,
            },
        )
        add_text(
            group,
            str(item["language"]),
            x=fmt(label_x + 20.0),
            y=fmt(ly),
            **{
                "class": "fill-fg profile-language-label",
                "dominant-baseline": "middle",
                "text-anchor": "start",
            },
        )
        start = end
    return group


def set_background(root: ET.Element, new_height: float, theme: str) -> None:
    for child in list(root):
        if local_name(child.tag) == "rect" and child.attrib.get("class") == "fill-bg":
            child.attrib["height"] = fmt(new_height)
            if theme == "light":
                child.attrib["class"] = "profile-background"
                child.attrib["fill"] = "transparent"
            return
    raise ValueError("SVG background rectangle was not found")


def process(svg_path: Path, year: int, theme: str, languages_path: Path) -> None:
    tree = ET.parse(svg_path)
    root = tree.getroot()

    if root.attrib.get("data-profile-enhancement") == MARKER:
        validate_root(root, year, theme)
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

    perspective_shift = PERSPECTIVE_PIVOT_Y * (1.0 - PERSPECTIVE_SCALE)
    previous_transform = calendar.attrib.get("transform", "").strip()
    flattened = f"translate(0 {fmt(perspective_shift)}) scale(1 {fmt(PERSPECTIVE_SCALE)})"
    calendar.attrib["transform"] = f"{flattened} {previous_transform}" if previous_transform else flattened
    calendar.attrib["data-perspective-scale"] = fmt(PERSPECTIVE_SCALE)
    if add_beat(calendar) <= 0:
        raise ValueError("no contribution bars were available for beat animation")

    append_style(root, theme)

    wrapper = ET.Element(qname("g"), {"id": "profile-original-content", "transform": f"translate(0 {fmt(TOP_BAND)})"})
    movable: list[ET.Element] = []
    for child in list(root):
        if child is calendar or local_name(child.tag) == "g":
            movable.append(child)
    first_index = min((list(root).index(child) for child in movable), default=len(root))
    for child in movable:
        root.remove(child)
        wrapper.append(child)
    root.insert(first_index, wrapper)

    languages = load_languages(languages_path, year)
    language_index, language_group = find_language_group(wrapper, height)
    wrapper.remove(language_group)
    wrapper.insert(language_index, build_language_chart(languages, theme, 40.0, height - 260.0 - 70.0))

    new_height = height + TOP_BAND
    root.attrib["height"] = fmt(new_height)
    root.attrib["viewBox"] = f"0 0 {fmt(width)} {fmt(new_height)}"
    root.attrib["data-profile-enhancement"] = MARKER
    root.attrib["data-profile-year"] = str(year)
    root.attrib["data-profile-theme"] = theme
    set_background(root, new_height, theme)

    overlay = ET.SubElement(root, qname("g"), {"id": "profile-temporal-context"})
    add_text(overlay, str(year), x="54", y="27", **{"class": "profile-time-title", "text-anchor": "start"})

    legend_y = 18.0
    add_text(overlay, "Menos", x=fmt(width - 242), y="27", **{"class": "profile-time-legend", "text-anchor": "start"})
    square_x = width - 190
    for level in range(5):
        ET.SubElement(overlay, qname("rect"), {
            "x": fmt(square_x + level * 18), "y": fmt(legend_y), "width": "11", "height": "11", "rx": "2", "class": f"cont-top-{level}"
        })
    add_text(overlay, "Mais", x=fmt(width - 82), y="27", **{"class": "profile-time-legend", "text-anchor": "start"})

    calendar_sunday = sunday_of_week(start_date)
    for month, label in enumerate(MONTH_LABELS, start=1):
        month_start = date(year, month, 1)
        week = (month_start - calendar_sunday).days // 7
        x = offset_x + week * dx
        add_text(overlay, label, x=fmt(x), y="60", **{"class": "profile-time-label", "text-anchor": "middle"})

    for weekday, label in DAY_LABELS:
        target = first_on_or_after(start_date, weekday)
        week = (target - calendar_sunday).days // 7
        day = github_weekday(target)
        base_x = offset_x + (week - day) * dx
        base_y = offset_y + (week + day) * dy
        display_y = TOP_BAND + perspective_shift + PERSPECTIVE_SCALE * base_y + 4.0
        add_text(overlay, label, x=fmt(max(18.0, base_x - 12.0)), y=fmt(display_y), **{"class": "profile-time-day", "text-anchor": "end"})

    validate_root(root, year, theme)
    tree.write(svg_path, encoding="unicode", xml_declaration=False)
    print(f"Post-processed {svg_path} for {year} ({theme})")


def validate_root(root: ET.Element, year: int, theme: str) -> None:
    if root.attrib.get("data-profile-enhancement") != MARKER:
        raise ValueError("profile enhancement marker is missing")
    if root.attrib.get("data-profile-year") != str(year):
        raise ValueError("SVG year marker does not match requested year")
    if root.attrib.get("data-profile-theme") != theme:
        raise ValueError("SVG theme marker does not match requested theme")

    overlay = wrapper = language_chart = None
    for elem in root.iter(qname("g")):
        elem_id = elem.attrib.get("id")
        if elem_id == "profile-temporal-context":
            overlay = elem
        elif elem_id == "profile-original-content":
            wrapper = elem
        elif elem_id == "profile-language-chart":
            language_chart = elem
    if overlay is None or wrapper is None or language_chart is None:
        raise ValueError("expected profile enhancement groups are missing")

    texts = [(elem.text or "").strip() for elem in overlay.iter(qname("text"))]
    required = {str(year), *MONTH_LABELS, "Seg", "Qua", "Sex", "Menos", "Mais"}
    missing = sorted(required.difference(texts))
    if missing:
        raise ValueError(f"missing temporal labels: {', '.join(missing)}")

    calendar = find_calendar_group(wrapper)
    if calendar.attrib.get("data-perspective-scale") != fmt(PERSPECTIVE_SCALE):
        raise ValueError("perspective adjustment marker is missing")
    if calendar.attrib.get("data-profile-beat") != BEAT_MARKER:
        raise ValueError("beat animation marker is missing")
    if int(calendar.attrib.get("data-profile-beat-bars", "0")) <= 0:
        raise ValueError("beat animation contains no bars")

    language_count = int(language_chart.attrib.get("data-language-count", "0"))
    visible_count = int(language_chart.attrib.get("data-language-visible", "0"))
    if language_count < 2 or visible_count < 2:
        raise ValueError("custom language chart does not contain enough languages")
    if visible_count > MAX_LANGUAGES:
        raise ValueError("language chart exceeds configured visible-language limit")

    if theme == "light":
        backgrounds = [elem for elem in root.iter(qname("rect")) if elem.attrib.get("class") == "profile-background"]
        if not backgrounds or backgrounds[0].attrib.get("fill") != "transparent":
            raise ValueError("light theme background is not transparent")


def validate(svg_path: Path, year: int, theme: str) -> None:
    root = ET.parse(svg_path).getroot()
    validate_root(root, year, theme)
    print(f"Validation OK: {svg_path} ({theme})")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--year", required=True, type=int)
    parser.add_argument("--theme", required=True, choices=("dark", "light"))
    parser.add_argument("--languages-json", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    if not args.input.is_file():
        raise FileNotFoundError(args.input)
    if not 2008 <= args.year <= 2100:
        raise ValueError("year outside supported GitHub contribution range")

    if args.check:
        validate(args.input, args.year, args.theme)
    else:
        if not args.languages_json or not args.languages_json.is_file():
            raise FileNotFoundError(args.languages_json or "--languages-json")
        process(args.input, args.year, args.theme, args.languages_json)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
