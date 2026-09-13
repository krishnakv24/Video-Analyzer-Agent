"""Authentication and per-user workspace isolation."""
import asyncio
import hashlib
import os
import tempfile
import unittest
from io import BytesIO
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
                    self.assertEqual((await alice.post("/api/auth/change-password", json={"current_password": "wrong", "new_password": "new-long-password"}, headers=ah)).status_code, 401)
                    self.assertEqual((await alice.post("/api/auth/change-password", json={"current_password": "long-test-password", "new_password": "new-long-password"}, headers=ah)).status_code, 200)
                    self.assertEqual((await alice.post("/api/auth/logout", headers=ah)).status_code, 200)
                    self.assertEqual((await alice.get("/api/jobs")).status_code, 401)
                    self.assertEqual((await alice.post("/api/auth/login", json={"username": "alice", "password": "long-test-password"})).status_code, 401)
                    self.assertEqual((await alice.post("/api/auth/login", json={"username": "alice", "password": "new-long-password"})).status_code, 200)
            asyncio.run(flow())


if __name__ == "__main__":
    unittest.main()
