"""Generate a PDF or CSV table report of glucose readings from the SQLite database."""

from __future__ import annotations

import argparse
import csv
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

from reportlab.graphics.charts.linecharts import HorizontalLineChart
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics.charts.legends import Legend
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from ._version import __version__
from .assignments import AssignmentStore, resolve_profile
from .config import (
    ConfigError,
    DEFAULT_REPORT_CONFIG,
    ProfileConfig,
    ProfilesConfig,
    ReportConfig,
    load_config,
    load_onboarding_config,
    load_profiles_config,
    load_report_config,
)
from .dosing import SlidingScaleBand, lookup_dose
from .storage import ensure_schema

_PERIOD_DAYS = {"7d": 7, "30d": 30, "90d": 90, "1y": 365}

# mg/dL -> (conversion factor, unit label)
_UNIT_CONVERSIONS = {
    "mg_dl": (1.0, "mg/dL"),
    "mmol_l": (1 / 18.0182, "mmol/L"),
}

_PAGE_SIZES = {"letter": letter, "a4": A4}

_DATE_TIME_FORMATS = {
    "us": "%m/%d/%Y %I:%M:%S %p",
    "world": "%d/%m/%Y %H:%M:%S",
}

# Number of side-by-side Date/Glucose column pairs in the "simple" layout.
_SIMPLE_LAYOUT_COLUMN_PAIRS = 3

# Maximum number of x-axis date labels on the "chart" layout before thinning.
_CHART_MAX_LABELS = 10


def _format_datetime(device_time: datetime, date_format: str) -> str:
    """Format a naive local device_time using the given date_format preset."""
    return device_time.strftime(_DATE_TIME_FORMATS[date_format])


def _format_value(value_mg_dl: int, unit_factor: float) -> float:
    return round(value_mg_dl * unit_factor, 2)


@dataclass
class ReportRow:
    """One reading row as read back from the database, with profile resolved."""

    device_time: datetime
    device_id: str
    model: str
    value_mg_dl: int
    out_of_range: str | None
    profile: str | None


def _resolve_range(
    period: str, from_date: str | None, to_date: str | None
) -> tuple[datetime | None, datetime | None]:
    """Resolve period/from/to options into a device-local datetime range."""
    if from_date:
        start = datetime.strptime(from_date, "%Y-%m-%d")
        end = (
            datetime.strptime(to_date, "%Y-%m-%d") + timedelta(days=1)
            if to_date
            else datetime.now(timezone.utc).replace(tzinfo=None)
        )
        return start, end

    if period == "all":
        return None, None

    days = _PERIOD_DAYS[period]
    end = datetime.now(timezone.utc).replace(tzinfo=None)
    start = end - timedelta(days=days)
    return start, end


def _attach_profiles(
    rows: list[tuple], profiles_config: ProfilesConfig, assignments: AssignmentStore
) -> list[ReportRow]:
    cache: dict[str, str | None] = {}
    result = []
    for device_time, device_id, model, value_mg_dl, out_of_range in rows:
        if device_id not in cache:
            cache[device_id] = resolve_profile(device_id, profiles_config, assignments)
        result.append(
            ReportRow(
                device_time=datetime.fromisoformat(device_time),
                device_id=device_id,
                model=model,
                value_mg_dl=value_mg_dl,
                out_of_range=out_of_range,
                profile=cache[device_id],
            )
        )
    return result


def fetch_rows(
    db_path: str,
    profiles_config: ProfilesConfig,
    assignments: AssignmentStore,
    device_id: str | None,
    start: datetime | None,
    end: datetime | None,
    profile: str | None = None,
) -> list[ReportRow]:
    """Query readings within an optional device_id/date/profile filter, oldest first.

    is_control_solution readings are always excluded -- see storage.py;
    in practice the sync loop never stores them anyway (the library
    excludes them by default), but the filter is explicit here too.
    """
    query = (
        "SELECT device_time, device_id, model, value_mg_dl, out_of_range "
        "FROM readings WHERE is_control_solution = 0"
    )
    clauses: list[str] = []
    params: list[str] = []

    if device_id:
        clauses.append("device_id = ?")
        params.append(device_id)
    elif profile:
        ids = device_ids_for_profile(profile, profiles_config, assignments)
        if not ids:
            return []
        clauses.append(f"device_id IN ({','.join('?' * len(ids))})")
        params.extend(ids)
    if start is not None:
        clauses.append("device_time >= ?")
        params.append(start.isoformat())
    if end is not None:
        clauses.append("device_time < ?")
        params.append(end.isoformat())

    if clauses:
        query += " AND " + " AND ".join(clauses)
    query += " ORDER BY device_time ASC"

    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(query, params).fetchall()
    finally:
        connection.close()

    return _attach_profiles(rows, profiles_config, assignments)


