from datetime import datetime
from pathlib import Path
import shutil
import zipfile

from django.conf import settings
from django.core.management import BaseCommand, call_command


class Command(BaseCommand):
    help = "Crea un respaldo ZIP de datos UOCT: dump JSON, base SQLite si aplica y archivos media."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output-dir",
            default=str(settings.BASE_DIR / "backups"),
            help="Directorio donde se guardará el respaldo.",
        )

    def handle(self, *args, **options):
        output_dir = Path(options["output_dir"]).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        work_dir = output_dir / f"uoct_backup_{timestamp}"
        work_dir.mkdir()

        dump_path = work_dir / "data.json"
        with dump_path.open("w", encoding="utf-8") as dump_file:
            call_command(
                "dumpdata",
                "--natural-foreign",
                "--natural-primary",
                indent=2,
                stdout=dump_file,
            )

        db_settings = settings.DATABASES["default"]
        if db_settings["ENGINE"].endswith("sqlite3"):
            db_path = Path(db_settings["NAME"])
            if db_path.exists():
                shutil.copy2(db_path, work_dir / db_path.name)

        media_root = Path(settings.MEDIA_ROOT)
        if media_root.exists():
            shutil.copytree(media_root, work_dir / "media", dirs_exist_ok=True)

        zip_path = output_dir / f"{work_dir.name}.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in work_dir.rglob("*"):
                archive.write(path, path.relative_to(work_dir))

        shutil.rmtree(work_dir)
        self.stdout.write(self.style.SUCCESS(f"Respaldo creado: {zip_path}"))
