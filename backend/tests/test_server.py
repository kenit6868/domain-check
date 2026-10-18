import contextlib
import io
import signal
import socket
import sys
import unittest
from unittest.mock import MagicMock, patch

from domaincheck import server


class BackendLauncherTests(unittest.TestCase):
    def test_reload_applies_only_to_api_and_uses_current_python(self):
        from domaincheck.settings import Settings
        with patch('domaincheck.settings.get_settings', return_value=Settings(_env_file=None, browser_capture_enabled=True)):
            services = server.commands('127.0.0.1', 8123, True)
        self.assertEqual(len(services), 7)
        self.assertTrue(all(command[0] == sys.executable for _, command in services))
        self.assertIn('--reload', services[0][1])
        self.assertIn('8123', services[0][1])
        self.assertNotIn('--reload', services[1][1] + services[2][1])
        self.assertEqual({command[-1] for _, command in services[1:-1]},
                         {'quick_report', 'check_domain', 'domain_worker_precheck', 'domain_worker_send', 'browser_evidence'})

    def test_child_failure_stops_siblings_and_returns_failure(self):
        failed, live = MagicMock(), MagicMock()
        failed.poll.return_value = 7
        live.poll.return_value = None
        with patch.object(server.subprocess, 'Popen', side_effect=[failed, live]), \
             patch.object(server.os, 'killpg', create=True) as kill, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(server.supervise([('failed', ['a']), ('live', ['b'])]), 7)
        if server.os.name == 'nt':
            live.send_signal.assert_called_once_with(signal.CTRL_BREAK_EVENT)
        else:
            kill.assert_called_once_with(live.pid, signal.SIGINT)
        failed.wait.assert_called_once_with()
        live.wait.assert_called_once_with()

    def test_partial_launch_failure_cleans_up_started_child(self):
        live = MagicMock()
        live.poll.return_value = None
        with patch.object(server.subprocess, 'Popen', side_effect=[live, OSError('fixture')]), \
             patch.object(server.os, 'killpg', create=True), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(OSError):
                server.supervise([('first', ['a']), ('second', ['b'])])
        live.wait.assert_called_once_with()

    def test_interrupt_stops_children_without_force_kill(self):
        live = MagicMock()
        live.poll.return_value = None
        with patch.object(server.subprocess, 'Popen', return_value=live), \
             patch.object(server.os, 'killpg', create=True), \
             patch.object(server.time, 'sleep', side_effect=KeyboardInterrupt), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(server.supervise([('worker', ['a'])]), 0)
        live.kill.assert_not_called()
        live.terminate.assert_not_called()
        live.wait.assert_called_once_with()

    def test_dry_run_starts_nothing(self):
        with patch.object(sys, 'argv', ['domaincheck', '--dry-run']), \
             patch.object(server.subprocess, 'Popen') as launch, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(server.main(), 0)
        launch.assert_not_called()
        self.assertIn('Cloudflare runner:', output.getvalue())

    def test_second_launcher_cannot_start_children(self):
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1', 0))
            port = occupied.getsockname()[1]
            with patch.object(sys, 'argv', ['domaincheck', '--supervisor-port', str(port)]), \
                 patch.object(server.subprocess, 'Popen') as launch, \
                 contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(server.main(), 1)
            launch.assert_not_called()
