from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase


class EnsureDefaultAdminCommandTests(TestCase):
    def test_command_creates_admin_from_environment(self):
        env = {
            "DJANGO_SUPERUSER_USERNAME": "admin_test",
            "DJANGO_SUPERUSER_PASSWORD": "Admin.Test.2026!",
            "DJANGO_SUPERUSER_EMAIL": "admin@test.local",
            "DJANGO_SUPERUSER_DISPLAY_NAME": "Admin Test",
        }
        with patch.dict("os.environ", env):
            stdout = StringIO()
            call_command("ensure_default_admin", stdout=stdout)

        user = get_user_model().objects.get(username="admin_test")
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.is_staff)
        self.assertEqual(user.role, user.Role.ADMIN)
        self.assertTrue(user.check_password("Admin.Test.2026!"))
