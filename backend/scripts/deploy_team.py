"""Build first, drain application services, migrate, then start the team stack."""
import argparse
from pathlib import Path
import subprocess

BACKEND = Path(__file__).resolve().parents[1]
SERVICES = ['api', 'quick-report', 'check-domain', 'precheck', 'evidence', 'send-mail', 'cloudflare-runner']


def deployment_commands(*, aapanel=False):
    compose = ['docker', 'compose', '--env-file', '.env', '--env-file', '../frontend/.env',
               '-f', 'compose.yaml', '-f', 'compose.production.yaml']
    if aapanel:
        compose += ['-f', 'compose.aapanel.yaml']
    return [
        compose + ['build'],
        compose + ['up', '-d', '--wait', 'db', 'redis'],
        compose + ['stop', *SERVICES],
        compose + ['up', '--no-deps', '--force-recreate', '--exit-code-from', 'migrate', 'migrate'],
        compose + ['up', '-d', '--no-build', '--wait', *SERVICES, 'web'],
    ]


def main():
    parser = argparse.ArgumentParser(description='Deploy the team stack with graceful worker drain')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--aapanel', action='store_true', help='Use aaPanel HTTPS proxy with web on loopback port 8088')
    args = parser.parse_args()
    if not args.dry_run and not (BACKEND / '.env').is_file():
        parser.error('Configure backend/.env using the documented production examples first')
    if not args.dry_run and not (BACKEND.parent / 'frontend' / '.env').is_file():
        parser.error('Configure frontend/.env from frontend/.env.example with the public extension ID first')
    for command in deployment_commands(aapanel=args.aapanel):
        print(subprocess.list2cmdline(command), flush=True)
        if not args.dry_run:
            result = subprocess.run(command, cwd=BACKEND, check=False)
            if result.returncode:
                return result.returncode
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