def fetch_device_ids(db_path: str, start: datetime | None, end: datetime | None) -> list[str]:
    """Return distinct device_ids with at least one reading in range."""
    query = "SELECT device_id FROM readings WHERE is_control_solution = 0"
    clauses: list[str] = []
    params: list[str] = []

    if start is not None:
        clauses.append("device_time >= ?")
        params.append(start.isoformat())
    if end is not None:
        clauses.append("device_time < ?")
        params.append(end.isoformat())
    if clauses:
        query += " AND " + " AND ".join(clauses)
    query += " GROUP BY device_id ORDER BY MIN(device_time) ASC"

    connection = sqlite3.connect(db_path)
    try:
        return [row[0] for row in connection.execute(query, params).fetchall()]
    finally:
        connection.close()


def device_ids_for_profile(
    profile: str, profiles_config: ProfilesConfig, assignments: AssignmentStore
) -> list[str]:
    """Every device_id currently attributed to profile, static and dynamic."""
    ids: set[str] = set()
    if profile in profiles_config.profiles:
        ids.update(profiles_config.profiles[profile].device_ids)
    ids.update(
        device_id for device_id, name in assignments.all().items() if name == profile
    )
    return sorted(ids)


def _table_style(align_cols: list[int]) -> TableStyle:
    style_commands = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2f5d8a")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f0f0f0")]),
    ]
    style_commands.extend(("ALIGN", (idx, 1), (idx, -1), "RIGHT") for idx in align_cols)
    return TableStyle(style_commands)


@dataclass
class _FullColumns:
    header: list[str]
    rows: list[list[object]]


def _full_columns(
    rows: list[ReportRow],
    report_config: ReportConfig,
    sliding_scale: tuple[SlidingScaleBand, ...] = (),
    include_device_model_columns: bool = True,
    include_profile_column: bool = True,
) -> _FullColumns:
    """Build the header and raw values for whichever columns are enabled.

    sliding_scale, if non-empty and report_config.include_sliding_scale,
    adds Dose/Note columns from that one profile's configured bands -- see
    dosing.py. This is a display lookup of a table the caller already
    configured, not a computed/generated recommendation.

    include_device_model_columns and include_profile_column are False for
    the PDF table (see _build_full_table): with Dose/Note columns already
    in the mix, repeating a long device_id -- or a profile name already
    named once in the header/section heading -- on every row pushed rows
    off the page edge. PDF instead shows meter identity once in the header
    (see _meter_summary_elements) and the owning profile once in the
    Patient header or section heading; report_config.include_device_id/
    include_model/include_profile only govern CSV, which has no
    page-width constraint and no such header to fall back on.
    """
    unit_factor, unit_label = _UNIT_CONVERSIONS[report_config.unit]
    value_header = f"Glucose ({unit_label})"
    show_dose = report_config.include_sliding_scale and bool(sliding_scale)
    show_device_id = include_device_model_columns and report_config.include_device_id
    show_model = include_device_model_columns and report_config.include_model
    show_profile = include_profile_column and report_config.include_profile

    header = ["Date/Time"]
    if show_device_id:
        header.append("Device ID")
    if show_model:
        header.append("Model")
    if show_profile:
        header.append("Profile")
    header.append(value_header)
    header.append("Flag")
    if show_dose:
        header.append("Dose (units)")
        header.append("Note")

    data: list[list[object]] = []
    for row in rows:
        line: list[object] = [_format_datetime(row.device_time, report_config.date_format)]
        if show_device_id:
            line.append(row.device_id)
        if show_model:
            line.append(row.model)
        if show_profile:
            line.append(row.profile)
        line.append(_format_value(row.value_mg_dl, unit_factor))
        line.append(row.out_of_range.upper() if row.out_of_range else "-")
        if show_dose:
            band = lookup_dose(row.value_mg_dl, sliding_scale)
            line.append(band.dose_units if band is not None else None)
            line.append(band.label if band is not None else "no guidance configured")
        data.append(line)

    return _FullColumns(header=header, rows=data)


