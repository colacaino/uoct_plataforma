from datetime import time
from io import StringIO
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse
from openpyxl import Workbook

from config.settings import database_from_url

from .analysis_engine import build_executive_analysis, compare_route_files
from .importers import parse_bitacora
from .models import Analysis, Cruce, SharedReport, UploadedFile
from .reporting import build_analysis_excel, build_analysis_pdf


class BitacoraImporterTests(TestCase):
    def test_parse_realistic_bitacora_headers(self):
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = "Bitácora 2026"
        worksheet.append([])
        worksheet.append(
            [
                "Fecha",
                "Jornada",
                "Horario Inicio",
                "Horario Fin",
                "Solicita",
                "Proyecto/ Obra",
                "Código",
                "Intersección",
                "Comuna",
                "Evaluador",
                "Estado",
                "Estado de semaforo",
                "Se realizo Modificación",
                "Modificación",
                "Observaciones",
                "Polígono",
                "Rutas ",
            ]
        )
        worksheet.append(
            [
                "2026-03-02 00:00:00",
                "PM",
                "07:00- 08:30",
                "",
                "Juan Silva",
                "Nudo Baquedano",
                "J017421",
                "Alameda-Irene Morales",
                "Santiago",
                "CFL- CSV",
                "Realizado",
                "En Sistema",
                "SI",
                "PLAN:1 CL:120 F1:29 F2:78",
                "Cruce observado",
                "Centro",
                "Ruta 1; Ruta 2",
            ]
        )

        temp_file = NamedTemporaryFile(suffix=".xlsx", delete=False)
        temp_path = Path(temp_file.name)
        temp_file.close()
        try:
            workbook.save(temp_path)
            result = parse_bitacora(temp_path)
        finally:
            temp_path.unlink(missing_ok=True)

        self.assertEqual(result.sheet_name, "Bitácora 2026")
        self.assertEqual(result.header_row, 2)
        self.assertEqual(len(result.rows), 1)
        row = result.rows[0]
        self.assertEqual(row["codigo_j"], "J017421")
        self.assertEqual(row["interseccion"], "Alameda-Irene Morales")
        self.assertEqual(row["fecha_terreno"], "2026-03-02")
        self.assertEqual(row["horario_inicio"], "07:00")
        self.assertEqual(row["horario_fin"], "08:30")
        self.assertEqual(row["project_name"], "Nudo Baquedano")
        self.assertEqual(row["estado_semaforo"], "En Sistema")
        self.assertTrue(row["realizo_modificacion"])
        self.assertEqual(row["rutas_texto"], "Ruta 1; Ruta 2")


