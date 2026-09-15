# Deploy VideoLens with Docker Compose

Verified on 2026-09-14: this machine has WSL 2, Ubuntu 24.04.2, Docker Engine 29.5.2, and Docker Compose v5.1.4 working inside Ubuntu. Use that existing engine; Docker Desktop and another Docker installation are unnecessary here. The image uses **Ubuntu 24.04**, installs Python 3.12 plus FFmpeg/`ffprobe`, and packages FastAPI, the static frontend, and the Python dependencies from `requirements.txt`. It creates its own Linux virtual environment at `/opt/venv`. No Windows `.venv`, CUDA, or GPU is needed for the current frontend/backend. Docker Compose is the deployment path for both initial testing and the Ubuntu server. The Multiagent Service remains deferred.

Validation on the WSL environment above: **passed**. The image built successfully, all three backend unittest suites passed inside it, and the [Docker smoke test](../../tests/docker_smoke.py) verified non-root execution, `ffprobe`, static pages, login/CSRF, video upload, image attachments, chat, and persistence after full container recreation. Tests used isolated temporary storage and removed their sample containers/data; existing development data was untouched. Real 24-hour-video throughput and native-server networking still require deployment-specific checks.

To repeat the smoke test after building `videolens:local`, run `python3 tests/docker_smoke.py` from Ubuntu. It requires Docker access, creates its own temporary Compose project and data directory, and does not use your configured application data.

## What happens when the container starts

The [base Dockerfile](../../Dockerfile.base) installs Ubuntu packages, Python, FFmpeg, and the dependencies from `requirements.txt` when the reusable base is created. The [application Dockerfile](../../Dockerfile) starts from that prepared base and copies the current `main.py`, `backend/`, `frontend/`, `manage_users.py`, and `cleanup_data.py` into `/app`. It sets the Uvicorn startup command and health check. Application packaging contains no package-installation commands; starting the container launches FastAPI automatically.

