import uuid

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField("creado", auto_now_add=True)
    updated_at = models.DateTimeField("actualizado", auto_now=True)

    class Meta:
        abstract = True


class Project(TimeStampedModel):
    name = models.CharField("nombre", max_length=180, unique=True)
    description = models.TextField("descripción", blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_projects",
    )

    class Meta:
        ordering = ["name"]
        verbose_name = "proyecto"
        verbose_name_plural = "proyectos"

    def __str__(self):
        return self.name


class Cruce(TimeStampedModel):
    class Jornada(models.TextChoices):
        PM = "PM", "Punta mañana"
        PT = "PT", "Punta tarde"
        PMD = "PMD", "Mediodía"
        FP = "FP", "Fuera de punta"

    class EstadoSemaforo(models.TextChoices):
        EN_SISTEMA = "En Sistema", "En Sistema"
        AISLADO = "Aislado", "Aislado"

    class EstadoCruce(models.TextChoices):
        REALIZADO = "Realizado", "Realizado"
        PROGRAMADO = "Programado", "Programado"
        EVENTO = "Evento Extraprogramático", "Evento Extraprogramático"
        POR_CONFIRMAR = "Por confirmar", "Por confirmar"

    codigo_j = models.CharField("código J", max_length=40, blank=True)
    interseccion = models.CharField("intersección", max_length=240)
    comuna = models.CharField("comuna", max_length=120, blank=True)
    fecha_terreno = models.DateField("fecha de terreno", null=True, blank=True)
    evaluador = models.CharField("evaluador", max_length=120, blank=True)
    jornada = models.CharField("jornada", max_length=10, choices=Jornada.choices, blank=True)
    horario_inicio = models.TimeField("horario inicio", null=True, blank=True)
    horario_fin = models.TimeField("horario fin", null=True, blank=True)
    solicita = models.CharField("solicita", max_length=180, blank=True)
    project = models.ForeignKey(
        Project,
        verbose_name="proyecto/obra",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cruces",
    )
    estado_semaforo = models.CharField(
        "estado semáforo",
        max_length=40,
        choices=EstadoSemaforo.choices,
        blank=True,
    )
    estado = models.CharField("estado", max_length=80, choices=EstadoCruce.choices, blank=True)
    realizo_modificacion = models.BooleanField("se realizó modificación", default=False)
    modificacion_texto = models.TextField("modificación", blank=True)
    problema = models.TextField("problema/motivo", blank=True)
    observaciones = models.TextField("observaciones", blank=True)
    rutas_texto = models.TextField("rutas asociadas", blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_cruces",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="updated_cruces",
    )
    shared_with = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="shared_cruces",
        verbose_name="compartido con",
    )

    class Meta:
        ordering = ["-fecha_terreno", "interseccion"]
        constraints = [
            models.UniqueConstraint(
                fields=["codigo_j"],
                condition=~models.Q(codigo_j=""),
                name="unique_non_empty_codigo_j",
            )
        ]
        verbose_name = "cruce"
        verbose_name_plural = "cruces"

    def __str__(self):
        if self.codigo_j:
            return f"{self.codigo_j} - {self.interseccion}"
        return self.interseccion

    def get_absolute_url(self):
        return reverse("cruces:detail", kwargs={"pk": self.pk})


class UploadedFile(TimeStampedModel):
    class FileType(models.TextChoices):
        BITACORA = "bitacora", "Bitácora"
        RUTAS_ANTES = "rutas_antes", "Rutas antes"
        RUTAS_DESPUES = "rutas_despues", "Rutas después"
        REPORTE = "reporte", "Reporte"
        OTRO = "otro", "Otro"

    file = models.FileField("archivo", upload_to="uploads/%Y/%m/")
    original_name = models.CharField("nombre original", max_length=255)
    file_type = models.CharField("tipo", max_length=30, choices=FileType.choices, default=FileType.OTRO)
    cruce = models.ForeignKey(Cruce, on_delete=models.SET_NULL, null=True, blank=True, related_name="files")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "archivo"
        verbose_name_plural = "archivos"

    def __str__(self):
        return self.original_name


class Analysis(TimeStampedModel):
    class Result(models.TextChoices):
        MEJORO = "mejoro", "Mejoró"
        EMPEORO = "empeoro", "Empeoró"
        SIN_CAMBIO = "sin_cambio", "Sin cambio"
        SIN_DATOS = "sin_datos", "Sin datos"

    cruce = models.ForeignKey(Cruce, on_delete=models.CASCADE, related_name="analyses")
    name = models.CharField("nombre", max_length=180)
    horario_inicio = models.TimeField("horario inicio", null=True, blank=True)
    horario_fin = models.TimeField("horario fin", null=True, blank=True)
    result = models.CharField("resultado", max_length=20, choices=Result.choices, default=Result.SIN_DATOS)
    summary = models.JSONField("resumen", default=dict, blank=True)
    route_rows = models.JSONField("detalle rutas", default=list, blank=True)
    file_before = models.ForeignKey(
        UploadedFile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="analyses_before",
    )
    file_after = models.ForeignKey(
        UploadedFile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="analyses_after",
    )
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    shared_with = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="shared_analyses",
        verbose_name="compartido con",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "análisis"
        verbose_name_plural = "análisis"

    def __str__(self):
        return f"{self.name} - {self.cruce}"

    def get_absolute_url(self):
        return reverse("cruces:analysis_detail", kwargs={"pk": self.pk})


class SharedReport(TimeStampedModel):
    analysis = models.ForeignKey(Analysis, on_delete=models.CASCADE, related_name="shared_reports")
    token = models.UUIDField("token", default=uuid.uuid4, unique=True, editable=False)
    title = models.CharField("título", max_length=180, blank=True)
    is_active = models.BooleanField("activo", default=True)
    expires_at = models.DateTimeField("expira", null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "reporte compartido"
        verbose_name_plural = "reportes compartidos"

    def __str__(self):
        return self.title or f"Reporte compartido {self.analysis}"

    @property
    def is_available(self):
        if not self.is_active:
            return False
        if self.expires_at and self.expires_at <= timezone.now():
            return False
        return True

    def get_absolute_url(self):
        return reverse("cruces:shared_report", kwargs={"token": self.token})
