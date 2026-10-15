# Push localization rollout

Device registration accepts an optional `locale`: `ko`, `en`, `ja`, `es`,
`pt-BR`, `fr`, or `de`. Region/underscore variants normalize to the supported
language (`en_US` → `en`, `pt_BR` → `pt-BR`). Unsupported explicit values use
English; null/blank and legacy devices with no locale use Korean. Omitting the
field preserves the device's stored language. Clients should register the actual
resolved application locale after login, language changes, and token refresh.

## Deployment order

1. Back up the database using the deployment's normal procedure. Deploy the
   server release and run `python manage.py migrate todo_together --database=toto_db` from `django/`, using the
   production settings/environment. Migration `0037_device_locale` adds a
   nullable column; existing devices continue to receive Korean.
2. Restart **all Celery workers with the new code before starting the new web
   processes**. New producers enqueue optional `event_key`/`event_args` keyword
   arguments. Old workers cannot consume those new arguments. Pause producers
   during this boundary if a rolling restart cannot guarantee that order.
   Restart the reminder/beat processes using the deployment's normal procedure.
3. Start the new web release, then release the Flutter app that sends `locale`.
   Verify device registration persists the selected language and check an invite
   and reminder on test devices with different languages on the same account.

Old queued generic push tasks keep working with their existing title/body;
they cannot retroactively translate already-rendered Korean text. New tasks
store event names and user-text snapshots, then render against each device's
latest locale when sent. TODO lifecycle tasks also retain their previous
signatures through optional snapshot arguments. Notification history continues
to store its existing Korean message; user-written project names, TODO titles,
and actor names are never translated. Silent synchronization stays data-only.

There is no production deployment workflow configured in this repository;
apply these steps through the existing host/service manager. Do not roll back
workers to old code while new-format tasks remain in the queue. An application
rollback can leave the nullable locale column in place safely.
