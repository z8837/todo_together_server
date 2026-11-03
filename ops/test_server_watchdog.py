import json
import socketserver
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

import server_watchdog as watchdog


class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.config = watchdog.Config(app_dir="/tmp")
        self.state = {}
        self.service = {"MainPID": "123", "ActiveState": "active", "Restart": "on-failure"}
        self.patches = [
            patch.object(watchdog, "inspect_service", return_value=self.service),
            patch.object(watchdog, "verified_main", return_value=True),
            patch.object(watchdog, "process_age", return_value=100),
            patch.object(watchdog, "recent_crashes", return_value=0),
            patch.object(watchdog, "probe_http", return_value=watchdog.Probe(True, "HTTP 200", 30)),
            patch.object(watchdog, "restart_service", return_value=True),
            patch.object(watchdog.os, "geteuid", return_value=1000),
        ]
        self.inspect, self.verify, self.age, self.crashes, self.probe, self.restart, self.uid = [p.start() for p in self.patches]
        for p in self.patches:
            self.addCleanup(p.stop)
        self.monitor = watchdog.Watchdog(self.config, self.state)

    def test_healthy_requests_do_not_restart(self):
        result = self.monitor.run_once(1000)
        self.assertEqual(result["https"], "HTTP 200")
        self.restart.assert_not_called()

    def test_two_consecutive_failures_replace_master(self):
        self.probe.return_value = watchdog.Probe(False, "TimeoutError")
        self.monitor.run_once(1000)
        self.restart.assert_not_called()
        self.monitor.run_once(1030)
        self.restart.assert_called_once_with(self.config, self.config.service, 123)

    def test_success_clears_transient_failure(self):
        self.probe.return_value = watchdog.Probe(False, "TimeoutError")
        self.monitor.run_once(1000)
        self.probe.return_value = watchdog.Probe(True, "HTTP 200")
        self.monitor.run_once(1030)
        self.probe.return_value = watchdog.Probe(False, "TimeoutError")
        self.monitor.run_once(1060)
        self.restart.assert_not_called()

    def test_worker_crashes_trigger_recovery_even_when_one_worker_responds(self):
        self.crashes.return_value = 10
        self.monitor.run_once(1000)
        self.restart.assert_called_once_with(self.config, self.config.service, 123)

    def test_database_failure_does_not_restart_web_service(self):
        self.probe.side_effect = [watchdog.Probe(True, "HTTP 200"), watchdog.Probe(False, "HTTP 503"), watchdog.Probe(True, "HTTP 200")]
        self.monitor.run_once(1000)
        self.restart.assert_not_called()

    def test_stopped_service_is_respected(self):
        self.uid.return_value = 0
        self.service.update(MainPID="0", ActiveState="inactive")
        self.monitor.run_once(1000)
        self.probe.assert_not_called()
        self.restart.assert_not_called()

    def test_failed_service_can_be_recovered_by_root(self):
        self.uid.return_value = 0
        self.service.update(MainPID="0", ActiveState="failed")
        self.monitor.run_once(1000)
        self.restart.assert_called_once_with(self.config, self.config.service, None)

    def test_startup_grace_prevents_killing_cold_start(self):
        self.age.return_value = 2
        self.monitor.run_once(1000)
        self.probe.assert_not_called()
        self.restart.assert_not_called()

    def test_new_master_does_not_inherit_failure_count(self):
        self.state.update(master_pid=456, liveness_failures=1)
        self.probe.return_value = watchdog.Probe(False, "TimeoutError")
        self.monitor.run_once(1000)
        self.restart.assert_not_called()

    def test_restart_cooldown_and_budget(self):
        self.crashes.return_value = 10
        for stamp in (1000, 1030, 1121, 1242, 1363):
            self.monitor.run_once(stamp)
        self.assertEqual(self.restart.call_count, 3)
        self.monitor.run_once(2000)
        self.assertEqual(self.restart.call_count, 4)

    def test_dry_run_never_restarts(self):
        self.crashes.return_value = 10
        watchdog.Watchdog(self.config, self.state, dry_run=True).run_once(1000)
        self.restart.assert_not_called()

    def test_certificate_error_does_not_restart_nginx(self):
        self.uid.return_value = 0
        for stamp in (1000, 1030):
            self.probe.side_effect = [watchdog.Probe(True, "HTTP 200"), watchdog.Probe(True, "HTTP 200"), watchdog.Probe(False, "SSLCertVerificationError")]
            self.monitor.run_once(stamp)
        self.restart.assert_not_called()

    def test_https_transport_failure_restarts_nginx_in_root_mode(self):
        self.uid.return_value = 0
        for stamp in (1000, 1030):
            self.probe.side_effect = [watchdog.Probe(True, "HTTP 200"), watchdog.Probe(True, "HTTP 200"), watchdog.Probe(False, "ConnectionRefusedError")]
            self.monitor.run_once(stamp)
        self.restart.assert_called_once_with(self.config, "nginx.service", None)

    def test_journal_failure_keeps_http_checks_running(self):
        self.crashes.side_effect = RuntimeError("journal unavailable")
        result = self.monitor.run_once(1000)
        self.assertEqual(result["application"], "HTTP 200")


