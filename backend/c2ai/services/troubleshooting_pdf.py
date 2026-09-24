"""Server-side PDF rendering for completed troubleshooting jobs."""

from __future__ import annotations

import html
import io
import unicodedata
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Flowable,
    KeepTogether,
    LongTable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from c2ai.schemas.troubleshooting import (
    TroubleshootingMetric,
    TroubleshootingReportRequest,
    TroubleshootingReportResponse,
)

_TEAL = colors.HexColor("#10AFC2")
_DARK = colors.HexColor("#101827")
_MUTED = colors.HexColor("#667085")
_LIGHT = colors.HexColor("#EEF3F7")
_BORDER = colors.HexColor("#D8E2EA")
_GREEN = colors.HexColor("#039855")
_AMBER = colors.HexColor("#DC6803")
_RED = colors.HexColor("#D92D20")


def _safe(value: object) -> str:
    text = str(value if value is not None else "")
    text = (
        text.replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2011", "-")
        .replace("\u2192", "->")
        .replace("\u00b7", "-")
    )
    normalized = unicodedata.normalize("NFKD", text)
    return normalized.encode("ascii", "replace").decode("ascii")


def _paragraph(text: object, style: ParagraphStyle) -> Paragraph:
    return Paragraph(html.escape(_safe(text)).replace("\n", "<br/>"), style)


def _summary_paragraph(
    severity: str,
    summary: str,
    style: ParagraphStyle,
) -> Paragraph:
    severity_colors = {
        "critical": "#D92D20",
        "warning": "#DC6803",
        "normal": "#039855",
    }
    safe_summary = html.escape(_safe(summary)).replace("\n", "<br/>")
    label = html.escape(_safe(severity.title()))
    return Paragraph(
        f'<font color="{severity_colors[severity]}"><b>{label}:</b></font> '
        f"{safe_summary}",
        style,
    )


def _timestamp(value: datetime | None) -> str:
    if value is None:
        return "Could not be determined"
    return value.astimezone().strftime("%b %d, %Y %I:%M:%S %p %Z")


def troubleshooting_pdf_filename(generated_at: datetime) -> str:
    """Return the canonical date-stamped troubleshooting PDF filename."""

    report_date = generated_at.astimezone().strftime("%d-%m-%Y")
    return f"C2AI-application-troubleshooting-report-{report_date}.pdf"


def _metric_value(metric: TroubleshootingMetric) -> str:
    if metric.value is None:
        return "No data"
    number = str(metric.value)
    return f"{number} {metric.unit}".strip()


class MetricChart(Flowable):
    """Compact metric card and line chart rendered directly into the PDF."""

    def __init__(self, metric: TroubleshootingMetric, width: float, height: float):
        super().__init__()
        self.metric = metric
        self.width = width
        self.height = height

    def draw(self) -> None:
        canvas = self.canv
        canvas.saveState()
        canvas.setFillColor(colors.white)
        canvas.setStrokeColor(_BORDER)
        canvas.roundRect(0, 0, self.width, self.height, 4 * mm, fill=1, stroke=1)

        canvas.setFillColor(_MUTED)
        canvas.setFont("Helvetica-Bold", 7)
        canvas.drawString(5 * mm, self.height - 8 * mm, _safe(self.metric.label)[:42])
        canvas.setFillColor(_DARK)
        canvas.setFont("Helvetica-Bold", 13)
        canvas.drawString(
            5 * mm, self.height - 17 * mm, _safe(_metric_value(self.metric))
        )
        canvas.setFillColor(_MUTED)
        canvas.setFont("Courier", 5.5)
        canvas.drawString(5 * mm, self.height - 23 * mm, _safe(self.metric.name)[:57])

        left = 5 * mm
        right = self.width - 5 * mm
        bottom = 5 * mm
        top = self.height - 30 * mm
        canvas.setStrokeColor(_LIGHT)
        canvas.setLineWidth(0.5)
        for fraction in (0.25, 0.5, 0.75):
            y = bottom + (top - bottom) * fraction
            canvas.line(left, y, right, y)

        points = self.metric.points
        if points:
            values = [point.value for point in points]
            minimum = min(values)
            maximum = max(values)
            value_range = maximum - minimum
            if value_range == 0:
                value_range = max(abs(maximum) * 0.1, 1.0)
                minimum -= value_range / 2
            coordinates: list[tuple[float, float]] = []
            for index, point in enumerate(points):
                x = (
                    (left + right) / 2
                    if len(points) == 1
                    else left + index * (right - left) / (len(points) - 1)
                )
                y = bottom + (point.value - minimum) * (top - bottom) / value_range
                coordinates.append((x, y))
            canvas.setStrokeColor(_TEAL)
            canvas.setLineWidth(1.2)
            path = canvas.beginPath()
            path.moveTo(*coordinates[0])
            for x, y in coordinates[1:]:
                path.lineTo(x, y)
            canvas.drawPath(path, stroke=1, fill=0)

        else:
            canvas.setFillColor(_MUTED)
            canvas.setFont("Helvetica", 7)
            canvas.drawCentredString(
                self.width / 2,
                (bottom + top) / 2,
                "No metric data available",
            )
        canvas.restoreState()


