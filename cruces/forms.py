from django.conf import settings
from django import forms
from django.contrib.auth import get_user_model

from .models import Cruce, Project, UploadedFile
from .permissions import visible_cruces


def validate_excel_upload(file):
    if not file.name.lower().endswith((".xlsx", ".xlsm")):
        raise forms.ValidationError("Carga un archivo Excel .xlsx o .xlsm.")
    max_size = getattr(settings, "UOCT_MAX_UPLOAD_SIZE", 100 * 1024 * 1024)
    if file.size > max_size:
        max_mb = max_size // (1024 * 1024)
        raise forms.ValidationError(f"El archivo supera el máximo permitido de {max_mb} MB.")
    return file


def validate_library_upload(file):
    allowed_extensions = (".xlsx", ".xlsm", ".csv", ".pdf", ".docx", ".png", ".jpg", ".jpeg", ".zip")
    if not file.name.lower().endswith(allowed_extensions):
        raise forms.ValidationError("Formato no soportado. Usa Excel, CSV, PDF, DOCX, imagen o ZIP.")
    max_size = getattr(settings, "UOCT_MAX_UPLOAD_SIZE", 100 * 1024 * 1024)
    if file.size > max_size:
        max_mb = max_size // (1024 * 1024)
        raise forms.ValidationError(f"El archivo supera el mÃ¡ximo permitido de {max_mb} MB.")
    return file


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ["name", "description"]


