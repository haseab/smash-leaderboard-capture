#!/usr/bin/env python3
"""
Report local match storage by day.

This is meant to sanity-check OpenAI/Gemini processing cost spikes against the
amount of match media produced locally. It scans a matches directory, groups file
sizes by day, and prints a terminal bar chart.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_MATCHES_DIR = Path("local/matches")
FILENAME_DATE_RE = re.compile(r"(?:^|[-_/])(?P<date>20\d{6})[_-](?P<time>\d{6})(?:\D|$)")


@dataclass(frozen=True)
class FileRecord:
    path: Path
    size_bytes: int
    day: dt.date
    date_source: str


def human_bytes(size_bytes: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    size = float(size_bytes)
    for unit in units:
        if abs(size) < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size_bytes} B"


def parse_filename_datetime(path: Path) -> dt.datetime | None:
    match = FILENAME_DATE_RE.search(path.name)
    if not match:
        return None

    raw = f"{match.group('date')}{match.group('time')}"
    try:
        return dt.datetime.strptime(raw, "%Y%m%d%H%M%S")
    except ValueError:
        return None


def file_day(path: Path, date_source: str) -> tuple[dt.date, str]:
    if date_source in {"auto", "filename"}:
        parsed = parse_filename_datetime(path)
        if parsed:
            return parsed.date(), "filename"
        if date_source == "filename":
            raise ValueError(f"No YYYYMMDD_HHMMSS timestamp found in {path}")

    stat = path.stat()
    if date_source == "ctime":
        return dt.datetime.fromtimestamp(stat.st_ctime).date(), "ctime"

    return dt.datetime.fromtimestamp(stat.st_mtime).date(), "mtime"


def iter_files(matches_dir: Path, date_source: str, include_hidden: bool) -> Iterable[FileRecord]:
    for path in sorted(matches_dir.rglob("*")):
        if not path.is_file():
            continue
        if not include_hidden and any(part.startswith(".") for part in path.relative_to(matches_dir).parts):
            continue

        day, source = file_day(path, date_source)
        yield FileRecord(path=path, size_bytes=path.stat().st_size, day=day, date_source=source)


def complete_date_range(start: dt.date, end: dt.date) -> list[dt.date]:
    days = []
    current = start
    while current <= end:
        days.append(current)
        current += dt.timedelta(days=1)
    return days


def parse_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    return dt.date.fromisoformat(value)


def terminal_chart(day_totals: dict[dt.date, int], days: list[dt.date], width: int) -> str:
    max_bytes = max((day_totals.get(day, 0) for day in days), default=0)
    lines = []

    for day in days:
        total = day_totals.get(day, 0)
        if max_bytes == 0:
            bar = ""
        else:
            bar_len = max(1, round((total / max_bytes) * width)) if total else 0
            bar = "█" * bar_len
        lines.append(f"{day.isoformat()}  {human_bytes(total):>9}  {bar}")

    return "\n".join(lines)


def colored(text: str, enabled: bool) -> str:
    if not enabled:
        return text
    return f"\033[38;5;33m{text}\033[0m"


def vertical_chart(
    day_totals: dict[dt.date, int],
    days: list[dt.date],
    height: int,
    use_color: bool,
) -> str:
    max_bytes = max((day_totals.get(day, 0) for day in days), default=0)
    if not days:
        return ""
    if max_bytes == 0:
        max_bytes = 1

    lines = []
    for row in range(height, 0, -1):
        threshold = max_bytes * row / height
        axis_label = human_bytes(round(threshold))
        cells = []
        for day in days:
            value = day_totals.get(day, 0)
            has_bar = value > 0 and (value >= threshold or row == 1)
            cells.append(colored("██", use_color) if has_bar else "  ")
        lines.append(f"{axis_label:>10} ┤ {''.join(cells)}")

    tick_line = "".join("╵ " if index % 7 == 0 or index == len(days) - 1 else "  " for index, _ in enumerate(days))
    day_line = "".join(
        f"{day.day:02d}" if index % 7 == 0 or index == len(days) - 1 else "  "
        for index, day in enumerate(days)
    )
    tick_labels = [
        f"{day.strftime('%b')} {day.day}"
        for index, day in enumerate(days)
        if index % 7 == 0 or index == len(days) - 1
    ]

    lines.append(f"{'0 B':>10} └ {'──' * len(days)}")
    lines.append(f"{'':>12} {tick_line}")
    lines.append(f"{'':>12} {day_line}")
    lines.append(f"{'Ticks:':>10} {', '.join(tick_labels)}")
    return "\n".join(lines)


def nonzero_daily_rows(day_totals: dict[dt.date, int], days: list[dt.date]) -> list[str]:
    rows = []
    for day in days:
        total = day_totals.get(day, 0)
        if total:
            rows.append(f"{day.isoformat()}  {human_bytes(total):>9}")
    return rows


def largest_file_rows(matches_dir: Path, records: list[FileRecord], limit: int) -> list[str]:
    rows = []
    for record in sorted(records, key=lambda item: item.size_bytes, reverse=True)[:limit]:
        relative_path = record.path.relative_to(matches_dir)
        rows.append(
            f"{human_bytes(record.size_bytes):>9}  {record.day.isoformat()}  "
            f"{record.date_source:<8}  {relative_path}"
        )
    return rows


def extension_totals(records: list[FileRecord]) -> list[tuple[str, int, int]]:
    totals: dict[str, tuple[int, int]] = {}
    for record in records:
        extension = record.path.suffix.lower() or "(none)"
        size, count = totals.get(extension, (0, 0))
        totals[extension] = (size + record.size_bytes, count + 1)
    return [(extension, size, count) for extension, (size, count) in sorted(totals.items(), key=lambda item: item[1][0], reverse=True)]


def print_report(
    matches_dir: Path,
    records: list[FileRecord],
    day_totals: dict[dt.date, int],
    days: list[dt.date],
    chart_height: int,
    chart_style: str,
    bar_width: int,
    use_color: bool,
) -> None:
    visible_total = sum(day_totals.get(day, 0) for day in days)
    all_total = sum(record.size_bytes for record in records)
    peak_day, peak_bytes = max(
        ((day, day_totals.get(day, 0)) for day in days),
        key=lambda item: item[1],
        default=(None, 0),
    )

    first_day = days[0].isoformat() if days else "n/a"
    last_day = days[-1].isoformat() if days else "n/a"
    peak_label = f"{peak_day.isoformat()} ({human_bytes(peak_bytes)})" if peak_day and peak_bytes else "n/a"

    print(f"Match storage ({first_day} - {last_day})")
    print(f"Scanned: {matches_dir.resolve()}")
    print(f"Files: {len(records)}")
    print(f"Displayed range storage: {human_bytes(visible_total)}")
    print(f"All scanned storage:     {human_bytes(all_total)}")
    print(f"Peak day:                {peak_label}")
    print()

    if chart_style in {"vertical", "both"}:
        print(vertical_chart(day_totals, days, chart_height, use_color))
        print()

    if chart_style in {"horizontal", "both"}:
        print(terminal_chart(day_totals, days, bar_width))
        print()

    rows = nonzero_daily_rows(day_totals, days)
    print("Non-zero days")
    if rows:
        print("\n".join(rows))
    else:
        print("No files in displayed range.")
    print()

    extension_rows = extension_totals(records)
    print("By file type")
    if extension_rows:
        for extension, size, count in extension_rows:
            print(f"{extension:<8} {human_bytes(size):>9}  {count} file{'s' if count != 1 else ''}")
    else:
        print("No files found.")
    print()

    print("Largest files")
    rows = largest_file_rows(matches_dir, records, limit=10)
    if rows:
        print("\n".join(rows))
    else:
        print("No files found.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add up files in the matches folder and print a daily storage bar chart.",
    )
    parser.add_argument(
        "matches_dir",
        nargs="?",
        default=str(DEFAULT_MATCHES_DIR),
        help="Matches directory to scan (default: local/matches).",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=28,
        help="Number of days to show, ending at --end-date or today (default: 28).",
    )
    parser.add_argument(
        "--start-date",
        type=parse_date,
        help="First day to include, YYYY-MM-DD. Overrides --days when used with --end-date.",
    )
    parser.add_argument(
        "--end-date",
        type=parse_date,
        help="Last day to include, YYYY-MM-DD. Defaults to today.",
    )
    parser.add_argument(
        "--date-source",
        choices=("auto", "filename", "mtime", "ctime"),
        default="auto",
        help="How to assign files to days. auto uses YYYYMMDD_HHMMSS filenames first, then mtime.",
    )
    parser.add_argument(
        "--include-hidden",
        action="store_true",
        help="Include hidden files like .DS_Store.",
    )
    parser.add_argument(
        "--bar-width",
        type=int,
        default=42,
        help="Width of horizontal chart bars (default: 42).",
    )
    parser.add_argument(
        "--chart-height",
        type=int,
        default=12,
        help="Height of vertical chart in terminal rows (default: 12).",
    )
    parser.add_argument(
        "--chart",
        choices=("vertical", "horizontal", "both"),
        default="vertical",
        help="Chart style to print (default: vertical).",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI color in the terminal chart.",
    )
    args = parser.parse_args()

    matches_dir = Path(args.matches_dir).expanduser()
    if not matches_dir.exists() or not matches_dir.is_dir():
        print(f"Matches directory not found: {matches_dir}", file=sys.stderr)
        return 2

    records = list(iter_files(matches_dir, args.date_source, args.include_hidden))

    day_totals: dict[dt.date, int] = {}
    for record in records:
        day_totals[record.day] = day_totals.get(record.day, 0) + record.size_bytes

    end_day = args.end_date or dt.date.today()
    if args.start_date:
        start_day = args.start_date
    else:
        start_day = end_day - dt.timedelta(days=max(1, args.days) - 1)

    if start_day > end_day:
        print("--start-date must be before or equal to --end-date", file=sys.stderr)
        return 2

    days = complete_date_range(start_day, end_day)
    use_color = sys.stdout.isatty() and not args.no_color
    print_report(
        matches_dir=matches_dir,
        records=records,
        day_totals=day_totals,
        days=days,
        chart_height=max(3, args.chart_height),
        chart_style=args.chart,
        bar_width=max(1, args.bar_width),
        use_color=use_color,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
