from __future__ import annotations

from io import BytesIO
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .analysis_engine import build_executive_analysis


def _value(value: Any, default: str = ""):
    return default if value is None else value


def _autosize_sheet(sheet):
    for column_cells in sheet.columns:
        max_length = 0
        column = get_column_letter(column_cells[0].column)
        for cell in column_cells:
            max_length = max(max_length, len(str(cell.value or "")))
        sheet.column_dimensions[column].width = min(max(max_length + 2, 12), 45)


def _traffic_narrative(summary: dict[str, Any]) -> list[str]:
    dashboard = summary.get("traffic_dashboard") or {}
    kpis = dashboard.get("kpis") or {}
    insights = dashboard.get("insights") or {}
    if not kpis:
        return []
    peak = insights.get("peak_queue") or {}
    minimum = insights.get("min_speed") or {}
    paragraphs = [
        (
            "Se analizaron las velocidades y la cola estimada por intervalo horario, "
            f"considerando {kpis.get('records', 0)} registros distribuidos en {kpis.get('routes', 0)} ruta(s). "
            f"La velocidad promedio del periodo fue {kpis.get('avg_speed', 0)} km/h y la cola promedio "
            f"estimada fue {kpis.get('avg_queue_km', 0)} km."
        )
    ]
    if peak:
        paragraphs.append(
            f"El mayor punto de cola se observa en {peak.get('route')} durante {peak.get('period')} "
            f"a las {peak.get('bucket')}, con {peak.get('queue_km')} km estimados."
        )
    if minimum:
        paragraphs.append(
            f"La menor velocidad registrada se observa en {minimum.get('route')} durante {minimum.get('period')} "
            f"a las {minimum.get('bucket')}, con {minimum.get('speed')} km/h."
        )
    paragraphs.append(
        "La cola se calcula usando la velocidad libre P90 de cada ruta como referencia operacional. "
        "Esta lectura sirve para detectar saturacion horaria y no reemplaza la validacion en terreno."
    )
    return paragraphs


