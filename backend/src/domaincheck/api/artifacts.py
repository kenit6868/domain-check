from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from domaincheck.api.dependencies import current_user, get_db_session
from domaincheck.application.auth import AuthenticatedUser
from domaincheck.domain.artifacts import can_read_artifact
from domaincheck.infrastructure.db.repositories import ArtifactRepository
from domaincheck.ports.artifacts import ArtifactIntegrityError


router = APIRouter(tags=["artifacts"])


@router.get("/artifacts/{artifact_id}")
async def download_artifact(
    artifact_id: UUID,
    request: Request,
    actor: AuthenticatedUser = Depends(current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    artifact = await ArtifactRepository(session, actor.user_id).get(artifact_id)
    if artifact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artifact not found")
    if actor.status != "active" or not can_read_artifact(artifact.kind, actor.permissions):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Permission denied")
    try:
        content = request.app.state.artifact_store.read_verified(
            storage_key=artifact.storage_key,
            expected_size=artifact.byte_size,
            expected_sha256=artifact.sha256,
        )
    except (ArtifactIntegrityError, FileNotFoundError):
        raise HTTPException(status.HTTP_409_CONFLICT, "Artifact is unavailable or failed integrity validation")

    extension = ".png" if artifact.mime_type == "image/png" else ".jpg" if artifact.mime_type == "image/jpeg" else ".bin"
    return Response(
        content=content,
        media_type=artifact.mime_type,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'inline; filename="artifact-{artifact.id}{extension}"',
            "Content-Security-Policy": "sandbox; default-src 'none'",
            "X-Content-Type-Options": "nosniff",
        },
    )
