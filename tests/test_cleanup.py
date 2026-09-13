"""The cleanup CLI previews changes and preserves accounts by default."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]


class CleanupTest(unittest.TestCase):
    def test_preview_and_execute(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, "FRAME_DATA_DIR": directory}
            upload_id, job_id, user_id = (str(uuid4()) for _ in range(3))
            seed = f"""
from backend.db import db_connection
with db_connection() as db:
    db.execute('INSERT INTO users(id, username, password_hash) VALUES (?, ?, ?)', ({user_id!r}, 'tester', 'hash'))
    db.execute('INSERT INTO uploads(id, filename, size, status, user_id) VALUES (?, ?, ?, ?, ?)', ({upload_id!r}, 'test.mp4', 3, 'complete', {user_id!r}))
    db.execute('INSERT INTO jobs(id, upload_id, status, entities, instructions) VALUES (?, ?, ?, ?, ?)', ({job_id!r}, {upload_id!r}, 'ready', '[]', ''))
"""
            subprocess.run([sys.executable, "-c", seed], cwd=ROOT, env=env, check=True, capture_output=True)
            video = Path(directory) / "videos" / f"{upload_id}.video"
            video.write_bytes(b"abc")
            preview = subprocess.run([sys.executable, "cleanup_data.py"], cwd=ROOT, env=env,
                                     check=True, capture_output=True, text=True)
            self.assertIn("Preview only", preview.stdout)
            self.assertTrue(video.exists())
            applied = subprocess.run([sys.executable, "cleanup_data.py", "--execute"], cwd=ROOT,
                                     env=env, check=True, capture_output=True, text=True)
            self.assertIn("Cleanup complete", applied.stdout)
            self.assertFalse(video.exists())
            verify = "from backend.db import db_connection; db=db_connection(); c=db.__enter__(); print(c.execute('SELECT COUNT(*) FROM users').fetchone()[0], c.execute('SELECT COUNT(*) FROM jobs').fetchone()[0])"
            result = subprocess.run([sys.executable, "-c", verify], cwd=ROOT, env=env,
                                    check=True, capture_output=True, text=True)
            self.assertEqual(result.stdout.strip(), "1 0")
