"""Request-local streaming checks. No user workspace, result database or result cache."""
import asyncio
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

from domaincheck.core.investigation import validate_target
from domaincheck.core.link_status import check_bounded, unknown

# Bound network activity, not the size of the user's input.
PROBES = ThreadPoolExecutor(max_workers=5, thread_name_prefix='link-status')


class LinkStatusService:
    def __init__(self, *, checker=None, now=None):
        self.checker = checker or check_bounded
        self.now = now or (lambda: datetime.now(UTC))

    def prepare(self, text):
        targets, invalid, duplicates, groups, seen = [], [], [], {}, set()
        brand = ''
        for line in text.splitlines():
            candidates = re.findall(r'https?://[^\s<>\[\]{}(),;]+|(?<![@\w])(?:[\w-]+\.)+[\w-]{2,63}(?::\d+)?(?:/[^\s<>\[\]{}(),;]*)?', line, re.I)
            if not candidates:
                if line.strip(): brand = line.strip()[:120]
                continue
            for value in candidates:
                try:
                    url, _ = validate_target(value.rstrip('.!\'"'))
                    from urllib.parse import urlsplit, urlunsplit
                    parts = urlsplit(url)
                    host = parts.hostname.encode('idna').decode('ascii')
                    url = urlunsplit((parts.scheme, host + (f':{parts.port}' if parts.port else ''), parts.path, parts.query, ''))
                except (ValueError, UnicodeError): invalid.append('invalid_target'); continue
                if url in seen: duplicates.append(url)
                else: targets.append(url); seen.add(url); groups[url] = brand
        if not targets: raise ValueError('No valid public HTTP(S) domain URLs')
        return {'id': str(uuid4()), 'phase': 'CHECKING', 'urls': targets, 'results': [],
            'processed': 0, 'total': len(targets), 'invalid_count': len(invalid),
            'duplicate_count': len(duplicates), 'started_at': self.now().isoformat(),
            'vantage': 'backend', 'groups': groups}

    async def stream(self, data):
        yield {'type': 'start', 'batch': data}
        work = iter(enumerate(data['urls']))
        results = asyncio.Queue(maxsize=10)
        loop = asyncio.get_running_loop()

        async def worker():
            for index, url in work:
                try:
                    result = await loop.run_in_executor(PROBES, self.checker, url)
                except Exception:
                    result = unknown(url)
                result['group'] = data['groups'].get(url, '')
                await results.put((index, result))

        tasks = [asyncio.create_task(worker()) for _ in range(min(5, len(data['urls'])))]
        processed = 0
        try:
            while processed < data['total']:
                try:
                    index, result = await asyncio.wait_for(results.get(), timeout=10)
                except TimeoutError:
                    yield {'type': 'heartbeat'}
                    continue
                processed += 1
                yield {'type': 'result', 'index': index, 'result': result, 'processed': processed}
            yield {'type': 'complete', 'processed': processed}
        finally:
            for task in tasks: task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