class RecoverySafetyTests(unittest.TestCase):
    @patch.object(watchdog, "verified_main", return_value=True)
    @patch.object(watchdog, "inspect_service", return_value={"MainPID": "456", "Restart": "on-failure"})
    @patch.object(watchdog.os, "kill")
    def test_changed_main_pid_is_never_signalled(self, kill, inspect, verify):
        self.assertFalse(watchdog.restart_service(watchdog.Config(), "gunicorn.service", 123))
        kill.assert_not_called()

    @patch.object(watchdog, "verified_main", return_value=False)
    @patch.object(watchdog, "inspect_service", return_value={"MainPID": "123", "Restart": "on-failure"})
    @patch.object(watchdog.os, "kill")
    def test_unrelated_process_is_never_signalled(self, kill, inspect, verify):
        self.assertFalse(watchdog.restart_service(watchdog.Config(), "gunicorn.service", 123))
        kill.assert_not_called()

    @patch.object(watchdog, "verified_main", return_value=True)
    @patch.object(watchdog, "inspect_service", return_value={"MainPID": "123", "Restart": "no"})
    @patch.object(watchdog.os, "geteuid", return_value=1000)
    @patch.object(watchdog.os, "kill")
    def test_no_restart_policy_prevents_user_mode_kill(self, kill, uid, inspect, verify):
        self.assertFalse(watchdog.restart_service(watchdog.Config(), "gunicorn.service", 123))
        kill.assert_not_called()

    def test_crash_events_from_previous_master_are_ignored(self):
        output = "\n".join(json.dumps({"_PID": pid}) for pid in ("123", "456", "123"))
        with patch.object(watchdog, "run_command", return_value=MagicMock(returncode=0, stdout=output)):
            self.assertEqual(watchdog.recent_crashes(watchdog.Config(), 123, 1000), 2)


class HTTPProbeIntegrationTests(unittest.TestCase):
    def test_unix_socket_checks_status_and_body(self):
        class Handler(socketserver.StreamRequestHandler):
            body = b'{"status":"ok"}'

            def handle(self):
                while self.rfile.readline().strip():
                    pass
                self.wfile.write(b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: " + str(len(self.body)).encode() + b"\r\n\r\n" + self.body)

        with tempfile.TemporaryDirectory() as directory:
            config = watchdog.Config(app_dir=directory)
            with socketserver.UnixStreamServer(config.socket_path, Handler) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    self.assertTrue(watchdog.probe_http(config, "/health/live/").ok)
                    Handler.body = b'{"other":"page"}'
                    self.assertFalse(watchdog.probe_http(config, "/health/live/").ok)
                finally:
                    server.shutdown()
                    thread.join()


if __name__ == "__main__":
    unittest.main()