class RouteAnalysisEngineTests(TestCase):
    def _make_routes_file(self, rows):
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.append(["timestamp", "routeName", "length", "time"])
        for row in rows:
            worksheet.append(row)

        temp_file = NamedTemporaryFile(suffix=".xlsx", delete=False)
        temp_path = Path(temp_file.name)
        temp_file.close()
        workbook.save(temp_path)
        return temp_path

    def test_compare_route_files_classifies_speed_improvement(self):
        before_path = self._make_routes_file(
            [
                ["2026-05-03 12:05:00", "Ruta A", 1000, 200],
                ["2026-05-03 12:15:00", "Ruta A", 1000, 200],
                ["2026-05-03 12:10:00", "Ruta B", 1000, 100],
            ]
        )
        after_path = self._make_routes_file(
            [
                ["2026-05-04 12:05:00", "Ruta A", 1000, 100],
                ["2026-05-04 12:15:00", "Ruta A", 1000, 100],
                ["2026-05-04 12:10:00", "Ruta B", 1000, 100],
            ]
        )
        try:
            result = compare_route_files(
                before_path,
                after_path,
                time(hour=12),
                time(hour=14),
                threshold_pct=5,
                route_hints_text="Ruta A",
            )
        finally:
            before_path.unlink(missing_ok=True)
            after_path.unlink(missing_ok=True)

        self.assertEqual(result["summary"]["result"], "mejoro")
        self.assertEqual(result["summary"]["routes_analyzed"], 1)
        self.assertEqual(result["summary"]["parameters"]["threshold_pct"], 5.0)
        self.assertEqual(result["summary"]["parameters"]["percentile_low"], 15.0)
        self.assertIn("methodology", result["summary"])
        self.assertEqual(result["route_rows"][0]["route"], "Ruta A")
        self.assertEqual(result["route_rows"][0]["speed_delta_pct"], 100.0)
        self.assertIn("analysis_text", result["route_rows"][0])
        executive = result["summary"]["executive_analysis"]
        self.assertEqual(executive["risk"]["level"], "bajo")
        self.assertEqual(executive["counts"]["improved"], 1)
        self.assertIn("Se analizaron", executive["headline"])

    def test_compare_route_files_respects_minimum_samples(self):
        before_path = self._make_routes_file(
            [
                ["2026-05-03 12:05:00", "Ruta A", 1000, 200],
                ["2026-05-03 12:10:00", "Ruta B", 1200, 200],
                ["2026-05-03 12:15:00", "Ruta B", 1200, 200],
            ]
        )
        after_path = self._make_routes_file(
            [
                ["2026-05-04 12:05:00", "Ruta A", 1000, 180],
                ["2026-05-04 12:10:00", "Ruta B", 1200, 180],
                ["2026-05-04 12:15:00", "Ruta B", 1200, 180],
            ]
        )
        try:
            result = compare_route_files(
                before_path,
                after_path,
                time(hour=12),
                time(hour=14),
                min_samples=2,
            )
        finally:
            before_path.unlink(missing_ok=True)
            after_path.unlink(missing_ok=True)

        self.assertEqual(result["summary"]["routes_analyzed"], 1)
        self.assertEqual(result["route_rows"][0]["route"], "Ruta B")
        self.assertEqual(len(result["summary"]["excluded_low_sample_routes"]), 1)

    def test_build_executive_analysis_detects_critical_routes(self):
        summary = {
            "routes_analyzed": 2,
            "threshold_pct": 5,
            "speed_delta_pct": -12,
            "time_delta_pct": 18,
            "counts": {"mejoro": 0, "empeoro": 1, "sin_cambio": 1},
            "before_quality": {"total_rows": 100, "processed_rows": 90},
            "after_quality": {"total_rows": 100, "processed_rows": 85},
        }
        rows = [
            {
                "route": "Ruta critica",
                "samples_before": 40,
                "samples_after": 35,
                "speed_delta_pct": -18,
                "time_delta_pct": 24,
                "result": "empeoro",
                "result_label": "Empeoro",
            },
            {
                "route": "Ruta estable",
                "samples_before": 30,
                "samples_after": 31,
                "speed_delta_pct": 1,
                "time_delta_pct": -1,
                "result": "sin_cambio",
                "result_label": "Sin cambio",
            },
        ]
        executive = build_executive_analysis(summary, rows)
        self.assertEqual(executive["risk"]["level"], "alto")
        self.assertEqual(executive["critical_routes"][0]["route"], "Ruta critica")
        self.assertTrue(executive["recommendations"])


class ReportViewsTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="tester", password="clave-segura")
        self.cruce = Cruce.objects.create(interseccion="Alameda - Prueba", created_by=self.user, updated_by=self.user)
        self.analysis = Analysis.objects.create(
            cruce=self.cruce,
            name="Análisis prueba",
            result="mejoro",
            horario_inicio=time(hour=12),
            horario_fin=time(hour=14),
            summary={
                "result": "mejoro",
                "result_label": "Mejoró",
                "speed_delta_pct": 12.5,
                "time_delta_pct": -8.2,
                "avg_speed_before": 18.0,
                "avg_speed_after": 20.3,
                "routes_analyzed": 1,
                "common_routes": 1,
                "conclusion": "Prueba de reporte.",
                "before_quality": {"total_rows": 2, "processed_rows": 2, "out_of_window_rows": 0, "invalid_rows": 0, "routes": 1},
                "after_quality": {"total_rows": 2, "processed_rows": 2, "out_of_window_rows": 0, "invalid_rows": 0, "routes": 1},
                "warnings": [],
            },
            route_rows=[
                {
                    "route": "Ruta A",
                    "samples_before": 2,
                    "samples_after": 2,
                    "speed_before": 18,
                    "speed_after": 20.3,
                    "speed_delta_pct": 12.5,
                    "time_before": 200,
                    "time_after": 180,
                    "time_delta_pct": -10,
                    "speed_p15_before": 17,
                    "speed_p85_before": 19,
                    "speed_p15_after": 20,
                    "speed_p85_after": 21,
                    "result": "mejoro",
                    "result_label": "Mejoró",
                }
            ],
            created_by=self.user,
        )

    def test_report_builders_return_files(self):
        self.assertGreater(len(build_analysis_excel(self.analysis)), 1000)
        self.assertGreater(len(build_analysis_pdf(self.analysis)), 1000)

    def test_export_and_shared_report_views(self):
        client = Client()
        self.assertTrue(client.login(username="tester", password="clave-segura"))
        excel_response = client.get(reverse("cruces:analysis_export_excel", args=[self.analysis.pk]))
        pdf_response = client.get(reverse("cruces:analysis_export_pdf", args=[self.analysis.pk]))
        self.assertEqual(excel_response.status_code, 200)
        self.assertEqual(pdf_response.status_code, 200)

        share_response = client.post(reverse("cruces:analysis_share_create", args=[self.analysis.pk]), {"days": "7"})
        self.assertEqual(share_response.status_code, 302)
        share = SharedReport.objects.get(analysis=self.analysis)
        public_response = client.get(share.get_absolute_url())
        self.assertEqual(public_response.status_code, 200)
        self.assertContains(public_response, "Análisis prueba")
        self.assertContains(public_response, "Resumen ejecutivo")


class PermissionTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(username="owner", password="clave-segura")
        self.other = user_model.objects.create_user(username="other", password="clave-segura")
        self.admin = user_model.objects.create_user(
            username="adminrole",
            password="clave-segura",
            role="admin",
            is_staff=True,
        )
        self.cruce = Cruce.objects.create(interseccion="Cruce privado", created_by=self.owner, updated_by=self.owner)
        self.analysis = Analysis.objects.create(
            cruce=self.cruce,
            name="Análisis privado",
            result="sin_cambio",
            summary={
                "speed_delta_pct": 0,
                "time_delta_pct": 0,
                "avg_speed_before": 10,
                "avg_speed_after": 10,
                "routes_analyzed": 0,
                "conclusion": "Sin cambios.",
                "before_quality": {"total_rows": 0, "processed_rows": 0, "out_of_window_rows": 0, "invalid_rows": 0, "routes": 0},
                "after_quality": {"total_rows": 0, "processed_rows": 0, "out_of_window_rows": 0, "invalid_rows": 0, "routes": 0},
            },
            created_by=self.owner,
        )

    def test_private_analysis_is_hidden_until_shared(self):
        client = Client()
        self.assertTrue(client.login(username="other", password="clave-segura"))
        response = client.get(self.analysis.get_absolute_url())
        self.assertEqual(response.status_code, 404)

        self.analysis.shared_with.add(self.other)
        response = client.get(self.analysis.get_absolute_url())
        self.assertEqual(response.status_code, 200)

    def test_admin_role_can_see_everything(self):
        client = Client()
        self.assertTrue(client.login(username="adminrole", password="clave-segura"))
        response = client.get(self.analysis.get_absolute_url())
        self.assertEqual(response.status_code, 200)


class CruceFileLibraryTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(username="fileowner", password="clave-segura")
        self.other = user_model.objects.create_user(username="fileother", password="clave-segura")
        self.cruce = Cruce.objects.create(interseccion="Cruce archivo", created_by=self.owner, updated_by=self.owner)

    def test_owner_can_upload_download_and_delete_file(self):
        client = Client()
        self.assertTrue(client.login(username="fileowner", password="clave-segura"))
        upload = SimpleUploadedFile(
            "reporte.pdf",
            b"contenido demo",
            content_type="application/pdf",
        )
        response = client.post(
            reverse("cruces:cruce_file_upload", args=[self.cruce.pk]),
            {"file_type": UploadedFile.FileType.REPORTE, "file": upload},
        )
        self.assertEqual(response.status_code, 302)
        uploaded = UploadedFile.objects.get(cruce=self.cruce)
        detail_response = client.get(self.cruce.get_absolute_url())
        self.assertContains(detail_response, "Biblioteca de archivos")
        self.assertContains(detail_response, "reporte.pdf")

        download_response = client.get(reverse("cruces:uploaded_file_download", args=[uploaded.pk]))
        self.assertEqual(download_response.status_code, 200)
        download_response.close()

        delete_response = client.post(reverse("cruces:uploaded_file_delete", args=[uploaded.pk]))
        self.assertEqual(delete_response.status_code, 302)
        self.assertFalse(UploadedFile.objects.filter(pk=uploaded.pk).exists())

    def test_unshared_user_cannot_download_file(self):
        uploaded = UploadedFile.objects.create(
            file=SimpleUploadedFile("privado.pdf", b"privado", content_type="application/pdf"),
            original_name="privado.pdf",
            file_type=UploadedFile.FileType.REPORTE,
            cruce=self.cruce,
            uploaded_by=self.owner,
        )
        client = Client()
        self.assertTrue(client.login(username="fileother", password="clave-segura"))
        response = client.get(reverse("cruces:uploaded_file_download", args=[uploaded.pk]))
        self.assertEqual(response.status_code, 404)

    def test_shared_user_can_view_cruce_and_download_file(self):
        uploaded = UploadedFile.objects.create(
            file=SimpleUploadedFile("equipo.pdf", b"equipo", content_type="application/pdf"),
            original_name="equipo.pdf",
            file_type=UploadedFile.FileType.REPORTE,
            cruce=self.cruce,
            uploaded_by=self.owner,
        )
        owner_client = Client()
        self.assertTrue(owner_client.login(username="fileowner", password="clave-segura"))
        share_response = owner_client.post(
            reverse("cruces:cruce_share_user", args=[self.cruce.pk]),
            {"user": self.other.pk},
        )
        self.assertEqual(share_response.status_code, 302)

        shared_client = Client()
        self.assertTrue(shared_client.login(username="fileother", password="clave-segura"))
        detail_response = shared_client.get(self.cruce.get_absolute_url())
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, "equipo.pdf")

        download_response = shared_client.get(reverse("cruces:uploaded_file_download", args=[uploaded.pk]))
        self.assertEqual(download_response.status_code, 200)
        download_response.close()


class BackupCommandTests(TestCase):
    def test_backup_command_creates_zip(self):
        with TemporaryDirectory() as temp_dir:
            call_command("backup_uoct", output_dir=temp_dir)
            backups = list(Path(temp_dir).glob("uoct_backup_*.zip"))
        self.assertEqual(len(backups), 1)


class EnvironmentConfigTests(TestCase):
    def test_database_url_parser_supports_postgresql(self):
        config = database_from_url("postgresql://uoct_user:secret@db:5432/uoct")
        self.assertEqual(config["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(config["NAME"], "uoct")
        self.assertEqual(config["USER"], "uoct_user")
        self.assertEqual(config["HOST"], "db")

    def test_check_environment_command_runs(self):
        stdout = StringIO()
        call_command("check_uoct_env", stdout=stdout)
        self.assertIn("Conexión a base de datos: OK", stdout.getvalue())
