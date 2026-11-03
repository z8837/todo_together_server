from unittest.mock import MagicMock, patch

from django.test import RequestFactory, SimpleTestCase

from .health import live, ready


class HealthTests(SimpleTestCase):
    def setUp(self):
        self.request = RequestFactory().get("/health/live/")

    def test_liveness_does_not_access_databases(self):
        with patch("mysite.core.health.connections") as connections:
            response = live(self.request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'{"status": "ok"}')
        self.assertIn("no-store", response["Cache-Control"])
        connections.__iter__.assert_not_called()

    def test_readiness_checks_every_alias_without_writes(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = (1,)
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        with patch("mysite.core.health.connections", {"default": connection, "toto_db": connection}):
            response = ready(self.request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(cursor.execute.call_count, 2)
        cursor.execute.assert_called_with("SELECT 1")

    def test_readiness_failure_does_not_expose_database_details(self):
        connection = MagicMock()
        connection.cursor.side_effect = RuntimeError("private database address and password")
        with patch("mysite.core.health.connections", {"private_alias": connection}):
            response = ready(self.request)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.content, b'{"status": "unavailable"}')

    def test_unexpected_database_result_is_not_ready(self):
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value.fetchone.return_value = (0,)
        with patch("mysite.core.health.connections", {"default": connection}):
            self.assertEqual(ready(self.request).status_code, 503)

    def test_health_routes_accept_only_safe_methods(self):
        for view in (live, ready):
            self.assertEqual(view(RequestFactory().post("/health/live/")).status_code, 405)
