#!/usr/bin/env bash
# Build dependencies once; reuse the exported base for application releases.
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
version="${1:-1.0.0}"
if [[ $# -gt 1 || ! "$version" =~ ^[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,127}$ ]]; then
  printf 'Usage: bash build_base.sh [base-version, default 1.0.0]\n' >&2
  exit 1
fi
docker info >/dev/null
image="videolens-base:$version"
output_dir="${PACKAGE_OUTPUT_DIR:-$project_dir/dist}"
mkdir -p -- "$output_dir"
output_dir="$(cd -- "$output_dir" && pwd)"
archive="$output_dir/videolens-base-$version.tar"
if [[ -e "$archive" ]]; then
  printf 'Base archive already exists: %s. Use a new base version.\n' "$archive" >&2
  exit 1
fi
staging="$(mktemp -d "$output_dir/.base.XXXXXX")"
trap 'rm -rf -- "$staging"' EXIT

docker build --builder default --file "$project_dir/Dockerfile.base" --tag "$image" "$project_dir"
docker image save --output "$staging/base.tar" "$image"
mv -- "$staging/base.tar" "$archive"
printf '\nBase image ready: %s\nBase archive: %s\n' "$image" "$archive"
printf 'Reuse this archive when packaging code changes. Rebuild the base when dependencies change.\n'
