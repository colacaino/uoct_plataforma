from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class PreparedRouteData:
    data: dict[str, dict[str, float]]
    stats: dict[str, Any]
    columns: dict[str, str]


def normalize_key(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-zA-Z0-9]+", "", text).lower()


def detect_column(columns: list[str], candidates: list[str]) -> str | None:
    normalized = {normalize_key(column): column for column in columns}
    for candidate in candidates:
        found = normalized.get(normalize_key(candidate))
        if found:
            return found
    return None


def detect_route_columns(columns: list[str]) -> dict[str, str]:
    mapping = {
        "route": detect_column(columns, ["routeName", "ruta", "route", "nombreRuta", "nombre_ruta"]),
        "length": detect_column(columns, ["length", "distancia", "largo", "longitud", "distance", "dist"]),
        "time": detect_column(columns, ["time", "tiempo", "duration", "duracion", "segundos", "travelTime"]),
        "timestamp": detect_column(columns, ["timestamp", "hora", "datetime", "fecha_hora", "ts", "horaInicio"]),
    }
    missing = [key for key, value in mapping.items() if not value]
    if missing:
        raise ValueError(f"No se pudieron detectar columnas obligatorias: {', '.join(missing)}.")
    return mapping


def parse_clock(value: Any) -> time | None:
    if value in (None, ""):
        return None
    if isinstance(value, time):
        return value
    if isinstance(value, datetime):
        return value.time().replace(second=0, microsecond=0)
    text = str(value).strip()
    match = re.search(r"(\d{1,2})\s*[:.]\s*(\d{2})", text)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return time(hour=hour, minute=minute)


def time_to_minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def timestamp_to_minutes(value: Any) -> float:
    if value is None:
        return math.nan
    try:
        if pd.isna(value):
            return math.nan
    except TypeError:
        pass
    if isinstance(value, pd.Timestamp):
        return value.hour * 60 + value.minute
    if isinstance(value, datetime):
        return value.hour * 60 + value.minute
    if isinstance(value, time):
        return value.hour * 60 + value.minute
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        frac = float(value) % 1
        return round(frac * 1440)

    text = str(value).strip()
    if not text:
        return math.nan
    parsed = pd.to_datetime(text, errors="coerce")
    if not pd.isna(parsed):
        return parsed.hour * 60 + parsed.minute
    match = re.search(r"(\d{1,2})\s*[:.]\s*(\d{2})", text)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
        if hour <= 23 and minute <= 59:
            return hour * 60 + minute
    return math.nan


def in_window(minutes: float, start_minute: int, end_minute: int) -> bool:
    if math.isnan(minutes):
        return False
    if start_minute <= end_minute:
        return start_minute <= minutes <= end_minute
    return minutes >= start_minute or minutes <= end_minute


def parse_route_hints(value: str) -> list[str]:
    text = value or ""
    pieces = re.split(r"[\n;,|]+", text)
    cleaned = []
    for piece in pieces:
        piece = piece.strip()
        if piece and normalize_key(piece) not in {"ruta", "rutas"}:
            cleaned.append(piece)
    return cleaned


def percentile(values: pd.Series, q: float) -> float:
    if values.empty:
        return 0.0
    return float(values.quantile(q / 100))


def round_float(value: Any, digits: int = 2) -> float:
    if value is None:
        return 0.0
    if isinstance(value, float) and math.isnan(value):
        return 0.0
    return round(float(value), digits)


