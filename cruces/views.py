from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import FileResponse, Http404, HttpResponse
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_time
from django.views.generic import CreateView, DeleteView, DetailView, ListView, TemplateView, UpdateView
import plotly.graph_objects as go
from plotly.offline import plot

from .analysis_engine import build_executive_analysis, compare_route_files
from .forms import AnalysisCreateForm, BitacoraUploadForm, CruceFileUploadForm, CruceForm, CruceShareUserForm
from .importers import normalize_text, parse_bitacora
from .models import Analysis, Cruce, Project, SharedReport, UploadedFile
from .permissions import can_manage_analysis, can_manage_cruce, is_admin_user, visible_analyses, visible_cruces
from .reporting import build_analysis_excel, build_analysis_pdf


BITACORA_SESSION_KEY = "bitacora_import_preview"
PLOTLY_CONFIG = {"displayModeBar": False, "responsive": True}


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _short_label(value, limit=48):
    text = str(value or "")
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _sample_delta_pct(row):
    before = _safe_float(row.get("samples_before"))
    after = _safe_float(row.get("samples_after"))
    if not before:
        return 0.0
    return ((after - before) / before) * 100


def _plot_div(fig, include_plotlyjs):
    return plot(
        fig,
        output_type="div",
        include_plotlyjs=include_plotlyjs,
        config=PLOTLY_CONFIG,
    )