def build_analysis_excel(analysis) -> bytes:
    summary = analysis.summary or {}
    rows = analysis.route_rows or []
    executive = summary.get("executive_analysis") or build_executive_analysis(summary, rows)
    parameters = summary.get("parameters") or {}
    percentile_low = parameters.get("percentile_low", summary.get("percentile_low", 15))
    percentile_high = parameters.get("percentile_high", summary.get("percentile_high", 85))

    workbook = Workbook()
    summary_sheet = workbook.active
    summary_sheet.title = "Resumen"
    summary_sheet.append(["Campo", "Valor"])
    summary_items = [
        ("Análisis", analysis.name),
        ("Cruce", analysis.cruce.interseccion),
        ("Código J", analysis.cruce.codigo_j),
        ("Resultado", analysis.get_result_display()),
        ("Riesgo operacional", executive.get("risk", {}).get("label")),
        ("Horario", f"{analysis.horario_inicio:%H:%M} - {analysis.horario_fin:%H:%M}"),
        ("Variación velocidad (%)", summary.get("speed_delta_pct")),
        ("Variación tiempo (%)", summary.get("time_delta_pct")),
        ("Velocidad antes (km/h)", summary.get("avg_speed_before")),
        ("Velocidad después (km/h)", summary.get("avg_speed_after")),
        ("Rutas analizadas", summary.get("routes_analyzed")),
        ("Umbral clasificacion (%)", parameters.get("threshold_pct", summary.get("threshold_pct"))),
        ("Percentiles", f"P{percentile_low} - P{percentile_high}"),
        ("Largo minimo (m)", parameters.get("min_length_m", summary.get("min_length_m"))),
        ("Minimo observaciones", parameters.get("min_samples", summary.get("min_samples"))),
        ("Resumen ejecutivo", executive.get("headline")),
        ("Conclusión", summary.get("conclusion")),
    ]
    for item in summary_items:
        summary_sheet.append(item)
    for cell in summary_sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1D4ED8")
    for row in range(1, summary_sheet.max_row + 1):
        summary_sheet[f"B{row}"].alignment = Alignment(wrap_text=True, vertical="top")
    _autosize_sheet(summary_sheet)

    executive_sheet = workbook.create_sheet("Analisis ejecutivo")
    executive_sheet.append(["Seccion", "Detalle"])
    executive_sheet.append(["Resumen", executive.get("headline")])
    executive_sheet.append(["Narrativa", executive.get("narrative")])
    executive_sheet.append(["Nivel de riesgo", executive.get("risk", {}).get("label")])
    executive_sheet.append(["Motivo riesgo", executive.get("risk", {}).get("reason")])
    executive_sheet.append(["Calidad datos", executive.get("quality", {}).get("label")])
    executive_sheet.append(["Lecturas clave", ""])
    for item in executive.get("readouts", []):
        executive_sheet.append(["", item])
    executive_sheet.append(["Recomendaciones", ""])
    for item in executive.get("recommendations", []):
        executive_sheet.append(["", item])
    executive_sheet.append(["Rutas criticas", "Velocidad %, Tiempo %, Muestras, Confianza"])
    for row in executive.get("critical_routes", []):
        executive_sheet.append(
            [
                row.get("route"),
                f"{row.get('speed_delta_pct')}%, {row.get('time_delta_pct')}%, "
                f"{row.get('samples_before')}/{row.get('samples_after')}, {row.get('confidence_label')}",
            ]
        )
    executive_sheet.append(["Mejores mejoras", "Velocidad %, Tiempo %, Muestras"])
    for row in executive.get("top_improvements", []):
        executive_sheet.append(
            [
                row.get("route"),
                f"{row.get('speed_delta_pct')}%, {row.get('time_delta_pct')}%, "
                f"{row.get('samples_before')}/{row.get('samples_after')}",
            ]
        )
    for cell in executive_sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1D4ED8")
    for row in range(1, executive_sheet.max_row + 1):
        executive_sheet[f"B{row}"].alignment = Alignment(wrap_text=True, vertical="top")
    _autosize_sheet(executive_sheet)

    detail_sheet = workbook.create_sheet("Detalle rutas")
    detail_sheet.append(
        [
            "Ruta",
            "Obs. antes",
            "Obs. después",
            "Vel. antes",
            "Vel. después",
            "Delta vel. %",
            "Tiempo antes",
            "Tiempo después",
            "Delta tiempo %",
            "Resultado",
            "Largo antes km",
            "Largo despues km",
            f"P{percentile_low} antes",
            f"P{percentile_high} antes",
            f"P{percentile_low} despues",
            f"P{percentile_high} despues",
            "Confianza",
            "Analisis ruta",
        ]
    )
    for row in rows:
        detail_sheet.append(
            [
                row.get("route"),
                row.get("samples_before"),
                row.get("samples_after"),
                row.get("speed_before"),
                row.get("speed_after"),
                row.get("speed_delta_pct"),
                row.get("time_before"),
                row.get("time_after"),
                row.get("time_delta_pct"),
                row.get("result_label"),
                row.get("length_before_km"),
                row.get("length_after_km"),
                row.get("speed_p_low_before", row.get("speed_p15_before")),
                row.get("speed_p_high_before", row.get("speed_p85_before")),
                row.get("speed_p_low_after", row.get("speed_p15_after")),
                row.get("speed_p_high_after", row.get("speed_p85_after")),
                row.get("confidence_label"),
                row.get("analysis_text"),
            ]
        )
    for cell in detail_sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="334155")
    _autosize_sheet(detail_sheet)

    quality_sheet = workbook.create_sheet("Calidad")
    quality_sheet.append(
        [
            "Archivo",
            "Filas totales",
            "Procesadas",
            "Fuera horario",
            "Invalidas",
            "Hora invalida",
            "Valores invalidos",
            "Largo bajo minimo",
            "Rutas",
        ]
    )
    for label, key in (("ANTES", "before_quality"), ("DESPUÉS", "after_quality")):
        quality = summary.get(key, {})
        quality_sheet.append(
            [
                label,
                quality.get("total_rows"),
                quality.get("processed_rows"),
                quality.get("out_of_window_rows"),
                quality.get("invalid_rows"),
                quality.get("invalid_timestamp_rows"),
                quality.get("invalid_value_rows"),
                quality.get("short_length_rows"),
                quality.get("routes"),
            ]
        )
    for cell in quality_sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="334155")
    _autosize_sheet(quality_sheet)

    methodology_sheet = workbook.create_sheet("Metodologia")
    methodology_sheet.append(["Tema", "Detalle"])
    methodology_sheet.append(["Formula velocidad", parameters.get("speed_formula", "velocidad_kmh = largo_m / tiempo_s * 3.6")])
    methodology_sheet.append(["Formula delta", parameters.get("delta_formula", "delta_pct = (despues - antes) / antes * 100")])
    for item in summary.get("methodology", []):
        methodology_sheet.append([item.get("title"), item.get("body")])
    for cell in methodology_sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="334155")
    for row in range(1, methodology_sheet.max_row + 1):
        methodology_sheet[f"B{row}"].alignment = Alignment(wrap_text=True, vertical="top")
    _autosize_sheet(methodology_sheet)

    traffic = summary.get("traffic_dashboard") or {}
    if traffic.get("points"):
        traffic_sheet = workbook.create_sheet("Dashboard rutas")
        traffic_sheet.append(["Ruta", "Periodo", "Fecha", "Hora", "Velocidad km/h", "Cola km", "Registros", "Largo km"])
        for point in traffic.get("points", [])[:2000]:
            traffic_sheet.append(
                [
                    point.get("route"),
                    point.get("period"),
                    point.get("date"),
                    point.get("bucket"),
                    point.get("speed"),
                    point.get("queue_km"),
                    point.get("samples"),
                    point.get("length_km"),
                ]
            )
        for cell in traffic_sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="1D4ED8")
        _autosize_sheet(traffic_sheet)

    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def build_analysis_pdf(analysis) -> bytes:
    summary = analysis.summary or {}
    rows = analysis.route_rows or []
    executive = summary.get("executive_analysis") or build_executive_analysis(summary, rows)
    parameters = summary.get("parameters") or {}
    percentile_low = parameters.get("percentile_low", summary.get("percentile_low", 15))
    percentile_high = parameters.get("percentile_high", summary.get("percentile_high", 85))
    output = BytesIO()
    doc = SimpleDocTemplate(
        output,
        pagesize=landscape(A4),
        rightMargin=1.2 * cm,
        leftMargin=1.2 * cm,
        topMargin=1.2 * cm,
        bottomMargin=1.2 * cm,
    )
    styles = getSampleStyleSheet()
    story = [
        Paragraph(f"Reporte operacional: {analysis.name}", styles["Title"]),
        Paragraph(f"Cruce: {analysis.cruce.interseccion}", styles["Heading2"]),
        Paragraph(_value(summary.get("conclusion"), "Sin conclusión disponible."), styles["BodyText"]),
        Spacer(1, 0.35 * cm),
    ]

    cruce = analysis.cruce
    field_table = Table(
        [
            ["Eje / cruce evaluado", cruce.interseccion, "Codigo", cruce.codigo_j or "-"],
            ["Comuna", cruce.comuna or "-", "Jornada", cruce.jornada or "-"],
            [
                "Fecha terreno",
                cruce.fecha_terreno.strftime("%d/%m/%Y") if cruce.fecha_terreno else "-",
                "Evaluador",
                cruce.evaluador or "-",
            ],
            ["Solicita", cruce.solicita or "-", "Proyecto", cruce.project.name if cruce.project else "-"],
        ],
        colWidths=[4 * cm, 9 * cm, 3 * cm, 7 * cm],
    )
    field_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EEF8")),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#CBD5E1")),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("PADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.extend([Paragraph("Ficha operativa", styles["Heading2"]), field_table, Spacer(1, 0.35 * cm)])

    summary_table = Table(
        [
            ["Resultado", analysis.get_result_display(), "Horario", f"{analysis.horario_inicio:%H:%M} - {analysis.horario_fin:%H:%M}"],
            ["Delta velocidad", f"{summary.get('speed_delta_pct', 0)}%", "Delta tiempo", f"{summary.get('time_delta_pct', 0)}%"],
            ["Vel. antes", f"{summary.get('avg_speed_before', 0)} km/h", "Vel. después", f"{summary.get('avg_speed_after', 0)} km/h"],
            ["Rutas analizadas", summary.get("routes_analyzed", 0), "Rutas comunes", summary.get("common_routes", 0)],
        ],
        colWidths=[4 * cm, 4 * cm, 4 * cm, 4 * cm],
    )
    summary_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EEF8")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("PADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.extend([summary_table, Spacer(1, 0.45 * cm)])

    parameter_table = Table(
        [
            ["Parametro", "Valor", "Para que sirve"],
            [
                "Umbral",
                f"{parameters.get('threshold_pct', summary.get('threshold_pct', 5))}%",
                "Clasifica mejora, deterioro o sin cambio segun la variacion de velocidad.",
            ],
            [
                "Percentiles",
                f"P{percentile_low} - P{percentile_high}",
                "Muestra dispersion por ruta sin depender solo del promedio.",
            ],
            [
                "Largo minimo",
                f"{parameters.get('min_length_m', summary.get('min_length_m', 0))} m",
                "Descarta registros demasiado cortos o ruidosos.",
            ],
            [
                "Minimo observaciones",
                parameters.get("min_samples", summary.get("min_samples", 1)),
                "Excluye rutas con baja muestra antes/despues.",
            ],
        ],
        colWidths=[4.2 * cm, 4 * cm, 14 * cm],
    )
    parameter_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1D4ED8")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#CBD5E1")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("PADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.extend([Paragraph("Parametros aplicados", styles["Heading2"]), parameter_table, Spacer(1, 0.35 * cm)])

    story.extend(
        [
            Paragraph("Resumen ejecutivo", styles["Heading2"]),
            Paragraph(_value(executive.get("headline"), "Sin resumen ejecutivo disponible."), styles["BodyText"]),
            Paragraph(_value(executive.get("narrative"), ""), styles["BodyText"]),
            Paragraph(
                f"Nivel de riesgo: {executive.get('risk', {}).get('label', 'Sin datos')}. "
                f"{executive.get('risk', {}).get('reason', '')}",
                styles["BodyText"],
            ),
            Spacer(1, 0.25 * cm),
            Paragraph("Lecturas clave", styles["Heading3"]),
        ]
    )
    for item in executive.get("readouts", []):
        story.append(Paragraph(f"- {item}", styles["BodyText"]))
    story.append(Spacer(1, 0.2 * cm))
    story.append(Paragraph("Recomendaciones", styles["Heading3"]))
    for item in executive.get("recommendations", []):
        story.append(Paragraph(f"- {item}", styles["BodyText"]))
    story.append(Spacer(1, 0.35 * cm))

    story.append(Paragraph("Metodologia de calculo", styles["Heading2"]))
    story.append(
        Paragraph(
            f"Formula de velocidad: {parameters.get('speed_formula', 'velocidad_kmh = largo_m / tiempo_s * 3.6')}. "
            f"Formula de variacion: {parameters.get('delta_formula', 'delta_pct = (despues - antes) / antes * 100')}.",
            styles["BodyText"],
        )
    )
    for item in summary.get("methodology", []):
        story.append(Paragraph(f"<b>{item.get('title')}</b>: {item.get('body')}", styles["BodyText"]))
    story.append(Spacer(1, 0.25 * cm))

    quality_rows = [["Archivo", "Total", "Procesadas", "Fuera horario", "Invalidas", "Hora invalida", "Valores", "Largo bajo", "Rutas"]]
    for label, key in (("Antes", "before_quality"), ("Despues", "after_quality")):
        quality = summary.get(key, {})
        quality_rows.append(
            [
                label,
                quality.get("total_rows", 0),
                quality.get("processed_rows", 0),
                quality.get("out_of_window_rows", 0),
                quality.get("invalid_rows", 0),
                quality.get("invalid_timestamp_rows", 0),
                quality.get("invalid_value_rows", 0),
                quality.get("short_length_rows", 0),
                quality.get("routes", 0),
            ]
        )
    quality_table = Table(quality_rows, repeatRows=1)
    quality_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#334155")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#CBD5E1")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("PADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.extend([Paragraph("Calidad de datos", styles["Heading3"]), quality_table, Spacer(1, 0.35 * cm)])

    traffic_paragraphs = _traffic_narrative(summary)
    if traffic_paragraphs:
        story.append(Paragraph("Analisis de los datos por horario", styles["Heading2"]))
        for paragraph in traffic_paragraphs:
            story.append(Paragraph(paragraph, styles["BodyText"]))
        route_summary = (summary.get("traffic_dashboard") or {}).get("route_summary") or []
        if route_summary:
            route_summary_data = [["Ruta", "Vel. prom.", "Cola prom.", "Cola max.", "Vel. min.", "Registros"]]
            for item in route_summary[:8]:
                route_summary_data.append(
                    [
                        str(item.get("route", ""))[:58],
                        f"{item.get('avg_speed', 0)} km/h",
                        f"{item.get('avg_queue_km', 0)} km",
                        f"{item.get('max_queue_km', 0)} km",
                        f"{item.get('min_speed', 0)} km/h",
                        item.get("samples", 0),
                    ]
                )
            route_summary_table = Table(route_summary_data, repeatRows=1)
            route_summary_table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F766E")),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#CBD5E1")),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("FONTSIZE", (0, 0), (-1, -1), 7),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("PADDING", (0, 0), (-1, -1), 4),
                    ]
                )
            )
            story.extend([Spacer(1, 0.15 * cm), route_summary_table])
        story.append(Spacer(1, 0.35 * cm))

    if executive.get("critical_routes"):
        critical_table_data = [["Ruta critica", "Delta vel.", "Delta tiempo", "Muestras", "Confianza"]]
        for row in executive.get("critical_routes", [])[:5]:
            critical_table_data.append(
                [
                    str(row.get("route", ""))[:60],
                    f"{row.get('speed_delta_pct', 0)}%",
                    f"{row.get('time_delta_pct', 0)}%",
                    f"{row.get('samples_before', 0)}/{row.get('samples_after', 0)}",
                    row.get("confidence_label", ""),
                ]
            )
        critical_table = Table(critical_table_data, repeatRows=1)
        critical_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#7F1D1D")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#CBD5E1")),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 7),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("PADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.extend([Paragraph("Rutas priorizadas", styles["Heading3"]), critical_table, Spacer(1, 0.35 * cm)])

    route_table_data = [
        [
            "Ruta",
            "Obs.",
            "Conf.",
            "Largo km",
            "Vel. antes",
            "Vel. despues",
            "Delta vel.",
            "Tiempo antes",
            "Tiempo despues",
            "Resultado",
        ]
    ]
    for row in rows[:35]:
        route_table_data.append(
            [
                str(row.get("route", ""))[:52],
                f"{row.get('samples_before', 0)}/{row.get('samples_after', 0)}",
                row.get("confidence_label", ""),
                f"{row.get('length_before_km', 0)}/{row.get('length_after_km', 0)}",
                row.get("speed_before", 0),
                row.get("speed_after", 0),
                f"{row.get('speed_delta_pct', 0)}%",
                row.get("time_before", 0),
                row.get("time_after", 0),
                row.get("result_label", ""),
            ]
        )
    route_table = Table(route_table_data, repeatRows=1)
    route_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#334155")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#CBD5E1")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("PADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.extend([Paragraph("Detalle de rutas", styles["Heading2"]), route_table, Spacer(1, 0.3 * cm)])

    if rows:
        interpretation_data = [["Ruta", "Interpretacion especifica"]]
        for row in rows[:12]:
            interpretation_data.append([str(row.get("route", ""))[:45], str(row.get("analysis_text", ""))[:260]])
        interpretation_table = Table(interpretation_data, repeatRows=1, colWidths=[7 * cm, 18 * cm])
        interpretation_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E3A8A")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#CBD5E1")),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 7),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("PADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.extend([Paragraph("Analisis especifico por ruta", styles["Heading3"]), interpretation_table])
    doc.build(story)
    return output.getvalue()
