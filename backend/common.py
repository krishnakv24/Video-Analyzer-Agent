"""Common for Frame."""

from uuid import UUID
from fastapi import HTTPException


def checked_id(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise HTTPException(404, "Upload or job not found") from exc