def build_analysis_chart_items(rows, include_plotlyjs=True):
    prepared_rows = []
    for row in rows:
        prepared_rows.append(
            {
                "route": str(row.get("route") or "Ruta sin nombre"),
                "speed_delta_pct": _safe_float(row.get("speed_delta_pct")),
                "time_delta_pct": _safe_float(row.get("time_delta_pct")),
                "speed_before": _safe_float(row.get("speed_before")),
                "speed_after": _safe_float(row.get("speed_after")),
                "time_before": _safe_float(row.get("time_before")),
                "time_after": _safe_float(row.get("time_after")),
                "samples_before": _safe_float(row.get("samples_before")),
                "samples_after": _safe_float(row.get("samples_after")),
                "sample_delta_pct": _sample_delta_pct(row),
                "result": row.get("result") or "sin_datos",
                "result_label": row.get("result_label") or "Sin datos",
            }
        )
    prepared_rows = [row for row in prepared_rows if row["route"]]
    if not prepared_rows:
        return []

    charts = []
    include_next_js = include_plotlyjs

    def append_chart(slug, title, description, fig):
        nonlocal include_next_js
        charts.append(
            {
                "slug": slug,
                "title": title,
                "description": description,
                "html": _plot_div(fig, include_next_js),
            }
        )
        include_next_js = False

    speed_rows = sorted(prepared_rows, key=lambda row: row["speed_delta_pct"], reverse=True)[:30]
    speed_values = [row["speed_delta_pct"] for row in speed_rows]
    fig_speed = go.Figure(
        data=[
            go.Bar(
                x=[_short_label(row["route"]) for row in speed_rows],
                y=speed_values,
                marker_color=["#15803d" if value >= 0 else "#b91c1c" for value in speed_values],
                customdata=[row["route"] for row in speed_rows],
                hovertemplate="%{customdata}<br>Delta velocidad: %{y:.1f}%<extra></extra>",
            )
        ]
    )
    fig_speed.update_layout(
        height=360,
        margin={"l": 45, "r": 20, "t": 20, "b": 120},
        yaxis_title="Delta velocidad (%)",
        xaxis_tickangle=-35,
        template="plotly_white",
    )
    append_chart(
        "speed_delta",
        "Velocidad por ruta",
        "Top 30 ordenadas por mayor mejora de velocidad.",
        fig_speed,
    )

    time_rows = sorted(prepared_rows, key=lambda row: abs(row["time_delta_pct"]), reverse=True)[:30]
    time_values = [row["time_delta_pct"] for row in time_rows]
    fig_time = go.Figure(
        data=[
            go.Bar(
                x=[_short_label(row["route"]) for row in time_rows],
                y=time_values,
                marker_color=["#b91c1c" if value > 0 else "#15803d" for value in time_values],
                customdata=[row["route"] for row in time_rows],
                hovertemplate="%{customdata}<br>Delta tiempo: %{y:.1f}%<extra></extra>",
            )
        ]
    )
    fig_time.update_layout(
        height=360,
        margin={"l": 45, "r": 20, "t": 20, "b": 120},
        yaxis_title="Delta tiempo (%)",
        xaxis_tickangle=-35,
        template="plotly_white",
    )
    append_chart(
        "time_delta",
        "Tiempo de viaje",
        "Rutas con mayor variacion de tiempo, positiva o negativa.",
        fig_time,
    )

    heat_rows = sorted(
        prepared_rows,
        key=lambda row: abs(row["speed_delta_pct"]) + abs(row["time_delta_pct"]),
        reverse=True,
    )[:22]

    def normalized_column(values, higher_is_better=True):
        if not values:
            return []
        low = min(values)
        high = max(values)
        if high == low:
            return [0 for _ in values]
        normalized = [((value - low) / (high - low)) * 200 - 100 for value in values]
        if not higher_is_better:
            normalized = [-value for value in normalized]
        return [max(-100, min(100, value)) for value in normalized]

    def impact_delta(value, multiplier=2):
        return max(-100, min(100, value * multiplier))

    speed_before_score = normalized_column([row["speed_before"] for row in heat_rows])
    speed_after_score = normalized_column([row["speed_after"] for row in heat_rows])
    time_before_score = normalized_column([row["time_before"] for row in heat_rows], higher_is_better=False)
    time_after_score = normalized_column([row["time_after"] for row in heat_rows], higher_is_better=False)
    samples_before_score = normalized_column([row["samples_before"] for row in heat_rows])
    samples_after_score = normalized_column([row["samples_after"] for row in heat_rows])

    heat_z = []
    for index, row in enumerate(heat_rows):
        heat_z.append(
            [
                speed_before_score[index],
                speed_after_score[index],
                impact_delta(row["speed_delta_pct"]),
                time_before_score[index],
                time_after_score[index],
                impact_delta(-row["time_delta_pct"]),
                samples_before_score[index],
                samples_after_score[index],
            ]
        )
    heat_text = [
        [
            f"{row['speed_before']:.1f}",
            f"{row['speed_after']:.1f}",
            f"{row['speed_delta_pct']:+.1f}%",
            f"{row['time_before']:.0f}s",
            f"{row['time_after']:.0f}s",
            f"{row['time_delta_pct']:+.1f}%",
            f"{row['samples_before']:.0f}",
            f"{row['samples_after']:.0f}",
        ]
        for row in heat_rows
    ]
    fig_heat = go.Figure(
        data=[
            go.Heatmap(
                z=heat_z,
                x=[
                    "Vel. antes",
                    "Vel. despues",
                    "Delta vel.",
                    "Tiempo antes",
                    "Tiempo despues",
                    "Delta tiempo",
                    "Obs. antes",
                    "Obs. despues",
                ],
                y=[_short_label(row["route"], 64) for row in heat_rows],
                text=heat_text,
                customdata=[[row["route"]] * 8 for row in heat_rows],
                colorscale=[[0, "#dc2626"], [0.5, "#f8fafc"], [1, "#16a34a"]],
                zmid=0,
                zmin=-100,
                zmax=100,
                xgap=1,
                ygap=1,
                texttemplate="%{text}",
                textfont={"size": 11, "color": "#111827"},
                colorbar={"title": "Indice", "tickvals": [-100, -50, 0, 50, 100]},
                hovertemplate="%{customdata}<br>%{x}: %{text}<br>Indice: %{z:.0f}<extra></extra>",
            )
        ]
    )
    fig_heat.update_layout(
        height=max(460, min(820, 150 + len(heat_rows) * 30)),
        margin={"l": 220, "r": 20, "t": 20, "b": 70},
        xaxis={"tickangle": 0, "side": "bottom", "showgrid": False},
        yaxis={"autorange": "reversed", "showgrid": False, "tickfont": {"size": 11}},
        template="plotly_white",
    )
    append_chart(
        "impact_heatmap",
        "Mapa de calor de impacto",
        "Color por indice normalizado; el valor real aparece dentro de cada celda.",
        fig_heat,
    )

    result_colors = {
        "mejoro": ("Mejoro", "#15803d"),
        "empeoro": ("Empeoro", "#b91c1c"),
        "sin_cambio": ("Sin cambio", "#64748b"),
        "sin_datos": ("Sin datos", "#94a3b8"),
    }
    fig_scatter = go.Figure()
    scatter_rows = sorted(
        prepared_rows,
        key=lambda row: abs(row["speed_delta_pct"]) + abs(row["time_delta_pct"]),
        reverse=True,
    )[:120]
    for result, (label, color) in result_colors.items():
        group = [row for row in scatter_rows if row["result"] == result]
        if not group:
            continue
        fig_scatter.add_trace(
            go.Scatter(
                x=[row["speed_delta_pct"] for row in group],
                y=[row["time_delta_pct"] for row in group],
                mode="markers",
                name=label,
                text=[row["route"] for row in group],
                customdata=[
                    [int(row["samples_before"]), int(row["samples_after"])]
                    for row in group
                ],
                marker={
                    "size": [
                        max(8, min(30, (row["samples_before"] + row["samples_after"]) / 8))
                        for row in group
                    ],
                    "color": color,
                    "opacity": 0.78,
                    "line": {"width": 1, "color": "#ffffff"},
                },
                hovertemplate=(
                    "%{text}<br>Delta velocidad: %{x:.1f}%"
                    "<br>Delta tiempo: %{y:.1f}%"
                    "<br>Muestras: %{customdata[0]} / %{customdata[1]}<extra></extra>"
                ),
            )
        )
    fig_scatter.add_hline(y=0, line_color="#cbd5e1", line_width=1)
    fig_scatter.add_vline(x=0, line_color="#cbd5e1", line_width=1)
    fig_scatter.update_layout(
        height=420,
        margin={"l": 55, "r": 20, "t": 20, "b": 55},
        xaxis_title="Delta velocidad (%)",
        yaxis_title="Delta tiempo (%)",
        legend_title="Resultado",
        template="plotly_white",
    )
    append_chart(
        "speed_time_scatter",
        "Velocidad vs tiempo",
        "Cada punto es una ruta; el tamano representa la cantidad de observaciones.",
        fig_scatter,
    )

    sample_rows = sorted(
        prepared_rows,
        key=lambda row: row["samples_before"] + row["samples_after"],
        reverse=True,
    )[:20]
    fig_samples = go.Figure(
        data=[
            go.Bar(
                name="Antes",
                x=[_short_label(row["route"]) for row in sample_rows],
                y=[row["samples_before"] for row in sample_rows],
                marker_color="#2563eb",
                customdata=[row["route"] for row in sample_rows],
                hovertemplate="%{customdata}<br>Muestras antes: %{y}<extra></extra>",
            ),
            go.Bar(
                name="Despues",
                x=[_short_label(row["route"]) for row in sample_rows],
                y=[row["samples_after"] for row in sample_rows],
                marker_color="#0f766e",
                customdata=[row["route"] for row in sample_rows],
                hovertemplate="%{customdata}<br>Muestras despues: %{y}<extra></extra>",
            ),
        ]
    )
    fig_samples.update_layout(
        barmode="group",
        height=360,
        margin={"l": 45, "r": 20, "t": 20, "b": 120},
        yaxis_title="Observaciones",
        xaxis_tickangle=-35,
        template="plotly_white",
    )
    append_chart(
        "sample_coverage",
        "Cobertura de muestras",
        "Rutas con mayor volumen comparando antes y despues.",
        fig_samples,
    )

    return charts


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "cruces/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        cruces = visible_cruces(self.request.user)
        analyses = visible_analyses(self.request.user).select_related("cruce")
        files = UploadedFile.objects.select_related("cruce", "uploaded_by")
        if not is_admin_user(self.request.user):
            files = files.filter(Q(cruce__in=cruces) | Q(cruce__isnull=True, uploaded_by=self.request.user)).distinct()
        visible_analysis_ids = analyses.values_list("id", flat=True)
        shared_reports = SharedReport.objects.filter(analysis_id__in=visible_analysis_ids)
        result_counts = {
            choice_value: analyses.filter(result=choice_value).count()
            for choice_value, _ in Analysis.Result.choices
        }
        speed_rows = [
            analysis
            for analysis in analyses
            if isinstance(analysis.summary, dict) and analysis.summary.get("speed_delta_pct") is not None
        ]
        avg_speed_delta = None
        if speed_rows:
            avg_speed_delta = round(
                sum(float(analysis.summary.get("speed_delta_pct", 0)) for analysis in speed_rows) / len(speed_rows),
                2,
            )
        avg_time_delta = None
        time_rows = [
            analysis
            for analysis in analyses
            if isinstance(analysis.summary, dict) and analysis.summary.get("time_delta_pct") is not None
        ]
        if time_rows:
            avg_time_delta = round(
                sum(float(analysis.summary.get("time_delta_pct", 0)) for analysis in time_rows) / len(time_rows),
                2,
            )
        today = timezone.localdate()
        month_start = today.replace(day=1)
        risk_counts = {"alto": 0, "medio": 0, "bajo": 0, "sin_datos": 0}
        total_routes_analyzed = 0
        critical_routes = 0
        top_degradations = []
        for analysis in analyses:
            summary = analysis.summary if isinstance(analysis.summary, dict) else {}
            route_rows = analysis.route_rows if isinstance(analysis.route_rows, list) else []
            total_routes_analyzed += int(summary.get("routes_analyzed") or len(route_rows) or 0)
            executive = summary.get("executive_analysis") or build_executive_analysis(summary, route_rows)
            risk_level = executive.get("risk", {}).get("level", "sin_datos")
            risk_counts[risk_level if risk_level in risk_counts else "sin_datos"] += 1
            critical_routes += int(executive.get("counts", {}).get("critical") or 0)
            for row in route_rows:
                speed_delta = float(row.get("speed_delta_pct") or 0)
                if speed_delta < 0:
                    top_degradations.append(
                        {
                            "route": row.get("route", "Ruta sin nombre"),
                            "cruce": analysis.cruce.interseccion,
                            "analysis_url": analysis.get_absolute_url(),
                            "speed_delta_pct": round(speed_delta, 2),
                            "time_delta_pct": row.get("time_delta_pct", 0),
                        }
                    )
        top_degradations = sorted(top_degradations, key=lambda row: row["speed_delta_pct"])[:8]
        total_cruces = cruces.count()
        context["total_cruces"] = cruces.count()
        context["total_analisis"] = analyses.count()
        context["total_archivos"] = files.count()
        context["total_reportes"] = shared_reports.count()
        context["total_proyectos"] = cruces.exclude(project__isnull=True).values("project_id").distinct().count()
        context["cruces_mes"] = cruces.filter(created_at__date__gte=month_start).count()
        context["analisis_mes"] = analyses.filter(created_at__date__gte=month_start).count()
        context["con_modificacion"] = cruces.filter(realizo_modificacion=True).count()
        context["modificacion_pct"] = round((context["con_modificacion"] / total_cruces) * 100, 1) if total_cruces else 0
        context["avg_speed_delta"] = avg_speed_delta
        context["avg_time_delta"] = avg_time_delta
        context["total_routes_analyzed"] = total_routes_analyzed
        context["critical_routes"] = critical_routes
        context["risk_counts"] = risk_counts
        context["top_degradations"] = top_degradations
        context["ultimos_cruces"] = cruces.select_related("project").order_by("-created_at")[:6]
        context["ultimos_analisis"] = analyses.order_by("-created_at")[:6]
        context["por_estado"] = cruces.values("estado").annotate(total=Count("id")).order_by("estado")
        context["result_counts"] = result_counts
        if analyses.exists():
            labels = [label for _, label in Analysis.Result.choices]
            values = [result_counts[value] for value, _ in Analysis.Result.choices]
            fig_results = go.Figure(
                data=[
                    go.Pie(
                        labels=labels,
                        values=values,
                        hole=0.48,
                        marker_colors=["#16a34a", "#dc2626", "#64748b", "#94a3b8"],
                    )
                ]
            )
            fig_results.update_layout(height=320, margin={"l": 10, "r": 10, "t": 20, "b": 20}, template="plotly_white")
            context["results_chart"] = plot(
                fig_results,
                output_type="div",
                include_plotlyjs=True,
                config={"displayModeBar": False, "responsive": True},
            )

        bar_rows = sorted(speed_rows, key=lambda item: item.created_at)[-12:]
        if bar_rows:
            labels = [item.cruce.interseccion[:26] for item in bar_rows]
            values = [float(item.summary.get("speed_delta_pct", 0)) for item in bar_rows]
            colors = ["#15803d" if value >= 0 else "#b91c1c" for value in values]
            fig_speed = go.Figure(data=[go.Bar(x=labels, y=values, marker_color=colors)])
            fig_speed.update_layout(
                height=320,
                margin={"l": 45, "r": 10, "t": 20, "b": 90},
                yaxis_title="Delta velocidad (%)",
                xaxis_tickangle=-35,
                template="plotly_white",
            )
            context["speed_chart"] = plot(
                fig_speed,
                output_type="div",
                include_plotlyjs=False,
                config={"displayModeBar": False, "responsive": True},
            )

        estado_rows = list(cruces.values("estado").annotate(total=Count("id")).order_by("-total")[:8])
        if estado_rows:
            fig_estado = go.Figure(
                data=[
                    go.Bar(
                        x=[row["estado"] or "Sin estado" for row in estado_rows],
                        y=[row["total"] for row in estado_rows],
                        marker_color="#2563eb",
                    )
                ]
            )
            fig_estado.update_layout(
                height=320,
                margin={"l": 45, "r": 10, "t": 20, "b": 90},
                yaxis_title="Cruces",
                xaxis_tickangle=-25,
                template="plotly_white",
            )
            context["estado_chart"] = plot(
                fig_estado,
                output_type="div",
                include_plotlyjs=not analyses.exists(),
                config={"displayModeBar": False, "responsive": True},
            )

        jornada_rows = list(cruces.values("jornada").annotate(total=Count("id")).order_by("-total"))
        if jornada_rows:
            fig_jornada = go.Figure(
                data=[
                    go.Pie(
                        labels=[row["jornada"] or "Sin jornada" for row in jornada_rows],
                        values=[row["total"] for row in jornada_rows],
                        hole=0.52,
                        marker_colors=["#2563eb", "#0f766e", "#f59e0b", "#64748b", "#94a3b8"],
                    )
                ]
            )
            fig_jornada.update_layout(height=320, margin={"l": 10, "r": 10, "t": 20, "b": 20}, template="plotly_white")
            context["jornada_chart"] = plot(
                fig_jornada,
                output_type="div",
                include_plotlyjs=False,
                config={"displayModeBar": False, "responsive": True},
            )

        if analyses.exists():
            risk_labels = ["Alto", "Medio", "Bajo", "Sin datos"]
            risk_values = [risk_counts["alto"], risk_counts["medio"], risk_counts["bajo"], risk_counts["sin_datos"]]
            fig_risk = go.Figure(
                data=[go.Bar(x=risk_labels, y=risk_values, marker_color=["#dc2626", "#f59e0b", "#16a34a", "#94a3b8"])]
            )
            fig_risk.update_layout(
                height=320,
                margin={"l": 45, "r": 10, "t": 20, "b": 60},
                yaxis_title="Analisis",
                template="plotly_white",
            )
            context["risk_chart"] = plot(
                fig_risk,
                output_type="div",
                include_plotlyjs=False,
                config={"displayModeBar": False, "responsive": True},
            )

        file_counts = {
            value: files.filter(file_type=value).count()
            for value, _ in UploadedFile.FileType.choices
        }
        if any(file_counts.values()):
            fig_files = go.Figure(
                data=[
                    go.Bar(
                        x=[label for value, label in UploadedFile.FileType.choices],
                        y=[file_counts[value] for value, _ in UploadedFile.FileType.choices],
                        marker_color="#0f766e",
                    )
                ]
            )
            fig_files.update_layout(
                height=320,
                margin={"l": 45, "r": 10, "t": 20, "b": 85},
                yaxis_title="Archivos",
                xaxis_tickangle=-25,
                template="plotly_white",
            )
            context["files_chart"] = plot(
                fig_files,
                output_type="div",
                include_plotlyjs=False,
                config={"displayModeBar": False, "responsive": True},
            )
        return context


