# Server health and recovery

`/health/live/` checks Django request handling without authentication or a database
query. `/health/ready/` runs `SELECT 1` against each configured database. Responses
contain only a status, disable caching, and accept GET/HEAD. A failed readiness
check returns 503 without exposing driver exceptions or connection settings.

`django/gunicorn.conf.py` enables Python fault tracebacks before worker fork and
recycles each worker after 2,000–2,200 requests. Gunicorn automatically loads it
from the existing `/home/tokeii/django` working directory. The current worker,
thread, and long-running firstapp request settings are preserved.

The independent, standard-library-only watchdog checks every 30 seconds:

- Two consecutive Unix-socket liveness failures replace the **main** Gunicorn
  process, rather than repeatedly forking from the same possibly corrupt master.
- Six native worker crashes within 65 seconds also trigger replacement, even
  when one remaining worker can still answer HTTP requests.
- Database readiness, loopback HTTPS with hostname/certificate verification,
  certificate expiry under 14 days, and low disk space are checked separately.
  Database or certificate failures do not cause a healthy Gunicorn to be killed.
- A new main process has a 45-second startup grace period. Recovery has a
  two-minute cooldown and a maximum of three attempts per 15 minutes. The monitor
  does not restart a service deliberately stopped with `systemctl stop`.
- The process identity and owner are checked again immediately before recovery.
  Old-master crash logs cannot trigger recovery of a replacement master.
- State is written atomically and logs rotate at 1 MiB, retaining five backups.

## Installation

Deploy the health views, URL registration, Gunicorn config, and `ops/` first.
Back up existing files before replacement, and validate the app before restart.

Without sudo, the existing `tokeii` account can install a cron-based fallback:

```sh
python3 /home/tokeii/django/ops/install_watchdog.py
```

This preserves unrelated cron jobs and survives logout and reboot through the
system cron daemon. It only signals the account's exact Gunicorn main process,
after verifying systemd is configured to replace a failed process. It cannot
restart root-owned nginx or repair a service already in the `failed` state.

For the full system service and timer, run once with administrator authentication:

```sh
sudo python3 /home/tokeii/django/ops/install_watchdog.py --system
```

This installs root-owned code under `/opt/toto-watchdog`, a sandboxed oneshot
service, a boot-enabled timer, and restart-policy drop-ins for nginx and Gunicorn.
It checks nginx configuration before any nginx recovery. It removes only the
managed user cron block after enabling the system timer. It does not edit the
existing service files or their environment/credentials.

```sh
# User installation: current health and recovery decisions, without restarting.
python3 ~/.local/lib/toto-watchdog/server_watchdog.py \
  --config ~/.config/toto-watchdog.json --check
cat ~/.local/state/toto-watchdog/state.json
tail -n 30 ~/.local/state/toto-watchdog/watchdog.log

# System installation:
systemctl status toto-watchdog.timer
sudo python3 -I /opt/toto-watchdog/server_watchdog.py \
  --config /etc/toto-watchdog.json --check
sudo cat /var/lib/toto-watchdog/state.json
```

To pause user recovery, remove just the marked `toto-watchdog` block with
`crontab -e`. For system recovery, use `sudo systemctl disable --now
toto-watchdog.timer`. To undo system restart-policy changes, remove only
`90-toto-recovery.conf` from the nginx and Gunicorn drop-in directories and run
`sudo systemctl daemon-reload`. Keep other drop-ins and logs.

## Validation

```sh
python3 -m unittest discover -s ops -p 'test_*.py'
TOTO_TEST_GUNICORN=/home/tokeii/django/venv/bin/gunicorn \
  python3 -m unittest discover -s ops -p 'test_recovery_integration.py'
cd django
venv/bin/python3 manage.py test mysite.core.test_health \
  mysite.apps.todo_together --settings=mysite.test_settings
```

Tests cover timeouts, partial worker failure, successful recovery, cooldowns,
restart limits, manual stops, PID changes, unrelated processes, database/TLS
failures, real Unix-socket responses, and preserving unrelated cron entries.
API regressions use the project's uniquely named temporary MySQL test database.

This reduces recovery time for this failure class; it cannot guarantee zero
outages on a single machine. Power, the router/ISP, DNS, disk/hardware failures,
invalid deployments, and failed certificate renewal can require intervention.
An independent external uptime check and a second serving machine are needed
to detect or tolerate failures of the entire host/network.
