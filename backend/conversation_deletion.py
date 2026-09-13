"""Delete one conversation and its media without exposing arbitrary file paths."""

import logging
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException

from .common import checked_id
from .config import DATA_DIR, IMAGE_DIR, UPLOAD_DIR
from .db import db_connection


logger = logging.getLogger(__name__)


def _is_link(path: Path) -> bool:
    # Junction detection is available in Python 3.12; resolve checks also cover
    # redirected parent paths when running earlier supported Python versions.
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def _media_path(root: Path, *parts: str) -> Path:
    """Accept ordinary files under the configured root; reject links and traversal."""
    if root.resolve() != DATA_DIR / root.name or _is_link(root):
        raise ValueError("Storage directory must not be a link")
    path = root
    for part in parts:
        if not part or part in {".", ".."} or "/" in part or "\\" in part or ":" in part:
            raise ValueError("Invalid stored media path")
        path = path / part
        if _is_link(path):
            raise ValueError("Stored media must not be a link")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Stored media is outside its storage directory")
    if path.exists() and not path.is_file():
        raise ValueError("Stored media is not a file")
    return path


def delete_conversation(job_id: str, user_id: str) -> dict:
    """Stage files, commit the SQL deletion, then purge only those staged files.

    Failed staging or SQL commits restore media. A failed final purge leaves
    unreferenced quarantine files and reports cleanup_pending. An abrupt process
    exit between staging and commit still requires operator recovery.
    """
    job_id = checked_id(job_id)
    staged = []
    operation_id = uuid4().hex
    try:
        with db_connection() as db:
            # Image and message writers use the same transaction boundary. Never
            # hold this lock while reading an HTTP request body.
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT j.upload_id, j.status FROM jobs j
                JOIN uploads u ON u.id = j.upload_id
                WHERE j.id = ? AND u.user_id = ?""", (job_id, user_id)).fetchone()
            if row is None:
                raise HTTPException(404, "Conversation not found")
            if row["status"] in {"queued", "preprocessing"}:
                raise HTTPException(409, "Wait for video preparation to finish before deleting this conversation")
            upload_id = str(UUID(row["upload_id"]))
            if upload_id != row["upload_id"]:
                raise ValueError("Invalid stored upload ID")
            paths = [_media_path(UPLOAD_DIR, f"{upload_id}.{suffix}") for suffix in ("video", "part")]
            paths.extend(_media_path(IMAGE_DIR, job_id, image["stored_name"])
                         for image in db.execute("SELECT stored_name FROM images WHERE session_id = ?", (job_id,)))
            # Validate every candidate before moving any media. Renames stay on
            # the same filesystem even when videos and images use separate mounts.
            for path in dict.fromkeys(paths):
                if path.exists():
                    quarantine = path.with_name(f".deleted-{operation_id}-{path.name}")
                    path.rename(quarantine)
                    staged.append((path, quarantine))
            db.execute("DELETE FROM images WHERE session_id = ?", (job_id,))
            db.execute("DELETE FROM messages WHERE job_id = ?", (job_id,))
            db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            db.execute("DELETE FROM uploads WHERE id = ?", (upload_id,))
    except Exception as exc:
        recovery_failed = False
        for path, quarantine in reversed(staged):
            try:
                quarantine.rename(path)
            except OSError:
                recovery_failed = True
                logger.exception("Could not restore conversation media %s from %s", path, quarantine)
        if recovery_failed:
            raise HTTPException(500, "Deletion failed; stored media needs administrator recovery") from exc
        if isinstance(exc, HTTPException):
            raise
        logger.exception("Conversation deletion rolled back for %s", job_id)
        raise HTTPException(500, "Could not delete this conversation; no conversation data was removed") from exc

    cleanup_pending = False
    for _, quarantine in staged:
        try:
            quarantine.unlink()
        except OSError:
            cleanup_pending = True
            logger.exception("Conversation deleted but quarantined media needs cleanup: %s", quarantine)
    # Only remove an empty directory; never recursively remove untracked media.
    directory = IMAGE_DIR / job_id
    try:
        directory.rmdir()
    except OSError:
        pass
    return {"status": "deleted", "id": job_id, "cleanup_pending": cleanup_pending}
