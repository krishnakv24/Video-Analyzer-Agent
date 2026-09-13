"""Jobs for Frame."""

import asyncio
import json
import sqlite3
from uuid import uuid4
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from .video_metadata import inspect_video
from .common import checked_id
from .conversation_deletion import delete_conversation
from .config import UPLOAD_DIR
from .db import db_connection
from .schemas import JobCreate
from .uploads import get_upload

router = APIRouter()
pending_tasks = set()


def preprocess_video(job_id: str):
    """Prepare a local upload in the background; detection is a separate worker."""
    try:
        with db_connection() as db:
            row = db.execute("""SELECT u.id, u.filename, u.size FROM jobs j
                JOIN uploads u ON u.id = j.upload_id WHERE j.id = ?""", (job_id,)).fetchone()
        path = UPLOAD_DIR / f"{row['id']}.video"
        metadata = inspect_video(path, row["filename"], row["size"])
        with db_connection() as db:
            db.execute("UPDATE jobs SET status = 'ready', metadata = ?, error = NULL WHERE id = ?",
                       (json.dumps(metadata), job_id))
    except Exception as exc:
        with db_connection() as db:
            db.execute("UPDATE jobs SET status = 'failed', error = ? WHERE id = ?",
                       (str(exc)[:500], job_id))


async def resume_pending_preprocessing():
    """Resume local jobs left unfinished by a development-server restart."""
    with db_connection() as db:
        rows = db.execute("SELECT id FROM jobs WHERE status IN ('queued', 'preprocessing')").fetchall()
    for row in rows:
        task = asyncio.create_task(asyncio.to_thread(preprocess_video, row["id"]))
        pending_tasks.add(task)
        task.add_done_callback(pending_tasks.discard)


@router.get("/api/jobs")
def list_jobs(request: Request):
    with db_connection() as db:
        rows = db.execute("""SELECT j.id, j.upload_id, j.status, j.created_at, u.filename FROM jobs j
            JOIN uploads u ON u.id = j.upload_id WHERE u.user_id = ?
            ORDER BY j.created_at DESC, j.rowid DESC""", (request.state.user_id,)).fetchall()
    return {"jobs": [dict(row) for row in rows]}


@router.post("/api/jobs", status_code=201)
def create_job(payload: JobCreate, background_tasks: BackgroundTasks, request: Request):
    if not all(entity in {"People", "Cars", "Motorcycles", "Bicycles", "Animals"}
               for entity in payload.entities):
        raise HTTPException(422, "Unsupported entity")
    with db_connection() as db:
        upload = get_upload(db, payload.upload_id)
        if upload["user_id"] != request.state.user_id:
            raise HTTPException(404, "Upload not found")
        if upload["status"] != "complete":
            raise HTTPException(409, "Finish the upload before creating a job")
        if db.execute("SELECT 1 FROM jobs WHERE upload_id = ?", (upload["id"],)).fetchone():
            raise HTTPException(409, "This video already has a session")
        job_id = str(uuid4())
        try:
            db.execute("INSERT INTO jobs(id, upload_id, status, entities, instructions) VALUES (?, ?, 'preprocessing', ?, ?)",
                       (job_id, upload["id"], json.dumps(payload.entities), payload.instructions))
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "This video already has a session") from exc
    background_tasks.add_task(preprocess_video, job_id)
    return {"id": job_id, "session_id": job_id, "upload_id": upload["id"],
            "status": "preprocessing"}


@router.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    with db_connection() as db:
        row = db.execute("SELECT id, upload_id, status, entities, instructions, metadata, error FROM jobs WHERE id = ?",
                         (checked_id(job_id),)).fetchone()
        if row is None:
            raise HTTPException(404, "Job not found")
    return {"id": row["id"], "session_id": row["id"], "upload_id": row["upload_id"],
            "status": row["status"],
            "entities": json.loads(row["entities"]), "instructions": row["instructions"],
            "metadata": json.loads(row["metadata"]) if row["metadata"] else None,
            "error": row["error"]}


@router.delete("/api/jobs/{job_id}")
def delete_job(job_id: str, request: Request):
    return delete_conversation(job_id, request.state.user_id)


def get_session(db, session_id: str):
    row = db.execute("SELECT id FROM jobs WHERE id = ?", (checked_id(session_id),)).fetchone()
    if row is None:
        raise HTTPException(404, "Session not found")
    return row
