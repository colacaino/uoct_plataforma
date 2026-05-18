from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "admin", "Administrador"
        USUARIO = "usuario", "Usuario"

    role = models.CharField(
        "rol",
        max_length=20,
        choices=Role.choices,
        default=Role.USUARIO,
    )
    display_name = models.CharField("nombre visible", max_length=160, blank=True)

    @property
    def is_admin_role(self):
        return self.is_superuser or self.role == self.Role.ADMIN

    def __str__(self):
        return self.display_name or self.get_full_name() or self.username
