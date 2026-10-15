from __future__ import annotations

import asyncio
import os
import unittest
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from sqlalchemy import delete, select

from domaincheck.application.jobs import JobConflict, JobItemInput, JobService
from domaincheck.domain.jobs import validate_result_metadata
from domaincheck.infrastructure.db.models import Job, SenderAccount, User
from domaincheck.infrastructure.db.repositories import JobRepository
from domaincheck.infrastructure.db.session import Database, UnitOfWork


TEST_DATABASE_URL = os.environ.get("DOMAINCHECK_TEST_DATABASE_URL")


def job(owner: User, *, state: str = "queued", lease_expires_at=None) -> Job:
    return Job(
        owner_user_id=owner.id,
        actor_user_id=owner.id,
        job_type="fixture",
        state=state,
        stage="pending",
        business_day=date(2026, 10, 15),
        timezone="Asia/Bangkok",
        account_scope=[],
        stop_requested=False,
        lease_token="expired-worker" if state == "running" else None,
        lease_expires_at=lease_expires_at,
        result_metadata={},
    )


def item(identity: str, index: int) -> JobItemInput:
    return JobItemInput(identity, index, {"source": "fixture"})


def sender(owner: User, identity: str) -> SenderAccount:
    return SenderAccount(
        owner_user_id=owner.id, label="Fixture", normalized_identity=identity,
        enabled=True, is_default=False, smtp_host="mail.example.test", smtp_port=465,
        smtp_username=identity, smtp_transport="implicit_tls", imap_config={},
        proxy_config={}, signature_overrides={}, secret_metadata={},
    )