class CruceListView(LoginRequiredMixin, ListView):
    model = Cruce
    template_name = "cruces/list.html"
    context_object_name = "cruces"
    paginate_by = 20

    def get_queryset(self):
        queryset = (
            visible_cruces(self.request.user)
            .select_related("project", "created_by")
            .annotate(total_analisis=Count("analyses"))
            .order_by("-fecha_terreno", "interseccion")
        )
        query = self.request.GET.get("q", "").strip()
        estado = self.request.GET.get("estado", "").strip()
        jornada = self.request.GET.get("jornada", "").strip()
        if query:
            queryset = queryset.filter(
                Q(interseccion__icontains=query)
                | Q(codigo_j__icontains=query)
                | Q(comuna__icontains=query)
                | Q(project__name__icontains=query)
            )
        if estado:
            queryset = queryset.filter(estado=estado)
        if jornada:
            queryset = queryset.filter(jornada=jornada)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["q"] = self.request.GET.get("q", "")
        context["estado"] = self.request.GET.get("estado", "")
        context["jornada"] = self.request.GET.get("jornada", "")
        context["estado_choices"] = Cruce.EstadoCruce.choices
        context["jornada_choices"] = Cruce.Jornada.choices
        return context


class CruceDetailView(LoginRequiredMixin, DetailView):
    model = Cruce
    template_name = "cruces/detail.html"
    context_object_name = "cruce"

    def get_queryset(self):
        return visible_cruces(self.request.user).select_related("project", "created_by", "updated_by").prefetch_related("analyses", "files")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        files = list(self.object.files.select_related("uploaded_by").order_by("file_type", "-created_at"))
        file_type_labels = dict(UploadedFile.FileType.choices)
        summary = []
        for value, label in UploadedFile.FileType.choices:
            count = sum(1 for item in files if item.file_type == value)
            if count:
                summary.append({"value": value, "label": label, "count": count})
        context["can_manage_cruce"] = can_manage_cruce(self.request.user, self.object)
        context["file_upload_form"] = CruceFileUploadForm()
        context["share_user_form"] = CruceShareUserForm(cruce=self.object)
        context["shared_users"] = self.object.shared_with.order_by("username")
        context["library_files"] = files
        context["file_type_summary"] = summary
        context["file_type_labels"] = file_type_labels
        context["analyses"] = self.object.analyses.select_related("created_by", "file_before", "file_after").order_by("-created_at")
        return context