class CruceForm(forms.ModelForm):
    project_name = forms.CharField(
        label="Proyecto / Obra",
        required=False,
        help_text="Si no existe, se crea automáticamente.",
    )

    class Meta:
        model = Cruce
        fields = [
            "codigo_j",
            "interseccion",
            "comuna",
            "fecha_terreno",
            "evaluador",
            "jornada",
            "horario_inicio",
            "horario_fin",
            "solicita",
            "estado_semaforo",
            "estado",
            "realizo_modificacion",
            "modificacion_texto",
            "problema",
            "observaciones",
            "rutas_texto",
        ]
        widgets = {
            "fecha_terreno": forms.DateInput(attrs={"type": "date"}),
            "horario_inicio": forms.TimeInput(attrs={"type": "time"}),
            "horario_fin": forms.TimeInput(attrs={"type": "time"}),
            "problema": forms.Textarea(attrs={"rows": 4}),
            "observaciones": forms.Textarea(attrs={"rows": 4}),
            "modificacion_texto": forms.Textarea(attrs={"rows": 3}),
            "rutas_texto": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.project_id:
            self.fields["project_name"].initial = self.instance.project.name
        for field in self.fields.values():
            css = "form-control"
            if isinstance(field.widget, forms.CheckboxInput):
                css = "form-check-input"
            field.widget.attrs.setdefault("class", css)

    def save(self, commit=True):
        instance = super().save(commit=False)
        project_name = self.cleaned_data.get("project_name", "").strip()
        if project_name:
            project, _ = Project.objects.get_or_create(name=project_name)
            instance.project = project
        else:
            instance.project = None
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class BitacoraUploadForm(forms.Form):
    file = forms.FileField(
        label="Archivo bitácora Excel",
        help_text="Formatos soportados: .xlsx y .xlsm.",
        widget=forms.ClearableFileInput(
            attrs={
                "accept": ".xlsx,.xlsm",
                "class": "form-control",
            }
        ),
    )

    def clean_file(self):
        return validate_excel_upload(self.cleaned_data["file"])


class CruceFileUploadForm(forms.Form):
    file = forms.FileField(
        label="Archivo",
        help_text="Excel, CSV, PDF, DOCX, imagen o ZIP.",
        widget=forms.ClearableFileInput(
            attrs={
                "accept": ".xlsx,.xlsm,.csv,.pdf,.docx,.png,.jpg,.jpeg,.zip",
                "class": "form-control",
            }
        ),
    )
    file_type = forms.ChoiceField(
        label="Tipo",
        choices=UploadedFile.FileType.choices,
        initial=UploadedFile.FileType.OTRO,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")

    def clean_file(self):
        return validate_library_upload(self.cleaned_data["file"])


class CruceShareUserForm(forms.Form):
    user = forms.ModelChoiceField(
        label="Usuario",
        queryset=None,
        empty_label="Selecciona un usuario",
    )

    def __init__(self, *args, cruce=None, **kwargs):
        super().__init__(*args, **kwargs)
        user_model = get_user_model()
        queryset = user_model.objects.filter(is_active=True).order_by("username")
        if cruce is not None:
            excluded_ids = [user_id for user_id in [cruce.created_by_id] if user_id]
            excluded_ids.extend(cruce.shared_with.values_list("id", flat=True))
            queryset = queryset.exclude(id__in=excluded_ids)
        self.fields["user"].queryset = queryset
        self.fields["user"].widget.attrs.setdefault("class", "form-control")


class AnalysisCreateForm(forms.Form):
    cruce = forms.ModelChoiceField(
        label="Cruce",
        queryset=Cruce.objects.select_related("project").all(),
        empty_label="Selecciona un cruce",
    )
    name = forms.CharField(label="Nombre del análisis", max_length=180, required=False)
    horario_inicio = forms.TimeField(
        label="Horario inicio",
        widget=forms.TimeInput(attrs={"type": "time"}),
    )
    horario_fin = forms.TimeField(
        label="Horario fin",
        widget=forms.TimeInput(attrs={"type": "time"}),
    )
    threshold_pct = forms.DecimalField(
        label="Umbral de clasificación (%)",
        min_value=0,
        max_value=100,
        decimal_places=1,
        max_digits=5,
        initial=5,
        help_text="Sobre este porcentaje clasifica como mejora o empeoramiento.",
    )
    percentile_low = forms.DecimalField(
        label="Percentil bajo",
        min_value=1,
        max_value=49,
        decimal_places=1,
        max_digits=5,
        initial=15,
        help_text="Define el borde inferior del rango de dispersion mostrado por ruta.",
    )
    percentile_high = forms.DecimalField(
        label="Percentil alto",
        min_value=51,
        max_value=99,
        decimal_places=1,
        max_digits=5,
        initial=85,
        help_text="Define el borde superior del rango de dispersion mostrado por ruta.",
    )
    min_length_m = forms.DecimalField(
        label="Largo minimo de registro (m)",
        min_value=0,
        max_value=100000,
        decimal_places=1,
        max_digits=9,
        initial=0,
        help_text="Filtra registros muy cortos o ruidosos. Usa 0 para no aplicar filtro.",
    )
    min_samples = forms.IntegerField(
        label="Minimo de observaciones por ruta",
        min_value=1,
        max_value=100000,
        initial=1,
        help_text="Rutas con menos muestras que este minimo se excluyen del resultado.",
    )
    file_before = forms.FileField(
        label="Archivo ANTES",
        widget=forms.ClearableFileInput(attrs={"accept": ".xlsx,.xlsm", "class": "form-control"}),
    )
    file_after = forms.FileField(
        label="Archivo DESPUÉS",
        widget=forms.ClearableFileInput(attrs={"accept": ".xlsx,.xlsm", "class": "form-control"}),
    )
    route_hints = forms.CharField(
        label="Rutas del cruce",
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Opcional. Si coincide con nombres de ruta, el análisis se limita a esas rutas; si no, usa todas las comunes.",
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None:
            self.fields["cruce"].queryset = visible_cruces(user).select_related("project")
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")

    def clean_file_before(self):
        return self._clean_excel_file("file_before")

    def clean_file_after(self):
        return self._clean_excel_file("file_after")

    def _clean_excel_file(self, field_name):
        return validate_excel_upload(self.cleaned_data[field_name])

    def clean(self):
        cleaned_data = super().clean()
        low = cleaned_data.get("percentile_low")
        high = cleaned_data.get("percentile_high")
        if low is not None and high is not None and low >= high:
            self.add_error("percentile_high", "El percentil alto debe ser mayor que el percentil bajo.")
        return cleaned_data
