from django.contrib import admin

from .models import Analysis, Cruce, Project, SharedReport, UploadedFile


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "created_by", "created_at")
    search_fields = ("name", "description")


class AnalysisInline(admin.TabularInline):
    model = Analysis
    extra = 0
    fields = ("name", "result", "horario_inicio", "horario_fin", "created_at")
    readonly_fields = ("created_at",)


@admin.register(Cruce)
class CruceAdmin(admin.ModelAdmin):
    list_display = (
        "interseccion",
        "codigo_j",
        "fecha_terreno",
        "jornada",
        "evaluador",
        "estado",
        "estado_semaforo",
        "realizo_modificacion",
    )
    list_filter = ("jornada", "estado", "estado_semaforo", "realizo_modificacion", "fecha_terreno")
    search_fields = ("interseccion", "codigo_j", "comuna", "evaluador", "project__name")
    autocomplete_fields = ("project", "created_by", "updated_by")
    filter_horizontal = ("shared_with",)
    inlines = [AnalysisInline]


@admin.register(UploadedFile)
class UploadedFileAdmin(admin.ModelAdmin):
    list_display = ("original_name", "file_type", "cruce", "uploaded_by", "created_at")
    list_filter = ("file_type", "created_at")
    search_fields = ("original_name", "cruce__interseccion", "cruce__codigo_j")
    autocomplete_fields = ("cruce", "uploaded_by")


@admin.register(Analysis)
class AnalysisAdmin(admin.ModelAdmin):
    list_display = ("name", "cruce", "result", "horario_inicio", "horario_fin", "created_by", "created_at")
    list_filter = ("result", "created_at")
    search_fields = ("name", "cruce__interseccion", "cruce__codigo_j")
    autocomplete_fields = ("cruce", "file_before", "file_after", "created_by")
    filter_horizontal = ("shared_with",)


@admin.register(SharedReport)
class SharedReportAdmin(admin.ModelAdmin):
    list_display = ("title", "analysis", "is_active", "expires_at", "created_by", "created_at")
    list_filter = ("is_active", "expires_at", "created_at")
    search_fields = ("title", "analysis__name", "analysis__cruce__interseccion")
    readonly_fields = ("token", "created_at", "updated_at")
    autocomplete_fields = ("analysis", "created_by")

# Register your models here.