class CruceCreateView(LoginRequiredMixin, CreateView):
    model = Cruce
    form_class = CruceForm
    template_name = "cruces/form.html"

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        form.instance.updated_by = self.request.user
        messages.success(self.request, "Cruce guardado correctamente.")
        return super().form_valid(form)


class CruceUpdateView(LoginRequiredMixin, UpdateView):
    model = Cruce
    form_class = CruceForm
    template_name = "cruces/form.html"

    def get_queryset(self):
        return visible_cruces(self.request.user)

    def form_valid(self, form):
        if not can_manage_cruce(self.request.user, self.object):
            messages.error(self.request, "No tienes permiso para editar este cruce.")
            return redirect(self.object.get_absolute_url())
        form.instance.updated_by = self.request.user
        messages.success(self.request, "Cruce actualizado correctamente.")
        return super().form_valid(form)


class CruceDeleteView(LoginRequiredMixin, DeleteView):
    model = Cruce
    template_name = "cruces/confirm_delete.html"
    success_url = reverse_lazy("cruces:list")

    def get_queryset(self):
        return visible_cruces(self.request.user)

    def form_valid(self, form):
        if not can_manage_cruce(self.request.user, self.object):
            messages.error(self.request, "No tienes permiso para eliminar este cruce.")
            return redirect(self.object.get_absolute_url())
        messages.success(self.request, "Cruce eliminado correctamente.")
        return super().form_valid(form)


