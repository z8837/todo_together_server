from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from rest_framework.test import APITestCase

from .models import Project, ProjectMember
from .serializers import ProjectSyncSerializer


class ProjectColorAPITests(APITestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            email="owner@example.test", provider="local", provider_user_id="color-owner",
        )
        self.client.force_authenticate(self.owner)
        self.project = Project.objects.create(
            owner=self.owner, name="Existing project", description="Keep this", color="#1A56FF",
        )
        self.detail_url = f"/toto/projects/{self.project.id}/"
        notifications = patch(
            "mysite.apps.todo_together.views.send_project_updated_notifications"
        )
        notifications.start()
        self.addCleanup(notifications.stop)

    def test_legacy_create_without_color_succeeds(self):
        response = self.client.post("/toto/projects/", {"name": "Old app"}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["color"], "")
        self.assertEqual(Project.objects.get(id=response.data["id"]).color, "")

    def test_create_normalizes_hex_with_or_without_hash(self):
        for color in ("#ab12cd", "ab12cd", "  #Ab12Cd  "):
            with self.subTest(color=color):
                response = self.client.post(
                    "/toto/projects/", {"name": "Colored", "color": color}, format="json",
                )
                self.assertEqual(response.status_code, 201)
                self.assertEqual(response.data["color"], "#AB12CD")
                self.assertEqual(Project.objects.get(id=response.data["id"]).color, "#AB12CD")

    def test_null_or_blank_on_create_means_unset(self):
        for color in (None, "", "   "):
            with self.subTest(color=color):
                response = self.client.post(
                    "/toto/projects/", {"name": "No color", "color": color}, format="json",
                )
                self.assertEqual(response.status_code, 201)
                self.assertEqual(response.data["color"], "")

    def test_patch_color_increments_version_and_is_returned_by_sync(self):
        old_version = self.project.version
        response = self.client.patch(self.detail_url, {"color": "ff9800"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["color"], "#FF9800")
        self.assertEqual(response.data["version"], old_version + 1)
        self.project.refresh_from_db()
        self.assertEqual(self.project.color, "#FF9800")
        self.assertIsNotNone(self.project.recent_update_at)
        sync = self.client.post(
            "/toto/projects/sync/",
            {"projects": [{"id": str(self.project.id), "version": old_version}]},
            format="json",
        )
        self.assertEqual(sync.status_code, 200)
        self.assertEqual(sync.data["projects"][0]["color"], "#FF9800")
        self.assertEqual(sync.data["projects"][0]["version"], old_version + 1)
        unchanged = self.client.post(
            "/toto/projects/sync/",
            {"projects": [{"id": str(self.project.id), "version": old_version + 1}]},
            format="json",
        )
        self.assertEqual(unchanged.data["projects"], [])

    def test_legacy_patch_preserves_existing_color(self):
        response = self.client.patch(self.detail_url, {"name": "Old app edit"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.project.refresh_from_db()
        self.assertEqual(self.project.name, "Old app edit")
        self.assertEqual(self.project.color, "#1A56FF")

    def test_legacy_put_preserves_existing_color(self):
        response = self.client.put(
            self.detail_url, {"name": "Old full edit", "description": "Updated"}, format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.project.refresh_from_db()
        self.assertEqual(self.project.description, "Updated")
        self.assertEqual(self.project.color, "#1A56FF")

    def test_null_or_blank_explicitly_clears_color(self):
        for color in (None, "", "   "):
            with self.subTest(color=color):
                Project.objects.filter(id=self.project.id).update(color="#1A56FF")
                response = self.client.patch(self.detail_url, {"color": color}, format="json")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data["color"], "")
                self.project.refresh_from_db()
                self.assertEqual(self.project.color, "")

    def test_invalid_colors_fail_without_changing_project(self):
        for color in ("red", "#123", "12345", "#12345678", "GG0000", "##123456", 123456, True, {}, []):
            with self.subTest(color=color):
                response = self.client.patch(self.detail_url, {"color": color}, format="json")
                self.assertEqual(response.status_code, 400)
                self.assertIn("color", response.data)
                self.project.refresh_from_db()
                self.assertEqual(self.project.color, "#1A56FF")
                self.assertEqual(self.project.version, 1)

    def test_list_detail_and_sync_include_color_for_colored_and_unset_projects(self):
        empty = Project.objects.create(owner=self.owner, name="Unset")
        response = self.client.get("/toto/projects/")
        self.assertEqual(response.status_code, 200)
        rows = response.data["results"] if isinstance(response.data, dict) else response.data
        colors = {row["id"]: row["color"] for row in rows}
        self.assertEqual(colors[str(self.project.id)], "#1A56FF")
        self.assertEqual(colors[str(empty.id)], "")
        detail = self.client.get(self.detail_url)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["color"], "#1A56FF")
        sync = self.client.post("/toto/projects/sync/", {"projects": []}, format="json")
        self.assertEqual(sync.status_code, 200)
        colors = {row["id"]: row["color"] for row in sync.data["projects"]}
        self.assertEqual(colors[str(empty.id)], "")
        self.assertEqual(ProjectSyncSerializer(self.project).data["color"], "#1A56FF")

    def test_existing_owner_and_manager_permissions_apply_to_color(self):
        for role, expected_status in (("reader", 403), ("writer", 403), ("manager", 200)):
            with self.subTest(role=role):
                user = get_user_model().objects.create_user(
                    email=f"{role}@example.test", provider="local", provider_user_id=f"color-{role}",
                )
                ProjectMember.objects.create(project=self.project, user=user, role=role)
                self.client.force_authenticate(user)
                response = self.client.patch(self.detail_url, {"color": "00ff00"}, format="json")
                self.assertEqual(response.status_code, expected_status)
                self.project.refresh_from_db()
                self.assertEqual(self.project.color, "#00FF00" if expected_status == 200 else "#1A56FF")

    def test_unrelated_user_cannot_read_or_change_color(self):
        user = get_user_model().objects.create_user(
            email="outsider@example.test", provider="local", provider_user_id="color-outsider",
        )
        self.client.force_authenticate(user)
        self.assertEqual(self.client.get(self.detail_url).status_code, 404)
        response = self.client.patch(self.detail_url, {"color": "00ff00"}, format="json")
        self.assertEqual(response.status_code, 404)
        self.project.refresh_from_db()
        self.assertEqual(self.project.color, "#1A56FF")

    def test_unauthenticated_requests_do_not_expose_or_change_color(self):
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.detail_url).status_code, (401, 403))
        response = self.client.patch(self.detail_url, {"color": "00ff00"}, format="json")
        self.assertIn(response.status_code, (401, 403))
        self.project.refresh_from_db()
        self.assertEqual(self.project.color, "#1A56FF")


