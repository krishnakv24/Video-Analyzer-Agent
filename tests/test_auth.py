"""Authentication and per-user workspace isolation."""
import asyncio
import hashlib
import os
import tempfile
import unittest
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
from PIL import Image


class AuthTest(unittest.TestCase):
    def test_accounts_and_sessions(self):
        with tempfile.TemporaryDirectory() as directory:
            os.environ["FRAME_DATA_DIR"] = directory
            import main
            with main.db_connection() as db:
                for name in ("alice", "bob"):
                    db.execute("INSERT INTO users(id, username, password_hash) VALUES (?, ?, ?)",
                               (str(uuid4()), name, main.password_hash.hash("long-test-password")))

            async def flow():
                transport = httpx.ASGITransport(app=main.app)
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as alice, \
                           httpx.AsyncClient(transport=transport, base_url="http://test") as bob:
                    self.assertEqual((await alice.get("/api/jobs")).status_code, 401)
                    self.assertEqual((await alice.post("/api/auth/login", json={"username": "alice", "password": "bad"})).status_code, 401)
                    a = (await alice.post("/api/auth/login", json={"username": "alice", "password": "long-test-password"})).json()
                    b = (await bob.post("/api/auth/login", json={"username": "bob", "password": "long-test-password"})).json()
                    ah = {"X-CSRF-Token": a["csrf_token"]}
                    bh = {"X-CSRF-Token": b["csrf_token"]}
                    first_parallel = (await alice.post("/api/uploads", json={"filename": "first.mp4", "size": 3}, headers=ah)).json()["id"]
                    second_parallel = (await alice.post("/api/uploads", json={"filename": "second.mp4", "size": 3}, headers=ah)).json()["id"]
                    first_stream_waiting = asyncio.Event()
                    release_first_stream = asyncio.Event()

                    async def slow_first_chunk():
                        yield b"a"
                        first_stream_waiting.set()
                        await release_first_stream.wait()
                        yield b"bc"

                    first_task = asyncio.create_task(alice.patch(
                        f"/api/uploads/{first_parallel}", content=slow_first_chunk(),
                        headers={**ah, "Upload-Offset": "0"}))
                    try:
                        await asyncio.wait_for(first_stream_waiting.wait(), 2)
                        second_response = await asyncio.wait_for(alice.patch(
                            f"/api/uploads/{second_parallel}", content=b"xyz",
                            headers={**ah, "Upload-Offset": "0"}), 2)
                        self.assertEqual(second_response.status_code, 200)
                        self.assertEqual(second_response.json()["offset"], 3)
                    finally:
                        release_first_stream.set()
                    self.assertEqual((await first_task).json()["offset"], 3)
                    self.assertEqual((await alice.post("/api/uploads", json={"filename": "a.mp4", "size": 3})).status_code, 403)
                    upload = (await alice.post("/api/uploads", json={"filename": "a.mp4", "size": 3}, headers=ah)).json()
                    uid = upload["id"]
                    self.assertEqual((await bob.get(f"/api/uploads/{uid}")).status_code, 404)
                    self.assertEqual((await bob.patch(f"/api/uploads/{uid}", content=b"abc", headers={**bh, "Upload-Offset": "0"})).status_code, 404)
                    self.assertEqual((await alice.patch(f"/api/uploads/{uid}", content=b"abc", headers={**ah, "Upload-Offset": "0"})).status_code, 200)
                    self.assertEqual((await alice.post(f"/api/uploads/{uid}/complete", headers=ah)).status_code, 200)
                    self.assertEqual((await bob.post("/api/jobs", json={"upload_id": uid, "entities": ["Cars"]}, headers=bh)).status_code, 404)
                    job_response = await alice.post("/api/jobs", json={"upload_id": uid, "entities": ["Cars"]}, headers=ah)
                    self.assertEqual(job_response.status_code, 201)
                    jid = job_response.json()["id"]
                    prepared = (await alice.get(f"/api/jobs/{jid}")).json()
                    self.assertEqual(prepared["metadata"]["sha256"], hashlib.sha256(b"abc").hexdigest())
                    picture = BytesIO()
                    Image.new("RGB", (3, 3), "red").save(picture, format="PNG")
                    uploaded_image = await alice.post(
                        f"/api/sessions/{jid}/images", content=picture.getvalue(),
                        headers={**ah, "X-Filename": "reference.png", "Content-Type": "image/png"})
                    self.assertEqual(uploaded_image.status_code, 201)
                    image = uploaded_image.json()
                    self.assertEqual((await alice.get(image["url"])).status_code, 200)
                    self.assertEqual((await bob.get(image["url"])).status_code, 404)
                    reply = await alice.post(f"/api/jobs/{jid}/messages", json={"content": "What is the filename?", "image_ids": [image["id"]]}, headers=ah)
                    self.assertEqual(reply.status_code, 201)
                    history = (await alice.get(f"/api/jobs/{jid}/messages")).json()["messages"]
                    self.assertEqual(history[0]["images"][0]["id"], image["id"])
                    self.assertEqual([j["id"] for j in (await alice.get("/api/jobs")).json()["jobs"]], [jid])
                    self.assertEqual((await alice.get("/api/jobs")).json()["jobs"][0]["upload_id"], uid)
                    self.assertEqual((await bob.get("/api/jobs")).json()["jobs"], [])
                    for path in (f"/api/jobs/{jid}", f"/api/jobs/{jid}/messages", f"/api/sessions/{jid}/images"):
                        self.assertEqual((await bob.get(path)).status_code, 404)
                    self.assertEqual((await bob.post(f"/api/jobs/{jid}/messages", json={"content": "hi"}, headers=bh)).status_code, 404)
                    for upload_id in (first_parallel, second_parallel):
                        self.assertEqual((await alice.post(f"/api/uploads/{upload_id}/complete", headers=ah)).status_code, 200)
                    parallel_jobs = []
                    for upload_id in (first_parallel, second_parallel):
                        response = await alice.post("/api/jobs", json={"upload_id": upload_id, "entities": ["People"]}, headers=ah)
                        self.assertEqual(response.status_code, 201)
                        parallel_jobs.append(response.json())
                    self.assertEqual(len({job["id"] for job in parallel_jobs}), 2)
                    self.assertEqual(len({job["session_id"] for job in parallel_jobs}), 2)
                    self.assertEqual(len((await alice.get("/api/jobs")).json()["jobs"]), 3)

                    # Deletion is scoped to a single owner and requires CSRF.
                    async with httpx.AsyncClient(transport=transport, base_url="http://test") as anonymous:
                        self.assertEqual((await anonymous.delete(f"/api/jobs/{jid}")).status_code, 401)
                    self.assertEqual((await alice.delete(f"/api/jobs/{jid}")).status_code, 403)
                    self.assertEqual((await bob.delete(f"/api/jobs/{jid}", headers=bh)).status_code, 404)
                    for status in ("preprocessing", "queued"):
                        with main.db_connection() as db:
                            db.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, jid))
                        self.assertEqual((await alice.delete(f"/api/jobs/{jid}", headers=ah)).status_code, 409)
                    with main.db_connection() as db:
                        db.execute("UPDATE jobs SET status = 'ready' WHERE id = ?", (jid,))

                    unattached = await alice.post(f"/api/sessions/{jid}/images", content=picture.getvalue(),
                                                  headers={**ah, "X-Filename": "unsent.png"})
                    self.assertEqual(unattached.status_code, 201)
                    with main.db_connection() as db:
                        media = [main.UPLOAD_DIR / f"{uid}.video"]
                        media.extend(main.IMAGE_DIR / jid / row[0] for row in db.execute(
                            "SELECT stored_name FROM images WHERE session_id = ?", (jid,)))
                        original_counts = {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                                           for table in ("users", "uploads", "jobs", "images", "messages", "auth_sessions")}

                    # Corrupt names must fail before staging even the valid video.
                    with main.db_connection() as db:
                        original_name = db.execute("SELECT stored_name FROM images WHERE id = ?", (image["id"],)).fetchone()[0]
                        db.execute("UPDATE images SET stored_name = '../../frame.sqlite3' WHERE id = ?", (image["id"],))
                    with self.assertLogs("backend.conversation_deletion", level="ERROR"):
                        self.assertEqual((await alice.delete(f"/api/jobs/{jid}", headers=ah)).status_code, 500)
                    self.assertTrue(all(path.exists() for path in media))
                    with main.db_connection() as db:
                        db.execute("UPDATE images SET stored_name = ? WHERE id = ?", (original_name, image["id"]))

                    # Reject linked media without following it, including links to another session.
                    original_is_symlink = Path.is_symlink
                    def linked_image(path):
                        return path == media[1] or original_is_symlink(path)
                    with patch.object(Path, "is_symlink", linked_image), self.assertLogs("backend.conversation_deletion", level="ERROR"):
                        self.assertEqual((await alice.delete(f"/api/jobs/{jid}", headers=ah)).status_code, 500)
                    self.assertTrue(all(path.exists() for path in media))

                    # A locked image after the video was staged restores that video.
                    original_rename = Path.rename
                    def fail_image_rename(path, target):
                        if path == media[1]:
                            raise PermissionError("Test image is locked")
                        return original_rename(path, target)
                    with patch.object(Path, "rename", fail_image_rename), self.assertLogs("backend.conversation_deletion", level="ERROR"):
                        self.assertEqual((await alice.delete(f"/api/jobs/{jid}", headers=ah)).status_code, 500)
                    self.assertTrue(all(path.exists() for path in media))

                    # A failed transaction must restore staged files and every SQL row.
                    import backend.conversation_deletion as deletion
                    @contextmanager
                    def failed_commit():
                        with main.db_connection() as db:
                            yield db
                            raise OSError("Test commit failure")
                    with patch.object(deletion, "db_connection", failed_commit), self.assertLogs("backend.conversation_deletion", level="ERROR"):
                        self.assertEqual((await alice.delete(f"/api/jobs/{jid}", headers=ah)).status_code, 500)
                    self.assertTrue(all(path.exists() for path in media))
                    with main.db_connection() as db:
                        for table, count in original_counts.items():
                            self.assertEqual(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], count)
                    self.assertFalse(list(Path(directory).rglob(".deleted-*")))

                    # An image request already streaming cannot resurrect the deleted session.
                    image_waiting, release_image = asyncio.Event(), asyncio.Event()
                    async def delayed_image():
                        yield picture.getvalue()[:8]
                        image_waiting.set()
                        await release_image.wait()
                        yield picture.getvalue()[8:]
                    image_task = asyncio.create_task(alice.post(f"/api/sessions/{jid}/images",
                                                               content=delayed_image(), headers=ah))
                    try:
                        await asyncio.wait_for(image_waiting.wait(), 2)
                        deleted = await alice.delete(f"/api/jobs/{jid}", headers=ah)
                        self.assertEqual(deleted.status_code, 200)
                        self.assertEqual(deleted.json(), {"id": jid, "status": "deleted", "cleanup_pending": False})
                    finally:
                        release_image.set()
                    self.assertEqual((await image_task).status_code, 404)
                    self.assertTrue(all(not path.exists() for path in media))
                    self.assertFalse((main.IMAGE_DIR / jid).exists())
                    with main.db_connection() as db:
                        for table, column, value in (("uploads", "id", uid), ("jobs", "id", jid),
                                                     ("messages", "job_id", jid), ("images", "session_id", jid)):
                            self.assertEqual(db.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", (value,)).fetchone()[0], 0)
                        for table in ("users", "auth_sessions"):
                            self.assertEqual(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], original_counts[table])
                    self.assertEqual((await alice.get(image["url"])).status_code, 404)
                    self.assertEqual((await alice.delete(f"/api/jobs/{jid}", headers=ah)).status_code, 404)
                    self.assertEqual((await alice.post(f"/api/jobs/{jid}/messages", json={"content": "hi"}, headers=ah)).status_code, 404)
                    remaining_ids = {job["id"] for job in (await alice.get("/api/jobs")).json()["jobs"]}
                    self.assertEqual(remaining_ids, {job["id"] for job in parallel_jobs})
                    for job in parallel_jobs:
                        self.assertTrue((main.UPLOAD_DIR / f"{job['upload_id']}.video").exists())

                    # A purge failure is reported after commit; it cannot break a surviving conversation.
                    purge_job = parallel_jobs[0]
                    original_unlink = Path.unlink
                    def fail_purge(path, *args, **kwargs):
                        if path.name.startswith(".deleted-"):
                            raise PermissionError("Test purge failure")
                        return original_unlink(path, *args, **kwargs)
                    with patch.object(Path, "unlink", fail_purge), self.assertLogs("backend.conversation_deletion", level="ERROR"):
                        purged = await alice.delete(f"/api/jobs/{purge_job['id']}", headers=ah)
                    self.assertEqual(purged.status_code, 200)
                    self.assertTrue(purged.json()["cleanup_pending"])
                    self.assertEqual((await alice.get(f"/api/jobs/{purge_job['id']}")).status_code, 404)
                    self.assertEqual(len(list(main.UPLOAD_DIR.glob(".deleted-*"))), 1)
                    self.assertEqual((await alice.get(f"/api/jobs/{parallel_jobs[1]['id']}")).status_code, 200)
                    self.assertEqual((await alice.post("/api/auth/change-password", json={"current_password": "wrong", "new_password": "new-long-password"}, headers=ah)).status_code, 401)
                    self.assertEqual((await alice.post("/api/auth/change-password", json={"current_password": "long-test-password", "new_password": "new-long-password"}, headers=ah)).status_code, 200)
                    self.assertEqual((await alice.post("/api/auth/logout", headers=ah)).status_code, 200)
                    self.assertEqual((await alice.get("/api/jobs")).status_code, 401)
                    self.assertEqual((await alice.post("/api/auth/login", json={"username": "alice", "password": "long-test-password"})).status_code, 401)
                    self.assertEqual((await alice.post("/api/auth/login", json={"username": "alice", "password": "new-long-password"})).status_code, 200)
            asyncio.run(flow())


if __name__ == "__main__":
    unittest.main()