def _can_access_uploaded_file(user, uploaded_file):
    if not user.is_authenticated:
        return False
    if is_admin_user(user):
        return True
    if uploaded_file.cruce_id:
        return visible_cruces(user).filter(pk=uploaded_file.cruce_id).exists()
    return uploaded_file.uploaded_by_id == user.id


def _can_manage_uploaded_file(user, uploaded_file):
    if is_admin_user(user):
        return True
    if uploaded_file.cruce_id:
        return can_manage_cruce(user, uploaded_file.cruce)
    return uploaded_file.uploaded_by_id == user.id


@login_required
def cruce_share_user_view(request, pk):
    cruce = get_object_or_404(visible_cruces(request.user), pk=pk)
    if not can_manage_cruce(request.user, cruce):
        messages.error(request, "No tienes permiso para compartir este cruce.")
        return redirect(cruce.get_absolute_url())
    if request.method != "POST":
        return redirect(cruce.get_absolute_url())

    form = CruceShareUserForm(request.POST, cruce=cruce)
    if form.is_valid():
        user = form.cleaned_data["user"]
        cruce.shared_with.add(user)
        messages.success(request, f"Cruce compartido con {user}.")
    else:
        messages.error(request, "No se pudo compartir el cruce con ese usuario.")
    return redirect(cruce.get_absolute_url() + "#equipo-acceso")


@login_required
def cruce_unshare_user_view(request, pk, user_id):
    cruce = get_object_or_404(visible_cruces(request.user), pk=pk)
    if not can_manage_cruce(request.user, cruce):
        messages.error(request, "No tienes permiso para modificar el equipo de este cruce.")
        return redirect(cruce.get_absolute_url())
    if request.method == "POST":
        cruce.shared_with.remove(user_id)
        messages.success(request, "Usuario removido del equipo del cruce.")
    return redirect(cruce.get_absolute_url() + "#equipo-acceso")


@login_required
def cruce_file_upload_view(request, pk):
    cruce = get_object_or_404(visible_cruces(request.user), pk=pk)
    if not can_manage_cruce(request.user, cruce):
        messages.error(request, "No tienes permiso para cargar archivos en este cruce.")
        return redirect(cruce.get_absolute_url())
    if request.method != "POST":
        return redirect(cruce.get_absolute_url())

    form = CruceFileUploadForm(request.POST, request.FILES)
    if form.is_valid():
        file = form.cleaned_data["file"]
        UploadedFile.objects.create(
            file=file,
            original_name=file.name,
            file_type=form.cleaned_data["file_type"],
            cruce=cruce,
            uploaded_by=request.user,
        )
        messages.success(request, "Archivo agregado a la biblioteca del cruce.")
    else:
        for error in form.errors.values():
            messages.error(request, error.as_text())
    return redirect(cruce.get_absolute_url() + "#biblioteca-archivos")


