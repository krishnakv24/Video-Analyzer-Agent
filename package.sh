#!/usr/bin/env bash
# Load a prepared dependency image, add current code, and export a release.
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
version="${1:-$(date -u +%Y%m%d-%H%M%S)}"
if [[ $# -gt 2 || ! "$version" =~ ^[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,127}$ ]]; then
  printf 'Usage: bash package.sh [app-version] [base-image.tar]\n' >&2
  exit 1
fi
base_archive="${2:-$project_dir/dist/videolens-base-1.0.0.tar}"
if [[ ! -f "$base_archive" ]]; then
  printf 'Base archive missing: %s\nCreate it first with bash build_base.sh.\n' "$base_archive" >&2
  exit 1
fi
docker info >/dev/null

release="videolens-$version"
image="videolens:$version"
output_dir="${PACKAGE_OUTPUT_DIR:-$project_dir/dist}"
mkdir -p -- "$output_dir"
output_dir="$(cd -- "$output_dir" && pwd)"
archive="$output_dir/$release.tar.gz"
if [[ -e "$archive" ]]; then
  printf 'Package already exists: %s. Use a new version.\n' "$archive" >&2
  exit 1
fi
staging="$(mktemp -d "$output_dir/.package.XXXXXX")"
trap 'rm -rf -- "$staging"' EXIT
bundle="$staging/$release"
mkdir -- "$bundle"

printf 'Loading dependency base: %s\n' "$base_archive"
loaded="$(docker image load --input "$base_archive")"
printf '%s\n' "$loaded"
mapfile -t base_images < <(printf '%s\n' "$loaded" | sed -n 's/^Loaded image: //p')
if [[ ${#base_images[@]} -ne 1 ]]; then
  printf 'The base archive must contain exactly one tagged image, as exported by build_base.sh.\n' >&2
  exit 1
fi
base_image=${base_images[0]}
kind="$(docker image inspect --format '{{ index .Config.Labels "org.videolens.image-kind" }}' "$base_image")"
if [[ "$kind" != runtime-base ]]; then
  printf 'This is not a dependency base image. Use the archive created with Dockerfile.base.\n' >&2
  exit 1
fi
base_hashes="$(docker run --rm --pull never --network none --read-only --entrypoint sha256sum "$base_image" \
  /opt/videolens/requirements.txt /opt/videolens/Dockerfile.base | cut -d ' ' -f 1)"
current_hashes="$(for file in requirements.txt Dockerfile.base; do
  sed 's/\r$//' "$project_dir/$file" | sha256sum | cut -d ' ' -f 1
done)"
if [[ "$base_hashes" != "$current_hashes" ]]; then
  printf 'requirements.txt or Dockerfile.base changed. Build a new dependency base before packaging this code.\n' >&2
  exit 1
fi
docker build --builder default --pull=false --network=none \
  --build-arg "BASE_IMAGE=$base_image" --tag "$image" "$project_dir"
docker image save --output "$bundle/image.tar" "$image"
platform="$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$image")"

# Pin the bundled image even when .env is reused from an older release.
# Keep the source Compose file as the single source of runtime settings.
sed "s|^    image: .*|    image: $image|" "$project_dir/compose.yaml" | tr -d '\r' > "$bundle/compose.yaml"
sed -e '/^VIDEOLENS_IMAGE=/d' -e 's|^HOST_DATA_DIR=.*|HOST_DATA_DIR=/srv/videolens/data|' "$project_dir/.env.example" | tr -d '\r' > "$bundle/.env.example"
cp -- "$project_dir/install.sh" "$bundle/"
chmod 0755 "$bundle/install.sh"
cat > "$bundle/README.md" <<EOF
# VideoLens $version

Image: $image ($platform). The Ubuntu host must support this CPU architecture.
Dependency base: $base_image. Its layers are included in image.tar.
The image includes the frontend, backend, Python packages, and FFmpeg.
User accounts, videos, images, and credentials are not included.

From this extracted directory on Ubuntu, install and start the application:

    sudo bash install.sh

The installer prepares Docker Engine and Compose when needed, creates .env and
the host data folder, loads image.tar, and starts FastAPI. On a new Ubuntu host,
Docker installation requires systemd and internet access to Docker's official apt
repository. Python and application dependencies are already inside the image.
An existing working Docker installation is reused without package upgrades.

Defaults: /srv/videolens/data and http://127.0.0.1:8001/.
For another data location on first install:

    sudo bash install.sh /path/to/data

For intended LAN access on first install:

    sudo env BIND_ADDRESS=0.0.0.0 HTTP_PORT=8001 bash install.sh

Existing .env settings, media, and file ownership are preserved. To change the
port or binding later, edit .env and rerun install.sh. To move existing storage,
stop the service and migrate the whole data directory before changing its path.
The backend creates the database, videos/, and images/ at startup.
WSL LAN access also needs host networking configuration.

After installation, create an account once (the command prompts for a password):

    sudo docker compose exec app python manage_users.py create admin --admin

Open http://localhost:8001/ locally, or http://<server-IP>:8001/ for configured LAN access.
Use sudo docker compose logs -f app to inspect logs, and sudo docker compose stop to stop.
If Docker is already working and the data/bundle directories are writable by
your account, install.sh can also run without sudo.

For an update, extract the new release into a separate directory, copy your existing
.env into it, and run sudo bash install.sh there. Keep the same Compose project name
(videolens by default) and HOST_DATA_DIR. Updates briefly interrupt the service.
Do not run another backend against this same data directory.
Back up the entire data directory with the service stopped before an update.
EOF

tar -czf "$staging/release.tar.gz" -C "$staging" "$release"
mv -- "$staging/release.tar.gz" "$archive"
printf '\nPackage ready: %s\nImage platform: %s\n' "$archive" "$platform"
printf 'Copy the archive to Ubuntu, extract it, and follow the included README.md.\n'
