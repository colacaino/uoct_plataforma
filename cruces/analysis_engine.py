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


def parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if isinstance(value, datetime):
        return value
    if isinstance(value, time):
        return datetime.combine(datetime.today().date(), value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        parsed = pd.to_datetime(value, unit="D", origin="1899-12-30", errors="coerce")
        return None if pd.isna(parsed) else parsed.to_pydatetime()
    parsed = pd.to_datetime(str(value).strip(), errors="coerce", dayfirst=True)
    return None if pd.isna(parsed) else parsed.to_pydatetime()


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


def floor_bucket(value: time, interval_minutes: int) -> str:
    interval = max(1, int(interval_minutes or 15))
    minutes = value.hour * 60 + value.minute
    snapped = (minutes // interval) * interval
    hour = (snapped // 60) % 24
    minute = snapped % 60
    return f"{hour:02d}:{minute:02d}"


def generate_time_slots(start: time, end: time, interval_minutes: int) -> list[str]:
    interval = max(1, int(interval_minutes or 15))
    start_minute = time_to_minutes(start)
    end_minute = time_to_minutes(end)
    slots = []
    cursor = (start_minute // interval) * interval
    end_floor = (end_minute // interval) * interval
    guard = 0
    while True:
        slots.append(f"{(cursor // 60) % 24:02d}:{cursor % 60:02d}")
        if cursor == end_floor:
            break
        cursor = (cursor + interval) % 1440
        guard += 1
        if guard > 1440 // interval + 2:
            break
    return slots


def prepare_route_file(
    path: str | Path,
    start: time,
    end: time,
    percentile_low: float = 15.0,
    percentile_high: float = 85.0,
    min_length_m: float = 0.0,
) -> PreparedRouteData:
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
    window_mask = valid_timestamp & in_range
    short_length = window_mask & valid_values & (work["length"] < min_length_m) if min_length_m else pd.Series(False, index=work.index)
    processed_df = work[window_mask & valid_values & ~short_length].copy()
    processed_df["speed"] = (processed_df["length"] / processed_df["travel_time"]) * 3.6

    route_data = {}
    for route_name, group in processed_df.groupby("route"):
        speed = group["speed"]
        travel_time = group["travel_time"]
        length = group["length"]
        speed_low = round_float(percentile(speed, percentile_low))
        speed_high = round_float(percentile(speed, percentile_high))
        time_low = round_float(percentile(travel_time, percentile_low))
        time_high = round_float(percentile(travel_time, percentile_high))
        route_data[route_name] = {
            "speed_mean": round_float(speed.mean()),
            "speed_median": round_float(speed.median()),
            "speed_std": round_float(speed.std(ddof=1) if len(speed) > 1 else 0),
            "speed_p_low": speed_low,
            "speed_p_high": speed_high,
            "speed_p15": speed_low,
            "speed_p85": speed_high,
            "time_mean": round_float(travel_time.mean()),
            "time_median": round_float(travel_time.median()),
            "time_p_low": time_low,
            "time_p_high": time_high,
            "time_p15": time_low,
            "time_p85": time_high,
            "length_mean_m": round_float(length.mean()),
            "length_mean_km": round_float(length.mean() / 1000),
            "length_min_m": round_float(length.min()),
            "length_max_m": round_float(length.max()),
            "samples": int(len(group)),
        }

    invalid_value_rows = int((window_mask & ~valid_values).sum())
    invalid_timestamp_rows = int((~valid_timestamp).sum())
    short_length_rows = int(short_length.sum())
    empty_route_rows = int((window_mask & work["route"].eq("")).sum())
    invalid_length_rows = int(
        (window_mask & (work["length"].isna() | (work["length"] <= 0))).sum()
    )
    invalid_time_rows = int(
        (window_mask & (work["travel_time"].isna() | (work["travel_time"] <= 0))).sum()
    )
    stats = {
        "total_rows": int(total_rows),
        "processed_rows": int(len(processed_df)),
        "invalid_rows": int(invalid_timestamp_rows + invalid_value_rows + short_length_rows),
        "out_of_window_rows": int((valid_timestamp & ~in_range).sum()),
        "invalid_timestamp_rows": invalid_timestamp_rows,
        "invalid_value_rows": invalid_value_rows,
        "empty_route_rows": empty_route_rows,
        "invalid_length_rows": invalid_length_rows,
        "invalid_time_rows": invalid_time_rows,
        "short_length_rows": short_length_rows,
        "min_length_m": round_float(min_length_m),
        "processed_pct": _percentage(len(processed_df), total_rows),
        "routes": int(len(route_data)),
    }
    return PreparedRouteData(data=route_data, stats=stats, columns=columns)


def _route_work_frame(path: str | Path, start: time, end: time, min_length_m: float = 0.0) -> tuple[pd.DataFrame, dict[str, str]]:
    df = pd.read_excel(path)
    if df.empty:
        raise ValueError("El archivo esta vacio.")
    columns = detect_route_columns([str(column) for column in df.columns])
    start_minute = time_to_minutes(start)
    end_minute = time_to_minutes(end)
    work = pd.DataFrame(
        {
            "route": df[columns["route"]].astype(str).str.strip(),
            "length": pd.to_numeric(df[columns["length"]], errors="coerce"),
            "travel_time": pd.to_numeric(df[columns["time"]], errors="coerce"),
            "timestamp": df[columns["timestamp"]].map(parse_timestamp),
        }
    )
    work["minute"] = work["timestamp"].map(lambda value: math.nan if value is None else value.hour * 60 + value.minute)
    valid_timestamp = work["minute"].apply(lambda value: not math.isnan(value))
    in_range = work["minute"].apply(lambda value: in_window(value, start_minute, end_minute))
    valid_values = (
        work["route"].ne("")
        & work["length"].notna()
        & work["travel_time"].notna()
        & (work["length"] > 0)
        & (work["travel_time"] > 0)
    )
    if min_length_m:
        valid_values = valid_values & (work["length"] >= min_length_m)
    filtered = work[valid_timestamp & in_range & valid_values].copy()
    filtered["speed"] = (filtered["length"] / filtered["travel_time"]) * 3.6
    return filtered, columns


def build_traffic_dashboard_data(
    before_path: str | Path,
    after_path: str | Path,
    start: time,
    end: time,
    route_names: list[str],
    min_length_m: float = 0.0,
    interval_minutes: int = 15,
) -> dict[str, Any]:
    selected_routes = [route for route in route_names if route]
    if not selected_routes:
        return {}

    frames = []
    for label, path in (("Antes", before_path), ("Despues", after_path)):
        frame, _columns = _route_work_frame(path, start, end, min_length_m)
        frame = frame[frame["route"].isin(selected_routes)].copy()
        if frame.empty:
            continue
        frame["period"] = label
        frame["date"] = frame["timestamp"].map(lambda value: value.strftime("%Y-%m-%d") if value else label)
        frames.append(frame)
    if not frames:
        return {}

    work = pd.concat(frames, ignore_index=True)
    free_flow = work.groupby("route")["speed"].quantile(0.9).to_dict()
    work["free_flow_speed"] = work["route"].map(free_flow).fillna(work["speed"])
    work["queue_km"] = (
        ((work["free_flow_speed"] - work["speed"]).clip(lower=0) / work["free_flow_speed"].replace(0, math.nan))
        * work["length"]
        / 1000
    ).fillna(0)
    work["bucket"] = work["timestamp"].map(lambda value: floor_bucket(value.time(), interval_minutes) if value else "")

    grouped = (
        work.groupby(["route", "period", "date", "bucket"], dropna=False)
        .agg(
            speed=("speed", "mean"),
            queue_km=("queue_km", "mean"),
            samples=("speed", "size"),
            length_km=("length", lambda values: values.mean() / 1000),
        )
        .reset_index()
    )
    grouped = grouped.sort_values(["route", "period", "date", "bucket"])
    points = [
        {
            "route": row.route,
            "period": row.period,
            "date": row.date,
            "bucket": row.bucket,
            "speed": round_float(row.speed, 1),
            "queue_km": round_float(row.queue_km, 2),
            "samples": int(row.samples),
            "length_km": round_float(row.length_km, 3),
        }
        for row in grouped.itertuples()
    ]
    route_summary = []
    for route, group in grouped.groupby("route"):
        route_summary.append(
            {
                "route": route,
                "avg_speed": round_float(group["speed"].mean(), 1),
                "avg_queue_km": round_float(group["queue_km"].mean(), 2),
                "max_queue_km": round_float(group["queue_km"].max(), 2),
                "min_speed": round_float(group["speed"].min(), 1),
                "samples": int(group["samples"].sum()),
            }
        )
    route_summary.sort(key=lambda item: (item["avg_queue_km"], -item["avg_speed"]), reverse=True)

    peak_queue = max(points, key=lambda item: item["queue_km"], default=None)
    min_speed = min(points, key=lambda item: item["speed"], default=None)
    return {
        "interval_minutes": interval_minutes,
        "start": start.strftime("%H:%M"),
        "end": end.strftime("%H:%M"),
        "routes": selected_routes,
        "periods": sorted(work["period"].dropna().unique().tolist()),
        "dates": sorted(work["date"].dropna().unique().tolist()),
        "slots": generate_time_slots(start, end, interval_minutes),
        "points": points,
        "route_summary": route_summary[:20],
        "kpis": {
            "routes": len(selected_routes),
            "points": len(points),
            "avg_speed": round_float(grouped["speed"].mean(), 1),
            "avg_queue_km": round_float(grouped["queue_km"].mean(), 2),
            "max_queue_km": round_float(grouped["queue_km"].max(), 2),
            "records": int(grouped["samples"].sum()),
        },
        "insights": {
            "peak_queue": peak_queue,
            "min_speed": min_speed,
            "free_flow_percentile": 90,
            "queue_formula": "cola_km = max(0, (velocidad_libre_p90 - velocidad) / velocidad_libre_p90) * largo_km",
        },
    }


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
            recommendations.append("Validar cobertura de datos en rutas con caida de muestras antes de tomar decisiones.")
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


def _clamp_percentile_pair(percentile_low: float, percentile_high: float) -> tuple[float, float]:
    low = max(1.0, min(49.0, float(percentile_low or 15.0)))
    high = max(51.0, min(99.0, float(percentile_high or 85.0)))
    if low >= high:
        return 15.0, 85.0
    return low, high


def _analysis_methodology(parameters: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "title": "Lectura de archivos",
            "body": (
                "Se detectan columnas de ruta, largo/distancia, tiempo de viaje y fecha/hora. "
                "Solo entran al calculo las filas dentro de la ventana horaria elegida."
            ),
        },
        {
            "title": "Velocidad",
            "body": "Velocidad km/h = largo en metros / tiempo en segundos * 3.6.",
        },
        {
            "title": "Comparacion",
            "body": "Delta % = (valor despues - valor antes) / valor antes * 100.",
        },
        {
            "title": "Clasificacion",
            "body": (
                f"Una ruta mejora si la velocidad sube mas de {parameters['threshold_pct']}%. "
                f"Empeora si baja mas de {parameters['threshold_pct']}%. Entre ambos limites queda como sin cambio."
            ),
        },
        {
            "title": "Percentiles",
            "body": (
                f"El rango P{parameters['percentile_low']}-P{parameters['percentile_high']} muestra dispersion: "
                "valores extremos quedan fuera para leer la tendencia con menos ruido."
            ),
        },
        {
            "title": "Filas invalidas",
            "body": (
                "Una fila se descarta si no tiene hora valida, queda fuera del horario, no tiene ruta, "
                "tiene largo/tiempo vacio o menor/igual a cero, o no cumple el largo minimo."
            ),
        },
    ]


def _route_analysis_text(row: dict[str, Any], threshold_pct: float) -> str:
    result = row.get("result")
    speed_delta = _number_from_mapping(row, "speed_delta_pct")
    time_delta = _number_from_mapping(row, "time_delta_pct")
    samples_before = int(_number_from_mapping(row, "samples_before"))
    samples_after = int(_number_from_mapping(row, "samples_after"))
    confidence = _confidence_label(samples_before, samples_after).lower()
    if result == "mejoro":
        base = (
            f"La ruta mejora porque la velocidad sube {speed_delta:+.1f}%, "
            f"superando el umbral de {threshold_pct:.1f}%."
        )
    elif result == "empeoro":
        base = (
            f"La ruta empeora porque la velocidad baja {speed_delta:+.1f}%, "
            f"superando el umbral de {threshold_pct:.1f}% en sentido negativo."
        )
    else:
        base = (
            f"La ruta queda sin cambio porque la variacion de velocidad ({speed_delta:+.1f}%) "
            f"queda dentro del rango de tolerancia de +/-{threshold_pct:.1f}%."
        )
    if time_delta > threshold_pct:
        time_note = f" El tiempo de viaje aumenta {time_delta:+.1f}%, lo que refuerza una lectura negativa."
    elif time_delta < -threshold_pct:
        time_note = f" El tiempo de viaje baja {time_delta:+.1f}%, lo que refuerza una lectura favorable."
    else:
        time_note = f" El tiempo de viaje cambia {time_delta:+.1f}%, dentro de una variacion acotada."
    return f"{base}{time_note} Confianza {confidence} con {samples_before}/{samples_after} observaciones."


def compare_route_files(
    before_path: str | Path,
    after_path: str | Path,
    start: time,
    end: time,
    threshold_pct: float = 5.0,
    route_hints_text: str = "",
    percentile_low: float = 15.0,
    percentile_high: float = 85.0,
    min_length_m: float = 0.0,
    min_samples: int = 1,
) -> dict[str, Any]:
    percentile_low, percentile_high = _clamp_percentile_pair(percentile_low, percentile_high)
    min_length_m = max(0.0, float(min_length_m or 0.0))
    min_samples = max(1, int(min_samples or 1))
    threshold_pct = max(0.0, float(threshold_pct or 0.0))
    before = prepare_route_file(before_path, start, end, percentile_low, percentile_high, min_length_m)
    after = prepare_route_file(after_path, start, end, percentile_low, percentile_high, min_length_m)
    common_routes = sorted(set(before.data).intersection(after.data))
    selected_routes, unmatched_hints = match_selected_routes(common_routes, route_hints_text)

    route_rows = []
    excluded_low_sample_routes = []
    for route_name in selected_routes:
        before_row = before.data[route_name]
        after_row = after.data[route_name]
        if min(before_row["samples"], after_row["samples"]) < min_samples:
            excluded_low_sample_routes.append(
                {
                    "route": route_name,
                    "samples_before": before_row["samples"],
                    "samples_after": after_row["samples"],
                }
            )
            continue
        speed_before = before_row["speed_mean"]
        speed_after = after_row["speed_mean"]
        time_before = before_row["time_mean"]
        time_after = after_row["time_mean"]
        speed_delta_pct = ((speed_after - speed_before) / speed_before * 100) if speed_before else 0
        time_delta_pct = ((time_after - time_before) / time_before * 100) if time_before else 0
        route_result = classify_route(speed_delta_pct, threshold_pct)

        row = {
            "route": route_name,
            "speed_before": round_float(speed_before),
            "speed_after": round_float(speed_after),
            "speed_delta_pct": round_float(speed_delta_pct),
            "time_before": round_float(time_before),
            "time_after": round_float(time_after),
            "time_delta_pct": round_float(time_delta_pct),
            "samples_before": before_row["samples"],
            "samples_after": after_row["samples"],
            "sample_delta_pct": round_float(
                ((after_row["samples"] - before_row["samples"]) / before_row["samples"] * 100)
                if before_row["samples"]
                else 0
            ),
            "speed_std_before": before_row["speed_std"],
            "speed_std_after": after_row["speed_std"],
            "speed_p_low_before": before_row["speed_p_low"],
            "speed_p_high_before": before_row["speed_p_high"],
            "speed_p_low_after": after_row["speed_p_low"],
            "speed_p_high_after": after_row["speed_p_high"],
            "speed_p15_before": before_row["speed_p15"],
            "speed_p85_before": before_row["speed_p85"],
            "speed_p15_after": after_row["speed_p15"],
            "speed_p85_after": after_row["speed_p85"],
            "time_p_low_before": before_row["time_p_low"],
            "time_p_high_before": before_row["time_p_high"],
            "time_p_low_after": after_row["time_p_low"],
            "time_p_high_after": after_row["time_p_high"],
            "length_before_m": before_row["length_mean_m"],
            "length_after_m": after_row["length_mean_m"],
            "length_before_km": before_row["length_mean_km"],
            "length_after_km": after_row["length_mean_km"],
            "result": route_result,
            "result_label": result_label(route_result),
            "confidence_label": _confidence_label(before_row["samples"], after_row["samples"]),
        }
        row["analysis_text"] = _route_analysis_text(row, threshold_pct)
        route_rows.append(row)

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

    if excluded_low_sample_routes:
        warnings.append(
            f"Se excluyeron {len(excluded_low_sample_routes)} ruta(s) por tener menos de {min_samples} observaciones."
        )

    parameters = {
        "threshold_pct": round_float(threshold_pct, 1),
        "percentile_low": round_float(percentile_low, 1),
        "percentile_high": round_float(percentile_high, 1),
        "min_length_m": round_float(min_length_m, 1),
        "min_length_km": round_float(min_length_m / 1000, 3),
        "min_samples": min_samples,
        "start": start.strftime("%H:%M"),
        "end": end.strftime("%H:%M"),
        "speed_formula": "velocidad_kmh = largo_m / tiempo_s * 3.6",
        "delta_formula": "delta_pct = (despues - antes) / antes * 100",
        "time_unit": "segundos",
        "speed_unit": "km/h",
    }
    traffic_dashboard = build_traffic_dashboard_data(
        before_path,
        after_path,
        start,
        end,
        [row["route"] for row in route_rows],
        min_length_m=min_length_m,
        interval_minutes=15,
    )

    summary = {
        "result": result,
        "result_label": result_label(result),
        "threshold_pct": round_float(threshold_pct, 1),
        "percentile_low": round_float(percentile_low, 1),
        "percentile_high": round_float(percentile_high, 1),
        "min_length_m": round_float(min_length_m, 1),
        "min_samples": min_samples,
        "start": start.strftime("%H:%M"),
        "end": end.strftime("%H:%M"),
        "routes_before": before.stats["routes"],
        "routes_after": after.stats["routes"],
        "common_routes": len(common_routes),
        "selected_routes": len(selected_routes),
        "routes_analyzed": len(route_rows),
        "excluded_low_sample_routes": excluded_low_sample_routes,
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
        "parameters": parameters,
        "methodology": _analysis_methodology(parameters),
        "quality_explanation": {
            "processed_rows": "Filas usadas efectivamente para calcular velocidades, tiempos y percentiles.",
            "out_of_window_rows": "Filas con hora valida, pero fuera del rango horario elegido.",
            "invalid_timestamp_rows": "Filas donde no se pudo leer una fecha/hora valida.",
            "invalid_value_rows": "Filas dentro del horario, pero sin ruta, largo o tiempo usable.",
            "short_length_rows": "Filas descartadas por estar bajo el largo minimo configurado.",
        },
        "traffic_dashboard": traffic_dashboard,
    }
    summary["conclusion"] = build_conclusion(summary)
    summary["executive_analysis"] = build_executive_analysis(summary, route_rows)
    return {"summary": summary, "route_rows": route_rows}