@login_required
def uploaded_file_download_view(request, pk):
    uploaded_file = get_object_or_404(UploadedFile.objects.select_related("cruce", "uploaded_by"), pk=pk)
    if not _can_access_uploaded_file(request.user, uploaded_file):
        raise Http404("Archivo no disponible.")
    return FileResponse(
        uploaded_file.file.open("rb"),
        as_attachment=True,
        filename=uploaded_file.original_name,
    )


@login_required
def uploaded_file_delete_view(request, pk):
    uploaded_file = get_object_or_404(UploadedFile.objects.select_related("cruce", "uploaded_by"), pk=pk)
    if not _can_manage_uploaded_file(request.user, uploaded_file):
        messages.error(request, "No tienes permiso para eliminar este archivo.")
        if uploaded_file.cruce_id:
            return redirect(uploaded_file.cruce.get_absolute_url())
        return redirect("cruces:dashboard")
    if request.method == "POST":
        cruce = uploaded_file.cruce
        uploaded_file.file.delete(save=False)
        uploaded_file.delete()
        messages.success(request, "Archivo eliminado de la biblioteca.")
        if cruce:
            return redirect(cruce.get_absolute_url() + "#biblioteca-archivos")
    return redirect("cruces:dashboard")


class AnalysisListView(LoginRequiredMixin, ListView):
    model = Analysis
    template_name = "cruces/analysis_list.html"
    context_object_name = "analyses"
    paginate_by = 20

    def get_queryset(self):
        queryset = visible_analyses(self.request.user).select_related("cruce", "created_by").order_by("-created_at")
        query = self.request.GET.get("q", "").strip()
        result = self.request.GET.get("result", "").strip()
        if query:
            queryset = queryset.filter(
                Q(name__icontains=query)
                | Q(cruce__interseccion__icontains=query)
                | Q(cruce__codigo_j__icontains=query)
            )
        if result:
            queryset = queryset.filter(result=result)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["q"] = self.request.GET.get("q", "")
        context["result"] = self.request.GET.get("result", "")
        context["result_choices"] = Analysis.Result.choices
        return context


class AnalysisDetailView(LoginRequiredMixin, DetailView):
    model = Analysis
    template_name = "cruces/analysis_detail.html"
    context_object_name = "analysis"

    def get_queryset(self):
        return visible_analyses(self.request.user).select_related("cruce", "file_before", "file_after", "created_by")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        rows = list(self.object.route_rows or [])
        context["executive_analysis"] = build_executive_analysis(self.object.summary or {}, rows)
        context["analysis_charts"] = build_analysis_chart_items(rows, include_plotlyjs=True)
        chart_rows = []
        if chart_rows:
            labels = [row["route"][:48] for row in chart_rows]
            deltas = [row["speed_delta_pct"] for row in chart_rows]
            colors = ["#15803d" if value >= 0 else "#b91c1c" for value in deltas]
            fig = go.Figure(
                data=[
                    go.Bar(
                        x=labels,
                        y=deltas,
                        marker_color=colors,
                        hovertemplate="%{x}<br>Δ velocidad: %{y:.1f}%<extra></extra>",
                    )
                ]
            )
            fig.update_layout(
                height=360,
                margin={"l": 45, "r": 20, "t": 20, "b": 120},
                yaxis_title="Δ velocidad (%)",
                xaxis_tickangle=-35,
                template="plotly_white",
            )
            context["speed_delta_chart"] = plot(
                fig,
                output_type="div",
                include_plotlyjs=True,
                config={"displayModeBar": False, "responsive": True},
            )
        context["can_manage_analysis"] = can_manage_analysis(self.request.user, self.object)
        if context["can_manage_analysis"]:
            context["active_shares"] = self.object.shared_reports.filter(is_active=True).order_by("-created_at")[:5]
        else:
            context["active_shares"] = []
        return context


@login_required
def analysis_create_view(request):
    selected_cruce = None
    initial = {}
    cruce_id = request.GET.get("cruce") or request.POST.get("cruce")
    if cruce_id:
        selected_cruce = visible_cruces(request.user).filter(pk=cruce_id).first()
    if selected_cruce:
        initial = {
            "cruce": selected_cruce,
            "name": f"Análisis {selected_cruce.interseccion}",
            "horario_inicio": selected_cruce.horario_inicio,
            "horario_fin": selected_cruce.horario_fin,
            "route_hints": selected_cruce.rutas_texto,
        }

    if request.method == "POST":
        form = AnalysisCreateForm(request.POST, request.FILES, initial=initial, user=request.user)
        if form.is_valid():
            cruce = form.cleaned_data["cruce"]
            file_before = form.cleaned_data["file_before"]
            file_after = form.cleaned_data["file_after"]
            before_upload = UploadedFile.objects.create(
                file=file_before,
                original_name=file_before.name,
                file_type=UploadedFile.FileType.RUTAS_ANTES,
                cruce=cruce,
                uploaded_by=request.user,
            )
            after_upload = UploadedFile.objects.create(
                file=file_after,
                original_name=file_after.name,
                file_type=UploadedFile.FileType.RUTAS_DESPUES,
                cruce=cruce,
                uploaded_by=request.user,
            )
            try:
                result = compare_route_files(
                    before_upload.file.path,
                    after_upload.file.path,
                    form.cleaned_data["horario_inicio"],
                    form.cleaned_data["horario_fin"],
                    float(form.cleaned_data["threshold_pct"]),
                    form.cleaned_data.get("route_hints", ""),
                )
            except Exception as exc:
                messages.error(request, f"No se pudo procesar el análisis: {exc}")
                return render(request, "cruces/analysis_form.html", {"form": form, "selected_cruce": cruce})

            analysis = Analysis.objects.create(
                cruce=cruce,
                name=form.cleaned_data["name"] or f"Análisis {cruce.interseccion}",
                horario_inicio=form.cleaned_data["horario_inicio"],
                horario_fin=form.cleaned_data["horario_fin"],
                result=result["summary"]["result"],
                summary=result["summary"],
                route_rows=result["route_rows"],
                file_before=before_upload,
                file_after=after_upload,
                created_by=request.user,
            )
            messages.success(request, "Análisis procesado y guardado correctamente.")
            return redirect(analysis.get_absolute_url())
    else:
        form = AnalysisCreateForm(initial=initial, user=request.user)

    return render(request, "cruces/analysis_form.html", {"form": form, "selected_cruce": selected_cruce})


