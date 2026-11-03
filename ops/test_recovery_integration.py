"""Exercise real, disposable Gunicorn processes; never touch production PIDs."""

import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

import server_watchdog as watchdog


@unittest.skipUnless(os.environ.get("TOTO_TEST_GUNICORN"), "Set TOTO_TEST_GUNICORN for isolated process recovery tests")
class ProcessRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="toto-recovery-test-")
        self.addCleanup(self.temporary.cleanup)
        self.app = Path(self.temporary.name)
        (self.app / "venv/bin").mkdir(parents=True)
        binary = self.app / "venv/bin/gunicorn"
        binary.symlink_to(os.environ["TOTO_TEST_GUNICORN"])
        (self.app / "mysite").mkdir()
        (self.app / "mysite/__init__.py").write_text("")
        (self.app / "mysite/wsgi.py").write_text('''import os
def application(environ, start_response):
    if environ['PATH_INFO'] == '/crash/':
        os._exit(139)
    body = b'{"status":"ok"}'
    start_response('200 OK', [('Content-Type','application/json'),('Content-Length',str(len(body)))])
    return [body]
''')
        self.config = watchdog.Config(
            app_dir=str(self.app), service="toto-disposable-test.service",
            timeout=0.2, startup_grace=0,
        )
        self.logfile = (self.app / "gunicorn.log").open("w+")
        self.addCleanup(self.logfile.close)
        self.processes = []
        self.addCleanup(self.stop_processes)
        self.start_master()

    def start_master(self):
        self.process = subprocess.Popen([
            self.config.gunicorn_path, "--workers", "1", "--worker-class", "gthread",
            "--threads", "2", "--graceful-timeout", "1",
            "--bind", f"unix:{self.config.socket_path}", "mysite.wsgi:application",
        ], cwd=self.app, stdout=self.logfile, stderr=subprocess.STDOUT, start_new_session=True)
        self.processes.append(self.process)
        self.wait_for_health()

    def wait_for_health(self):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if watchdog.probe_http(self.config, "/health/live/").ok:
                return
            time.sleep(0.05)
        self.fail("Disposable Gunicorn did not become healthy")

    def stop_processes(self):
        for process in self.processes:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)

    def monitor(self, crashes):
        service = {"MainPID": str(self.process.pid), "ActiveState": "active", "Restart": "on-failure"}
        actual_probe = watchdog.probe_http

        def local_probe(config, path, *, https=False):
            return watchdog.Probe(True, "HTTP 200") if https else actual_probe(config, path)

        monitor = watchdog.Watchdog(self.config, {})
        with patch.object(watchdog, "inspect_service", return_value=service), \
                patch.object(watchdog, "recent_crashes", return_value=crashes), \
                patch.object(watchdog, "probe_http", side_effect=local_probe):
            monitor.run_once(1000)
            if not crashes:
                self.assertIsNone(self.process.poll(), "A single timeout must not kill the master")
                monitor.run_once(1030)
        self.assertEqual(self.process.wait(timeout=5), -signal.SIGKILL)

    def test_hung_worker_replaces_actual_master_and_fresh_process_recovers(self):
        children = Path(f"/proc/{self.process.pid}/task/{self.process.pid}/children").read_text().split()
        self.assertTrue(children)
        for child in children:
            os.kill(int(child), signal.SIGSTOP)
        self.monitor(crashes=0)
        # Clean up the deliberately frozen old worker before the test supervisor
        # starts a replacement, as the real systemd cgroup cleanup would do.
        os.killpg(self.process.pid, signal.SIGKILL)
        Path(self.config.socket_path).unlink(missing_ok=True)
        self.start_master()
        self.assertTrue(watchdog.probe_http(self.config, "/health/live/").ok)

    def test_real_worker_crash_loop_replaces_master_while_http_still_works(self):
        for unused in range(6):
            self.assertFalse(watchdog.probe_http(self.config, "/crash/").ok)
            self.wait_for_health()
        self.logfile.flush()
        log = (self.app / "gunicorn.log").read_text()
        self.assertGreaterEqual(log.count("exited with code 139"), 6)
        self.monitor(crashes=6)
        Path(self.config.socket_path).unlink(missing_ok=True)
        self.start_master()
        self.assertTrue(watchdog.probe_http(self.config, "/health/live/").ok)


if __name__ == "__main__":
    unittest.main()
