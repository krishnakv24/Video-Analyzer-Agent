"""Images for Frame."""

from io import BytesIO
from pathlib import Path
from urllib.parse import unquote
from uuid import uuid4
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError
from .common import checked_id
from .config import IMAGE_DIR, MAX_IMAGE_SIZE
from .db import db_connection
from .jobs import get_session

router = APIRouter()


def image_response(row):
    return {"id": row["id"], "session_id": row["session_id"],
            "filename": row["filename"], "size": row["size"],
            "media_type": row["media_type"],
            "message_id": row["message_id"],
            "url": f"/api/sessions/{row['session_id']}/images/{row['id']}"}


@router.get("/api/sessions/{session_id}/images")
def list_images(session_id: str):
    with db_connection() as db:
        get_session(db, session_id)
        rows = db.execute("SELECT * FROM images WHERE session_id = ? ORDER BY created_at, id",
                          (session_id,)).fetchall()
    return {"images": [image_response(row) for row in rows]}


@router.post("/api/sessions/{session_id}/images", status_code=201)
async def upload_image(session_id: str, request: Request):
    session_id = checked_id(session_id)
    with db_connection() as db:
        get_session(db, session_id)
    raw = bytearray()
    async for piece in request.stream():
        raw.extend(piece)
        if len(raw) > MAX_IMAGE_SIZE:
            raise HTTPException(413, "Image exceeds 20 MiB limit")
    if not raw:
        raise HTTPException(400, "Empty image")
    try:
        with Image.open(BytesIO(raw)) as image:
            if image.width * image.height > 50_000_000:
                raise HTTPException(413, "Image dimensions are too large")
            image.verify()
            format_name = image.format
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise HTTPException(415, "Invalid image") from exc
    allowed = {"PNG": ("png", "image/png"), "JPEG": ("jpg", "image/jpeg"),
               "WEBP": ("webp", "image/webp"), "GIF": ("gif", "image/gif")}
    if format_name not in allowed:
        raise HTTPException(415, "Use PNG, JPEG, WebP, or GIF")
    extension, media_type = allowed[format_name]
    image_id = str(uuid4())
    stored_name = f"{image_id}.{extension}"
    directory = IMAGE_DIR / session_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / stored_name
    path.write_bytes(raw)
    filename = Path(unquote(request.headers.get("X-Filename", "image")).replace("\\", "/")).name[:255]
    try:
        with db_connection() as db:
            db.execute("""INSERT INTO images(id, session_id, filename, stored_name, media_type, size)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (image_id, session_id, filename, stored_name, media_type, len(raw)))
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return {"id": image_id, "session_id": session_id, "filename": filename,
            "size": len(raw), "media_type": media_type,
            "url": f"/api/sessions/{session_id}/images/{image_id}"}


@router.get("/api/sessions/{session_id}/images/{image_id}")
def get_image(session_id: str, image_id: str):
    with db_connection() as db:
        row = db.execute("SELECT * FROM images WHERE id = ? AND session_id = ?",
                         (checked_id(image_id), checked_id(session_id))).fetchone()
        if row is None:
            raise HTTPException(404, "Image not found")
    return FileResponse(IMAGE_DIR / session_id / row["stored_name"], media_type=row["media_type"])
