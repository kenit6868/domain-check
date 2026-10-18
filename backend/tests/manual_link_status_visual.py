"""Local browser fixture smoke; no real authentication or investigation requests."""
import json
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    from domaincheck.core.link_status import unknown
    rows = []
    for index, (domain, website) in enumerate([('DOMAIN_HOLD', 'UNKNOWN'), ('DNS_OK', 'CF_PHISHING_BLOCKED'),
                                             ('DNS_OK', 'CF_CHALLENGE'), ('DNS_OK', 'HTTP_OK')]):
        row = unknown(f'https://fixture-{index}.example.com/login')
        row.update(domain_status=domain, website_status=website, status=website,
            group='Fixture brand', http_code=200 if index else None,
            provider='Cloudflare' if index in {1, 2} else '')
        rows.append(row)
    batch = {'id': 'fixture-run', 'phase': 'COMPLETED', 'urls': [row['url'] for row in rows], 'results': rows,
        'processed': 4, 'total': 4, 'invalid_count': 0, 'duplicate_count': 0, 'started_at': rows[0]['checked_at']}
    me = {'id': 'fixture-owner', 'display_name': 'Fixture Operator', 'telegram_username': 'fixture',
        'status': 'active', 'timezone': 'Asia/Bangkok', 'permissions': ['link_status.view', 'link_status.check']}
    output = Path(tempfile.gettempdir()) / 'domaincheck-link-status-visual'
    output.mkdir(exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for name, width in [('desktop', 1440), ('mobile', 390)]:
            page = browser.new_page(viewport={'width': width, 'height': 1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.route('**/*', lambda route: route.continue_() if route.request.url.startswith('http://127.0.0.1:5173/') else route.abort())
            def api(route):
                path = route.request.url.split('/api/v1')[-1]
                if path == '/me': data = me
                elif path == '/link-status/check':
                    events = [{'type': 'start', 'batch': {**batch, 'results': [], 'processed': 0, 'phase': 'CHECKING'}}]
                    events.extend({'type': 'result', 'index': i, 'result': row, 'processed': i + 1} for i, row in enumerate(rows))
                    events.append({'type': 'complete', 'processed': 4})
                    route.fulfill(status=200, content_type='application/x-ndjson', body='\n'.join(json.dumps(e) for e in events) + '\n')
                    return
                else: data = {}
                route.fulfill(status=200, content_type='application/json', body=json.dumps(data))
            page.route('**/api/v1/**', api)
            page.goto('http://127.0.0.1:5173/link-status', wait_until='networkidle')
            assert page.get_by_role('table').count() == 0
            page.get_by_label('Danh sách domain/URL').fill('https://example.com')
            page.get_by_role('button', name='Kiểm tra trạng thái', exact=True).click()
            page.get_by_role('table').wait_for()
            assert page.get_by_role('row').count() == 5
            page.get_by_label('Lọc trạng thái website').select_option('CF_CHALLENGE')
            assert page.get_by_role('row').count() == 2
            page.get_by_role('button', name='Xóa bộ lọc').click()
            assert page.get_by_role('row').count() == 5
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            assert not errors, errors
            page.screenshot(path=str(output / f'{name}.png'), full_page=True)
            page.reload(wait_until='networkidle')
            assert page.get_by_role('table').count() == 0
            print(name, 'filter/clear/render passed; no horizontal page overflow;', output / f'{name}.png')
            page.close()
        browser.close()


if __name__ == '__main__': main()
