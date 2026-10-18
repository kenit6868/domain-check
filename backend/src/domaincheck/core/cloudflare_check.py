"""Bound the legacy CDN precheck; subprocess input never includes Dashboard cookies."""
import contextlib
import io
import json
import subprocess
import sys


def check(url, config):
    completed = subprocess.run([sys.executable, '-m', 'domaincheck.core.cloudflare_check'],
        input=json.dumps({'url': url, 'config': config}), capture_output=True,
        text=True, encoding='utf-8', timeout=180)
    if completed.returncode != 0: raise RuntimeError('precheck_failed')
    return json.loads(completed.stdout)


def main():
    try:
        values = json.loads(sys.stdin.read(65536))
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            import phishing_toolkit
            result = phishing_toolkit.run_cdn_check(values['url'], values['config'])
        sys.stdout.write(json.dumps({'cloudflare': bool(result.get('cloudflare'))}))
    except Exception:
        sys.exit(1)


if __name__ == '__main__': main()
