"""Shared SMTP side effect after a durable delivery intent has committed."""
import asyncio

from domaincheck.application.deliveries import DeliveryService


async def dispatch_intent(sessions, store, sender, envelope, *, owner, actor, intent, spec,
                          message, now, sent_copier=None):
    try:
        outcome, stage, reason = await asyncio.to_thread(sender.send, spec, message)
    except Exception:
        outcome, stage, reason = 'unknown', 'send_message', 'transport_outcome_uncertain'
    async with sessions() as session, session.begin():
        await DeliveryService(session, store, owner_user_id=owner, actor_user_id=actor,
            request_id='report-send').checkpoint(intent.delivery_id, attempt_id=intent.attempt_id,
            claim_token=intent.claim_token, outcome=outcome, smtp_stage=stage,
            result={'reason_code': reason, 'evidence_source': 'browser_or_manual'}, now=now())
    if outcome == 'sent' and sent_copier is not None:
        from domaincheck.application.sent_copy import SentCopyService
        await SentCopyService(sessions, store, sent_copier, envelope).save(
            owner, actor, intent.delivery_id, message=message)
    return outcome
