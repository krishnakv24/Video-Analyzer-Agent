"""Basic metadata inspection for a locally stored upload."""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path


def inspect_video(path: Path, filename: str, size: int) -> dict:
    """Stream a video to calculate its checksum and optionally read its duration."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    metadata = {"filename": filename, "size": size, "sha256": digest.hexdigest()}
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        probe = subprocess.run([ffprobe, "-v", "error", "-show_entries",
                                "format=duration", "-of", "json", str(path)],
                               capture_output=True, text=True, timeout=120, check=False)
        if probe.returncode == 0:
            duration = json.loads(probe.stdout).get("format", {}).get("duration")
            if duration is not None:
                metadata["duration_seconds"] = float(duration)
    return metadata