def prepare_route_file(path: str | Path, start: time, end: time) -> PreparedRouteData:
    df = pd.read_excel(path)
    if df.empty:
        raise ValueError("El archivo está vacío.")

    columns = detect_route_columns([str(column) for column in df.columns])
    start_minute = time_to_minutes(start)
    end_minute = time_to_minutes(end)

    total_rows = len(df)
    work = pd.DataFrame(
        {
            "route": df[columns["route"]].astype(str).str.strip(),
            "length": pd.to_numeric(df[columns["length"]], errors="coerce"),
            "travel_time": pd.to_numeric(df[columns["time"]], errors="coerce"),
            "minute": df[columns["timestamp"]].map(timestamp_to_minutes),
        }
    )
    valid_timestamp = work["minute"].apply(lambda value: not math.isnan(value))
    in_range = work["minute"].apply(lambda value: in_window(value, start_minute, end_minute))
    valid_values = (
        work["route"].ne("")
        & work["length"].notna()
        & work["travel_time"].notna()
        & (work["length"] > 0)
        & (work["travel_time"] > 0)
    )
    processed_df = work[valid_timestamp & in_range & valid_values].copy()
    processed_df["speed"] = (processed_df["length"] / processed_df["travel_time"]) * 3.6

    route_data = {}
    for route_name, group in processed_df.groupby("route"):
        speed = group["speed"]
        travel_time = group["travel_time"]
        route_data[route_name] = {
            "speed_mean": round_float(speed.mean()),
            "speed_median": round_float(speed.median()),
            "speed_std": round_float(speed.std(ddof=1) if len(speed) > 1 else 0),
            "speed_p15": round_float(percentile(speed, 15)),
            "speed_p85": round_float(percentile(speed, 85)),
            "time_mean": round_float(travel_time.mean()),
            "time_median": round_float(travel_time.median()),
            "time_p15": round_float(percentile(travel_time, 15)),
            "time_p85": round_float(percentile(travel_time, 85)),
            "samples": int(len(group)),
        }

    stats = {
        "total_rows": int(total_rows),
        "processed_rows": int(len(processed_df)),
        "invalid_rows": int((~valid_timestamp | (in_range & ~valid_values)).sum()),
        "out_of_window_rows": int((valid_timestamp & ~in_range).sum()),
        "routes": int(len(route_data)),
    }
    return PreparedRouteData(data=route_data, stats=stats, columns=columns)


def match_selected_routes(common_routes: list[str], route_hints_text: str) -> tuple[list[str], list[str]]:
    hints = parse_route_hints(route_hints_text)
    if not hints:
        return common_routes, []

    normalized_routes = {normalize_key(route): route for route in common_routes}
    selected = set()
    unmatched = []

    for hint in hints:
        normalized_hint = normalize_key(hint)
        if normalized_hint in normalized_routes:
            selected.add(normalized_routes[normalized_hint])
            continue
        partial_matches = []
        if len(normalized_hint) >= 8:
            partial_matches = [
                route
                for route in common_routes
                if normalized_hint in normalize_key(route) or normalize_key(route) in normalized_hint
            ]
        if partial_matches:
            selected.update(partial_matches)
        else:
            unmatched.append(hint)

    if not selected:
        return common_routes, unmatched
    return sorted(selected), unmatched


def classify_route(speed_delta_pct: float, threshold_pct: float) -> str:
    if speed_delta_pct > threshold_pct:
        return "mejoro"
    if speed_delta_pct < -threshold_pct:
        return "empeoro"
    return "sin_cambio"


def result_label(result: str) -> str:
    return {
        "mejoro": "Mejoró",
        "empeoro": "Empeoró",
        "sin_cambio": "Sin cambio",
        "sin_datos": "Sin datos",
    }.get(result, result)


def build_conclusion(summary: dict[str, Any]) -> str:
    if summary["routes_analyzed"] == 0:
        return "No se encontraron rutas comunes para el horario seleccionado."
    direction = result_label(summary["result"]).lower()
    return (
        f"El análisis global indica que el cruce {direction}: la velocidad promedio cambió "
        f"{summary['speed_delta_pct']:+.1f}% y el tiempo promedio cambió {summary['time_delta_pct']:+.1f}% "
        f"en {summary['routes_analyzed']} ruta(s) analizada(s)."
    )