class EvidenceFlow(Flowable):
    """UI-style evidence and analysis flow rendered as rounded pills."""

    def __init__(self, width: float):
        super().__init__()
        self.width = width
        self.height = 9 * mm

    def _source_pill(self, x: float, width: float, label: str) -> None:
        canvas = self.canv
        canvas.setFillColor(colors.white)
        canvas.setStrokeColor(_BORDER)
        canvas.setLineWidth(0.7)
        canvas.roundRect(
            x,
            0,
            width,
            self.height,
            self.height / 2,
            fill=1,
            stroke=1,
        )

        check_x = x + 3.8 * mm
        check_y = self.height / 2
        canvas.setStrokeColor(colors.HexColor("#12B76A"))
        canvas.setLineWidth(1.2)
        canvas.line(check_x - 1.2 * mm, check_y, check_x, check_y - 1.2 * mm)
        canvas.line(check_x, check_y - 1.2 * mm, check_x + 2 * mm, check_y + 1.7 * mm)

        canvas.setFillColor(_MUTED)
        canvas.setFont("Helvetica", 6.2)
        canvas.drawString(x + 7.5 * mm, check_y - 2.2, label)

    def draw(self) -> None:
        canvas = self.canv
        canvas.saveState()

        gap = 2 * mm
        source_pills = [
            (32 * mm, "Application Logs"),
            (28 * mm, "Live Metrics"),
            (36 * mm, "Kubernetes Events"),
        ]
        x = 0.0
        for width, label in source_pills:
            self._source_pill(x, width, label)
            x += width + gap

        arrow_width = 6 * mm
        arrow_y = self.height / 2
        canvas.setStrokeColor(_MUTED)
        canvas.setLineWidth(1)
        canvas.line(x + 1 * mm, arrow_y, x + arrow_width - 2 * mm, arrow_y)
        canvas.line(
            x + arrow_width - 4 * mm,
            arrow_y + 2 * mm,
            x + arrow_width - 2 * mm,
            arrow_y,
        )
        canvas.line(
            x + arrow_width - 4 * mm,
            arrow_y - 2 * mm,
            x + arrow_width - 2 * mm,
            arrow_y,
        )
        x += arrow_width + gap

        llm_width = min(28 * mm, self.width - x)
        canvas.setFillColor(colors.HexColor("#E6F8FA"))
        canvas.setStrokeColor(_TEAL)
        canvas.setLineWidth(0.8)
        canvas.roundRect(
            x,
            0,
            llm_width,
            self.height,
            self.height / 2,
            fill=1,
            stroke=1,
        )
        canvas.setFillColor(_TEAL)
        canvas.setFont("Helvetica-Bold", 6.8)
        canvas.drawCentredString(
            x + llm_width / 2,
            arrow_y - 2.3,
            "AI Analysis",
        )
        canvas.restoreState()


