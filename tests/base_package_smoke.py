"""Check package.sh guards using a temporary project and fake Docker commands.

Run with Linux/WSL Python: python3 tests/base_package_smoke.py
No Docker daemon, containers, network, package installs, or existing data are used.
This checks command flow and bundle contents; real image builds are separate tests.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile


FAKE_TOOL = r'''import hashlib
import json
import os
from pathlib import Path
import sys

state = Path(os.environ['PACKAGE_TEST_STATE'])
tool = Path(sys.argv[0]).name
args = sys.argv[1:]
with (state / 'calls.jsonl').open('a') as output:
    output.write(json.dumps([tool, *args]) + '\n')
if tool != 'docker':
    raise SystemExit('Package attempted to install dependencies: ' + tool)
metadata = json.loads((state / 'base.json').read_text())
if args == ['info']:
    pass
elif args[:2] == ['image', 'load']:
    assert Path(args[args.index('--input') + 1]).is_file()
    for tag in metadata['tags']:
        print('Loaded image: ' + tag)
elif args[:2] == ['image', 'inspect']:
    if 'org.videolens.image-kind' in args[args.index('--format') + 1]:
        print(metadata['kind'])
    else:
        print('linux/amd64')
elif args and args[0] == 'run':
    assert args[args.index('--network') + 1] == 'none'
    assert '--read-only' in args and '--rm' in args
    assert args[args.index('--entrypoint') + 1] == 'sha256sum'
    if os.environ.get('HASH_FAILURE'):
        raise SystemExit(9)
    print(metadata['requirements_hash'] + '  /opt/videolens/requirements.txt')
    print(metadata['recipe_hash'] + '  /opt/videolens/Dockerfile.base')
elif args and args[0] == 'build':
    assert args[args.index('--builder') + 1] == 'default'
    assert '--pull=false' in args and '--network=none' in args
    assert args[args.index('--build-arg') + 1] == 'BASE_IMAGE=' + metadata['tags'][0]
    project = Path(args[-1])
    recipe = (project / 'Dockerfile').read_text()
    assert not any(line.lstrip().upper().startswith('RUN ') for line in recipe.splitlines()), \
        'The application Dockerfile must not install dependencies'
    built = {'tag': args[args.index('--tag') + 1], 'base': metadata['tags'][0],
             'source': (project / 'main.py').read_text()}
    (state / 'built.json').write_text(json.dumps(built))
elif args[:2] == ['image', 'save']:
    built = json.loads((state / 'built.json').read_text())
    assert args[-1] == built['tag']
    Path(args[args.index('--output') + 1]).write_text(json.dumps(built))
else:
    raise SystemExit('Unexpected Docker operation: ' + repr(args))
'''


def normalized_digest(path):
    return hashlib.sha256(re.sub(rb"\r(?=\n|$)", b"", path.read_bytes())).hexdigest()


def check_scenario(repo, scenario):
    with tempfile.TemporaryDirectory(prefix="videolens-base-guards-") as temporary:
        work = Path(temporary)
        project, state, fake_bin, caller = (work / name for name in ("project", "state", "bin", "caller"))
        for directory in (project, state, fake_bin, caller):
            directory.mkdir()
        for name in ("package.sh", "Dockerfile", "Dockerfile.base", "requirements.txt",
                     "compose.yaml", ".env.example", "install.sh"):
            shutil.copyfile(repo / name, project / name)
        (project / "main.py").write_text("# Current application source, not code from the base\n")
        metadata = {
            "tags": ["videolens-base:7.4.2"], "kind": "runtime-base",
            "requirements_hash": normalized_digest(project / "requirements.txt"),
            "recipe_hash": normalized_digest(project / "Dockerfile.base"),
        }
        archive = work / "renamed dependency archive.tar"
        if scenario != "missing-base":
            archive.write_bytes(b"fake saved base image")
        if scenario == "changed-requirements":
            with (project / "requirements.txt").open("a") as output:
                output.write("\nnew-runtime-dependency==1.0\n")
        elif scenario == "changed-os-recipe":
            with (project / "Dockerfile.base").open("a") as output:
                output.write("\nRUN apt-get install -y extra-os-library\n")
        elif scenario == "crlf-inputs":
            for name in ("requirements.txt", "Dockerfile.base"):
                content = re.sub(rb"\r(?=\n|$)", b"", (project / name).read_bytes())
                (project / name).write_bytes(content.replace(b"\n", b"\r\n"))
        elif scenario == "wrong-image-kind":
            metadata["kind"] = "application"
        elif scenario == "multiple-images":
            metadata["tags"].append("videolens-base:7.4.3")
        (state / "base.json").write_text(json.dumps(metadata))
        for name in ("docker", "apt-get", "apt", "pip", "pip3"):
            target = fake_bin / name
            target.write_text(f"#!{sys.executable}\n" + FAKE_TOOL)
            target.chmod(0o755)
        output_dir = work / "output releases"
        env = dict(os.environ, PATH=str(fake_bin) + os.pathsep + os.environ["PATH"],
                   PACKAGE_TEST_STATE=str(state), PACKAGE_OUTPUT_DIR=str(output_dir))
        if scenario == "hash-check-failure":
            env["HASH_FAILURE"] = "1"
        result = subprocess.run(
            ["bash", str(project / "package.sh"), "0.1.2", str(archive)], cwd=caller,
            env=env, capture_output=True, text=True, timeout=30,
        )
        log = state / "calls.jsonl"
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        release = output_dir / "videolens-0.1.2.tar.gz"
        if scenario in ("versioned-base", "crlf-inputs"):
            assert result.returncode == 0, result.stdout + result.stderr
            assert release.is_file()
            assert all(row[0] == "docker" for row in calls), calls
            assert sum(row[:2] == ["docker", "build"] for row in calls) == 1
            assert sum(row[:3] == ["docker", "image", "save"] for row in calls) == 1
            with tarfile.open(release, "r:gz") as bundle:
                files = {member.name for member in bundle if member.isfile()}
                prefix = "videolens-0.1.2/"
                assert files == {prefix + name for name in
                                 ("image.tar", "compose.yaml", ".env.example", "install.sh", "README.md")}
                built = json.load(bundle.extractfile(prefix + "image.tar"))
                assert built["base"] == "videolens-base:7.4.2"
                assert built["source"] == (project / "main.py").read_text()
                compose = bundle.extractfile(prefix + "compose.yaml").read().decode()
                assert "name: videolens\n" in compose
                assert "image: videolens:0.1.2\n" in compose
                readme = bundle.extractfile(prefix + "README.md").read().decode()
                assert "Dependency base: videolens-base:7.4.2" in readme
        else:
            assert result.returncode != 0, result.stdout
            assert not release.exists()
            assert not any(row[:2] == ["docker", "build"] for row in calls), calls
            assert not any(row[:3] == ["docker", "image", "save"] for row in calls), calls
            if scenario == "missing-base":
                assert not calls, "Missing base must fail before Docker is invoked"
            if scenario.startswith("changed-"):
                assert "Build a new dependency base" in result.stderr
        assert not output_dir.exists() or not list(output_dir.glob(".package.*"))
        print("PASS:", scenario, flush=True)


def main():
    repo = Path(__file__).resolve().parents[1]
    for scenario in ("missing-base", "changed-requirements", "changed-os-recipe",
                     "wrong-image-kind", "multiple-images", "hash-check-failure",
                     "versioned-base", "crlf-inputs"):
        check_scenario(repo, scenario)


if __name__ == "__main__":
    main()
