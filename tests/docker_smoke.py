"""Isolated persistence smoke test: python3 tests/docker_smoke.py (Linux/WSL).

Requires the existing videolens:local image and Docker Compose. Uses only a
unique Compose project and a temporary /tmp bind directory; never builds.
"""

import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from uuid import uuid4


def main():
    if os.name != "posix" or os.getuid() == 0:
        raise SystemExit("Run with Linux/WSL Python as a non-root Docker user.")
    repo = Path(__file__).resolve().parents[1]
    project = f"videolens-smoke-{uuid4().hex}"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix=project + "-", dir="/tmp") as temporary:
        data = Path(temporary)
        env = dict(os.environ, HOST_DATA_DIR=temporary, APP_UID=str(os.getuid()),
                   APP_GID=str(os.getgid()), BIND_ADDRESS="127.0.0.1",
                   HTTP_PORT=str(port), VIDEOLENS_IMAGE="videolens:local")
        command = ["docker", "compose", "-f", str(repo / "compose.yaml"), "-p", project]

        def compose(*args, stdin=None):
            result = subprocess.run(command + list(args), env=env, cwd=repo,
                                    input=stdin, text=True, capture_output=True, timeout=120)
            if result.returncode:
                raise RuntimeError(f"Compose {args[0]} failed: {result.stderr}")
            return result.stdout.strip()

        cookies = http.cookiejar.CookieJar()
        client = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                            urllib.request.HTTPCookieProcessor(cookies))
        visitor = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        csrf = ""

        def request(path, method="GET", payload=None, headers=None, status=200,
                    authenticated=True, with_csrf=True):
            outgoing = dict(headers or {})
            if authenticated and with_csrf and csrf and method != "GET":
                outgoing["X-CSRF-Token"] = csrf
            if isinstance(payload, dict):
                payload = json.dumps(payload).encode()
                outgoing["Content-Type"] = "application/json"
            req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=payload,
                                         headers=outgoing, method=method)
            try:
                response = (client if authenticated else visitor).open(req, timeout=15)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                raw = response.read()
                assert response.status == status, f"{method} {path}: HTTP {response.status}"
                return json.loads(raw) if "application/json" in response.headers.get("Content-Type", "") else raw

        print(f"Starting isolated project {project}", flush=True)
        try:
            compose("up", "-d", "--no-build", "--wait", "--wait-timeout", "110")
            original_container = compose("ps", "-q", "app")
            identity = compose("exec", "-T", "app", "python", "-c",
                               "import os; print(os.getuid(), os.getgid())")
            assert identity == f"{os.getuid()} {os.getgid()}", identity
            assert "ffprobe version" in compose("exec", "-T", "app", "ffprobe", "-version")
            assert request("/api/health")["status"] == "ok"
            assert b"<!doctype html>" in request("/").lower()
            request("/api/me", status=401, authenticated=False)
            password = secrets.token_urlsafe(32)
            compose("exec", "-T", "app", "python", "manage_users.py", "create", "smoke-user",
                    stdin=f"{password}\n{password}\n")
            user = request("/api/auth/login", "POST", {"username": "smoke-user", "password": password})
            csrf = user["csrf_token"]
            assert cookies and request("/api/me") == user
            request("/api/uploads", "POST", {"filename": "smoke.mp4", "size": 1},
                    status=403, with_csrf=False)
            compose("exec", "-T", "app", "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=blue:s=48x48:r=5", "-t", "1", "-c:v", "mpeg4", "/data/fixture.mp4")
            compose("exec", "-T", "app", "python", "-c",
                    "from PIL import Image; Image.new('RGB', (3, 3), 'red').save('/data/fixture.png')")
            video = (data / "fixture.mp4").read_bytes()
            picture = (data / "fixture.png").read_bytes()
            upload = request("/api/uploads", "POST", {"filename": "smoke.mp4", "size": len(video)}, status=201)
            upload_path = f"/api/uploads/{upload['id']}"
            result = request(upload_path, "PATCH", video, {"Upload-Offset": "0", "Content-Type": "application/octet-stream"})
            assert result["offset"] == len(video)
            assert request(upload_path + "/complete", "POST")["status"] == "complete"
            job = request("/api/jobs", "POST", {"upload_id": upload["id"], "entities": ["People"]}, status=201)
            job_path = f"/api/jobs/{job['id']}"
            deadline = time.monotonic() + 30
            while True:
                ready = request(job_path)
                assert ready["status"] != "failed", ready.get("error")
                if ready["status"] == "ready":
                    break
                assert time.monotonic() < deadline, "Video preparation timed out"
                time.sleep(0.25)
            assert 0.9 <= ready["metadata"]["duration_seconds"] <= 1.1
            assert ready["metadata"]["sha256"] == hashlib.sha256(video).hexdigest()
            stored_video = data / "videos" / f"{upload['id']}.video"
            assert stored_video.read_bytes() == video
            image = request(f"/api/sessions/{job['id']}/images", "POST", picture,
                            {"X-Filename": "smoke.png", "Content-Type": "image/png"}, status=201)
            assert request(image["url"]) == picture
            request(image["url"], status=401, authenticated=False)
            reply = request(job_path + "/messages", "POST",
                            {"content": "What is the filename?", "image_ids": [image["id"]]}, status=201)
            assert "smoke.mp4" in reply["content"]
            history = request(job_path + "/messages")
            assert len(history["messages"]) == 2
            assert history["messages"][0]["images"][0]["id"] == image["id"]
            print("PASS: non-root runtime, ffprobe, static page, auth/CSRF, video, image and chat", flush=True)

            compose("down", "--timeout", "10")
            compose("up", "-d", "--no-build", "--wait", "--wait-timeout", "110")
            assert compose("ps", "-q", "app") != original_container
            assert request("/api/me") == user, "Existing login session did not persist"
            assert request(job_path) == ready
            assert [row["id"] for row in request("/api/jobs")["jobs"]] == [job["id"]]
            assert request(upload_path)["status"] == "complete"
            assert stored_video.read_bytes() == video
            assert request(image["url"]) == picture
            request(image["url"], status=401, authenticated=False)
            assert request(job_path + "/messages") == history
            request(job_path + "/messages", "POST", {"content": "What is the filename?"}, status=201)
            print("PASS: recreated container preserves login/CSRF, job, video bytes, image and chat", flush=True)
        finally:
            compose("down", "--timeout", "10")
    print("PASS: isolated project removed and temporary sample data cleaned", flush=True)


if __name__ == "__main__":
    main()
