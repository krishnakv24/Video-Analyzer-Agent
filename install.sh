#!/usr/bin/env bash
# Install and start an extracted release: sudo bash install.sh [data-directory]
# Package source: https://docs.docker.com/engine/install/ubuntu/
set -euo pipefail

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ $# -le 1 ]] || fail 'Usage: sudo bash install.sh [absolute-data-directory]'
bundle_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$bundle_dir"
[[ -f image.tar && -f compose.yaml ]] || fail 'Run install.sh from an extracted release created by package.sh'
# shellcheck source=/dev/null
source /etc/os-release
[[ "${ID:-}" == ubuntu ]] || fail 'This installer requires Ubuntu'

validate_data_dir() {
  [[ "$data_dir" == /* && "$data_dir" != / && "$data_dir" != *$'\n'* && "$data_dir" != *$'\r'* && "$data_dir" != *"'"* ]] \
    || fail 'Choose a dedicated absolute data directory without quotes or line breaks'
  [[ "$(realpath -m -- "$data_dir")" == "$data_dir" ]] || fail 'Use a canonical data path without symlinks'
  [[ ! -e "$data_dir" || -d "$data_dir" ]] || fail "Expected a directory: $data_dir"
}
data_dir=${1:-${HOST_DATA_DIR:-/srv/videolens/data}}
data_dir=${data_dir%/}
validate_data_dir
requested_dir=$data_dir
host_port=${HTTP_PORT:-8001}
bind_address=${BIND_ADDRESS:-127.0.0.1}
operator_uid=${SUDO_UID:-$EUID}
operator_gid=${SUDO_GID:-$(id -g)}
# Existing .env settings are authoritative on updates and reruns.
unset HOST_DATA_DIR APP_UID APP_GID HTTP_PORT BIND_ADDRESS VIDEOLENS_IMAGE

if command -v docker >/dev/null 2>&1; then
  docker compose version >/dev/null 2>&1 || fail 'Existing Docker lacks a working Compose plugin; repair it using the Docker Ubuntu installation guide'
  printf 'Docker and Compose are installed; keeping their existing versions.\n'
else
  [[ $EUID -eq 0 ]] || fail 'Docker installation requires sudo bash install.sh'
  [[ -d /run/systemd/system ]] || fail 'Running systemd is required; enable it first if using WSL'
  # Refuse to remove packages or replace a partial/unknown container installation.
  for package in docker.io docker-compose docker-compose-v2 docker-doc docker-buildx podman-docker \
      containerd runc docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin; do
    if dpkg-query -s "$package" >/dev/null 2>&1; then
      fail "Existing package $package found; resolve the installation using https://docs.docker.com/engine/install/ubuntu/ before rerunning"
    fi
  done
  for path in /etc/docker /var/lib/docker /etc/containerd /var/lib/containerd \
      /etc/apt/keyrings/docker.asc /etc/apt/sources.list.d/docker.sources /etc/apt/sources.list.d/docker.list \
      /etc/systemd/system/docker.service /lib/systemd/system/docker.service; do
    [[ ! -e "$path" && ! -L "$path" ]] || fail "Existing Docker/containerd configuration found at $path; inspect it before installing"
  done
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates curl
  install -d -m 0755 /etc/apt/keyrings
  curl --fail --show-error --location --connect-timeout 15 --max-time 120 \
    https://download.docker.com/linux/ubuntu/gpg --output /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${UBUNTU_CODENAME:-$VERSION_CODENAME}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker || fail 'Docker could not start; inspect sudo journalctl -u docker'
fi

if ! docker info >/dev/null 2>&1; then
  [[ $EUID -eq 0 && -d /run/systemd/system ]] || fail 'Docker is unavailable; check engine access or rerun with sudo'
  systemctl enable --now docker || fail 'Docker could not start; inspect sudo journalctl -u docker'
  docker info >/dev/null || fail 'Docker is not responding; inspect sudo journalctl -u docker'
fi
docker compose version

compose=(docker compose --project-directory "$bundle_dir" --env-file "$bundle_dir/.env" -f "$bundle_dir/compose.yaml")
if [[ -f .env ]]; then
  # Let Compose parse dotenv quoting/interpolation; never execute .env as Bash.
  settings="$("${compose[@]}" config --environment)"
  setting() { printf '%s\n' "$settings" | sed -n "s/^$1=//p"; }
  data_dir="$(setting HOST_DATA_DIR)"
  app_uid="$(setting APP_UID)"; app_uid=${app_uid:-1000}
  app_gid="$(setting APP_GID)"; app_gid=${app_gid:-1000}
  validate_data_dir
  [[ $# -eq 0 || "$requested_dir" == "$data_dir" ]] || fail 'The requested data directory differs from .env; keep the existing path or explicitly migrate your data first'
else
  if [[ -d "$data_dir" ]]; then
    app_uid="$(stat -c '%u' -- "$data_dir")"
    app_gid="$(stat -c '%g' -- "$data_dir")"
  else
    app_uid=$operator_uid; app_gid=$operator_gid
    if [[ "$app_uid" == 0 ]]; then app_uid=1000; app_gid=1000; fi
  fi
  [[ "$host_port" =~ ^[1-9][0-9]{0,4}$ && "$host_port" -le 65535 ]] || fail 'HTTP_PORT must be between 1 and 65535'
  [[ "$bind_address" =~ ^[a-zA-Z0-9.:_-]+$ ]] || fail 'BIND_ADDRESS must be a host IP address'
fi
[[ "$app_uid:$app_gid" =~ ^[0-9]+:[0-9]+$ ]] || fail 'APP_UID and APP_GID must be numeric'
if [[ ! -e "$data_dir" ]]; then
  if [[ $EUID -eq 0 ]]; then
    install -d -m 0750 -o "$app_uid" -g "$app_gid" -- "$data_dir"
  else
    [[ "$app_uid" == "$EUID" && "$app_gid" == "$(id -g)" ]] || fail 'Creating storage for this UID/GID requires sudo'
    mkdir -p -- "$data_dir"
    chmod 0750 -- "$data_dir"
  fi
  printf 'Created data directory: %s\n' "$data_dir"
else
  printf 'Preserved existing directory and file ownership: %s\n' "$data_dir"
fi
if [[ ! -e .env ]]; then
  (umask 077; set -o noclobber
    printf "HOST_DATA_DIR='%s'\nAPP_UID=%s\nAPP_GID=%s\nHTTP_PORT=%s\nBIND_ADDRESS=%s\n" \
      "$data_dir" "$app_uid" "$app_gid" "$host_port" "$bind_address" > .env)
  if [[ $EUID -eq 0 ]]; then chown "$operator_uid:$operator_gid" .env; fi
  printf 'Created .env with the host storage and port settings.\n'
fi
"${compose[@]}" config --quiet
docker image load --input "$bundle_dir/image.tar"
if ! "${compose[@]}" up -d --no-build --pull never --wait --wait-timeout 120; then
  printf '\nApplication startup failed. Check the port, storage permissions, and logs below.\n' >&2
  "${compose[@]}" ps --all || true
  "${compose[@]}" logs --tail=50 app || true
  exit 1
fi
printf '\nVideoLens is running and its health check passed. Data: %s\n' "$data_dir"
"${compose[@]}" ps
printf 'Published address: '
"${compose[@]}" port app 8000
printf 'Open that address with http://; for 0.0.0.0 use the server IP.\n'
printf 'Create a new account when needed: sudo docker compose exec app python manage_users.py create admin --admin\n'
