"""Run regression tests in a fresh MySQL database, never the production schema."""

from copy import deepcopy
from uuid import uuid4

from .settings import *  # noqa: F403

# Legacy admin migrations depend on the first todo migration, but the custom
# User model was introduced later. Keep API tests independent of that history.
INSTALLED_APPS = [app for app in INSTALLED_APPS if app != "django.contrib.admin"]  # noqa: F405
ROOT_URLCONF = "mysite.test_urls"
DATABASE_ROUTERS = []

# Historical migrations contain MySQL-specific SQL. Use the same backend, but
# replace both the normal and test database names with a fresh, unique name.
_test_database = "test_toto_color_" + uuid4().hex
_test_connection = deepcopy(DATABASES["toto_db"])  # noqa: F405
_test_connection["NAME"] = _test_database
_test_connection["TEST"] = {"NAME": _test_database, "MIGRATE": False}
DATABASES = {"default": _test_connection}
TEST_RUNNER = "mysite.test_runner.SchemaBaselineTestRunner"
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
CELERY_TASK_ALWAYS_EAGER = True