def _page_header_footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFillColor(_DARK)
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawString(doc.leftMargin, A4[1] - 14 * mm, "C2AI")
    canvas.setFillColor(_TEAL)
    canvas.drawRightString(
        A4[0] - doc.rightMargin,
        A4[1] - 14 * mm,
        "APPLICATION TROUBLESHOOTING REPORT",
    )
    canvas.setStrokeColor(_BORDER)
    canvas.line(
        doc.leftMargin,
        A4[1] - 17 * mm,
        A4[0] - doc.rightMargin,
        A4[1] - 17 * mm,
    )
    canvas.setFillColor(_MUTED)
    canvas.setFont("Helvetica", 7)
    canvas.drawString(doc.leftMargin, 10 * mm, "Generated by C2AI")
    canvas.drawRightString(
        A4[0] - doc.rightMargin,
        10 * mm,
        f"Page {doc.page}",
    )
    canvas.restoreState()


def build_troubleshooting_pdf(
    report: TroubleshootingReportResponse,
    request: TroubleshootingReportRequest,
) -> bytes:
    """Render every completed-report section visible in the UI."""

    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=23 * mm,
        bottomMargin=17 * mm,
        title=troubleshooting_pdf_filename(report.generated_at).removesuffix(
            ".pdf"
        ),
        author="C2AI",
        subject="AI application troubleshooting report",
        pageCompression=1,
    )
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "ReportTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=24,
        textColor=_DARK,
        alignment=TA_LEFT,
        spaceAfter=4 * mm,
    )
    section = ParagraphStyle(
        "Section",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=14,
        textColor=_DARK,
        spaceBefore=4 * mm,
        spaceAfter=2.5 * mm,
    )
    section_teal = ParagraphStyle(
        "SectionTeal",
        parent=section,
        fontSize=10,
        leading=13,
        textColor=_TEAL,
        spaceBefore=5 * mm,
        spaceAfter=3 * mm,
    )
    body = ParagraphStyle(
        "Body",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=9,
        leading=13,
        textColor=_DARK,
    )
    small = ParagraphStyle(
        "Small",
        parent=body,
        fontSize=7,
        leading=9,
        textColor=_MUTED,
    )
    table_header = ParagraphStyle(
        "TableHeader",
        parent=small,
        fontName="Helvetica-Bold",
        textColor=colors.white,
    )
    story: list[Flowable] = []

    story.append(_paragraph(f"{request.deployment} - Troubleshooting", title))
    identity_rows = [
        ["Namespace", request.subdomain, "Tier", request.tier or "Not specified"],
        [
            "Status",
            request.status or "Not provided",
            "Version",
            request.version or "Not provided",
        ],
        [
            "Instance",
            request.instance or "Not provided",
            "Client",
            request.client or "Not provided",
        ],
        ["Window", f"Latest {report.window.hours} hour(s)", "Analysis", "Complete"],
    ]
    identity_table = Table(
        identity_rows, colWidths=[22 * mm, 60 * mm, 21 * mm, 60 * mm]
    )
    identity_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
                ("BOX", (0, 0), (-1, -1), 0.5, _BORDER),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, _BORDER),
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 7.5),
                ("TEXTCOLOR", (0, 0), (-1, -1), _DARK),
                ("TEXTCOLOR", (0, 0), (0, -1), _MUTED),
                ("TEXTCOLOR", (2, 0), (2, -1), _MUTED),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.extend([identity_table, Spacer(1, 4 * mm)])

    story.append(_paragraph("AI Ops - Root Cause Analysis", section))

    story.extend([EvidenceFlow(document.width), Spacer(1, 5 * mm)])

    summary_style = ParagraphStyle(
        "Summary",
        parent=body,
        fontName="Helvetica",
        fontSize=9.5,
        leading=14,
        textColor=_DARK,
        spaceAfter=2.5 * mm,
    )
    story.append(_summary_paragraph(report.severity, report.summary, summary_style))
    story.append(
        _paragraph(f"Started {_timestamp(report.issue_started)}", small)
    )

    story.append(_paragraph("RECOMMENDED ACTIONS", section))
    for index, action in enumerate(report.recommended_actions, start=1):
        badge_style = ParagraphStyle(
            f"ActionBadge{index}",
            parent=small,
            fontName="Helvetica-Bold",
            fontSize=8,
            leading=10,
            alignment=1,
            textColor=_TEAL,
        )
        action_table = Table(
            [[_paragraph(index, badge_style), _paragraph(action, body)]],
            colWidths=[8 * mm, document.width - 8 * mm],
            hAlign="LEFT",
        )
        action_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#E6F8FA")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("ALIGN", (0, 0), (0, 0), "CENTER"),
                    ("LEFTPADDING", (0, 0), (0, 0), 3),
                    ("RIGHTPADDING", (0, 0), (0, 0), 3),
                    ("TOPPADDING", (0, 0), (0, 0), 4),
                    ("BOTTOMPADDING", (0, 0), (0, 0), 4),
                    ("LEFTPADDING", (1, 0), (1, 0), 7),
                    ("RIGHTPADDING", (1, 0), (1, 0), 0),
                    ("TOPPADDING", (1, 0), (1, 0), 4),
                    ("BOTTOMPADDING", (1, 0), (1, 0), 4),
                ]
            )
        )
        story.extend([action_table, Spacer(1, 1.5 * mm)])

    metrics_heading = _paragraph(
        f"APPLICATION METRICS FLAGGED BY AI ({len(report.application_metrics)})",
        section_teal,
    )
    if report.application_metrics:
        chart_width = (document.width - 4 * mm) / 2
        chart_height = 57 * mm
        chart_rows = []
        for index in range(0, len(report.application_metrics), 2):
            charts = [
                MetricChart(metric, chart_width, chart_height)
                for metric in report.application_metrics[index : index + 2]
            ]
            if len(charts) == 1:
                charts.append(Spacer(chart_width, chart_height))
            chart_rows.append([charts[0], Spacer(4 * mm, 1), charts[1]])
        chart_table = Table(
            chart_rows,
            colWidths=[chart_width, 4 * mm, chart_width],
            hAlign="LEFT",
        )
        chart_table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 2 * mm),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2 * mm),
                ]
            )
        )
        story.append(KeepTogether([metrics_heading, chart_table]))
    else:
        story.append(metrics_heading)
        story.append(
            _paragraph(
                "No Prometheus application metrics were available through Grafana.",
                body,
            )
        )

    story.extend([PageBreak(), _paragraph("CLUSTER EVENTS", section)])
    if report.cluster_events:
        event_data = [
            [
                _paragraph("Timestamp", table_header),
                _paragraph("Resource", table_header),
                _paragraph("Type", table_header),
                _paragraph("Reason", table_header),
                _paragraph("Message", table_header),
            ]
        ]
        for event in report.cluster_events:
            event_data.append(
                [
                    _paragraph(_timestamp(event.timestamp), small),
                    _paragraph(event.resource, small),
                    _paragraph(event.event_type, small),
                    _paragraph(event.reason or "-", small),
                    _paragraph(event.message, small),
                ]
            )
        event_table = LongTable(
            event_data,
            colWidths=[29 * mm, 34 * mm, 22 * mm, 25 * mm, 68 * mm],
            repeatRows=1,
            splitByRow=1,
        )
        event_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), _DARK),
                    ("BOX", (0, 0), (-1, -1), 0.5, _BORDER),
                    ("INNERGRID", (0, 0), (-1, -1), 0.25, _BORDER),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    (
                        "ROWBACKGROUNDS",
                        (0, 1),
                        (-1, -1),
                        [colors.white, colors.HexColor("#F8FAFC")],
                    ),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.append(event_table)
    else:
        story.append(
            _paragraph("No cluster events were selected for this report.", body)
        )

    document.build(
        story,
        onFirstPage=_page_header_footer,
        onLaterPages=_page_header_footer,
    )
    return buffer.getvalue()
