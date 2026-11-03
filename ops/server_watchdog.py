#!/usr/bin/env python3
"""Bounded local HTTP checks and recovery, independent of the app virtualenv."""

import argparse
import dataclasses
import datetime
import fcntl
import http.client
import json
import logging
import logging.handlers
import os
from pathlib import Path
import signal
import shutil
import socket
import ssl
import subprocess
import time


LOG = logging.getLogger("toto-watchdog")


@dataclasses.dataclass
class Config:
    app_dir: str = "/home/tokeii/django"
    host: str = "www.heeyun.co.kr"
    service: str = "gunicorn.service"
    state_dir: str = "/home/tokeii/.local/state/toto-watchdog"
    log_dir: str = "/home/tokeii/.local/state/toto-watchdog"
    timeout: float = 3
    failures_before_restart: int = 2
    crash_threshold: int = 6
    startup_grace: int = 45
    restart_cooldown: int = 120
    restart_window: int = 900
    restart_limit: int = 3

    @property
    def socket_path(self):
        return str(Path(self.app_dir) / "mysite.sock")

    @property
    def gunicorn_path(self):
        return str(Path(self.app_dir) / "venv/bin/gunicorn")


@dataclasses.dataclass
class Probe:
    ok: bool
    detail: str
    certificate_days: float | None = None


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, config):
        super().__init__(config.host, timeout=config.timeout)
        self.socket_path = config.socket_path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.socket_path)


class LocalHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        # Verify the real certificate/SNI without relying on DNS or router NAT.
        raw = socket.create_connection(("127.0.0.1", 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


def probe_http(config, path, *, https=False):
    connection = LocalHTTPSConnection(config.host, timeout=config.timeout) if https else UnixConnection(config)
    try:
        connection.request("GET", path, headers={
            "Host": config.host,
            "X-Forwarded-Proto": "https",
            "User-Agent": "toto-watchdog/1",
            "Connection": "close",
        })
        days = None
        if https and connection.sock:
            certificate = connection.sock.getpeercert()
            expiry = ssl.cert_time_to_seconds(certificate["notAfter"])
            days = (expiry - time.time()) / 86400
        response = connection.getresponse()
        body = response.read(4096)
        ok = response.status == 200 and json.loads(body) == {"status": "ok"}
        return Probe(ok, f"HTTP {response.status}", days)
    except (OSError, ValueError, http.client.HTTPException) as exc:
        # Log the exception type, never request headers or application bodies.
        return Probe(False, type(exc).__name__)
    finally:
        connection.close()


def run_command(args, timeout=4):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)


def inspect_service(name):
    result = run_command([
        "systemctl", "show", name, "--property=MainPID,ActiveState,SubState,Restart",
    ])
    if result.returncode:
        raise RuntimeError("Cannot inspect service")
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


def recent_crashes(config, pid, now):
    result = run_command([
        "journalctl", "--unit", config.service, "--no-pager", "--output=json",
        "--since", f"@{int(now - 65)}", "--lines=32",
        "--grep", r"Worker.*(SIGSEGV|SIGBUS|code 139|code 135)",
    ])
    if result.returncode:
        raise RuntimeError("Cannot read crash journal")
    count = 0
    for line in result.stdout.splitlines():
        entry = json.loads(line)
        if entry.get("_PID") == str(pid):
            count += 1
    return count


def process_age(pid):
    # /proc/stat's command name can contain spaces or parentheses.
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    start_seconds = int(fields[19]) / os.sysconf("SC_CLK_TCK")
    uptime = float(Path("/proc/uptime").read_text().split()[0])
    return uptime - start_seconds


def verified_main(config, pid):
    if pid <= 1:
        return False
    try:
        args = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        args = [os.fsdecode(arg) for arg in args if arg]
        # Only the exact application service can be signalled in user mode.
        uid = Path(f"/proc/{pid}").stat().st_uid
        return (
            config.gunicorn_path in args
            and f"unix:{config.socket_path}" in args
            and "mysite.wsgi:application" in args
            and (os.geteuid() == 0 or uid == os.geteuid())
        )
    except (OSError, ValueError):
        return False


def restart_service(config, name, pid=None):
    current = inspect_service(name)
    if name == config.service:
        failed_without_process = (
            os.geteuid() == 0 and pid is None
            and current.get("ActiveState") == "failed"
            and int(current.get("MainPID", "0")) == 0
        )
        if failed_without_process:
            command = run_command(["systemctl", "show", name, "--property=ExecStart", "--value"]).stdout
            if config.gunicorn_path not in command or f"unix:{config.socket_path}" not in command:
                LOG.error("Recovery cancelled: unexpected application service command")
                return False
        elif int(current.get("MainPID", "0")) != pid or not verified_main(config, pid):
            LOG.error("Recovery cancelled: service process identity changed")
            return False
    if os.geteuid() == 0:
        if name == "nginx.service":
            if run_command(["/usr/sbin/nginx", "-t"]).returncode:
                LOG.error("Recovery cancelled: nginx configuration check failed")
                return False
        return run_command(["systemctl", "--no-block", "restart", name]).returncode == 0
    if name != config.service or current.get("Restart") not in {"on-failure", "always", "on-abnormal", "on-abort"}:
        LOG.error("Recovery unavailable without a safe service restart policy")
        return False
    # This account owns the confirmed master. Its existing systemd on-failure
    # policy replaces it, including its corrupt fork state, after five seconds.
    descriptor = os.pidfd_open(pid)
    try:
        if not verified_main(config, pid):
            LOG.error("Recovery cancelled: process identity changed before signalling")
            return False
        # A pidfd stays attached to this process, even if its numeric PID is reused.
        signal.pidfd_send_signal(descriptor, signal.SIGKILL)
        return True
    except ProcessLookupError:
        return False
    finally:
        os.close(descriptor)


class Watchdog:
    def __init__(self, config, state, *, dry_run=False):
        self.config = config
        self.state = state
        self.dry_run = dry_run

    def restart(self, name, reason, now, pid=None):
        key = f"restarts:{name}"
        recent = [stamp for stamp in self.state.get(key, []) if now - stamp < self.config.restart_window]
        self.state[key] = recent
        if recent and now - recent[-1] < self.config.restart_cooldown:
            LOG.warning("Recovery cooling down: %s", name)
            return False
        if len(recent) >= self.config.restart_limit:
            LOG.error("Recovery budget exhausted: %s; operator action required", name)
            return False
        LOG.error("Recovery requested: %s reason=%s dry_run=%s", name, reason, self.dry_run)
        if self.dry_run:
            return False
        if restart_service(self.config, name, pid):
            recent.append(now)
            self.state["liveness_failures"] = 0
            self.state["https_failures"] = 0
            return True
        return False

    def run_once(self, now=None):
        now = time.time() if now is None else now
        config = self.config
        service = inspect_service(config.service)
        pid = int(service.get("MainPID", "0"))
        # Respect an intentional administrative stop; systemd handles crashes.
        if service.get("ActiveState") != "active" or pid <= 1:
            LOG.warning("Application is not active: state=%s", service.get("ActiveState"))
            self.state["liveness_failures"] = 0
            if service.get("ActiveState") == "failed" and os.geteuid() == 0:
                self.restart(config.service, "failed service", now)
            return {"application": "inactive"}
        if self.state.get("master_pid") != pid:
            self.state["master_pid"] = pid
            self.state["liveness_failures"] = 0
            self.state["https_failures"] = 0
        if not verified_main(config, pid):
            raise RuntimeError("Unexpected application main process")
        if process_age(pid) < config.startup_grace:
            return {"application": "starting"}

        live = probe_http(config, "/health/live/")
        self.state["liveness_failures"] = 0 if live.ok else self.state.get("liveness_failures", 0) + 1
        crashes = 0
        try:
            crashes = recent_crashes(config, pid, now)
        except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired):
            LOG.warning("Crash journal unavailable; HTTP checks remain active")

        result = {"application": live.detail, "worker_crashes": crashes}
        if crashes >= config.crash_threshold or self.state["liveness_failures"] >= config.failures_before_restart:
            reason = "worker crash loop" if crashes >= config.crash_threshold else "consecutive liveness failures"
            self.restart(config.service, reason, now, pid)
            return result
        if not live.ok:
            LOG.warning("Application liveness failed: %s", live.detail)
            return result

        ready = probe_http(config, "/health/ready/")
        result["database"] = ready.detail
        if not ready.ok:
            # A database outage does not justify killing a healthy web process.
            LOG.error("Database readiness failed: %s", ready.detail)

        https = probe_http(config, "/health/live/", https=True)
        result["https"] = https.detail
        self.state["https_failures"] = 0 if https.ok else self.state.get("https_failures", 0) + 1
        if not https.ok:
            LOG.error("Local HTTPS check failed: %s", https.detail)
            if os.geteuid() == 0 and self.state["https_failures"] >= config.failures_before_restart:
                nginx = inspect_service("nginx.service")
                # Certificate/configuration errors cannot be fixed by a restart.
                transport_error = https.detail in {
                    "ConnectionRefusedError", "ConnectionResetError", "TimeoutError",
                    "RemoteDisconnected", "SSLError", "HTTP 502", "HTTP 503", "HTTP 504",
                }
                if transport_error and nginx.get("ActiveState") in {"active", "failed"}:
                    self.restart("nginx.service", "HTTPS transport failure with healthy application", now)
        if https.certificate_days is not None:
            result["certificate_days"] = round(https.certificate_days, 1)
            if https.certificate_days < 14:
                LOG.error("TLS certificate renewal needed: days_remaining=%.1f", https.certificate_days)

        disk = shutil.disk_usage(config.app_dir)
        if disk.free < 2 * 1024**3 or disk.used / disk.total > 0.9:
            LOG.error("Disk capacity low: free_gib=%.1f", disk.free / 1024**3)

        self.state["last_check"] = datetime.datetime.fromtimestamp(now, datetime.timezone.utc).isoformat()
        self.state["last_result"] = result
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--check", action="store_true", help="Probe without restarting or changing saved counters")
    args = parser.parse_args()
    config = Config(**json.loads(args.config.read_text()))
    state_dir = Path(config.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    log_dir = Path(config.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    handler = logging.handlers.RotatingFileHandler(log_dir / "watchdog.log", maxBytes=1024 * 1024, backupCount=5)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOG.addHandler(handler)
    LOG.setLevel(logging.INFO)
    if args.check:
        LOG.addHandler(logging.StreamHandler())
    with (state_dir / "watchdog.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        state_file = state_dir / "state.json"
        try:
            try:
                state = json.loads(state_file.read_text()) if state_file.exists() else {}
                if not isinstance(state, dict):
                    raise ValueError("Unexpected state format")
            except (ValueError, OSError):
                LOG.error("Unreadable watchdog state; starting fresh")
                state = {}
            result = Watchdog(config, state, dry_run=args.check).run_once()
            if args.check:
                print(json.dumps(result, sort_keys=True))
            else:
                temporary = state_file.with_suffix(".tmp")
                temporary.write_text(json.dumps(state, sort_keys=True) + "\n")
                temporary.chmod(0o600)
                temporary.replace(state_file)
            return 0
        except Exception:
            LOG.exception("Watchdog check failed")
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
