"""Bounded isolated browser process for explicit review capture; never sends mail."""
import base64
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from domaincheck.core.evidence import CaptureFailed, LegacyBrowserCaptureAdapter
from domaincheck.core.investigation import sanitize_result
from domaincheck.ports.artifacts import ImageUpload


def capture_review(target, decision):
    with tempfile.TemporaryDirectory(prefix='review-browser-') as directory:
        try:
            subprocess.run([sys.executable, '-m', 'domaincheck.core.review_capture', target, decision, directory],
                check=True, timeout=180, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            path = Path(directory) / 'result.json'
            if path.stat().st_size > 96 * 1024 * 1024:
                raise CaptureFailed('invalid_capture_artifacts')
            payload = json.loads(path.read_text(encoding='utf-8'))
            images = [ImageUpload('capture.png', row['mime_type'], base64.b64decode(row['data'], validate=True))
                      for row in payload['images']]
            internal = [ImageUpload('observation.png', row['mime_type'], base64.b64decode(row['data'], validate=True))
                        for row in payload.get('internal_images', [])]
            return images, payload['manifest'], internal
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
            raise CaptureFailed('capture_failed') from exc


def _capture(target, decision, directory):
    root = Path(directory).resolve()
    internal = []
    if decision == 'not_cloaking':
        images, manifest = LegacyBrowserCaptureAdapter().capture(target, staging_root=root)
    elif decision == 'confirmed_cloaking':
        import cloaking_detector as detector
        result = detector.probe_playwright_cloaking(target, timeout_ms=15_000, evidence_root=str(root))
        if result.get('verdict') == 'BLOCKED_OR_UNAVAILABLE' or (result.get('site_state') or {}).get('all_profiles_terminal'):
            raise CaptureFailed('terminal_page')
        shots = result.get('screenshots') or []
        if len(shots) != 2 or len({shot.get('profile') for shot in shots if shot.get('profile')}) != 2:
            raise CaptureFailed('invalid_capture_artifacts')
        images = []
        for shot in shots:
            path = Path(shot['path']).resolve(strict=True)
            if not path.is_relative_to(root) or not 0 < path.stat().st_size <= 10 * 1024 * 1024:
                raise CaptureFailed('capture_path_outside_staging')
            data = path.read_bytes()
            images.append(ImageUpload('capture.png', 'image/png', data))
        for profile in (result.get('profiles') or {}).values():
            if not profile.get('screenshot_path'):
                continue
            path = Path(profile['screenshot_path']).resolve(strict=True)
            if not path.is_relative_to(root) or not 0 < path.stat().st_size <= 10 * 1024 * 1024:
                raise CaptureFailed('invalid_capture_artifacts')
            internal.append(ImageUpload('observation.png', 'image/png', path.read_bytes()))
        manifest = {'version': 1, 'source': 'browser_capture', 'evidence_type': 'cloaking_comparison',
            'requested_url': target, 'terminal': False, 'navigation_verified': False,
            'cloaking': sanitize_result({'cloaking': result})['cloaking']}
    else:
        raise ValueError('Unknown decision')
    payload = {'images': [{'mime_type': row.content_type, 'data': base64.b64encode(row.data).decode('ascii')}
                          for row in images], 'manifest': manifest,
               'internal_images': [{'mime_type': row.content_type, 'data': base64.b64encode(row.data).decode('ascii')}
                                   for row in internal]}
    (root / 'result.json').write_text(json.dumps(payload), encoding='utf-8')


if __name__ == '__main__':
    _capture(*sys.argv[1:])