def _number_from_mapping(source: dict[str, Any], key: str, default: float = 0.0) -> float:
    try:
        return float(source.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def _percentage(part: float, total: float) -> float:
    if not total:
        return 0.0
    return round_float((part / total) * 100)


def _sample_delta_pct(row: dict[str, Any]) -> float:
    samples_before = _number_from_mapping(row, "samples_before")
    samples_after = _number_from_mapping(row, "samples_after")
    if not samples_before:
        return 0.0
    return round_float(((samples_after - samples_before) / samples_before) * 100)


def _confidence_label(samples_before: int, samples_after: int) -> str:
    minimum = min(samples_before, samples_after)
    if minimum >= 30:
        return "Alta"
    if minimum >= 10:
        return "Media"
    return "Baja"


def _route_payload(row: dict[str, Any]) -> dict[str, Any]:
    samples_before = int(_number_from_mapping(row, "samples_before"))
    samples_after = int(_number_from_mapping(row, "samples_after"))
    speed_delta_pct = _number_from_mapping(row, "speed_delta_pct")
    time_delta_pct = _number_from_mapping(row, "time_delta_pct")
    sample_delta_pct = _sample_delta_pct(row)
    impact_score = max(0.0, -speed_delta_pct) * 0.6 + max(0.0, time_delta_pct) * 0.35 + max(0.0, -sample_delta_pct) * 0.05
    improvement_score = max(0.0, speed_delta_pct) * 0.65 + max(0.0, -time_delta_pct) * 0.35
    return {
        "route": row.get("route") or "Ruta sin nombre",
        "speed_before": round_float(_number_from_mapping(row, "speed_before")),
        "speed_after": round_float(_number_from_mapping(row, "speed_after")),
        "speed_delta_pct": round_float(speed_delta_pct),
        "time_before": round_float(_number_from_mapping(row, "time_before")),
        "time_after": round_float(_number_from_mapping(row, "time_after")),
        "time_delta_pct": round_float(time_delta_pct),
        "samples_before": samples_before,
        "samples_after": samples_after,
        "sample_delta_pct": sample_delta_pct,
        "speed_std_before": round_float(_number_from_mapping(row, "speed_std_before")),
        "speed_std_after": round_float(_number_from_mapping(row, "speed_std_after")),
        "result": row.get("result") or "sin_datos",
        "result_label": row.get("result_label") or result_label(row.get("result") or "sin_datos"),
        "confidence_label": _confidence_label(samples_before, samples_after),
        "impact_score": round_float(impact_score),
        "improvement_score": round_float(improvement_score),
    }


def _quality_block(summary: dict[str, Any]) -> dict[str, Any]:
    before = summary.get("before_quality") or {}
    after = summary.get("after_quality") or {}
    before_rate = _percentage(_number_from_mapping(before, "processed_rows"), _number_from_mapping(before, "total_rows"))
    after_rate = _percentage(_number_from_mapping(after, "processed_rows"), _number_from_mapping(after, "total_rows"))
    minimum_rate = min(before_rate, after_rate) if before_rate or after_rate else 0.0
    if minimum_rate >= 70:
        level = "alta"
        label = "Alta"
    elif minimum_rate >= 30:
        level = "media"
        label = "Media"
    elif minimum_rate > 0:
        level = "baja"
        label = "Baja"
    else:
        level = "sin_datos"
        label = "Sin datos"
    return {
        "level": level,
        "label": label,
        "before_processed_pct": before_rate,
        "after_processed_pct": after_rate,
        "minimum_processed_pct": minimum_rate,
    }


def build_executive_analysis(summary: dict[str, Any] | None, route_rows: list[dict[str, Any]] | None) -> dict[str, Any]:
    summary = summary or {}
    rows = [_route_payload(row) for row in (route_rows or [])]
    routes_analyzed = int(_number_from_mapping(summary, "routes_analyzed", len(rows)) or len(rows))
    threshold = _number_from_mapping(summary, "threshold_pct", 5.0)
    counts = summary.get("counts") if isinstance(summary.get("counts"), dict) else {}
    improved_count = int(counts.get("mejoro") or sum(1 for row in rows if row["result"] == "mejoro"))
    degraded_count = int(counts.get("empeoro") or sum(1 for row in rows if row["result"] == "empeoro"))
    unchanged_count = int(counts.get("sin_cambio") or sum(1 for row in rows if row["result"] == "sin_cambio"))
    speed_delta_pct = _number_from_mapping(summary, "speed_delta_pct")
    time_delta_pct = _number_from_mapping(summary, "time_delta_pct")
    quality = _quality_block(summary)

    critical_candidates = [
        row
        for row in rows
        if row["speed_delta_pct"] <= -threshold or row["time_delta_pct"] >= threshold
    ]
    critical_routes = sorted(critical_candidates, key=lambda row: row["impact_score"], reverse=True)[:5]
    top_improvements = sorted(
        [row for row in rows if row["speed_delta_pct"] > threshold or row["time_delta_pct"] < -threshold],
        key=lambda row: row["improvement_score"],
        reverse=True,
    )[:5]
    coverage_alerts = sorted(
        [
            row
            for row in rows
            if row["sample_delta_pct"] <= -25 or min(row["samples_before"], row["samples_after"]) < 5
        ],
        key=lambda row: (row["sample_delta_pct"], row["samples_after"]),
    )[:5]
    variable_routes = sorted(
        [row for row in rows if max(row["speed_std_before"], row["speed_std_after"]) > 0],
        key=lambda row: max(row["speed_std_before"], row["speed_std_after"]),
        reverse=True,
    )[:5]

    degraded_share = _percentage(degraded_count, routes_analyzed)
    improved_share = _percentage(improved_count, routes_analyzed)
    critical_share = _percentage(len(critical_candidates), routes_analyzed)

    if routes_analyzed == 0:
        risk = {
            "level": "sin_datos",
            "label": "Sin datos",
            "reason": "No hubo rutas comunes en el horario seleccionado.",
        }
    elif (
        degraded_share >= 35
        or critical_share >= 25
        or speed_delta_pct <= -10
        or time_delta_pct >= 15
        or quality["level"] == "baja"
    ):
        risk = {
            "level": "alto",
            "label": "Alto",
            "reason": "Hay deterioro relevante o baja calidad de datos para sostener la comparacion.",
        }
    elif degraded_count > 0 or speed_delta_pct < 0 or time_delta_pct > 0 or quality["level"] == "media":
        risk = {
            "level": "medio",
            "label": "Medio",
            "reason": "Existen rutas con deterioro o senales que requieren seguimiento.",
        }
    else:
        risk = {
            "level": "bajo",
            "label": "Bajo",
            "reason": "La comparacion muestra una condicion favorable y sin deterioros relevantes.",
        }

    if routes_analyzed == 0:
        headline = "No se detectaron rutas comparables para este horario."
        narrative = "La prioridad es revisar horario, columnas y nombres de rutas antes de interpretar resultados."
    else:
        headline = (
            f"Se analizaron {routes_analyzed} rutas: {improved_count} mejoran, "
            f"{degraded_count} empeoran y {unchanged_count} quedan sin cambio."
        )
        narrative = (
            f"La velocidad promedio cambio {speed_delta_pct:+.1f}% y el tiempo de viaje cambio "
            f"{time_delta_pct:+.1f}%. El riesgo operacional calculado es {risk['label'].lower()}."
        )

    readouts = []
    if routes_analyzed:
        readouts.extend(
            [
                f"Rutas con mejora: {improved_count} ({improved_share:.1f}%).",
                f"Rutas con deterioro: {degraded_count} ({degraded_share:.1f}%).",
                f"Rutas criticas detectadas: {len(critical_candidates)} ({critical_share:.1f}%).",
                (
                    "Calidad de datos procesados: "
                    f"{quality['before_processed_pct']:.1f}% antes y {quality['after_processed_pct']:.1f}% despues."
                ),
            ]
        )
    else:
        readouts.append("No hubo rutas con datos validos en ambos archivos.")

    recommendations = []
    if routes_analyzed == 0:
        recommendations.extend(
            [
                "Revisar que ambos archivos usen las mismas rutas o nombres equivalentes.",
                "Validar que el horario elegido tenga registros en ambos periodos.",
                "Confirmar columnas obligatorias: ruta, distancia, tiempo y fecha/hora.",
            ]
        )
    else:
        if critical_routes:
            names = ", ".join(row["route"] for row in critical_routes[:3])
            recommendations.append(f"Priorizar revision operacional en: {names}.")
        if degraded_count:
            recommendations.append("Contrastar rutas con deterioro contra cambios de programacion semaforica y condiciones de terreno.")
        if coverage_alerts:
            recommendations.append("Validar cobertura Big Data en rutas con caida de muestras antes de tomar decisiones.")
        if quality["level"] in {"baja", "media"}:
            recommendations.append("Revisar filtros de horario y formato de datos para mejorar la calidad procesada.")
        if improved_count and not degraded_count:
            recommendations.append("Documentar la configuracion aplicada y mantener seguimiento en el proximo periodo.")
        if not recommendations:
            recommendations.append("Mantener monitoreo y revisar manualmente las rutas de mayor volumen.")

    return {
        "headline": headline,
        "narrative": narrative,
        "risk": risk,
        "quality": quality,
        "counts": {
            "improved": improved_count,
            "degraded": degraded_count,
            "unchanged": unchanged_count,
            "critical": len(critical_candidates),
        },
        "shares": {
            "improved": improved_share,
            "degraded": degraded_share,
            "critical": critical_share,
        },
        "readouts": readouts,
        "recommendations": recommendations[:5],
        "critical_routes": critical_routes,
        "top_improvements": top_improvements,
        "coverage_alerts": coverage_alerts,
        "variable_routes": variable_routes,
    }


def compare_route_files(
    before_path: str | Path,
    after_path: str | Path,
    start: time,
    end: time,
    threshold_pct: float = 5.0,
    route_hints_text: str = "",
) -> dict[str, Any]:
    before = prepare_route_file(before_path, start, end)
    after = prepare_route_file(after_path, start, end)
    common_routes = sorted(set(before.data).intersection(after.data))
    selected_routes, unmatched_hints = match_selected_routes(common_routes, route_hints_text)

    route_rows = []
    for route_name in selected_routes:
        before_row = before.data[route_name]
        after_row = after.data[route_name]
        speed_before = before_row["speed_mean"]
        speed_after = after_row["speed_mean"]
        time_before = before_row["time_mean"]
        time_after = after_row["time_mean"]
        speed_delta_pct = ((speed_after - speed_before) / speed_before * 100) if speed_before else 0
        time_delta_pct = ((time_after - time_before) / time_before * 100) if time_before else 0
        route_result = classify_route(speed_delta_pct, threshold_pct)

        route_rows.append(
            {
                "route": route_name,
                "speed_before": round_float(speed_before),
                "speed_after": round_float(speed_after),
                "speed_delta_pct": round_float(speed_delta_pct),
                "time_before": round_float(time_before),
                "time_after": round_float(time_after),
                "time_delta_pct": round_float(time_delta_pct),
                "samples_before": before_row["samples"],
                "samples_after": after_row["samples"],
                "speed_std_before": before_row["speed_std"],
                "speed_std_after": after_row["speed_std"],
                "speed_p15_before": before_row["speed_p15"],
                "speed_p85_before": before_row["speed_p85"],
                "speed_p15_after": after_row["speed_p15"],
                "speed_p85_after": after_row["speed_p85"],
                "result": route_result,
                "result_label": result_label(route_result),
            }
        )

    route_rows.sort(key=lambda row: row["speed_delta_pct"], reverse=True)
    if route_rows:
        avg_speed_before = sum(row["speed_before"] for row in route_rows) / len(route_rows)
        avg_speed_after = sum(row["speed_after"] for row in route_rows) / len(route_rows)
        avg_time_before = sum(row["time_before"] for row in route_rows) / len(route_rows)
        avg_time_after = sum(row["time_after"] for row in route_rows) / len(route_rows)
        speed_delta_pct = ((avg_speed_after - avg_speed_before) / avg_speed_before * 100) if avg_speed_before else 0
        time_delta_pct = ((avg_time_after - avg_time_before) / avg_time_before * 100) if avg_time_before else 0
        result = classify_route(speed_delta_pct, threshold_pct)
    else:
        avg_speed_before = avg_speed_after = avg_time_before = avg_time_after = 0
        speed_delta_pct = time_delta_pct = 0
        result = "sin_datos"

    counts = {
        "mejoro": sum(1 for row in route_rows if row["result"] == "mejoro"),
        "empeoro": sum(1 for row in route_rows if row["result"] == "empeoro"),
        "sin_cambio": sum(1 for row in route_rows if row["result"] == "sin_cambio"),
    }
    warnings = []
    for label, stats in (("ANTES", before.stats), ("DESPUÉS", after.stats)):
        if stats["total_rows"] and stats["processed_rows"] / stats["total_rows"] < 0.1:
            warnings.append(f"{label}: menos del 10% de filas quedó dentro del horario y con datos válidos.")
    if unmatched_hints:
        warnings.append(f"No coincidieron {len(unmatched_hints)} ruta(s) sugerida(s) desde la bitácora.")

    summary = {
        "result": result,
        "result_label": result_label(result),
        "threshold_pct": round_float(threshold_pct, 1),
        "start": start.strftime("%H:%M"),
        "end": end.strftime("%H:%M"),
        "routes_before": before.stats["routes"],
        "routes_after": after.stats["routes"],
        "common_routes": len(common_routes),
        "routes_analyzed": len(route_rows),
        "avg_speed_before": round_float(avg_speed_before),
        "avg_speed_after": round_float(avg_speed_after),
        "speed_delta_pct": round_float(speed_delta_pct),
        "avg_time_before": round_float(avg_time_before),
        "avg_time_after": round_float(avg_time_after),
        "time_delta_pct": round_float(time_delta_pct),
        "counts": counts,
        "before_quality": before.stats,
        "after_quality": after.stats,
        "before_columns": before.columns,
        "after_columns": after.columns,
        "route_hints_used": bool(parse_route_hints(route_hints_text)),
        "unmatched_route_hints": unmatched_hints,
        "warnings": warnings,
        "top_improvements": route_rows[:5],
        "top_degradations": sorted(route_rows, key=lambda row: row["speed_delta_pct"])[:5],
    }
    summary["conclusion"] = build_conclusion(summary)
    summary["executive_analysis"] = build_executive_analysis(summary, route_rows)
    return {"summary": summary, "route_rows": route_rows}