`docker image load` imports the saved image and its tags. The release installer handles this import and starts the container through Compose, which applies the port mapping, persistent storage, and restart policy. [Docker image load](https://docs.docker.com/reference/cli/docker/image/load/), [Docker container run](https://docs.docker.com/reference/cli/docker/container/run/).

## Package in WSL and install on Ubuntu

The workflow separates dependency preparation from application releases:

```text
Create base once:   Ubuntu + Python + OS/Python libraries -> saved base image
For each release:   load saved base + copy latest code + startup command -> final archive
On Ubuntu server:  install.sh -> load final image -> start service
```

Rebuild the base when `requirements.txt` or `Dockerfile.base` changes, or when deliberately refreshing the base's packages. Ordinary frontend/backend code changes only need packaging.

| Script | Purpose | Run on |
| --- | --- | --- |
| [build_base.sh](../../build_base.sh) | Build all runtime dependencies and export the reusable base | WSL Ubuntu or a Linux build machine with Docker |
| [package.sh](../../package.sh) | Load the base archive, copy current application files, and export the final release | Same build machine |
| [install.sh](../../install.sh) | Prepare Docker, configuration and storage; load the image and start the application | Extracted release directory on Ubuntu |
| [setup.sh](../../setup.sh) | Prepare a Python virtual environment for development | Developer machine, when running without Docker |

From the repository in WSL, create the dependency base first:

```bash
bash build_base.sh 1.0.0
```

This builds `videolens-base:1.0.0` using `Dockerfile.base` and exports **`dist/videolens-base-1.0.0.tar`**. The base contains libraries and runtime configuration, with no application code. This step needs internet access for Ubuntu and Python packages.

After updating the application code, package a release using that saved base:

```bash
bash package.sh 0.1.2 dist/videolens-base-1.0.0.tar
```

The second argument defaults to that base archive when omitted. Packaging loads the archive into the local Docker engine, verifies its dependency/base-file fingerprints, and builds the final image by copying the current source and setting startup configuration. It selects Docker's local default builder with pull disabled. The final Dockerfile has no `RUN` instruction, and no `apt` or `pip` installation occurs during packaging.

The base archive must contain exactly one tagged runtime-base image, as exported by `build_base.sh`. Its tag is read from the load result. If the dependency list or base Dockerfile changed, packaging stops and asks for a new base version. For example, build base `1.1.0` and pass `dist/videolens-base-1.1.0.tar` to the next package command. Base archives remain separate from application releases.

This creates **`dist/videolens-0.1.2.tar.gz`**. Without an application version argument, `package.sh` uses a UTC timestamp. It refuses to overwrite an existing archive. The image contains the application and runtime dependencies; `.dockerignore` excludes uploaded files, the database, local environments, and credentials. The final `image.tar` contains both the inherited base layers and the updated application, so the Ubuntu deployment host does not need the separate base archive. The release directory contains:

```text
videolens-0.1.2/
  image.tar
  compose.yaml
  .env.example
  install.sh
  README.md
```

Copy the archive to Ubuntu, then run:

```bash
tar -xzf videolens-0.1.2.tar.gz
cd videolens-0.1.2
sudo bash install.sh
```

The installer completes these steps in one invocation:

1. Verify that the release contains its image and Compose configuration.
2. Reuse working Docker Engine and Compose, or install them from the [official Docker Ubuntu repository](https://docs.docker.com/engine/install/ubuntu/#install-using-the-apt-repository). Fresh host setup requires root, systemd, and internet access. Incomplete or conflicting installations require operator repair.
3. Create `/srv/videolens/data` if missing. Existing files and ownership are preserved. For a new directory, ownership follows the account invoking sudo, with UID/GID 1000 as the root-account fallback.
4. Generate `.env` when missing, with the data directory, its owner UID/GID, port 8001, and loopback binding. Existing `.env` settings are reused without rewriting the file or executing it as shell code.
5. Load the image and start Compose with no rebuild or registry pull. Wait up to 120 seconds for application health, displaying status and bounded logs if startup fails. [Compose startup options](https://docs.docker.com/reference/cli/docker/compose/up/).

The Dockerfile's Uvicorn command starts FastAPI, which creates SQLite and the `videos/` and `images/` subdirectories under the mounted data directory. The host does not need Python, a SQLite server, or GPU packages for this application.

For a custom data directory on the first install:

```bash
sudo bash install.sh /path/to/data
```

For intended LAN access on the first install:

```bash
sudo env BIND_ADDRESS=0.0.0.0 HTTP_PORT=8001 bash install.sh
```

Open `http://localhost:8001/` locally, or `http://<server-IP>:8001/` for configured LAN access. WSL access from other devices also depends on WSL networking and host firewall settings described below. To change the binding or port after installation, edit `.env` and rerun `install.sh`. Existing `.env` settings take precedence over these first-install environment options. A conflicting data-directory argument is rejected to avoid silently switching storage.

Create an account once for a new installation; the command prompts for a password:

```bash
sudo docker compose exec app python manage_users.py create admin --admin
```

If your account already has access to a running Docker engine and can write to the release and data directories, `bash install.sh` can run without sudo. It does not change host services or install packages when Docker is already working. For a WSL test with such an account, use `bash install.sh "$HOME/videolens-data"` from the extracted release directory.

The bundled Compose file pins the release image, so an older `.env` cannot select an old image tag. For an update, extract into a separate release directory, copy the existing `.env` into it, and run its `install.sh`. Keep the same Compose project name (`videolens`) and host data directory so Compose replaces the existing service. Updates briefly interrupt the application. Back up or migrate the full data directory using the stopped-service procedure below before changing storage. A saved Linux image targets the CPU architecture printed during packaging and in its README; the destination host must support that architecture.

The [base packaging checks](../../tests/base_package_smoke.py), run with `python3 tests/base_package_smoke.py`, use a fake Docker command to test load/build ordering, stale dependency rejection, and failure cleanup without accessing an engine. The [package smoke test](../../tests/package_smoke.py) checks an extracted archive in an isolated Docker project: `python3 tests/package_smoke.py dist/videolens-0.1.2.tar.gz`. It compares packaged application files with the current source, verifies library imports and inherited base layers, and checks installation and persistence. The [installer smoke test](../../tests/install_smoke.py), run with `python3 tests/install_smoke.py`, simulates host setup and failure paths in disposable Ubuntu containers with no network or Docker socket. These tests require developer Python; the image tests also require Docker access, and the installer test uses `videolens:local`. A real first-time Docker installation on the target Ubuntu machine remains a deployment check.

Validated on 2026-09-15 in WSL Ubuntu: base `1.0.0` and release `0.1.2` were built successfully. Packaging loaded the saved base and added application files and runtime configuration without installing dependencies. All 20 packaged source files matched the workspace, and all five base layers were retained. Required imports, FFprobe, the single-worker startup command, installation, login, and persistence checks passed, along with eight mocked packaging checks and three backend unittest suites. Tests used isolated containers and temporary data; they did not modify the existing application service, user data, or host software.

## Run directly from source in WSL

Run these commands in the Ubuntu terminal:

```bash
docker version
docker compose version
cd /mnt/c/Projects/Anlayser/Video-Analyzer-Agent
# If the saved base is not already loaded:
docker image load --input dist/videolens-base-1.0.0.tar
mkdir -p "$HOME/videolens-data"
if [ ! -e .env ]; then cp .env.example .env; fi
id -u
id -g
printf '%s\n' "$HOME/videolens-data"
nano .env
```

Create the base with `build_base.sh` first if its archive does not exist. Compose source builds use `videolens-base:1.0.0` by default; set `BASE_IMAGE` in `.env` to use another already-loaded base tag. Use `package.sh` for releases so it verifies the base matches current dependency definitions.

Set `HOST_DATA_DIR` to the absolute Linux path printed above. Set `APP_UID` and `APP_GID` to the two `id` results; both are currently `1000`. Keep `HTTP_PORT=8001` and `BIND_ADDRESS=127.0.0.1` for local use. The directory must exist and be writable by that UID/GID before startup. The container does not change ownership of existing host files.

Keep storage in Ubuntu's Linux filesystem for large uploads and SQLite. Building the source from `/mnt/c/Projects/...` is supported; the bind-mounted data should use the Linux path configured above. [Docker WSL filesystem guidance](https://docs.docker.com/desktop/features/wsl/best-practices/).

```bash
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose exec app python manage_users.py create admin --admin
curl --fail http://127.0.0.1:8001/api/health
```

The account command prompts for a password of at least 12 characters and confirmation. Run it once for a new account; there is no default login. Open **http://localhost:8001/** in the Windows browser and sign in. The UI and `/api` share this address. Port 8001 allows a separate development service on port 8000 to remain available, provided each uses its own data directory.

WSL normally forwards Windows `localhost` access to Linux services. Access from a phone or another LAN device requires separate WSL networking and firewall configuration; binding a container port alone does not configure it. See [Microsoft's WSL networking guide](https://learn.microsoft.com/en-us/windows/wsl/networking).

## Storage and operation

Compose bind-mounts the whole `HOST_DATA_DIR` at `/data`, and sets `FRAME_DATA_DIR=/data` inside the container:

| Host location | Container location | Contents |
| --- | --- | --- |
| `HOST_DATA_DIR/frame.sqlite3` | `/data/frame.sqlite3` | Accounts, login sessions, uploads, conversations, messages |
| `HOST_DATA_DIR/videos/` | `/data/videos/` | Partial and completed videos |
| `HOST_DATA_DIR/images/` | `/data/images/` | Conversation images |

Container replacement, rebuilds, and `docker compose down` preserve these host files. Reserve disk space for complete videos, images, and backups; the configured video limit is 250 GiB per upload. A bind mount uses the host directory's permissions, even when the image has a different owner for `/data`. [Docker bind mounts](https://docs.docker.com/engine/storage/bind-mounts/).

Use one app instance and one Uvicorn worker. Upload locks and background preparation are owned by that process. Never point this container at the same data directory as a running Windows server or another backend instance.

```bash
docker compose logs --tail=100 -f app
docker compose ps
docker compose stop
docker compose start
docker compose down
```

These are separate operations: logs follows output until Ctrl+C; stop preserves the container; start restarts a stopped container; down removes the container and Compose network. For a packaged release, rerun `install.sh` to recreate it after down. For development directly from the source repository, apply code changes with:

```bash
docker compose up -d --build
```

Updates cause a short outage. The health check queries `/api/health`; it confirms process responsiveness, not available disk space or full database health. The `restart: unless-stopped` policy restarts an exited container unless it was deliberately stopped; an unhealthy health-check status alone does not trigger a restart. Docker must be running; shutting down WSL stops its services too. [Docker restart policies](https://docs.docker.com/engine/containers/start-containers-automatically/).

## Move existing data or back it up

Stop the old application and this Compose service before copying. Copy the **whole data directory**, including SQLite sidecar files if present, videos, and images. Copy into a separate empty destination and preserve the original until the migrated application is verified. For the existing Windows development data, the source seen from Ubuntu is `/mnt/c/Projects/Anlayser/Video-Analyzer-Agent/data/`.

Set `HOST_DATA_DIR` to the destination. Verify that every copied file and directory is readable and writable by `APP_UID:APP_GID`; adjust ownership on the confirmed destination when needed. For example, if the destination is exactly `/home/dev/videolens-data` and the selected UID/GID are `1000:1000`, the administrator command is `sudo chown -R 1000:1000 /home/dev/videolens-data`. Change that example to match the actual destination and account. No automatic ownership changes run at container startup.

Start Compose against the copy, check login and existing conversations, and keep the old server stopped if it would use that same directory. For a simple consistent backup, stop the app, copy all of `HOST_DATA_DIR` to a backup location, then start the app again. Restore the complete directory together.

## Access on native Ubuntu

Use the [release installation workflow](#package-in-wsl-and-install-on-ubuntu) to transfer the tested image and its installer together. No source checkout is required on the server. The archive contains application code and dependencies; it does not contain the host database or uploaded media. Move those separately using the stopped-service procedure above. Rebuilding from source may resolve newer package versions within `requirements.txt`; deploying the saved image preserves the tested runtime.

For direct LAN access, configure `BIND_ADDRESS=0.0.0.0` on the first install or in the existing `.env`, and open `http://<server-IP>:8001/`. `hostname -I` lists the host's addresses. Native Ubuntu does not need WSL forwarding. Docker-published ports can bypass UFW rules, so configure access using the host/network controls described in [Docker's firewall guidance](https://docs.docker.com/engine/install/ubuntu/#firewall-limitations).

For deployment beyond a trusted local test, add HTTPS through a reverse proxy with the UI and API on one origin. Keep the app port restricted to that proxy, accept 20 MiB image requests and 8 MiB video chunks, and trust forwarded HTTPS headers only from the proxy. Hostname, TLS, and trusted proxy configuration remain deployment-specific.
