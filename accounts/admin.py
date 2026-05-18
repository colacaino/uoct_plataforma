from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class UoctUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ("UOCT", {"fields": ("role", "display_name")}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("UOCT", {"fields": ("role", "display_name")}),
    )
    list_display = ("username", "email", "display_name", "role", "is_staff", "is_active")
    list_filter = ("role", "is_staff", "is_superuser", "is_active")

# Register your models here.