@login_required
def analysis_export_excel_view(request, pk):
    analysis = get_object_or_404(visible_analyses(request.user).select_related("cruce"), pk=pk)
    content = build_analysis_excel(analysis)
    filename = f"analisis_{analysis.pk}.xlsx"
    response = HttpResponse(
        content,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
def analysis_export_pdf_view(request, pk):
    analysis = get_object_or_404(visible_analyses(request.user).select_related("cruce"), pk=pk)
    content = build_analysis_pdf(analysis)
    filename = f"analisis_{analysis.pk}.pdf"
    response = HttpResponse(content, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
def analysis_share_create_view(request, pk):
    analysis = get_object_or_404(visible_analyses(request.user), pk=pk)
    if not can_manage_analysis(request.user, analysis):
        messages.error(request, "No tienes permiso para compartir este análisis.")
        return redirect(analysis.get_absolute_url())
    if request.method != "POST":
        return redirect(analysis.get_absolute_url())
    days = int(request.POST.get("days") or 30)
    days = max(1, min(days, 365))
    share = SharedReport.objects.create(
        analysis=analysis,
        title=request.POST.get("title") or f"Reporte {analysis.name}",
        expires_at=timezone.now() + timedelta(days=days),
        created_by=request.user,
    )
    messages.success(request, "Enlace compartido creado correctamente.")
    return redirect(analysis.get_absolute_url() + f"#share-{share.pk}")


@login_required
def shared_report_revoke_view(request, pk):
    share = get_object_or_404(SharedReport.objects.select_related("analysis", "analysis__cruce"), pk=pk)
    if not can_manage_analysis(request.user, share.analysis):
        messages.error(request, "No tienes permiso para revocar este enlace.")
        return redirect(share.analysis.get_absolute_url())
    if request.method == "POST":
        share.is_active = False
        share.save(update_fields=["is_active", "updated_at"])
        messages.success(request, "Enlace compartido revocado.")
    return redirect(share.analysis.get_absolute_url())


def shared_report_view(request, token):
    share = get_object_or_404(
        SharedReport.objects.select_related("analysis", "analysis__cruce"),
        token=token,
    )
    if not share.is_available:
        raise Http404("Este reporte compartido no está disponible.")
    return render(
        request,
        "cruces/shared_report.html",
        {
            "share": share,
            "analysis": share.analysis,
            "executive_analysis": build_executive_analysis(share.analysis.summary or {}, share.analysis.route_rows or []),
            "analysis_charts": build_analysis_chart_items(share.analysis.route_rows or [], include_plotlyjs=True),
        },
    )


def _natural_key(row):
    return (
        normalize_text(row.get("interseccion")),
        row.get("fecha_terreno") or "",
        row.get("jornada") or "",
    )


def _annotate_bitacora_rows(rows):
    existing_codes = set(Cruce.objects.exclude(codigo_j="").values_list("codigo_j", flat=True))
    existing_natural = {
        (normalize_text(c.interseccion), c.fecha_terreno.isoformat() if c.fecha_terreno else "", c.jornada or "")
        for c in Cruce.objects.only("interseccion", "fecha_terreno", "jornada")
    }
    seen_codes = set()
    seen_natural = set()
    counters = {
        "total": 0,
        "ready": 0,
        "duplicate": 0,
        "error": 0,
        "warning": 0,
        "duplicate_existing_code": 0,
        "duplicate_file_code": 0,
        "duplicate_existing_natural": 0,
        "duplicate_file_natural": 0,
        "missing_intersection": 0,
    }
    annotated = []

    for source_row in rows:
        row = {**source_row}
        counters["total"] += 1
        code = (row.get("codigo_j") or "").strip()
        key = _natural_key(row)

        if not row.get("interseccion"):
            row["status"] = "error"
            row["status_label"] = "Error"
            row["status_detail"] = "Sin intersección."
            counters["error"] += 1
            counters["missing_intersection"] += 1
        elif code and code in existing_codes:
            row["status"] = "duplicate"
            row["status_label"] = "Duplicado"
            row["status_detail"] = "Ya existe un cruce con ese código J."
            counters["duplicate"] += 1
            counters["duplicate_existing_code"] += 1
        elif code and code in seen_codes:
            row["status"] = "duplicate"
            row["status_label"] = "Duplicado"
            row["status_detail"] = "Código J repetido dentro del archivo."
            counters["duplicate"] += 1
            counters["duplicate_file_code"] += 1
        elif not code and key in existing_natural:
            row["status"] = "duplicate"
            row["status_label"] = "Duplicado"
            row["status_detail"] = "Ya existe un cruce con la misma intersección, fecha y jornada."
            counters["duplicate"] += 1
            counters["duplicate_existing_natural"] += 1
        elif not code and key in seen_natural:
            row["status"] = "duplicate"
            row["status_label"] = "Duplicado"
            row["status_detail"] = "Intersección, fecha y jornada repetidas dentro del archivo."
            counters["duplicate"] += 1
            counters["duplicate_file_natural"] += 1
        else:
            row["status"] = "ready"
            row["status_label"] = "Listo"
            row["status_detail"] = "Se guardará al confirmar."
            counters["ready"] += 1

        if row.get("warnings"):
            counters["warning"] += 1
        if code:
            seen_codes.add(code)
        else:
            seen_natural.add(key)
        annotated.append(row)

    return annotated, counters


def _build_import_diagnostics(parse_result, counters):
    return [
        {
            "label": "Filas escaneadas",
            "value": parse_result.scanned_rows,
            "detail": "Filas revisadas bajo la fila de encabezados.",
        },
        {
            "label": "Filas interpretadas",
            "value": len(parse_result.rows),
            "detail": "Filas con codigo o interseccion detectada.",
        },
        {
            "label": "Vacias omitidas",
            "value": parse_result.skipped_empty_rows,
            "detail": "Filas sin valores en columnas reconocidas.",
        },
        {
            "label": "Sin cruce/codigo",
            "value": parse_result.skipped_no_identity_rows,
            "detail": "Filas con datos, pero sin identificador utilizable.",
        },
        {
            "label": "Duplicadas en sistema",
            "value": counters["duplicate_existing_code"] + counters["duplicate_existing_natural"],
            "detail": "Ya existian por codigo J o por interseccion+fecha+jornada.",
        },
        {
            "label": "Duplicadas en archivo",
            "value": counters["duplicate_file_code"] + counters["duplicate_file_natural"],
            "detail": "Repetidas dentro del mismo Excel.",
        },
    ]


def _create_cruce_from_import_row(row, user):
    project = None
    project_name = (row.get("project_name") or "").strip()
    if project_name:
        project, _ = Project.objects.get_or_create(
            name=project_name,
            defaults={"created_by": user},
        )
    return Cruce.objects.create(
        codigo_j=row.get("codigo_j", ""),
        interseccion=row.get("interseccion", ""),
        comuna=row.get("comuna", ""),
        fecha_terreno=parse_date(row.get("fecha_terreno") or ""),
        evaluador=row.get("evaluador", ""),
        jornada=row.get("jornada", ""),
        horario_inicio=parse_time(row.get("horario_inicio") or ""),
        horario_fin=parse_time(row.get("horario_fin") or ""),
        solicita=row.get("solicita", ""),
        project=project,
        estado=row.get("estado", ""),
        estado_semaforo=row.get("estado_semaforo", ""),
        realizo_modificacion=bool(row.get("realizo_modificacion")),
        modificacion_texto=row.get("modificacion_texto", ""),
        observaciones=row.get("observaciones", ""),
        rutas_texto=row.get("rutas_texto", ""),
        created_by=user,
        updated_by=user,
    )


@login_required
def bitacora_import_view(request):
    preview = request.session.get(BITACORA_SESSION_KEY)

    if request.method == "POST" and request.POST.get("action") == "confirm":
        if not preview:
            messages.error(request, "No hay una vista previa pendiente para guardar.")
            return redirect("cruces:import_bitacora")

        rows, counters = _annotate_bitacora_rows(preview["rows"])
        saved = 0
        with transaction.atomic():
            for row in rows:
                if row["status"] == "ready":
                    _create_cruce_from_import_row(row, request.user)
                    saved += 1

        request.session.pop(BITACORA_SESSION_KEY, None)
        messages.success(
            request,
            f"Importación completada: {saved} cruce(s) guardados, "
            f"{counters['duplicate']} duplicado(s) omitidos y {counters['error']} error(es).",
        )
        return redirect("cruces:list")

    if request.method == "POST":
        form = BitacoraUploadForm(request.POST, request.FILES)
        if form.is_valid():
            file = form.cleaned_data["file"]
            upload = UploadedFile.objects.create(
                file=file,
                original_name=file.name,
                file_type=UploadedFile.FileType.BITACORA,
                uploaded_by=request.user,
            )
            try:
                result = parse_bitacora(upload.file.path)
            except Exception as exc:
                messages.error(request, f"No se pudo leer la bitácora: {exc}")
                return redirect("cruces:import_bitacora")

            rows, counters = _annotate_bitacora_rows(result.rows)
            preview = {
                "file_id": upload.id,
                "file_name": upload.original_name,
                "sheet_name": result.sheet_name,
                "header_row": result.header_row,
                "headers": result.headers,
                "rows": rows,
                "counters": counters,
                "diagnostics": _build_import_diagnostics(result, counters),
            }
            request.session[BITACORA_SESSION_KEY] = preview
            messages.success(request, "Archivo leído correctamente. Revisa la vista previa antes de guardar.")
            return redirect("cruces:import_bitacora")
    else:
        form = BitacoraUploadForm()

    return render(
        request,
        "cruces/import_bitacora.html",
        {
            "form": form,
            "preview": preview,
        },
    )
