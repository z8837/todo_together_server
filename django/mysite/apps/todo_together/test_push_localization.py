from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import SimpleTestCase, TransactionTestCase
from rest_framework.test import APITestCase

from .models import Device, NotificationPreference
from .push_localization import normalize_push_locale, render_push_message
from .services import send_push_to_user
from .tasks import push_to_user_task, push_to_users_task


class PushCatalogTests(SimpleTestCase):
    def test_locale_normalization_and_legacy_fallback(self):
        for value, expected in ((None, 'ko'), ('', 'ko'), ('  ', 'ko'),
                                ('ko-KR', 'ko'), ('EN_us', 'en'), ('ja-JP', 'ja'),
                                ('zh-Hans-CN', 'en'), ('pt_BR', 'pt-BR'), ('xx', 'en')):
            with self.subTest(value=value):
                self.assertEqual(normalize_push_locale(value), expected)

    def test_every_event_has_complete_templates_and_preserves_user_text(self):
        args = {'project': 'Project {literal} 日本語', 'actor': 'Alice {name}',
                'todo': 'Task {literal_task} 한국어'}
        for event in ('project_invite', 'project_updated', 'project_destroy',
                      'todo_created', 'todo_updated', 'todo_destroy',
                      'reminder_todo', 'reminder_schedule'):
            rendered = []
            for locale in ('ko', 'en', 'ja', 'pt-BR', 'es', 'fr', 'de'):
                with self.subTest(event=event, locale=locale):
                    title, body = render_push_message(event, args, locale)
                    self.assertTrue(title)
                    self.assertTrue(body)
                    for marker in ('{project}', '{actor}', '{todo}'):
                        self.assertNotIn(marker, title + body)
                    self.assertIn(args['project'] if event.startswith('project_') else args['todo'], title + body)
                    rendered.append((title, body))
            self.assertEqual(len(set(rendered)), 7)


class PushDeviceLocaleTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email='push@example.test', provider='local', provider_user_id='push-test')
        self.client.force_authenticate(self.user)
        init = patch('mysite.apps.todo_together.services.get_firebase_app')
        init.start()
        self.addCleanup(init.stop)
        sender = patch('mysite.apps.todo_together.services.messaging.send_each_for_multicast')
        self.sender = sender.start()
        self.sender.side_effect = lambda message: SimpleNamespace(responses=[
            SimpleNamespace(success=True, exception=None) for _ in message.tokens])
        self.addCleanup(sender.stop)

    def device(self, token, locale):
        return Device.objects.create(user=self.user, fcm_token=token, locale=locale)

    def send(self, **kwargs):
        send_push_to_user(self.user, 'legacy title', 'legacy body',
                          {'type': 'todo_created', 'id': 123},
                          event_key='todo_created',
                          event_args={'project': 'Work', 'actor': 'Alice', 'todo': 'Ship'},
                          **kwargs)

    def test_register_create_update_and_legacy_omission(self):
        url = '/toto/devices/register/'
        response = self.client.post(url, {'fcm_token': 'registered', 'platform': 'android', 'locale': 'ja-JP'}, format='json')
        self.assertEqual(response.status_code, 201)
        device = Device.objects.get(fcm_token='registered')
        self.assertEqual(device.locale, 'ja')
        for extra, expected in (({}, 'ja'), ({'locale': 'zh'}, 'en'), ({'locale': None}, 'ko')):
            response = self.client.post(url, {'fcm_token': device.fcm_token, 'platform': 'ios', **extra}, format='json')
            self.assertEqual(response.status_code, 200)
            device.refresh_from_db()
            self.assertEqual(device.locale, expected)
        response = self.client.post(url, {'fcm_token': 'old-client', 'platform': 'android'}, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertIn(Device.objects.get(fcm_token='old-client').locale, (None, 'ko'))

    def test_mixed_devices_and_legacy_device_get_distinct_localized_messages(self):
        for token, locale in (('english', 'en'), ('japanese', 'ja'), ('legacy', None)):
            self.device(token, locale)
        self.send()
        messages = {call.args[0].tokens[0]: call.args[0] for call in self.sender.call_args_list}
        self.assertEqual(set(messages), {'english', 'japanese', 'legacy'})
        self.assertEqual(len({m.notification.body for m in messages.values()}), 3)
        self.assertEqual(messages['legacy'].notification.body, render_push_message(
            'todo_created', {'project': 'Work', 'actor': 'Alice', 'todo': 'Ship'}, 'ko')[1])
        self.assertTrue(all(m.data == {'type': 'todo_created', 'id': '123'} for m in messages.values()))

    def test_public_id_token_rotation_preserves_then_changes_locale(self):
        device = self.device('before', 'ja')
        for token, extra, expected in (('rotated', {}, 'ja'), ('rotated-again', {'locale': 'pt-br'}, 'pt-BR')):
            response = self.client.post('/toto/devices/register/', {
                'public_id': str(device.public_id), 'fcm_token': token,
                'platform': 'ios', **extra}, format='json')
            self.assertEqual(response.status_code, 200)
            device.refresh_from_db()
            self.assertEqual(device.fcm_token, token)
            self.assertEqual(device.locale, expected)
        self.assertEqual(self.user.devices.count(), 1)

    def test_reminder_notification_apns_and_data_share_device_locale(self):
        self.device('japanese', 'ja')
        args = {'project': 'Work', 'todo': 'Ship'}
        send_push_to_user(self.user, 'old', 'old', {
            'type': 'todo_reminder', 'title': 'old', 'body': 'old', 'message': 'old',
            'todoId': '123'}, event_key='reminder_schedule', event_args=args)
        message = self.sender.call_args.args[0]
        title, body = render_push_message('reminder_schedule', args, 'ja')
        self.assertEqual((message.notification.title, message.notification.body), (title, body))
        alert = message.apns.payload.aps.alert
        self.assertEqual((alert.title, alert.body), (title, body))
        self.assertEqual(message.android.priority, 'high')
        self.assertEqual(message.data, {'type': 'todo_reminder', 'title': title,
                                      'body': body, 'message': body, 'todoId': '123'})

    def test_locale_batches_respect_firebase_limit(self):
        Device.objects.bulk_create([Device(user=self.user, fcm_token=f'batch-{i}', locale='en') for i in range(501)])
        self.device('japanese', 'ja')
        self.send()
        messages = [call.args[0] for call in self.sender.call_args_list]
        self.assertEqual(sorted(len(m.tokens) for m in messages), [1, 1, 500])
        self.assertEqual(len({token for m in messages for token in m.tokens}), 502)

    def test_data_only_preferences_and_force_flag(self):
        self.device('english', 'en')
        NotificationPreference.objects.create(user=self.user, todo_activity_enabled=False)
        self.send()
        self.assertIsNone(self.sender.call_args.args[0].notification)
        self.sender.reset_mock()
        self.send(force_data_only=True)
        self.assertIsNone(self.sender.call_args.args[0].notification)

    def test_disabled_reminders_are_not_delivered(self):
        self.device('english', 'en')
        NotificationPreference.objects.create(user=self.user, todo_reminder_enabled=False)
        send_push_to_user(self.user, 'title', 'body', {'type': 'todo_reminder'},
                          event_key='reminder_todo', event_args={'todo': 'Ship'})
        self.sender.assert_not_called()

    def test_queued_task_resolves_device_locale_at_delivery_and_accepts_old_signature(self):
        device = self.device('queued', 'ko')
        task_args = (self.user.id, 'old title', 'old body', {'type': 'todo_created'})
        device.locale = 'ja'
        device.save(update_fields=['locale'])
        push_to_user_task.run(*task_args, event_key='todo_created',
                              event_args={'project': 'Work', 'actor': 'Alice', 'todo': 'Ship'})
        self.assertEqual(self.sender.call_args.args[0].notification.body,
                         render_push_message('todo_created', {'project': 'Work', 'actor': 'Alice', 'todo': 'Ship'}, 'ja')[1])
        for task, identity in ((push_to_user_task, self.user.id), (push_to_users_task, [self.user.id])):
            task.run(identity, 'old title', 'old body', {'type': 'old'}, False)
            self.assertEqual(self.sender.call_args.args[0].notification.body, 'old body')

    def test_failed_token_is_mapped_to_its_own_locale_group(self):
        devices = [self.device('en-ok', 'en'), self.device('ja-bad', 'ja'), self.device('ja-ok', 'ja')]
        def result(message):
            return SimpleNamespace(responses=[SimpleNamespace(
                success=token != 'ja-bad',
                exception=SimpleNamespace(code='registration-token-not-registered') if token == 'ja-bad' else None,
            ) for token in message.tokens])
        self.sender.side_effect = result
        self.send()
        for device in devices:
            device.refresh_from_db()
            self.assertEqual(device.is_active, device.fcm_token != 'ja-bad')

    def test_transient_failure_does_not_disable_device(self):
        device = self.device('temporary', 'en')
        self.sender.side_effect = lambda message: SimpleNamespace(responses=[
            SimpleNamespace(success=False, exception=SimpleNamespace(code='unavailable'))])
        self.send()
        device.refresh_from_db()
        self.assertTrue(device.is_active)

    def test_token_rotation_during_delivery_does_not_disable_new_token(self):
        device = self.device('old-token', 'en')
        def rotate(message):
            Device.objects.filter(pk=device.pk).update(fcm_token='refreshed-token')
            return SimpleNamespace(responses=[SimpleNamespace(
                success=False, exception=SimpleNamespace(code='unregistered'))])
        self.sender.side_effect = rotate
        self.send()
        device.refresh_from_db()
        self.assertTrue(device.is_active)
        self.assertEqual(device.fcm_token, 'refreshed-token')


class DeviceLocaleMigrationTests(TransactionTestCase):
    migrate_from = [('todo_together', '0036_project_user_preference')]
    migrate_to = [('todo_together', '0037_device_locale')]

    def test_existing_devices_and_old_worker_insert_remain_compatible(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        self.addCleanup(self.restore_schema)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        old_user = old_apps.get_model('todo_together', 'User')
        old_device = old_apps.get_model('todo_together', 'Device')
        user = old_user.objects.create(email='migration-push@example.test', provider='local', provider_user_id='migration-push')
        existing = old_device.objects.create(user_id=user.id, fcm_token='existing')
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        # The old model omits locale from INSERT, like a worker during rollout.
        old_device.objects.create(user_id=user.id, fcm_token='old-worker')
        self.assertIsNone(Device.objects.get(pk=existing.pk).locale)
        self.assertIsNone(Device.objects.get(fcm_token='old-worker').locale)

    def restore_schema(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