def _build_full_table(
    rows: list[ReportRow],
    report_config: ReportConfig,
    sliding_scale: tuple[SlidingScaleBand, ...] = (),
    include_profile_column: bool = True,
) -> Table:
    columns = _full_columns(
        rows, report_config, sliding_scale,
        include_device_model_columns=False, include_profile_column=include_profile_column,
    )
    header = columns.header
    _, unit_label = _UNIT_CONVERSIONS[report_config.unit]
    value_idx = header.index(f"Glucose ({unit_label})")
    profile_idx = header.index("Profile") if "Profile" in header else None
    dose_idx = header.index("Dose (units)") if "Dose (units)" in header else None

    data = [header]
    for line in columns.rows:
        formatted = list(line)
        formatted[value_idx] = f"{line[value_idx]:.2f}"
        if profile_idx is not None:
            formatted[profile_idx] = line[profile_idx] or "-"
        if dose_idx is not None:
            formatted[dose_idx] = (
                f"{line[dose_idx]:g}" if line[dose_idx] is not None else "-"
            )
        data.append(formatted)

    align_cols = [value_idx] + ([dose_idx] if dose_idx is not None else [])
    table = Table(data, repeatRows=1)
    table.setStyle(_table_style(align_cols))
    return table


def build_csv(
    rows: list[ReportRow],
    output_path: str,
    report_config: ReportConfig = DEFAULT_REPORT_CONFIG,
    sliding_scale: tuple[SlidingScaleBand, ...] = (),
) -> None:
    """Write reading rows as CSV. Layout/page_size/summary are PDF-only and don't apply.

    Unlike the PDF table, Device ID/Model stay as per-row columns here,
    governed by report_config.include_device_id/include_model -- CSV has
    no page-width constraint forcing them into a header instead.
    """
    columns = _full_columns(rows, report_config, sliding_scale)
    header = columns.header
    _, unit_label = _UNIT_CONVERSIONS[report_config.unit]
    value_idx = header.index(f"Glucose ({unit_label})")
    profile_idx = header.index("Profile") if report_config.include_profile else None
    dose_idx = header.index("Dose (units)") if "Dose (units)" in header else None

    with open(output_path, "w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(header)
        for line in columns.rows:
            formatted = list(line)
            formatted[value_idx] = f"{line[value_idx]:.2f}"
            if profile_idx is not None:
                formatted[profile_idx] = line[profile_idx] or ""
            if dose_idx is not None:
                formatted[dose_idx] = f"{line[dose_idx]:g}" if line[dose_idx] is not None else ""
            writer.writerow(formatted)


def _build_simple_table(rows: list[ReportRow], report_config: ReportConfig) -> Table:
    unit_factor, unit_label = _UNIT_CONVERSIONS[report_config.unit]
    pairs = min(_SIMPLE_LAYOUT_COLUMN_PAIRS, len(rows))
    rows_per_column = -(-len(rows) // pairs)

    header = ["Date/Time", f"Glucose ({unit_label})"] * pairs
    data = [header]
    for r in range(rows_per_column):
        line: list[str] = []
        for p in range(pairs):
            idx = p * rows_per_column + r
            if idx < len(rows):
                row = rows[idx]
                line.append(_format_datetime(row.device_time, report_config.date_format))
                line.append(f"{_format_value(row.value_mg_dl, unit_factor):.2f}")
            else:
                line.extend(["", ""])
        data.append(line)

    align_cols = [i for i in range(len(header)) if i % 2 == 1]
    table = Table(data, repeatRows=1)
    table.setStyle(_table_style(align_cols))
    return table


def _build_chart(rows: list[ReportRow], report_config: ReportConfig) -> Drawing:
    unit_factor, unit_label = _UNIT_CONVERSIONS[report_config.unit]
    points = [(row.device_time, row.value_mg_dl * unit_factor) for row in rows]

    drawing = Drawing(480, 260)
    if len(points) < 2:
        drawing.add(String(10, 130, "Not enough glucose data to plot a chart."))
        return drawing

    values = [point[1] for point in points]
    date_pattern = "%m/%d" if report_config.date_format == "us" else "%d/%m"
    date_labels = [point[0].strftime(date_pattern) for point in points]

    step = max(1, len(date_labels) // _CHART_MAX_LABELS)
    thinned_labels = [label if i % step == 0 else "" for i, label in enumerate(date_labels)]

    chart = HorizontalLineChart()
    chart.x = 50
    chart.y = 40
    chart.width = 400
    chart.height = 180
    chart.data = [values]
    chart.categoryAxis.categoryNames = thinned_labels
    chart.categoryAxis.labels.angle = 30
    chart.categoryAxis.labels.dx = -8
    chart.categoryAxis.labels.dy = -10
    chart.categoryAxis.labels.fontSize = 7
    chart.valueAxis.valueMin = min(values) - 1
    chart.valueAxis.valueMax = max(values) + 1
    chart.lines[0].strokeColor = colors.HexColor("#2f5d8a")
    chart.lines[0].strokeWidth = 1.5

    drawing.add(chart)
    drawing.add(
        String(
            chart.x, chart.y + chart.height + 15, f"Glucose ({unit_label}) over time",
            fontName="Helvetica-Bold", fontSize=10,
        )
    )
    return drawing


def _summary_line(rows: list[ReportRow], report_config: ReportConfig) -> str | None:
    unit_factor, unit_label = _UNIT_CONVERSIONS[report_config.unit]
    if not rows:
        return None
    values = [row.value_mg_dl * unit_factor for row in rows]
    high_count = sum(1 for row in rows if row.out_of_range == "high")
    low_count = sum(1 for row in rows if row.out_of_range == "low")
    return (
        f"Glucose summary: min {min(values):.2f} {unit_label} &middot; "
        f"max {max(values):.2f} {unit_label} &middot; "
        f"avg {sum(values) / len(values):.2f} {unit_label} &middot; "
        f"{high_count} high, {low_count} low reading(s)"
    )


#: Colors for the Time in Range chart: below target, in target, above target.
_TIR_COLORS = (colors.HexColor("#c0392b"), colors.HexColor("#27ae60"), colors.HexColor("#e67e22"))


def _time_in_range_counts(rows: list[ReportRow], low: int, high: int) -> tuple[int, int, int]:
    """Count readings below, within, and above [low, high] (inclusive), by value_mg_dl."""
    below = sum(1 for row in rows if row.value_mg_dl < low)
    above = sum(1 for row in rows if row.value_mg_dl > high)
    in_range = len(rows) - below - above
    return below, in_range, above


def _build_time_in_range_chart(below: int, in_range: int, above: int) -> Drawing:
    """Render a pie chart of below/in-range/above counts, with a percentage legend.

    Zero-count categories are omitted from the pie itself (a 0-value slice
    renders oddly) but still appear in the legend at 0%, so the three
    categories are always listed even when one never occurred.
    """
    total = below + in_range + above
    labels = ["Below range", "In range", "Above range"]
    counts = [below, in_range, above]

    drawing = Drawing(400, 160)
    pie = Pie()
    pie.x, pie.y = 20, 5
    pie.width = pie.height = 150
    pie.data = [c for c in counts if c > 0] or [1]
    pie.slices.strokeWidth = 0.5
    color_cycle = [color for count, color in zip(counts, _TIR_COLORS) if count > 0] or [
        colors.lightgrey
    ]
    for i, color in enumerate(color_cycle):
        pie.slices[i].fillColor = color
    drawing.add(pie)

    legend = Legend()
    legend.x, legend.y = 220, 130
    legend.dx = 8
    legend.dy = 8
    legend.fontSize = 8
    legend.alignment = "right"
    legend.colorNamePairs = [
        (color, f"{label}: {count} ({count / total:.0%})")
        for label, count, color in zip(labels, counts, _TIR_COLORS)
    ]
    drawing.add(legend)
    return drawing


def _resolve_tir_range(
    report_config: ReportConfig, profile: ProfileConfig | None
) -> tuple[int, int]:
    """Effective (low, high) target band: profile's own override, else the global default."""
    low, high = report_config.tir_low_mg_dl, report_config.tir_high_mg_dl
    if profile is not None:
        if profile.tir_low_mg_dl is not None:
            low = profile.tir_low_mg_dl
        if profile.tir_high_mg_dl is not None:
            high = profile.tir_high_mg_dl
    return low, high


def _time_in_range_elements(rows: list[ReportRow], low: int, high: int, styles) -> list:
    """"Time in Range" heading + pie chart, or a note if there's nothing to chart."""
    if not rows:
        return []
    below, in_range, above = _time_in_range_counts(rows, low, high)
    return [
        Paragraph(f"Time in Range ({low}-{high} mg/dL)", styles["Heading2"]),
        Spacer(1, 0.05 * inch),
        _build_time_in_range_chart(below, in_range, above),
        Spacer(1, 0.15 * inch),
    ]


def _meter_summary_elements(rows: list[ReportRow], styles) -> list:
    """One "Meter: <device_id> (<model>)" line per distinct meter in rows.

    Replaces the per-row Device ID/Model columns in the PDF table (still
    present in CSV) -- device_id strings are long, and repeating them on
    every row was, combined with Dose/Note columns, pushing the table off
    the page edge. Shown regardless of whether a profile was given.
    """
    seen: list[tuple[str, str]] = []
    for row in rows:
        pair = (row.device_id, row.model)
        if pair not in seen:
            seen.append(pair)
    return [
        Paragraph(f"Meter: {escape(device_id)} ({escape(model)})", styles["Normal"])
        for device_id, model in seen
    ]


def _profile_info_elements(profile: ProfileConfig, rows: list[ReportRow], styles) -> list:
    """Header lines identifying whose report this is, and which meter(s) it's from.

    Order: Patient, Email, Meter, Notes. Unconditional if profile is given
    (unlike sliding_scale, there's no include_* toggle for this -- matches
    etekcity-scale-daemon's patient name/email header, always shown when a
    profile is selected). See _meter_summary_elements for why meter
    identity lives here rather than as per-row table columns.
    """
    elements = [Paragraph(f"Patient: {escape(profile.full_name)}", styles["Normal"])]
    if profile.email:
        elements.append(Paragraph(f"Email: {escape(profile.email)}", styles["Normal"]))
    elements.extend(_meter_summary_elements(rows, styles))
    if profile.notes:
        elements.append(Paragraph(f"Notes: {escape(profile.notes)}", styles["Normal"]))
    return elements


def _sliding_scale_range_label(band: SlidingScaleBand) -> str:
    if band.low_mg_dl is None:
        return f"≤{band.high_mg_dl}"
    if band.high_mg_dl is None:
        return f"≥{band.low_mg_dl}"
    return f"{band.low_mg_dl}-{band.high_mg_dl}"


def _build_sliding_scale_table(sliding_scale: tuple[SlidingScaleBand, ...]) -> Table:
    """Render the profile's configured dosing bands as their own reference table.

    Distinct from the per-reading Dose/Note columns _full_columns adds to
    the main table -- this lists every configured band once, regardless of
    whether any reading in this report actually falls in it.
    """
    data = [["Glucose Range (mg/dL)", "Dose (units)", "Note"]]
    for band in sliding_scale:
        data.append(
            [_sliding_scale_range_label(band), f"{band.dose_units:g}", band.label or "-"]
        )
    table = Table(data, colWidths=[150, 90, 220])
    table.setStyle(_table_style([1]))
    return table


def _sliding_scale_elements(sliding_scale: tuple[SlidingScaleBand, ...], styles) -> list:
    if not sliding_scale:
        return []
    return [
        Paragraph("Sliding Scale (Reference)", styles["Heading2"]),
        Spacer(1, 0.05 * inch),
        _build_sliding_scale_table(sliding_scale),
        Spacer(1, 0.15 * inch),
    ]


def build_pdf(
    rows: list[ReportRow],
    output_path: str,
    report_config: ReportConfig = DEFAULT_REPORT_CONFIG,
    sliding_scale: tuple[SlidingScaleBand, ...] = (),
    profile: ProfileConfig | None = None,
) -> None:
    """Render reading rows as a table (or chart) in a PDF file.

    profile, if given, adds a Patient/Email/Notes header -- unconditional,
    not gated by report_config. sliding_scale (typically profile's own,
    when report_config.include_sliding_scale) adds a reference table of
    every configured band, and -- "full" layout only -- per-reading
    Dose/Note columns in the main table; see _full_columns's docstring.
    report_config.include_time_in_range adds a below/in-range/above pie
    chart, using profile's own tir_low_mg_dl/tir_high_mg_dl if set, else
    report_config's global default (70-180 mg/dL).
    """
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(output_path, pagesize=_PAGE_SIZES[report_config.page_size])
    elements = [
        Paragraph("TRUE METRIX Glucose Reading Report", styles["Title"]),
        Paragraph(
            f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
            f" &middot; {len(rows)} reading(s)",
            styles["Normal"],
        ),
    ]
    if profile is not None:
        elements.extend(_profile_info_elements(profile, rows, styles))
    else:
        elements.extend(_meter_summary_elements(rows, styles))
    if report_config.include_summary:
        summary = _summary_line(rows, report_config)
        if summary:
            elements.append(Paragraph(summary, styles["Normal"]))
    elements.append(Spacer(1, 0.25 * inch))

    if report_config.include_time_in_range:
        tir_low, tir_high = _resolve_tir_range(report_config, profile)
        elements.extend(_time_in_range_elements(rows, tir_low, tir_high, styles))

    if report_config.include_sliding_scale:
        elements.extend(_sliding_scale_elements(sliding_scale, styles))

    if report_config.layout == "simple":
        elements.append(_build_simple_table(rows, report_config))
    elif report_config.layout == "chart":
        elements.append(_build_chart(rows, report_config))
    else:
        elements.append(
            _build_full_table(
                rows, report_config, sliding_scale, include_profile_column=profile is None
            )
        )

    doc.build(elements)


def build_multi_meter_pdf(
    sections: list[tuple[str, list[ReportRow]]],
    output_path: str,
    report_config: ReportConfig = DEFAULT_REPORT_CONFIG,
    profiles_config: ProfilesConfig | None = None,
) -> None:
    """Render one PDF with a separate section (own table/chart) per meter.

    Each section's owning profile (and thus its email/notes, sliding_scale,
    and time-in-range target band) is resolved independently from that
    section's rows[0].profile -- one household's meters can belong to
    different people, each with their own (or no) configured details.
    """
    total_rows = sum(len(rows) for _, rows in sections)
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(output_path, pagesize=_PAGE_SIZES[report_config.page_size])
    elements = [
        Paragraph("TRUE METRIX Glucose Reading Report", styles["Title"]),
        Paragraph(
            f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
            f" &middot; {total_rows} reading(s) across {len(sections)} meter(s)",
            styles["Normal"],
        ),
        Spacer(1, 0.25 * inch),
    ]

    for index, (device_id, rows) in enumerate(sections):
        if index > 0:
            elements.append(PageBreak())
        owner = rows[0].profile or "unassigned"
        elements.append(
            Paragraph(
                f"Meter: {escape(device_id)} ({escape(rows[0].model)}) &mdash; {escape(owner)}",
                styles["Heading2"],
            )
        )

        section_profile: ProfileConfig | None = None
        if profiles_config is not None:
            section_profile = profiles_config.profiles.get(rows[0].profile)
        section_sliding_scale = section_profile.sliding_scale if section_profile else ()

        if section_profile is not None and (section_profile.email or section_profile.notes):
            if section_profile.email:
                elements.append(
                    Paragraph(f"Email: {escape(section_profile.email)}", styles["Normal"])
                )
            if section_profile.notes:
                elements.append(
                    Paragraph(f"Notes: {escape(section_profile.notes)}", styles["Normal"])
                )
        elements.append(Spacer(1, 0.1 * inch))

        if report_config.include_summary:
            summary = _summary_line(rows, report_config)
            if summary:
                elements.append(Paragraph(summary, styles["Normal"]))
                elements.append(Spacer(1, 0.1 * inch))

        if report_config.include_time_in_range:
            tir_low, tir_high = _resolve_tir_range(report_config, section_profile)
            elements.extend(_time_in_range_elements(rows, tir_low, tir_high, styles))

        if report_config.include_sliding_scale:
            elements.extend(_sliding_scale_elements(section_sliding_scale, styles))

        if report_config.layout == "simple":
            elements.append(_build_simple_table(rows, report_config))
        elif report_config.layout == "chart":
            elements.append(_build_chart(rows, report_config))
        else:
            # Every row in a section shares one device_id, so it shares one
            # owning profile too -- already named in the section heading
            # above, making a per-row Profile column redundant here too.
            elements.append(
                _build_full_table(
                    rows, report_config, section_sliding_scale, include_profile_column=False
                )
            )

    doc.build(elements)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="trividia-truemetrix-report",
        description="Generate a PDF or CSV table report from the daemon's reading database.",
    )
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "-c", "--config", help="Path to the daemon's INI config file (reads db_path from it)"
    )
    source.add_argument(
        "-d", "--db", help="Path to the SQLite database file, bypassing the config file"
    )
    parser.add_argument(
        "-F", "--format", choices=["pdf", "csv"], default="pdf",
        help="Output format (default: %(default)s)",
    )
    parser.add_argument(
        "-o", "--output", help="Output file path (default: readings-report.<format>)"
    )
    parser.add_argument(
        "-p", "--period", choices=["7d", "30d", "90d", "1y", "all"], default="all",
        help="Preset date range (default: %(default)s)",
    )
    parser.add_argument(
        "-f", "--from", dest="from_date", metavar="YYYY-MM-DD",
        help="Explicit start date, overrides --period",
    )
    parser.add_argument(
        "-t", "--to", dest="to_date", metavar="YYYY-MM-DD",
        help="Explicit end date (inclusive), defaults to now",
    )
    parser.add_argument("-i", "--device-id", help="Restrict the report to one meter's device_id")
    parser.add_argument(
        "-m", "--multi-meter", action="store_true",
        help=(
            "One PDF with a separate section per meter, instead of mixing "
            "every meter into one table (PDF only; mutually exclusive with "
            "--device-id, ignored for --format csv)"
        ),
    )
    parser.add_argument(
        "-P", "--profile",
        help="Restrict to readings from this profile's meter(s) (requires --config)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.multi_meter and args.device_id:
        print("Error: --multi-meter and --device-id are mutually exclusive")
        return 1
    if args.profile and not args.config:
        print("Error: --profile requires --config (profile membership lives in the config file)")
        return 1

    db_path = args.db
    report_config = DEFAULT_REPORT_CONFIG
    profiles_config = ProfilesConfig(profiles={})
    assignments_path = None
    if args.config:
        try:
            db_path = load_config(args.config).db_path
            report_config = load_report_config(args.config)
            profiles_config = load_profiles_config(args.config)
            assignments_path = load_onboarding_config(args.config).assignments_path
        except ConfigError as exc:
            print(f"Error: {exc}")
            return 1

    ensure_schema(db_path)
    assignments = AssignmentStore(assignments_path or "/dev/null")

    start, end = _resolve_range(args.period, args.from_date, args.to_date)
    output = args.output or f"readings-report.{args.format}"

    if args.multi_meter and args.format != "csv":
        device_ids = fetch_device_ids(db_path, start, end)
        sections = [
            (d, fetch_rows(db_path, profiles_config, assignments, d, start, end))
            for d in device_ids
        ]
        sections = [(d, rows) for d, rows in sections if rows]
        if not sections:
            print("No readings found for the given range/filters.")
            return 1
        # Sliding scale is resolved per section from each meter's own
        # profile, not required here -- see build_multi_meter_pdf.
        build_multi_meter_pdf(sections, output, report_config, profiles_config)
        total_rows = sum(len(rows) for _, rows in sections)
        print(f"Wrote {total_rows} reading(s) across {len(sections)} meter(s) to {output}")
        return 0

    sliding_scale: tuple = ()
    profile_obj = None
    if args.profile:
        if args.profile not in profiles_config.profiles:
            print(f"Error: no [profile.{args.profile}] section in the config")
            return 1
        profile_obj = profiles_config.profiles[args.profile]
        sliding_scale = profile_obj.sliding_scale
    elif report_config.include_sliding_scale:
        print(
            "Error: report.include_sliding_scale is enabled but no --profile was "
            "given -- the dosing table comes from that profile's "
            "[profile.<name>] section"
        )
        return 1

    rows = fetch_rows(
        db_path, profiles_config, assignments, args.device_id, start, end, args.profile
    )
    if not rows:
        print("No readings found for the given range/filters.")
        return 1

    if args.format == "csv":
        build_csv(rows, output, report_config, sliding_scale)
    else:
        build_pdf(rows, output, report_config, sliding_scale, profile_obj)
    print(f"Wrote {len(rows)} reading(s) to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
