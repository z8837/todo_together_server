#!/usr/bin/env python3
"""Install user recovery without sudo, or the full system timer as root."""

import argparse
import json
import os
from pathlib import Path
import pwd
import shlex
import subprocess


BEGIN = "# BEGIN toto-watchdog (managed)"
END = "# END toto-watchdog (managed)"


def replace_cron_block(existing, commands=()):
    lines = existing.splitlines()
    if lines.count(BEGIN) != lines.count(END) or lines.count(BEGIN) > 1:
        raise ValueError("Malformed watchdog cron block; refusing to replace crontab")
    preserved = []
    managed = False
    for line in lines:
        if line == BEGIN:
            managed = True
        elif line == END:
            if not managed:
                raise ValueError("Malformed watchdog cron block")
            managed = False
        elif not managed:
            preserved.append(line)
    if commands:
        preserved.extend([BEGIN, *commands, END])
    return "\n".join(preserved).rstrip() + "\n"


def update_cron(commands=(), user=None):
    command = ["crontab"] + (["-u", user] if user else [])
    result = subprocess.run([*command, "-l"], capture_output=True, text=True, check=False)
    if result.returncode not in (0, 1):
        raise RuntimeError("Cannot read crontab")
    if result.returncode == 1 and "no crontab" not in result.stderr.lower():
        raise RuntimeError("Cannot read crontab")
    updated = replace_cron_block(result.stdout, commands)
    subprocess.run([*command, "-"], input=updated, text=True, check=True)


def write_owned(path, text, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or (path.exists() and path.stat().st_uid != os.geteuid()):
        raise RuntimeError(f"Unexpected ownership or symlink: {path}")
    temporary = path.with_name(path.name + ".new")
    # O_EXCL prevents following a pre-existing link at a privileged target.
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(text)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def install_user(app_dir):
    account_home = Path.home()
    installed_script = account_home / ".local/lib/toto-watchdog/server_watchdog.py"
    config_file = account_home / ".config/toto-watchdog.json"
    state = account_home / ".local/state/toto-watchdog"
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_owned(installed_script, Path(__file__).with_name("server_watchdog.py").read_text())
    write_owned(config_file, json.dumps({"app_dir": str(app_dir), "state_dir": str(state), "log_dir": str(state)}, indent=2) + "\n")
    command = shlex.join(["/usr/bin/python3", "-I", str(installed_script), "--config", str(config_file)])
    update_cron([
        f"* * * * * {command} > /dev/null 2>&1",
        f"* * * * * /bin/sleep 30 && {command} > /dev/null 2>&1",
    ])
    subprocess.run(["/usr/bin/python3", "-I", str(installed_script), "--config", str(config_file), "--check"], check=True)
    print(f"User watchdog installed: 30-second checks; state at {state}")


def install_system(app_dir, user):
    if os.geteuid() != 0:
        raise PermissionError("System installation requires sudo")
    account = pwd.getpwnam(user)
    if app_dir.stat().st_uid != account.pw_uid:
        raise RuntimeError("Application directory must belong to the service account")
    subprocess.run(["/usr/sbin/nginx", "-t"], check=True)
    for directory in ("/opt/toto-watchdog", "/var/lib/toto-watchdog", "/var/log/toto-watchdog"):
        path = Path(directory)
        if path.is_symlink() or (path.exists() and path.stat().st_uid != 0):
            raise RuntimeError(f"Unexpected privileged directory: {path}")
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_owned("/opt/toto-watchdog/server_watchdog.py", Path(__file__).with_name("server_watchdog.py").read_text())
    write_owned("/etc/toto-watchdog.json", json.dumps({
        "app_dir": str(app_dir),
        "state_dir": "/var/lib/toto-watchdog",
        "log_dir": "/var/log/toto-watchdog",
    }, indent=2) + "\n")
    write_owned("/etc/systemd/system/toto-watchdog.service", """[Unit]
Description=Todo Together HTTP and worker crash recovery
After=network-online.target nginx.service gunicorn.service
Wants=network-online.target

[Service]
Type=oneshot
User=root
ExecStart=/usr/bin/python3 -I /opt/toto-watchdog/server_watchdog.py --config /etc/toto-watchdog.json
TimeoutStartSec=45
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/var/lib/toto-watchdog /var/log/toto-watchdog
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictRealtime=true
LockPersonality=true
SystemCallArchitectures=native
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
""", 0o644)
    write_owned("/etc/systemd/system/toto-watchdog.timer", """[Unit]
Description=Check Todo Together every 30 seconds

[Timer]
OnBootSec=60s
OnUnitInactiveSec=30s
AccuracySec=1s
Unit=toto-watchdog.service

[Install]
WantedBy=timers.target
""", 0o644)
    write_owned("/etc/systemd/system/nginx.service.d/90-toto-recovery.conf", """[Unit]
StartLimitIntervalSec=300
StartLimitBurst=10

[Service]
Restart=on-failure
RestartSec=5s
""", 0o644)
    write_owned("/etc/systemd/system/gunicorn.service.d/90-toto-recovery.conf", """[Unit]
StartLimitIntervalSec=300
StartLimitBurst=10

[Service]
Restart=always
RestartSec=5s
TimeoutStopSec=45s
Environment=PYTHONFAULTHANDLER=1
""", 0o644)
    subprocess.run(["systemd-analyze", "verify", "/etc/systemd/system/toto-watchdog.service", "/etc/systemd/system/toto-watchdog.timer"], check=True)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["/usr/bin/python3", "-I", "/opt/toto-watchdog/server_watchdog.py", "--config", "/etc/toto-watchdog.json", "--check"], check=True)
    subprocess.run(["systemctl", "enable", "--now", "toto-watchdog.timer"], check=True)
    update_cron(user=user)  # Retire the unprivileged fallback after system setup.
    print("System watchdog enabled; nginx and Gunicorn restart policies installed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-dir", type=Path, default=Path("/home/tokeii/django"))
    parser.add_argument("--system", action="store_true")
    parser.add_argument("--user", default="tokeii")
    args = parser.parse_args()
    app_dir = args.app_dir.resolve(strict=True)
    if not (app_dir / "venv/bin/gunicorn").is_file():
        raise RuntimeError("Application Gunicorn executable is missing")
    if args.system:
        install_system(app_dir, args.user)
    else:
        if os.geteuid() == 0:
            raise RuntimeError("Use --system for a root installation")
        install_user(app_dir)


if __name__ == "__main__":
    main()
