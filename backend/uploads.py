"""Uploads for Frame."""

import asyncio
import os
from pathlib import Path
from uuid import uuid4
from fastapi import APIRouter, HTTPException, Request
from .common import checked_id
from .config import CHUNK_SIZE, UPLOAD_DIR
from .db import db_connection
from .schemas import UploadCreate

router = APIRouter()
upload_lock = asyncio.Lock()


def get_upload(db, upload_id: str):
    row = db.execute("SELECT * FROM uploads WHERE id = ?", (checked_id(upload_id),)).fetchone()
    if row is None:
        raise HTTPException(404, "Upload not found")
    return row


def upload_response(row):
    return {"id": row["id"], "filename": row["filename"], "size": row["size"],
            "offset": row["offset"], "status": row["status"], "chunk_size": CHUNK_SIZE}


@router.get("/api/health")
def health():
    return {"status": "ok"}


@router.post("/api/uploads", status_code=201)
def create_upload(payload: UploadCreate, request: Request):
    upload_id = str(uuid4())
    # The filename is display metadata only; it never becomes a disk path.
    filename = Path(payload.filename.replace("\\", "/")).name
    with db_connection() as db:
        db.execute("INSERT INTO uploads(id, filename, size, status, user_id) VALUES (?, ?, ?, 'uploading', ?)",
                   (upload_id, filename, payload.size, request.state.user_id))
    (UPLOAD_DIR / f"{upload_id}.part").touch(exist_ok=False)
    return {"id": upload_id, "filename": filename, "size": payload.size,
            "offset": 0, "status": "uploading", "chunk_size": CHUNK_SIZE}


@router.get("/api/uploads/{upload_id}")
def upload_status(upload_id: str):
    with db_connection() as db:
        return upload_response(get_upload(db, upload_id))


@router.patch("/api/uploads/{upload_id}")
async def append_upload(upload_id: str, request: Request):
    try:
        client_offset = int(request.headers.get("Upload-Offset", ""))
    except ValueError as exc:
        raise HTTPException(400, "Upload-Offset header must be an integer") from exc
    if client_offset < 0:
        raise HTTPException(400, "Upload-Offset must be nonnegative")
    upload_id = checked_id(upload_id)
    async with upload_lock:
        with db_connection() as db:
            row = get_upload(db, upload_id)
            if row["status"] != "uploading":
                raise HTTPException(409, "Upload is already complete")
            if row["offset"] != client_offset:
                raise HTTPException(409, detail={"expected_offset": row["offset"]})
            path = UPLOAD_DIR / f"{upload_id}.part"
            if not path.exists() or path.stat().st_size < row["offset"]:
                raise HTTPException(500, "Stored upload is missing or incomplete")
            written = 0
            with path.open("r+b") as output:
                output.truncate(row["offset"])
                output.seek(row["offset"])
                try:
                    async for piece in request.stream():
                        written += len(piece)
                        if written > CHUNK_SIZE or row["offset"] + written > row["size"]:
                            output.truncate(row["offset"])
                            raise HTTPException(413, "Chunk exceeds allowed size")
                        output.write(piece)
                except Exception:
                    output.truncate(row["offset"])
                    raise
                if written == 0:
                    raise HTTPException(400, "Empty chunk")
                output.flush()
                os.fsync(output.fileno())
            new_offset = row["offset"] + written
            db.execute("UPDATE uploads SET offset = ? WHERE id = ?", (new_offset, upload_id))
            return {"id": upload_id, "offset": new_offset, "size": row["size"]}


@router.post("/api/uploads/{upload_id}/complete")
async def complete_upload(upload_id: str):
    upload_id = checked_id(upload_id)
    async with upload_lock:
        with db_connection() as db:
            row = get_upload(db, upload_id)
            if row["status"] == "complete":
                return upload_response(row)
            if row["offset"] != row["size"]:
                raise HTTPException(409, "Upload is not complete")
            source = UPLOAD_DIR / f"{upload_id}.part"
            target = UPLOAD_DIR / f"{upload_id}.video"
            if not source.exists() or source.stat().st_size != row["size"]:
                raise HTTPException(500, "Stored file size does not match upload")
            source.replace(target)
            db.execute("UPDATE uploads SET status = 'complete' WHERE id = ?", (upload_id,))
            return {**upload_response(row), "status": "complete"}