class ProjectColorMigrationTests(TransactionTestCase):
    migrate_from = [("todo_together", "0034_accountdeletionrequest")]
    migrate_to = [("todo_together", "0035_project_color")]

    def setUp(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        apps = executor.loader.project_state(self.migrate_from).apps
        self.old_project = apps.get_model("todo_together", "Project")
        old_user = apps.get_model("todo_together", "User")
        self.owner = old_user.objects.create(
            email="migration@example.test", provider="local", provider_user_id="color-migration",
        )
        self.existing = self.old_project.objects.create(
            owner_id=self.owner.id, name="Before migration", description="Preserved", version=8,
        )

    def tearDown(self):
        # Restore the schema even if a migration assertion fails.
        MigrationExecutor(connection).migrate(self.migrate_to)
        super().tearDown()

    def migrated_project(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        return executor.loader.project_state(self.migrate_to).apps.get_model("todo_together", "Project")

    def test_existing_rows_keep_data_and_receive_unset_color(self):
        project_model = self.migrated_project()
        project = project_model.objects.get(id=self.existing.id)
        self.assertEqual(project.color, "")
        self.assertEqual(project.name, "Before migration")
        self.assertEqual(project.description, "Preserved")
        self.assertEqual(project.version, 8)
        self.assertEqual(project.owner_id, self.owner.id)

    def test_old_server_writer_can_insert_after_migration(self):
        project_model = self.migrated_project()
        legacy = self.old_project.objects.create(owner_id=self.owner.id, name="Old worker insert")
        self.assertEqual(project_model.objects.get(id=legacy.id).color, "")
