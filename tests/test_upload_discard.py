"""Upload discard tests run in a subprocess to isolate import-time configuration."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class UploadDiscardTest(unittest.TestCase):
    def test_discard_in_isolated_process(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--isolated"],
                cwd=ROOT, env={**os.environ, "FRAME_DATA_DIR": directory},
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


def run_isolated_tests():
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    import hashlib
    import sqlite3
    from threading import Event
    from unittest.mock import patch
    from uuid import uuid4

    import httpx
    from fastapi import BackgroundTasks, HTTPException, Request

    sys.path.insert(0, str(ROOT))
    import main
    from backend import jobs, uploads
    from backend.schemas import JobCreate

    class DiscardCases(unittest.IsolatedAsyncioTestCase):
        async def asyncSetUp(self):
            self.user_id = str(uuid4())
            self.csrf = uuid4().hex
            self.token = uuid4().hex
            self.other_id = str(uuid4())
            self.other_token = uuid4().hex
            with main.db_connection() as db:
                for user_id, token in ((self.user_id, self.token), (self.other_id, self.other_token)):
                    db.execute("INSERT INTO users(id, username, password_hash) VALUES (?, ?, ?)",
                               (user_id, user_id, "unused-test-password"))
                    db.execute("""INSERT INTO auth_sessions(token_hash, user_id, csrf_token, expires_at)
                        VALUES (?, ?, ?, unixepoch() + 3600)""",
                               (hashlib.sha256(token.encode()).hexdigest(), user_id, self.csrf))
            transport = httpx.ASGITransport(app=main.app)
            self.client = httpx.AsyncClient(transport=transport, base_url="http://test")
            self.client.cookies.set("frame_session", self.token)
            self.headers = {"X-CSRF-Token": self.csrf}

        async def asyncTearDown(self):
            await self.client.aclose()

        async def create_upload(self, payload=b"abc", complete=False):
            response = await self.client.post("/api/uploads", headers=self.headers,
                                              json={"filename": "camera.mp4", "size": len(payload)})
            self.assertEqual(response.status_code, 201)
            upload_id = response.json()["id"]
            response = await self.client.patch(f"/api/uploads/{upload_id}", content=payload,
                                              headers={**self.headers, "Upload-Offset": "0"})
            self.assertEqual(response.status_code, 200)
            if complete:
                response = await self.client.post(f"/api/uploads/{upload_id}/complete", headers=self.headers)
                self.assertEqual(response.status_code, 200)
            return upload_id

        def assert_preserved(self, upload_id, paths):
            with main.db_connection() as db:
                row = db.execute("SELECT * FROM uploads WHERE id = ?", (upload_id,)).fetchone()
                self.assertIsNotNone(row)
            for path, expected in paths.items():
                self.assertEqual(path.read_bytes(), expected)

        def request(self):
            return Request({"type": "http", "state": {"user_id": self.user_id}})

        async def test_auth_scope_partial_complete_and_missing_files(self):
            first = await self.create_upload()
            second = await self.create_upload(b"other", complete=True)
            first_part = main.UPLOAD_DIR / f"{first}.part"
            first_final = main.UPLOAD_DIR / f"{first}.video"
            first_final.write_bytes(b"unlinked-final")
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
                                         base_url="http://test") as visitor:
                self.assertEqual((await visitor.delete(f"/api/uploads/{first}")).status_code, 401)
                visitor.cookies.set("frame_session", self.other_token)
                self.assertEqual((await visitor.delete(f"/api/uploads/{first}", headers=self.headers)).status_code, 404)
            self.assertEqual((await self.client.delete(f"/api/uploads/{first}")).status_code, 403)
            self.assertEqual((await self.client.delete("/api/uploads/not-a-uuid", headers=self.headers)).status_code, 404)
            self.assert_preserved(first, {first_part: b"abc", first_final: b"unlinked-final"})
            result = await self.client.delete(f"/api/uploads/{first}", headers=self.headers)
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json(), {"id": first, "status": "deleted", "cleanup_pending": False})
            self.assertFalse(first_part.exists())
            self.assertFalse(first_final.exists())
            self.assertEqual((await self.client.delete(f"/api/uploads/{first}", headers=self.headers)).status_code, 404)
            self.assert_preserved(second, {main.UPLOAD_DIR / f"{second}.video": b"other"})
            self.assertEqual((await self.client.delete(f"/api/uploads/{second}", headers=self.headers)).status_code, 200)
            self.assertFalse((main.UPLOAD_DIR / f"{second}.video").exists())
            missing = await self.create_upload()
            (main.UPLOAD_DIR / f"{missing}.part").unlink()
            self.assertEqual((await self.client.delete(f"/api/uploads/{missing}", headers=self.headers)).status_code, 200)
            with main.db_connection() as db:
                self.assertIsNotNone(db.execute("SELECT id FROM users WHERE id = ?", (self.user_id,)).fetchone())
                self.assertIsNotNone(db.execute("SELECT token_hash FROM auth_sessions WHERE user_id = ?", (self.user_id,)).fetchone())

        async def test_linked_conversation_is_never_discarded(self):
            upload_id = await self.create_upload(complete=True)
            response = await self.client.post("/api/jobs", headers=self.headers,
                                              json={"upload_id": upload_id, "entities": ["People"]})
            self.assertEqual(response.status_code, 201)
            job_id = response.json()["id"]
            for status in ("queued", "preprocessing", "ready", "failed"):
                with self.subTest(status=status):
                    with main.db_connection() as db:
                        db.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))
                    response = await self.client.delete(f"/api/uploads/{upload_id}", headers=self.headers)
                    self.assertEqual(response.status_code, 409)
                    self.assert_preserved(upload_id, {main.UPLOAD_DIR / f"{upload_id}.video": b"abc"})
                    self.assertEqual((await self.client.get(f"/api/jobs/{job_id}")).status_code, 200)

        async def test_path_guards_before_any_staging(self):
            upload_id = await self.create_upload()
            path = main.UPLOAD_DIR / f"{upload_id}.part"
            with patch("backend.conversation_deletion._is_link", side_effect=lambda candidate: candidate == path):
                self.assertEqual((await self.client.delete(f"/api/uploads/{upload_id}", headers=self.headers)).status_code, 500)
            self.assert_preserved(upload_id, {path: b"abc"})
            outside = main.UPLOAD_DIR.parent / "unrelated"
            outside.mkdir(exist_ok=True)
            unrelated = outside / f"{upload_id}.part"
            unrelated.write_bytes(b"leave-alone")
            # A configured root redirected outside DATA_DIR/videos is rejected.
            nested = outside / "videos"
            nested.mkdir(exist_ok=True)
            with patch.object(uploads, "UPLOAD_DIR", nested):
                self.assertEqual((await self.client.delete(f"/api/uploads/{upload_id}", headers=self.headers)).status_code, 500)
            self.assert_preserved(upload_id, {path: b"abc", unrelated: b"leave-alone"})
            final = main.UPLOAD_DIR / f"{upload_id}.video"
            final.mkdir()
            self.assertEqual((await self.client.delete(f"/api/uploads/{upload_id}", headers=self.headers)).status_code, 500)
            self.assert_preserved(upload_id, {path: b"abc"})
            final.rmdir()

        async def test_staging_and_commit_failures_restore_upload(self):
            upload_id = await self.create_upload()
            part = main.UPLOAD_DIR / f"{upload_id}.part"
            final = main.UPLOAD_DIR / f"{upload_id}.video"
            final.write_bytes(b"final")
            rename = Path.rename

            def reject_second(path, target):
                if path == final:
                    raise PermissionError("injected staging failure")
                return rename(path, target)

            with patch.object(Path, "rename", reject_second):
                self.assertEqual((await self.client.delete(f"/api/uploads/{upload_id}", headers=self.headers)).status_code, 500)
            self.assert_preserved(upload_id, {part: b"abc", final: b"final"})
            connection = uploads.db_connection

            @contextmanager
            def fail_commit():
                with connection() as db:
                    yield db
                    raise sqlite3.OperationalError("injected commit failure")

            with patch.object(uploads, "db_connection", fail_commit):
                self.assertEqual((await self.client.delete(f"/api/uploads/{upload_id}", headers=self.headers)).status_code, 500)
            self.assert_preserved(upload_id, {part: b"abc", final: b"final"})
            self.assertFalse(list(main.UPLOAD_DIR.glob(f".deleted-*-{upload_id}.*")))

        async def test_restore_failure_reports_admin_recovery(self):
            upload_id = await self.create_upload()
            connection = uploads.db_connection
            rename = Path.rename

            @contextmanager
            def fail_commit():
                with connection() as db:
                    yield db
                    raise sqlite3.OperationalError("injected commit failure")

            def reject_restore(path, target):
                if path.name.startswith(".deleted-"):
                    raise PermissionError("injected restore failure")
                return rename(path, target)

            with patch.object(uploads, "db_connection", fail_commit), patch.object(Path, "rename", reject_restore):
                result = await self.client.delete(f"/api/uploads/{upload_id}", headers=self.headers)
            self.assertEqual(result.status_code, 500)
            self.assertIn("administrator recovery", result.json()["detail"])
            self.assert_preserved(upload_id, {})
            quarantined = list(main.UPLOAD_DIR.glob(f".deleted-*-{upload_id}.part"))
            self.assertEqual(len(quarantined), 1)
            self.assertEqual(quarantined[0].read_bytes(), b"abc")

        async def test_purge_warning_after_database_deletion(self):
            upload_id = await self.create_upload()
            unlink = Path.unlink

            def reject_purge(path, *args, **kwargs):
                if path.name.startswith(".deleted-"):
                    raise PermissionError("injected purge failure")
                return unlink(path, *args, **kwargs)

            with patch.object(Path, "unlink", reject_purge):
                result = await self.client.delete(f"/api/uploads/{upload_id}", headers=self.headers)
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json(), {"status": "deleted", "id": upload_id, "cleanup_pending": True})
            self.assertEqual((await self.client.get(f"/api/uploads/{upload_id}")).status_code, 404)
            quarantine = list(main.UPLOAD_DIR.glob(f".deleted-*-{upload_id}.part"))
            self.assertEqual(len(quarantine), 1)
            self.assertEqual(quarantine[0].read_bytes(), b"abc")

        async def test_discard_waits_for_its_active_chunk_only(self):
            upload_id = (await self.client.post("/api/uploads", headers=self.headers,
                                                json={"filename": "slow.mp4", "size": 3})).json()["id"]
            other = await self.create_upload(b"keep")
            started = asyncio.Event()
            release = asyncio.Event()

            async def stream():
                yield b"a"
                started.set()
                await release.wait()
                yield b"bc"

            append = asyncio.create_task(self.client.patch(f"/api/uploads/{upload_id}", content=stream(),
                                        headers={**self.headers, "Upload-Offset": "0"}))
            await asyncio.wait_for(started.wait(), 2)
            discard = asyncio.create_task(self.client.delete(f"/api/uploads/{upload_id}", headers=self.headers))
            try:
                with self.assertRaises(asyncio.TimeoutError):
                    await asyncio.wait_for(asyncio.shield(discard), 0.05)
                unrelated = await asyncio.wait_for(self.client.delete(f"/api/uploads/{other}", headers=self.headers), 2)
                self.assertEqual(unrelated.status_code, 200)
            finally:
                release.set()
            self.assertEqual((await append).status_code, 200)
            self.assertEqual((await discard).status_code, 200)
            self.assertFalse((main.UPLOAD_DIR / f"{upload_id}.part").exists())

        async def test_job_creation_wins_race_and_is_preserved(self):
            upload_id = await self.create_upload(complete=True)
            checked = Event()
            release = Event()
            discard_started = Event()
            get_upload = jobs.get_upload
            connection = uploads.db_connection

            def pause_job(db, identity):
                row = get_upload(db, identity)
                checked.set()
                if not release.wait(3):
                    raise AssertionError("job race release timed out")
                return row

            @contextmanager
            def signal_discard():
                with connection() as db:
                    discard_started.set()
                    yield db

            with ThreadPoolExecutor(max_workers=2) as executor:
                with patch.object(jobs, "get_upload", pause_job), patch.object(uploads, "db_connection", signal_discard):
                    create = executor.submit(jobs.create_job, JobCreate(upload_id=upload_id, entities=["People"]),
                                             BackgroundTasks(), self.request())
                    try:
                        self.assertTrue(await asyncio.to_thread(checked.wait, 2))
                        discard = executor.submit(lambda: asyncio.run(uploads.discard_upload(upload_id, self.request())))
                        self.assertTrue(await asyncio.to_thread(discard_started.wait, 2))
                    finally:
                        release.set()
                    job = await asyncio.wrap_future(create)
                    with self.assertRaises(HTTPException) as error:
                        await asyncio.wrap_future(discard)
                    self.assertEqual(error.exception.status_code, 409)
            self.assert_preserved(upload_id, {main.UPLOAD_DIR / f"{upload_id}.video": b"abc"})
            self.assertEqual((await self.client.get(f"/api/jobs/{job['id']}")).status_code, 200)

    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(DiscardCases))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    if "--isolated" in sys.argv:
        raise SystemExit(run_isolated_tests())
    unittest.main()
