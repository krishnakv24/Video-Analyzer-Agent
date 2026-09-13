"""Chat for Frame."""

import json
from fastapi import APIRouter, HTTPException
from .common import checked_id
from .db import db_connection
from .images import image_response
from .schemas import ChatMessage

router = APIRouter()


@router.get("/api/jobs/{job_id}/messages")
def list_messages(job_id: str):
    with db_connection() as db:
        job = db.execute("SELECT id FROM jobs WHERE id = ?", (checked_id(job_id),)).fetchone()
        if job is None:
            raise HTTPException(404, "Job not found")
        rows = db.execute("SELECT id, role, content, created_at FROM messages WHERE job_id = ? ORDER BY id",
                          (job_id,)).fetchall()
        images = db.execute("SELECT * FROM images WHERE session_id = ? AND message_id IS NOT NULL ORDER BY created_at, id",
                            (job_id,)).fetchall()
    by_message = {}
    for image in images:
        by_message.setdefault(image["message_id"], []).append(image_response(image))
    return {"messages": [{**dict(row), "images": by_message.get(row["id"], [])} for row in rows]}


@router.post("/api/jobs/{job_id}/messages", status_code=201)
def send_message(job_id: str, payload: ChatMessage):
    job_id = checked_id(job_id)
    question = payload.content.strip()
    if not question and not payload.image_ids:
        raise HTTPException(422, "Add a question or an image")
    if len(set(payload.image_ids)) != len(payload.image_ids):
        raise HTTPException(422, "Duplicate image attachment")
    image_ids = [checked_id(value) for value in payload.image_ids]
    with db_connection() as db:
        # Serialize the final existence check and inserts with conversation deletion.
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT status, metadata, entities FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Job not found")
        if row["status"] != "ready":
            raise HTTPException(409, "Chat is available after preprocessing finishes")
        attachments = []
        for image_id in image_ids:
            image = db.execute("SELECT * FROM images WHERE id = ? AND session_id = ? AND message_id IS NULL",
                               (image_id, job_id)).fetchone()
            if image is None:
                raise HTTPException(422, "Image is missing, belongs to another session, or is already attached")
            attachments.append(image)
        metadata = json.loads(row["metadata"])
        lower = question.lower()
        if "how many images" in lower or "image count" in lower:
            count = db.execute("SELECT COUNT(*) FROM images WHERE session_id = ?", (job_id,)).fetchone()[0]
            answer = f"This session has {count} uploaded image{'s' if count != 1 else ''}. Image understanding is not connected yet."
        elif "filename" in lower or "file name" in lower or "name of the video" in lower:
            answer = f"The uploaded video is {metadata['filename']}."
        elif "file size" in lower or "how large" in lower:
            answer = f"The file size is {metadata['size'] / (1024 ** 3):.2f} GiB."
        elif "duration" in lower or "video length" in lower:
            answer = (f"The duration is {metadata['duration_seconds'] / 3600:.2f} hours."
                      if "duration_seconds" in metadata else
                      "Duration is unavailable because ffprobe is not installed on the server.")
        elif "selected filter" in lower or "selected entit" in lower:
            answer = f"The selected filters are: {', '.join(json.loads(row['entities']))}."
        else:
            answer = "I have your question and attached images, but video and image understanding are not connected yet. I cannot determine whether a person appears in the video."
        cursor = db.execute("INSERT INTO messages(job_id, role, content) VALUES (?, 'user', ?)",
                            (job_id, question))
        message_id = cursor.lastrowid
        for image_id in image_ids:
            db.execute("UPDATE images SET message_id = ? WHERE id = ?", (message_id, image_id))
        answer_id = db.execute("INSERT INTO messages(job_id, role, content) VALUES (?, 'assistant', ?)",
                               (job_id, answer)).lastrowid
    return {"id": answer_id, "role": "assistant", "content": answer, "images": [],
            "user_message": {"id": message_id, "role": "user", "content": question,
                             "images": [{**image_response(image), "message_id": message_id}
                                        for image in attachments]}}
