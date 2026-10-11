from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from rest_framework.test import APITestCase

from .models import Project, ProjectMember, ProjectUserPreference
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
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
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


class ProjectPersonalColorTests(APITestCase):
    def setUp(self):
        users = get_user_model().objects
        self.owner = users.create_user(email="personal-owner@example.test", provider="local", provider_user_id="personal-owner")
        self.reader = users.create_user(email="personal-reader@example.test", provider="local", provider_user_id="personal-reader")
        self.outsider = users.create_user(email="personal-outsider@example.test", provider="local", provider_user_id="personal-outsider")
        self.project = Project.objects.create(owner=self.owner, name="Shared", color="#009688")
        self.membership = ProjectMember.objects.create(project=self.project, user=self.reader, role="reader")
        self.detail_url = f"/toto/projects/{self.project.id}/"
        self.settings_url = self.detail_url + "my-settings/"
        self.client.force_authenticate(self.owner)
        self.notifications = patch("mysite.apps.todo_together.views.send_project_updated_notifications")
        self.notify = self.notifications.start()
        self.addCleanup(self.notifications.stop)

    def sync(self, version=None):
        body = {"projects": [{"id": str(self.project.id), "version": self.project.version}]}
        if version is not None:
            body["preferences"] = [{"project": str(self.project.id), "version": version}]
        response = self.client.post("/toto/projects/sync/", body, format="json")
        self.assertEqual(response.status_code, 200)
        return response.data

    def test_get_defaults_without_creating_private_rows(self):
        response = self.client.get(self.settings_url)
        self.assertEqual(response.data["version"], 0)
        self.assertEqual(response.data["color_override"], "")
        self.assertFalse(ProjectUserPreference.objects.exists())
        detail = self.client.get(self.detail_url)
        self.assertEqual(detail.data["my_color"], "")
        self.assertEqual(detail.data["display_color"], "#009688")

    def test_owner_and_reader_can_choose_independent_colors_without_shared_changes(self):
        for user, color in ((self.owner, "1a56ff"), (self.reader, "#AB12CD")):
            self.client.force_authenticate(user)
            response = self.client.patch(self.settings_url, {"color_override": color, "version": 0}, format="json")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data["version"], 1)
        self.project.refresh_from_db()
        self.assertEqual(self.project.version, 1)
        self.assertEqual(self.project.color, "#009688")
        self.notify.assert_not_called()
        for user, color in ((self.owner, "#1A56FF"), (self.reader, "#AB12CD")):
            self.client.force_authenticate(user)
            detail = self.client.get(self.detail_url)
            self.assertEqual(detail.data["my_color"], color)
            self.assertEqual(detail.data["display_color"], color)
            response = self.client.get("/toto/projects/")
            rows = response.data["results"] if isinstance(response.data, dict) else response.data
            self.assertEqual(rows[0]["display_color"], color)

    def test_private_sync_is_independent_and_never_returns_another_users_color(self):
        self.client.patch(self.settings_url, {"color_override": "1a56ff"}, format="json")
        data = self.sync(0)
        self.assertEqual(data["projects"], [])
        self.assertEqual(data["preferences"][0]["color_override"], "#1A56FF")
        self.assertEqual(self.sync(1)["preferences"], [])
        self.client.force_authenticate(self.reader)
        self.assertEqual(self.sync()["preferences"], [])
        self.client.patch(self.settings_url, {"color_override": "9c27b0"}, format="json")
        self.assertEqual(self.sync()["preferences"][0]["color_override"], "#9C27B0")

    def test_reset_keeps_version_so_other_devices_receive_default_again(self):
        self.client.patch(self.settings_url, {"color_override": "1a56ff"}, format="json")
        reset = self.client.put(self.settings_url, {"color_override": None, "version": 1}, format="json")
        self.assertEqual(reset.status_code, 200)
        self.assertEqual(reset.data["version"], 2)
        self.assertEqual(reset.data["color_override"], "")
        self.assertEqual(self.sync(1)["preferences"][0]["color_override"], "")
        self.assertEqual(self.client.get(self.detail_url).data["display_color"], "#009688")

    def test_default_changes_only_affect_users_who_follow_default(self):
        self.client.force_authenticate(self.reader)
        self.client.patch(self.settings_url, {"color_override": "1a56ff"}, format="json")
        self.client.force_authenticate(self.owner)
        self.client.patch(self.detail_url, {"color": "ff5722"}, format="json")
        self.assertEqual(self.client.get(self.detail_url).data["display_color"], "#FF5722")
        self.client.force_authenticate(self.reader)
        self.assertEqual(self.client.get(self.detail_url).data["display_color"], "#1A56FF")

    def test_stale_version_conflicts_and_repeated_save_is_idempotent(self):
        first = self.client.patch(self.settings_url, {"color_override": "1a56ff", "version": 0}, format="json")
        stale = self.client.patch(self.settings_url, {"color_override": "ff5722", "version": 0}, format="json")
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.data["preference"]["color_override"], "#1A56FF")
        same = self.client.patch(self.settings_url, {"color_override": "#1A56FF", "version": first.data["version"]}, format="json")
        self.assertEqual(same.data["version"], 1)
        self.assertEqual(ProjectUserPreference.objects.count(), 1)

    def test_invalid_or_missing_color_does_not_write_preferences(self):
        for color in ("#123", "#12345678", "GG0000", 123456, True):
            with self.subTest(color=color):
                response = self.client.patch(self.settings_url, {"color_override": color}, format="json")
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.patch(self.settings_url, {}, format="json").status_code, 400)
        self.assertFalse(ProjectUserPreference.objects.exists())

    def test_authenticated_user_cannot_edit_another_users_preference(self):
        self.client.force_authenticate(self.reader)
        self.client.patch(self.settings_url, {"color_override": "1a56ff", "user": self.owner.id}, format="json")
        self.assertTrue(ProjectUserPreference.objects.filter(user=self.reader).exists())
        self.assertFalse(ProjectUserPreference.objects.filter(user=self.owner).exists())

    def test_outsider_former_member_and_deleted_project_have_no_access(self):
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get(self.settings_url).status_code, 404)
        self.assertEqual(self.client.patch(self.settings_url, {"color_override": "1a56ff"}, format="json").status_code, 404)
        self.client.force_authenticate(self.reader)
        self.client.patch(self.settings_url, {"color_override": "1a56ff"}, format="json")
        self.membership.soft_delete()
        self.assertEqual(self.client.get(self.settings_url).status_code, 404)
        self.assertEqual(self.sync()["preferences"], [])
        self.client.force_authenticate(self.owner)
        self.project.soft_delete()
        self.assertEqual(self.client.get(self.settings_url).status_code, 404)
        self.assertEqual(self.sync()["preferences"], [])

    def test_unauthenticated_request_cannot_read_or_write_personal_color(self):
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.settings_url).status_code, (401, 403))
        self.assertIn(self.client.patch(self.settings_url, {"color_override": "1a56ff"}, format="json").status_code, (401, 403))

    def test_unset_project_has_valid_deterministic_fallback(self):
        self.project.color = ""
        self.project.save(update_fields=["color"])
        first = self.client.get(self.detail_url).data["display_color"]
        self.assertRegex(first, r"^#[0-9A-F]{6}$")
        self.assertEqual(first, self.client.get(self.detail_url).data["display_color"])
