import unittest
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from domaincheck.application.worker_batches import workspace_state


class WorkerWorkspaceDayTests(unittest.IsolatedAsyncioTestCase):
    async def workspace(self, days, current=None):
        jobs = [SimpleNamespace(id=uuid4(), created_at=datetime(2026, 10, 17, tzinfo=UTC),
            business_day=day, state='completed') for day in days]
        session = SimpleNamespace(scalars=AsyncMock(side_effect=[jobs, []] if jobs else [[]]))
        state = {'current_id': current, 'send_job_id': None, 'phase': 'precheck' if current else 'idle', 'locked': bool(current)}
        with patch('domaincheck.application.worker_batches.lock_batches', new=AsyncMock()), \
             patch('domaincheck.application.worker_batches.recover_owner', new=AsyncMock()), \
             patch('domaincheck.application.worker_batches.current_batch', new=AsyncMock(return_value=state)):
            # Bangkok is already October 18 while UTC remains October 17.
            result = await workspace_state(session, uuid4(), datetime(2026, 10, 17, 18, tzinfo=UTC), 'Asia/Bangkok')
        return result, jobs

    async def test_old_history_is_preserved_without_becoming_current(self):
        result, jobs = await self.workspace([date(2026, 10, 17)])
        self.assertEqual(result['business_day'], '2026-10-18')
        self.assertIsNone(result['current_id'])
        self.assertFalse(result['locked'])
        self.assertEqual(result['history'][0]['id'], str(jobs[0].id))

    async def test_recovers_only_today_when_idle(self):
        result, jobs = await self.workspace([date(2026, 10, 17), date(2026, 10, 18)])
        self.assertEqual(result['current_id'], str(jobs[1].id))

    async def test_active_lock_is_not_discarded_by_day_filter(self):
        current = str(uuid4())
        result, _ = await self.workspace([date(2026, 10, 17)], current)
        self.assertEqual(result['current_id'], current)
        self.assertTrue(result['locked'])
