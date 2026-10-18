"""Build first, drain application services, migrate, then start the team stack."""
import argparse
from pathlib import Path
import subprocess

BACKEND = Path(__file__).resolve().parents[1]
SERVICES = ['api', 'quick-report', 'check-domain', 'precheck', 'evidence', 'send-mail', 'cloudflare-runner']


def deployment_commands():
    compose = ['docker', 'compose', '--env-file', '.env', '-f', 'compose.yaml', '-f', 'compose.production.yaml']
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
    args = parser.parse_args()
    if not args.dry_run and not (BACKEND / '.env').is_file():
        parser.error('Configure backend/.env using the documented production examples first')
    for command in deployment_commands():
        print(subprocess.list2cmdline(command), flush=True)
        if not args.dry_run:
            result = subprocess.run(command, cwd=BACKEND, check=False)
            if result.returncode:
                return result.returncode
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
