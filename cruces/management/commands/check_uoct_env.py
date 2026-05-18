from django.conf import settings
from django.core.management import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = "Revisa configuración básica de UOCT para desarrollo o producción."

    def add_arguments(self, parser):
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Falla si encuentra advertencias relevantes para producción.",
        )

    def handle(self, *args, **options):
        warnings = []
        db_engine = settings.DATABASES["default"]["ENGINE"]
        self.stdout.write(f"DEBUG: {settings.DEBUG}")
        self.stdout.write(f"ALLOWED_HOSTS: {', '.join(settings.ALLOWED_HOSTS) or '(vacío)'}")
        self.stdout.write(f"DB ENGINE: {db_engine}")
        self.stdout.write(f"MEDIA_ROOT: {settings.MEDIA_ROOT}")
        self.stdout.write(f"STATIC_ROOT: {settings.STATIC_ROOT}")
        self.stdout.write(f"MAX UPLOAD MB: {settings.UOCT_MAX_UPLOAD_SIZE // (1024 * 1024)}")

        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        self.stdout.write(self.style.SUCCESS("Conexión a base de datos: OK"))

        if settings.DEBUG:
            warnings.append("DEBUG está activo. En producción debe ser False.")
        if "django-insecure" in settings.SECRET_KEY or len(set(settings.SECRET_KEY)) < 10:
            warnings.append("DJANGO_SECRET_KEY parece de desarrollo. Usa una clave larga y privada.")
        if not settings.ALLOWED_HOSTS:
            warnings.append("ALLOWED_HOSTS está vacío.")
        if db_engine.endswith("sqlite3"):
            warnings.append("SQLite sirve para desarrollo, pero para web multiusuario usa PostgreSQL.")

        if warnings:
            for warning in warnings:
                self.stdout.write(self.style.WARNING(f"Advertencia: {warning}"))
            if options["strict"]:
                raise CommandError("El entorno no cumple las condiciones estrictas.")
        else:
            self.stdout.write(self.style.SUCCESS("Entorno sin advertencias relevantes."))
