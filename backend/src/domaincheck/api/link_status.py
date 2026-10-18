"""Explicit, stateless streaming link checks, independent of mail settings."""
import json
import anyio

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from domaincheck.api.dependencies import require_permission
from domaincheck.application.link_status import LinkStatusService
from domaincheck.application.audit_log import persist

router = APIRouter(tags=['link status'])


class CheckInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=1)


@router.post('/link-status/check')
async def check(body: CheckInput, request: Request,
                actor=Depends(require_permission('link_status.check', csrf=True))):
    svc = LinkStatusService(checker=getattr(request.app.state, 'link_status_checker', None),
        now=request.app.state.clock.now)
    try: data = svc.prepare(body.text)
    except ValueError as exc: raise HTTPException(422, str(exc)) from None

    async def events():
        processed, complete = 0, False
        if getattr(request.state, 'audit_actor', None):
            await persist(request, owner=actor.user_id, actor=actor.user_id,
                action='link_status.started', metadata={'url_count': data['total']},
                event_key=f"link-start:{data['id']}")
        try:
            async for event in svc.stream(data):
                if event['type'] == 'result': processed = event['processed']
                if event['type'] == 'complete': complete = True
                yield json.dumps(event, ensure_ascii=False) + '\n'
        finally:
            if getattr(request.state, 'audit_actor', None):
                with anyio.CancelScope(shield=True):
                    await persist(request, owner=actor.user_id, actor=actor.user_id,
                        action='link_status.completed' if complete else 'link_status.interrupted',
                        outcome='success' if complete else 'interrupted',
                        metadata={'url_count': data['total'], 'processed': processed},
                        event_key=f"link-end:{data['id']}")

    return StreamingResponse(events(), media_type='application/x-ndjson', headers={
        'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})