class JobMetadataTests(unittest.TestCase):
    def test_checkpoint_metadata_rejects_secret_body_and_oversize_payload(self):
        for key in ("password", "cookie", "token", "body", "authorization"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_result_metadata({"nested": {key: "must-not-persist"}})
        with self.assertRaises(ValueError):
            validate_result_metadata({"message": "x" * 70_000})
        self.assertEqual(
            validate_result_metadata({"status_code": 200, "counts": [1, 2]}),
            {"status_code": 200, "counts": [1, 2]},
        )


@unittest.skipUnless(TEST_DATABASE_URL, "requires isolated PostgreSQL test database")
class JobRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.database = Database(TEST_DATABASE_URL or "")

    async def asyncTearDown(self):
        await self.database.dispose()

    async def test_two_workers_cannot_claim_the_same_job(self):
        async with self.database.session_factory() as session, session.begin():
            owner = User(display_name=f"claim-{uuid4()}", status="active", timezone="Asia/Bangkok")
            session.add(owner)
            await session.flush()
            queued = job(owner)
            session.add(queued)
            await session.flush()
            owner_id, job_id = owner.id, queued.id

        async def claim(token: str):
            async with self.database.session_factory() as session, session.begin():
                claimed = await JobRepository(session).claim_next(
                    worker_token=token, job_types=frozenset({"fixture"})
                )
                return claimed.id if claimed else None

        results = await asyncio.gather(claim("worker-a"), claim("worker-b"))
        self.assertEqual(results.count(job_id), 1)
        self.assertEqual(results.count(None), 1)
        async with self.database.session_factory() as session, session.begin():
            await session.execute(delete(Job).where(Job.id == job_id))
            await session.execute(delete(User).where(User.id == owner_id))

    async def test_expired_lease_can_be_reclaimed_and_active_lease_cannot(self):
        observed = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
        async with self.database.session_factory() as session:
            transaction = await session.begin()
            owner = User(display_name="Lease", status="active", timezone="Asia/Bangkok")
            session.add(owner)
            await session.flush()
            expired = job(owner, state="running", lease_expires_at=observed - timedelta(seconds=1))
            active = job(owner, state="running", lease_expires_at=observed + timedelta(seconds=30))
            session.add_all([expired, active])
            await session.flush()
            claimed = await JobRepository(session).claim_next(worker_token="new-worker", now=observed)
            self.assertEqual(claimed.id, expired.id)
            self.assertEqual(claimed.lease_token, "new-worker")
            self.assertEqual(claimed.version, 2)
            await transaction.rollback()

    async def test_heartbeat_requires_current_token_version_and_unexpired_lease(self):
        observed = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
        async with self.database.session_factory() as session:
            transaction = await session.begin()
            owner = User(display_name="Heartbeat", status="active", timezone="Asia/Bangkok")
            session.add(owner)
            await session.flush()
            running = job(owner, state="running", lease_expires_at=observed + timedelta(seconds=30))
            running.lease_token = "worker"
            session.add(running)
            await session.flush()
            renewed = await JobRepository(session).heartbeat(
                job_id=running.id, worker_token="worker", expected_version=1,
                now=observed, lease_seconds=60,
            )
            self.assertIsNotNone(renewed)
            self.assertEqual(renewed.version, 2)
            stale = await JobRepository(session).heartbeat(
                job_id=running.id, worker_token="worker", expected_version=1,
                now=observed, lease_seconds=60,
            )
            wrong_worker = await JobRepository(session).heartbeat(
                job_id=running.id, worker_token="other", expected_version=2,
                now=observed, lease_seconds=60,
            )
            self.assertIsNone(stale)
            self.assertIsNone(wrong_worker)
            await transaction.rollback()

    async def test_stop_request_is_owner_scoped(self):
        async with self.database.session_factory() as session:
            transaction = await session.begin()
            owner_a = User(display_name="A", status="active", timezone="Asia/Bangkok")
            owner_b = User(display_name="B", status="active", timezone="Asia/Bangkok")
            session.add_all([owner_a, owner_b])
            await session.flush()
            queued = job(owner_b)
            session.add(queued)
            await session.flush()
            repository = JobRepository(session)
            self.assertFalse(await repository.request_stop(owner_a.id, queued.id))
            self.assertFalse(queued.stop_requested)
            self.assertTrue(await repository.request_stop(owner_b.id, queued.id))
            self.assertTrue(queued.stop_requested)
            await transaction.rollback()

    async def test_unit_of_work_commits_and_rolls_back(self):
        committed_id = uuid4()
        async with UnitOfWork(self.database.session_factory) as uow:
            assert uow.session is not None
            uow.session.add(User(
                id=committed_id, display_name="Committed", status="active", timezone="Asia/Bangkok"
            ))
        async with self.database.session_factory() as session:
            self.assertIsNotNone(await session.get(User, committed_id))

        rolled_back_id = uuid4()
        with self.assertRaisesRegex(RuntimeError, "fixture rollback"):
            async with UnitOfWork(self.database.session_factory) as uow:
                assert uow.session is not None
                uow.session.add(User(
                    id=rolled_back_id, display_name="Rolled back", status="active", timezone="Asia/Bangkok"
                ))
                await uow.session.flush()
                raise RuntimeError("fixture rollback")
        async with self.database.session_factory() as session, session.begin():
            self.assertIsNone(await session.get(User, rolled_back_id))
            await session.execute(delete(User).where(User.id == committed_id))

    async def test_service_creates_ordered_durable_items_and_validates_account_owner(self):
        async with self.database.session_factory() as session:
            transaction = await session.begin()
            owner_a = User(display_name="A", status="active", timezone="Asia/Bangkok")
            owner_b = User(display_name="B", status="active", timezone="Asia/Bangkok")
            session.add_all([owner_a, owner_b])
            await session.flush()
            account_a = sender(owner_a, "a@example.test")
            account_b = sender(owner_b, "b@example.test")
            session.add_all([account_a, account_b])
            await session.flush()
            service = JobService(
                session, owner_user_id=owner_a.id, actor_user_id=owner_a.id,
                request_id="create-job",
            )
            snapshot = await service.create_job(
                job_type="fixture_job", business_day=date(2026, 10, 15),
                timezone="Asia/Bangkok", items=[item("hash-b", 1), item("hash-a", 0)],
                sender_account_ids=[account_a.id],
            )
            self.assertEqual([row.input_index for row in snapshot.items], [0, 1])
            restored = await service.get_snapshot(snapshot.job.id)
            self.assertEqual([row.item_identity for row in restored.items], ["hash-a", "hash-b"])
            self.assertEqual(snapshot.job.account_scope, [str(account_a.id)])
            with self.assertRaises(JobConflict):
                await service.create_job(
                    job_type="fixture_job", business_day=date(2026, 10, 15),
                    timezone="Asia/Bangkok", items=[item("cross-owner", 0)],
                    sender_account_ids=[account_b.id],
                )
            await transaction.rollback()

    async def test_worker_checkpoints_each_item_and_finalizes_without_reordering(self):
        observed = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
        async with self.database.session_factory() as session:
            transaction = await session.begin()
            owner = User(display_name="Worker", status="active", timezone="Asia/Bangkok")
            session.add(owner)
            await session.flush()
            created = await JobService(
                session, owner_user_id=owner.id, actor_user_id=owner.id, request_id="worker"
            ).create_job(
                job_type="fixture_job", business_day=date(2026, 10, 15),
                timezone="Asia/Bangkok", items=[item("first", 0), item("second", 1)],
            )
            repository = JobRepository(session)
            claimed = await repository.claim_next(worker_token="worker", now=observed)
            self.assertEqual(claimed.id, created.job.id)
            rows = await repository.list_claimed_items(claimed, worker_token="worker")
            self.assertEqual([row.item_identity for row in rows], ["first", "second"])
            first = await repository.start_item(
                owner_user_id=owner.id, job_id=claimed.id, item_id=rows[0].id,
                worker_token="worker", expected_version=1, stage="checking", now=observed,
            )
            first = await repository.checkpoint_item(
                owner_user_id=owner.id, job_id=claimed.id, item_id=rows[0].id,
                worker_token="worker", expected_version=first.version, state="succeeded",
                stage="checked", result_metadata={"status_code": 200}, now=observed,
            )
            second = await repository.start_item(
                owner_user_id=owner.id, job_id=claimed.id, item_id=rows[1].id,
                worker_token="worker", expected_version=1, stage="checking", now=observed,
            )
            second = await repository.checkpoint_item(
                owner_user_id=owner.id, job_id=claimed.id, item_id=rows[1].id,
                worker_token="worker", expected_version=second.version, state="failed",
                stage="check_failed", result_metadata={"error_code": "fixture"}, now=observed,
            )
            finished = await repository.finalize_job(
                job_id=claimed.id, worker_token="worker", expected_version=claimed.version,
                state="partial", stage="finished", result_metadata={"success": 1, "failed": 1},
                now=observed,
            )
            self.assertEqual(finished.state, "partial")
            restored = await JobService(
                session, owner_user_id=owner.id, actor_user_id=owner.id, request_id="read"
            ).get_snapshot(claimed.id)
            self.assertEqual([row.state for row in restored.items], ["succeeded", "failed"])
            self.assertEqual([row.input_index for row in restored.items], [0, 1])
            await transaction.rollback()

    async def test_stop_is_cooperative_and_resume_keeps_pending_membership(self):
        observed = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
        async with self.database.session_factory() as session:
            transaction = await session.begin()
            owner = User(display_name="Stop", status="active", timezone="Asia/Bangkok")
            session.add(owner)
            await session.flush()
            service = JobService(
                session, owner_user_id=owner.id, actor_user_id=owner.id, request_id="stop"
            )
            created = await service.create_job(
                job_type="fixture_job", business_day=date(2026, 10, 15),
                timezone="Asia/Bangkok", items=[item("active", 0), item("pending", 1)],
            )
            repository = JobRepository(session)
            claimed = await repository.claim_next(worker_token="worker", now=observed)
            active = await repository.start_item(
                owner_user_id=owner.id, job_id=claimed.id, item_id=created.items[0].id,
                worker_token="worker", expected_version=1, stage="checking", now=observed,
            )
            stopped = await service.request_stop(claimed.id)
            self.assertEqual(stopped.job.state, "stopping")
            self.assertIsNone(await repository.start_item(
                owner_user_id=owner.id, job_id=claimed.id, item_id=created.items[1].id,
                worker_token="worker", expected_version=1, stage="checking", now=observed,
            ))
            await repository.checkpoint_item(
                owner_user_id=owner.id, job_id=claimed.id, item_id=active.id,
                worker_token="worker", expected_version=active.version, state="succeeded",
                stage="checked", result_metadata={}, now=observed,
            )
            finalized = await repository.finalize_stop(
                owner_user_id=owner.id, job_id=claimed.id, worker_token="worker",
                expected_version=stopped.job.version,
            )
            self.assertEqual(finalized.state, "stopped")
            resumed = await service.resume(claimed.id)
            self.assertEqual(resumed.job.state, "queued")
            self.assertEqual([row.state for row in resumed.items], ["succeeded", "pending"])
            await transaction.rollback()

    async def test_expired_worker_running_item_becomes_unknown_and_is_not_retryable(self):
        observed = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
        async with self.database.session_factory() as session:
            transaction = await session.begin()
            owner = User(display_name="Recovery", status="active", timezone="Asia/Bangkok")
            session.add(owner)
            await session.flush()
            service = JobService(
                session, owner_user_id=owner.id, actor_user_id=owner.id, request_id="recover"
            )
            created = await service.create_job(
                job_type="fixture_job", business_day=date(2026, 10, 15),
                timezone="Asia/Bangkok", items=[item("uncertain", 0), item("pending", 1)],
            )
            repository = JobRepository(session)
            old_claim = await repository.claim_next(worker_token="old", now=observed)
            await repository.start_item(
                owner_user_id=owner.id, job_id=old_claim.id, item_id=created.items[0].id,
                worker_token="old", expected_version=1, stage="sending", now=observed,
            )
            old_claim.lease_expires_at = observed - timedelta(seconds=1)
            await session.flush()
            new_claim = await repository.claim_next(worker_token="new", now=observed)
            recovered = await repository.recover_interrupted_items(
                job=new_claim, worker_token="new", now=observed
            )
            self.assertEqual(len(recovered), 1)
            self.assertEqual(recovered[0].state, "unknown")
            snapshot = await service.get_snapshot(new_claim.id)
            self.assertEqual([row.state for row in snapshot.items], ["unknown", "pending"])
            with self.assertRaises(JobConflict):
                await service.retry_failed(new_claim.id, {recovered[0].id})
            await transaction.rollback()

    async def test_expired_cooperative_stop_finishes_without_retrying_running_item(self):
        observed = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
        async with self.database.session_factory() as session:
            transaction = await session.begin()
            owner = User(display_name="Stop recovery", status="active", timezone="Asia/Bangkok")
            session.add(owner)
            await session.flush()
            service = JobService(
                session, owner_user_id=owner.id, actor_user_id=owner.id,
                request_id="recover-stop",
            )
            created = await service.create_job(
                job_type="fixture_job", business_day=date(2026, 10, 15),
                timezone="Asia/Bangkok", items=[item("uncertain", 0), item("pending", 1)],
            )
            repository = JobRepository(session)
            claimed = await repository.claim_next(worker_token="old", now=observed)
            await repository.start_item(
                owner_user_id=owner.id, job_id=claimed.id, item_id=created.items[0].id,
                worker_token="old", expected_version=1, stage="sending", now=observed,
            )
            await service.request_stop(claimed.id)
            claimed.lease_expires_at = observed - timedelta(seconds=1)
            await session.flush()

            recovered_jobs = await repository.recover_expired_stops(now=observed)

            self.assertEqual([row.id for row in recovered_jobs], [claimed.id])
            snapshot = await service.get_snapshot(claimed.id)
            self.assertEqual(snapshot.job.state, "stopped")
            self.assertEqual(snapshot.job.stage, "stopped_after_recovery")
            self.assertEqual([row.state for row in snapshot.items], ["unknown", "pending"])
            self.assertIsNone(await repository.claim_next(worker_token="new", now=observed))
            await transaction.rollback()

    async def test_retry_resets_only_selected_failed_item_and_increments_attempt_on_start(self):
        observed = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
        async with self.database.session_factory() as session:
            transaction = await session.begin()
            owner = User(display_name="Retry", status="active", timezone="Asia/Bangkok")
            session.add(owner)
            await session.flush()
            service = JobService(
                session, owner_user_id=owner.id, actor_user_id=owner.id, request_id="retry"
            )
            created = await service.create_job(
                job_type="fixture_job", business_day=date(2026, 10, 15),
                timezone="Asia/Bangkok", items=[item("failed", 0)],
            )
            repository = JobRepository(session)
            claimed = await repository.claim_next(worker_token="first-worker", now=observed)
            running = await repository.start_item(
                owner_user_id=owner.id, job_id=claimed.id, item_id=created.items[0].id,
                worker_token="first-worker", expected_version=1, stage="checking", now=observed,
            )
            failed = await repository.checkpoint_item(
                owner_user_id=owner.id, job_id=claimed.id, item_id=running.id,
                worker_token="first-worker", expected_version=running.version, state="failed",
                stage="check_failed", result_metadata={"error_code": "temporary"}, now=observed,
            )
            await repository.finalize_job(
                job_id=claimed.id, worker_token="first-worker", expected_version=claimed.version,
                state="failed", stage="finished", result_metadata={"failed": 1}, now=observed,
            )
            retry = await service.retry_failed(claimed.id, {failed.id})
            self.assertEqual(retry.items[0].state, "pending")
            self.assertEqual(retry.items[0].attempt, 1)
            second_claim = await repository.claim_next(worker_token="second-worker", now=observed)
            second_attempt = await repository.start_item(
                owner_user_id=owner.id, job_id=second_claim.id, item_id=failed.id,
                worker_token="second-worker", expected_version=retry.items[0].version,
                stage="checking", now=observed,
            )
            self.assertEqual(second_attempt.attempt, 2)
            await transaction.rollback()


if __name__ == "__main__":
    unittest.main()
