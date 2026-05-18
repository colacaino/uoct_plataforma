import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Crea o actualiza un administrador inicial desde variables de entorno."

    def handle(self, *args, **options):
        username = os.environ.get("DJANGO_SUPERUSER_USERNAME", "").strip()
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "")
        email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "").strip()
        display_name = os.environ.get("DJANGO_SUPERUSER_DISPLAY_NAME", "Administrador").strip()

        if not username or not password:
            self.stdout.write("Admin inicial omitido: faltan DJANGO_SUPERUSER_USERNAME o DJANGO_SUPERUSER_PASSWORD.")
            return

        user_model = get_user_model()
        user, created = user_model.objects.get_or_create(username=username)
        user.email = email
        user.display_name = display_name
        user.role = user_model.Role.ADMIN
        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        user.set_password(password)
        user.save()

        action = "creado" if created else "actualizado"
        self.stdout.write(self.style.SUCCESS(f"Admin inicial {action}: {username}"))
