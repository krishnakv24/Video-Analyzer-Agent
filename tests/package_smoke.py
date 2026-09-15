"""Exercise a generated release: python3 tests/package_smoke.py ARCHIVE.tar.gz.

Run as a non-root Linux/WSL Docker user. Only a unique Compose project and
temporary sample data are removed; the release image and archive are retained.
"""

import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from uuid import uuid4


def main():
    if os.name != "posix" or os.getuid() == 0 or len(sys.argv) != 2:
        raise SystemExit("Usage (non-root Linux/WSL): python3 tests/package_smoke.py ARCHIVE.tar.gz")
    archive = Path(sys.argv[1]).resolve(strict=True)
    assert archive.name.startswith("videolens-") and archive.name.endswith(".tar.gz")
    release = archive.name.removesuffix(".tar.gz")
    expected_image = "videolens:" + release.removeprefix("videolens-")
    project = "videolens-package-smoke-" + uuid4().hex
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix=project + "-", dir="/tmp") as temporary:
        root = Path(temporary)
        expected_files = {"image.tar", "compose.yaml", ".env.example", "install.sh", "README.md"}
        with tarfile.open(archive, "r:gz") as package:
            members = package.getmembers()
            assert {member.name.rstrip("/") for member in members} == {
                release, *(f"{release}/{name}" for name in expected_files)
            }, "Unexpected files outside the packaged image"
            assert len(members) == 6 and all(member.isdir() or member.isfile() for member in members)
            package.extractall(root, filter="data")
        bundle, data, elsewhere = root / release, root / "data", root / "elsewhere"
        data.mkdir()
        elsewhere.mkdir()
        config_file = bundle / ".env"
        settings = {"HOST_DATA_DIR": str(data), "APP_UID": str(os.getuid()),
                    "APP_GID": str(os.getgid()), "BIND_ADDRESS": "127.0.0.1",
                    "HTTP_PORT": str(port), "VIDEOLENS_IMAGE": "videolens:stale-release-not-used"}
        env = {key: value for key, value in os.environ.items()
               if key not in settings and not key.startswith("COMPOSE_")}
        env.update(COMPOSE_PROJECT_NAME=project, HTTP_PORT=str(port), BIND_ADDRESS="127.0.0.1")
        guards, guard_log = root / "guards", root / "host-preparation-attempts"
        guards.mkdir()
        for command in ("sudo", "apt", "apt-get", "curl", "systemctl", "usermod", "groupadd"):
            guard = guards / command
            guard.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$0\" >> '{guard_log}'\n"
                             "printf 'Host preparation is forbidden in this test\\n' >&2\nexit 97\n")
            guard.chmod(0o755)
        env["PATH"] = str(guards) + os.pathsep + env["PATH"]

        def run(command, *, stdin=None, check=True, environment=None):
            result = subprocess.run(command, input=stdin, text=True, capture_output=True,
                                    cwd=elsewhere, env=environment or env, timeout=180)
            if check and result.returncode:
                raise RuntimeError(f"Command failed: {command}\n{result.stdout}\n{result.stderr}")
            return result

        compose = ["docker", "compose", "--project-directory", str(bundle),
                   "--env-file", str(config_file), "-f", str(bundle / "compose.yaml"), "-p", project]
        cookies = http.cookiejar.CookieJar()
        client = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                            urllib.request.HTTPCookieProcessor(cookies))

        def request(path, payload=None):
            body = json.dumps(payload).encode() if payload is not None else None
            req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body,
                                         headers={"Content-Type": "application/json"})
            with client.open(req, timeout=15) as response:
                raw = response.read()
                return json.loads(raw) if "application/json" in response.headers.get("Content-Type", "") else raw

        def container_id():
            return run(compose + ["ps", "-q", "app"]).stdout.strip()

        def install(*args):
            run(["bash", str(bundle / "install.sh"), *args])
            assert not guard_log.exists(), "Installer attempted host preparation despite working Docker"
            identity = container_id()
            assert identity, "The isolated Compose project has no app container"
            state = run(["docker", "inspect", "--format", "{{.Config.Image}} {{.State.Health.Status}}", identity])
            assert state.stdout.strip() == f"{expected_image} healthy", state.stdout
            assert request("/api/health")["status"] == "ok"
            assert b"<!doctype html>" in request("/").lower()
            return identity

        print(f"Testing {archive.name} as isolated project {project}", flush=True)
        try:
            run(["docker", "info"])
            run(["docker", "compose", "version"])
            incomplete = root / "incomplete"
            incomplete.mkdir()
            (incomplete / "install.sh").write_bytes((bundle / "install.sh").read_bytes())
            missing = run(["bash", str(incomplete / "install.sh"), str(data)], check=False)
            assert missing.returncode != 0 and not (incomplete / ".env").exists()
            assert not config_file.exists()
            assert not guard_log.exists(), "Missing bundle triggered host preparation"
            assert not run(["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"]).stdout.strip()
            print("PASS: five bundle files; missing bundle refuses installation without changing the host", flush=True)

            first_container = install(str(data))
            repo = Path(__file__).resolve().parents[1]
            sources = [repo / name for name in ("main.py", "manage_users.py", "cleanup_data.py")]
            sources += [path for folder in ("backend", "frontend") for path in (repo / folder).rglob("*")]
            expected_hashes = {}
            for path in sources:
                relative = path.relative_to(repo)
                if (not path.is_file() or relative.as_posix() == "frontend/README.md"
                        or {"__pycache__", "node_modules"}.intersection(relative.parts)
                        or path.suffix in {".pyc", ".pyo"} or path.name == ".env" or path.name.startswith(".env.")):
                    continue
                expected_hashes[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
            probe = ("import hashlib,json,sys; from pathlib import Path; "
                     "import fastapi,uvicorn,PIL,pwdlib,argon2,httpx; "
                     "print(json.dumps({p:hashlib.sha256((Path('/app')/p).read_bytes()).hexdigest() "
                     "for p in json.load(sys.stdin)}))")
            actual_hashes = json.loads(run(compose + ["exec", "-T", "app", "python", "-c", probe],
                                           stdin=json.dumps(list(expected_hashes))).stdout)
            assert actual_hashes == expected_hashes, [name for name in expected_hashes
                                                       if actual_hashes.get(name) != expected_hashes[name]]
            assert "ffprobe version" in run(compose + ["exec", "-T", "app", "ffprobe", "-version"]).stdout
            image_format = '{"command":{{json .Config.Cmd}},"labels":{{json .Config.Labels}},"layers":{{json .RootFS.Layers}}}'
            image_info = json.loads(run(["docker", "image", "inspect", expected_image, "--format", image_format]).stdout)
            command = image_info["command"]
            assert "uvicorn" in command and "main:app" in command and "--reload" not in command
            assert command.count("--workers") == 1 and command[command.index("--workers") + 1] == "1"
            labels = image_info["labels"] or {}
            base_image = labels.get("org.videolens.base-image")
            if labels.get("org.videolens.image-kind") == "application":
                assert base_image, "Application image does not identify its runtime base"
            if base_image:
                base_layers = json.loads(run(["docker", "image", "inspect", base_image,
                                              "--format", "{{json .RootFS.Layers}}"]).stdout)
                assert base_layers and image_info["layers"][:len(base_layers)] == base_layers
                print(f"PASS: release shares all {len(base_layers)} layers with loaded base {base_image}", flush=True)
            print(f"PASS: {len(expected_hashes)} current source files match; dependency imports, ffprobe and one-worker CMD", flush=True)
            generated = dict(line.split("=", 1) for line in config_file.read_text().splitlines()
                             if line and not line.startswith("#") and "=" in line)
            for key in ("HOST_DATA_DIR", "APP_UID", "APP_GID", "HTTP_PORT", "BIND_ADDRESS"):
                assert generated[key].strip("'\"") == settings[key], (key, generated[key])
            env.pop("HTTP_PORT")
            env.pop("BIND_ADDRESS")
            with config_file.open("a") as config:
                config.write("\nVIDEOLENS_IMAGE=videolens:stale-release-not-used\n")
            config_before = config_file.read_bytes()
            rendered = json.loads(run(compose + ["config", "--format", "json"]).stdout)
            assert rendered["name"] == project and set(rendered["services"]) == {"app"}
            app = rendered["services"]["app"]
            assert app["image"] == expected_image, "Stale .env image overrode the bundled release"
            assert app["user"] == f"{os.getuid()}:{os.getgid()}"
            assert app["volumes"][0]["source"] == str(data) and app["volumes"][0]["target"] == "/data"
            password = secrets.token_urlsafe(32)
            run(compose + ["exec", "-T", "app", "python", "manage_users.py", "create", "package-smoke-user"],
                stdin=f"{password}\n{password}\n")
            user = request("/api/auth/login", {"username": "package-smoke-user", "password": password})
            assert cookies and request("/api/me") == user
            markers = {data / "videos" / "package-smoke.video": secrets.token_bytes(257),
                       data / "images" / "package-smoke.image": secrets.token_bytes(127)}
            for path, content in markers.items():
                path.write_bytes(content)
            assert (data / "frame.sqlite3").is_file()
            print("PASS: install.sh creates .env and starts the pinned image; health, static page and CLI login work", flush=True)

            def preserved():
                assert config_file.read_bytes() == config_before, "Existing .env was modified"
                assert request("/api/me") == user, "Existing account/login/CSRF session was lost"
                assert all(path.read_bytes() == content for path, content in markers.items()), "Existing media was changed"

            conflict = root / "conflicting-data"
            rejected = run(["bash", str(bundle / "install.sh"), str(conflict)], check=False)
            assert rejected.returncode != 0 and not conflict.exists(), "Conflicting data path was accepted"
            assert container_id() == first_container
            preserved()
            assert install() == first_container, "Unchanged release unnecessarily recreated the app"
            preserved()
            print("PASS: conflicting data argument refused; reinstall preserves container, .env, login and media", flush=True)
            run(compose + ["down", "--timeout", "10"])
            assert install() != first_container
            preserved()
            print("PASS: removing and recreating the container preserves .env, login and media", flush=True)
        finally:
            # Explicit dummy settings allow scoped cleanup even before .env exists.
            cleanup = ["docker", "compose", "--env-file", "/dev/null", "-f", str(bundle / "compose.yaml"),
                       "-p", project, "down", "--timeout", "10"]
            run(cleanup, environment={**env, **settings})
    print("PASS: isolated project and temporary data removed; release image/archive retained", flush=True)


if __name__ == "__main__":
    main()
