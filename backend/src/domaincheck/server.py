"""Single entry point for the API and its required background services."""
from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time


def commands(host: str, port: int, reload: bool = False) -> list[tuple[str, list[str]]]:
    from domaincheck.settings import get_settings
    settings = get_settings()
    api = [sys.executable, '-m', 'uvicorn', 'domaincheck.main:create_app',
           '--factory', '--host', host, '--port', str(port)]
    if reload:
        api.append('--reload')
    return [
        ('API', api),
        *[(f'Worker {name}', [sys.executable, '-m', 'domaincheck.workers.cli', '--job-type', name])
          for name in settings.worker_slots()],
        ('Cloudflare runner', [sys.executable, '-m', 'domaincheck.cloudflare_runner']),
    ]


def supervise(services: list[tuple[str, list[str]]]) -> int:
    children: list[tuple[str, subprocess.Popen]] = []
    try:
        for name, command in services:
            options = ({'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
                       if os.name == 'nt' else {'start_new_session': True})
            children.append((name, subprocess.Popen(command, **options)))
            print(f'[backend] Started {name}', flush=True)
        while True:
            for name, child in children:
                code = child.poll()
                if code is not None:
                    print(f'[backend] {name} stopped (exit {code}); stopping the backend stack.', flush=True)
                    return code or 1
            time.sleep(0.5)
    except KeyboardInterrupt:
        print('[backend] Stopping services; waiting for shutdown.', flush=True)
        return 0
    finally:
        for name, child in reversed(children):
            if child.poll() is None:
                try:
                    if os.name == 'nt':
                        child.send_signal(signal.CTRL_BREAK_EVENT)
                    else:
                        os.killpg(child.pid, signal.SIGINT)
                except ProcessLookupError:
                    pass
        # Do not forcibly kill an in-flight delivery after an arbitrary timeout.
        for _, child in children:
            child.wait()


def main() -> int:
    parser = argparse.ArgumentParser(description='Run the complete backend: API, job worker and Cloudflare runner')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--reload', action='store_true', help='Reload only the API during development')
    parser.add_argument('--dry-run', action='store_true', help='Print service commands without starting them')
    parser.add_argument('--supervisor-port', type=int, default=8790,
                        help='Local singleton guard port; all launches on this host must use the same value')
    args = parser.parse_args()
    if not all(1 <= value <= 65535 for value in (args.port, args.supervisor_port)):
        parser.error('Ports must be between 1 and 65535')
    services = commands(args.host, args.port, args.reload)
    if args.dry_run:
        for name, command in services:
            print(f'{name}: {subprocess.list2cmdline(command)}')
        return 0
    # Hold a local socket for the entire lifetime, including child shutdown.
    # This prevents an accidental second launch from claiming queued jobs.
    with socket.socket() as guard:
        if os.name == 'nt':
            guard.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            guard.bind(('127.0.0.1', args.supervisor_port))
        except OSError:
            print('[backend] Another backend launcher is running or its guard port is occupied.', file=sys.stderr)
            return 1
        def stop(*_):
            raise KeyboardInterrupt

        previous = signal.signal(signal.SIGTERM, stop)
        try:
            return supervise(services)
        finally:
            signal.signal(signal.SIGTERM, previous)


if __name__ == '__main__':
    raise SystemExit(main())
