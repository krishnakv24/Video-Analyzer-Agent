"""Test the unified release installer with fake tools in Ubuntu containers.

Run in WSL/Linux: python3 tests/install_smoke.py
Requires the existing videolens:local image and Docker access. Containers have
no network or Docker socket; the installer is their only host mount (read-only).
"""

from pathlib import Path
import subprocess


CONTAINER_SCRIPT = r"""
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

scenario = sys.argv[1]
state = Path('/test-state')
fake_bin = Path('/test-bin')
state.mkdir(mode=0o777)
state.chmod(0o777)
fake_bin.mkdir()
Path('/run/systemd/system').mkdir(parents=True, exist_ok=True)
assert shutil.which('docker') is None, 'The test image must not contain Docker'
bundle = Path('/test-bundle')
bundle.mkdir()
shutil.copyfile('/installer/install.sh', bundle / 'install.sh')
(bundle / 'image.tar').write_bytes(b'fake image archive')
(bundle / 'compose.yaml').write_text('name: videolens\nservices:\n  app:\n    image: videolens:test\n')
tool_source = r'''#!/opt/venv/bin/python
import json
import os
from pathlib import Path
import shutil
import sys
state = Path('/test-state')
tool = Path(sys.argv[0]).name
args = sys.argv[1:]
with (state / 'calls').open('a') as output:
    output.write(json.dumps([tool, *args]) + '\n')
if tool == 'dpkg-query':
    sys.exit(0 if args[-1] == os.environ.get('CONFLICT') else 1)
if tool == 'apt-get':
    if os.environ.get('APT_FAIL'):
        sys.exit(8)
    if 'docker-ce' in args:
        shutil.copyfile(state / 'tool', '/test-bin/docker')
        Path('/test-bin/docker').chmod(0o755)
        (state / 'installed').touch()
elif tool == 'curl':
    Path(args[args.index('--output') + 1]).write_text('test GPG key\n')
elif tool == 'systemctl':
    sys.exit(0 if (state / 'installed').exists() else 1)
elif tool == 'docker':
    if args == ['compose', 'version']:
        if os.environ.get('COMPOSE_FAIL'):
            sys.exit(9)
        print('Docker Compose version v5.1.4')
    elif args == ['info'] and os.environ.get('ENGINE_FAIL'):
        sys.exit(9)
    elif args and args[0] == 'compose':
        operation = args[args.index('-f') + 2:]
        if operation == ['config', '--environment']:
            config = Path(args[args.index('--env-file') + 1])
            for line in config.read_text().splitlines():
                if line and not line.startswith('#'):
                    key, value = line.split('=', 1)
                    if value.startswith("'") and value.endswith("'"):
                        value = value[1:-1]
                    print(key + '=' + os.environ.get(key, value))
        elif operation and operation[0] == 'up' and os.environ.get('STARTUP_FAIL'):
            print('fake application health failure', file=sys.stderr)
            sys.exit(10)
        elif operation == ['port', 'app', '8000']:
            print('127.0.0.1:8001')
'''
(state / 'tool').write_text(tool_source)
for name in ('apt-get', 'curl', 'systemctl', 'dpkg-query'):
    target = fake_bin / name
    target.write_text(tool_source)
    target.chmod(0o755)
env = dict(os.environ, PATH=str(fake_bin) + ':' + os.environ['PATH'],
           SUDO_UID='1001', SUDO_GID='1002')
data = Path('/srv/videolens/data')
arguments = []
run_identity = {}
config_before = None
existing_docker = {'missing-compose', 'engine-failure', 'existing-config',
                   'mismatched-data', 'custom-path', 'nonroot-existing', 'startup-failure'}
if scenario in existing_docker:
    (fake_bin / 'docker').write_text(tool_source)
    (fake_bin / 'docker').chmod(0o755)
    (state / 'installed').touch()
if scenario in ('missing-compose', 'engine-failure'):
    env['COMPOSE_FAIL' if scenario == 'missing-compose' else 'ENGINE_FAIL'] = '1'
elif scenario == 'conflicting-package':
    env['CONFLICT'] = 'runc'
elif scenario == 'partial-config':
    Path('/etc/docker').mkdir()
elif scenario == 'non-ubuntu':
    Path('/etc/os-release').write_text('ID=debian\n')
elif scenario == 'apt-failure':
    env['APT_FAIL'] = '1'
elif scenario == 'missing-bundle':
    (bundle / 'image.tar').unlink()
elif scenario in ('existing-config', 'mismatched-data'):
    data = Path('/srv/existing-media')
    data.mkdir()
    os.chown(data, 3001, 3002)
    data.chmod(0o710)
    media = data / 'saved.video'
    media.write_bytes(b'existing media')
    os.chown(media, 2001, 2002)
    config_before = (f"# Keep this comment and custom settings\nHOST_DATA_DIR='{data}'\n"
                     'APP_UID=3001\nAPP_GID=3002\nHTTP_PORT=8111\nBIND_ADDRESS=127.0.0.1\n')
    (bundle / '.env').write_text(config_before)
    env.update(HOST_DATA_DIR='/srv/ignored-shell-path', APP_UID='9999', APP_GID='9999',
               HTTP_PORT='8999', BIND_ADDRESS='0.0.0.0')
    if scenario == 'mismatched-data':
        arguments = ['/srv/different-data']
elif scenario == 'custom-path':
    data = Path('/srv/install smoke/$records')
    arguments = [str(data)]
elif scenario in ('nonroot-existing', 'nonroot-missing-docker'):
    Path('/run/systemd/system').rmdir()
    parent = Path('/test-user')
    parent.mkdir()
    os.chown(parent, 1001, 1002)
    os.chown(bundle, 1001, 1002)
    data = parent / 'data'
    arguments = [str(data)]
    env.pop('SUDO_UID')
    env.pop('SUDO_GID')
    run_identity = {'user': 1001, 'group': 1002, 'extra_groups': []}
elif scenario == 'startup-failure':
    env['STARTUP_FAIL'] = '1'

def run():
    return subprocess.run(['bash', str(bundle / 'install.sh'), *arguments], env=env,
                          capture_output=True, text=True, timeout=30, **run_identity)

def calls():
    log = state / 'calls'
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

def compose_calls():
    return [row[row.index('-f') + 2:] for row in calls()
            if row[:2] == ['docker', 'compose'] and '-f' in row]

def assert_started():
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'VideoLens is running and its health check passed.' in result.stdout
    load = ['docker', 'image', 'load', '--input', str(bundle / 'image.tar')]
    assert load in calls()
    up = ['up', '-d', '--no-build', '--pull', 'never', '--wait', '--wait-timeout', '120']
    assert up in compose_calls()
    assert calls().index(load) < next(i for i, row in enumerate(calls()) if 'up' in row)
    assert ['config', '--quiet'] in compose_calls()
    assert ['port', 'app', '8000'] in compose_calls()

result = run()
if scenario == 'fresh-and-rerun':
    assert_started()
    expected = {'docker-ce', 'docker-ce-cli', 'containerd.io',
                'docker-buildx-plugin', 'docker-compose-plugin'}
    assert any(row[0] == 'apt-get' and expected.issubset(row) for row in calls())
    source = Path('/etc/apt/sources.list.d/docker.sources').read_text()
    assert 'URIs: https://download.docker.com/linux/ubuntu\n' in source
    assert 'Suites: noble\n' in source
    assert 'Signed-By: /etc/apt/keyrings/docker.asc\n' in source
    assert Path('/etc/apt/keyrings/docker.asc').read_text() == 'test GPG key\n'
    assert (data.stat().st_uid, data.stat().st_gid) == (1001, 1002)
    assert stat.S_IMODE(data.stat().st_mode) == 0o750
    assert not list(data.iterdir()), 'Installer must not create app files'
    config = bundle / '.env'
    config_before = config.read_bytes()
    assert f"HOST_DATA_DIR='{data}'\n".encode() in config_before
    assert b'APP_UID=1001\nAPP_GID=1002\nHTTP_PORT=8001\nBIND_ADDRESS=127.0.0.1\n' in config_before
    assert (config.stat().st_uid, config.stat().st_gid) == (1001, 1002)
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    media = data / 'saved.video'
    media.write_bytes(b'existing media')
    os.chown(media, 2001, 2002)
    os.chown(data, 3001, 3002)
    data.chmod(0o710)
    count = len(calls())
    result = run()
    assert_started()
    assert not any(row[0] in ('apt-get', 'curl', 'systemctl') for row in calls()[count:])
    assert media.read_bytes() == b'existing media'
    assert (media.stat().st_uid, media.stat().st_gid) == (2001, 2002)
    assert (data.stat().st_uid, data.stat().st_gid) == (3001, 3002)
    assert stat.S_IMODE(data.stat().st_mode) == 0o710
    assert config.read_bytes() == config_before
elif scenario in ('existing-config', 'custom-path', 'nonroot-existing'):
    assert_started()
    assert not any(row[0] in ('apt-get', 'curl', 'systemctl') for row in calls())
    if scenario == 'existing-config':
        assert (bundle / '.env').read_text() == config_before
        assert not Path('/srv/ignored-shell-path').exists()
        assert media.read_bytes() == b'existing media'
        assert (media.stat().st_uid, media.stat().st_gid) == (2001, 2002)
        assert (data.stat().st_uid, data.stat().st_gid) == (3001, 3002)
        assert stat.S_IMODE(data.stat().st_mode) == 0o710
        assert f'Data: {data}' in result.stdout
    else:
        assert f"HOST_DATA_DIR='{data}'\n" in (bundle / '.env').read_text()
        assert (data.stat().st_uid, data.stat().st_gid) == (1001, 1002)
elif scenario == 'startup-failure':
    assert result.returncode != 0
    assert data.is_dir() and (bundle / '.env').is_file()
    assert ['ps', '--all'] in compose_calls()
    assert ['logs', '--tail=50', 'app'] in compose_calls()
    assert 'Application startup failed' in result.stderr
    assert 'VideoLens is running' not in result.stdout
else:
    assert result.returncode != 0, result.stdout
    if scenario == 'mismatched-data':
        assert (bundle / '.env').read_text() == config_before
        assert media.read_bytes() == b'existing media'
        assert not Path('/srv/different-data').exists()
    else:
        assert not data.exists(), 'Failed preflight must not create the data directory'
        assert not (bundle / '.env').exists()
    assert 'VideoLens is running' not in result.stdout
    assert not any(row[:3] == ['docker', 'image', 'load'] for row in calls())
    assert not any(row and row[0] == 'up' for row in compose_calls())
    if scenario != 'apt-failure':
        assert not any(row[0] in ('apt-get', 'curl') for row in calls())
    if scenario == 'missing-bundle':
        assert not calls(), 'Missing bundle must fail before host tool calls'
print('PASS:', scenario)
"""


def main():
    installer = Path(__file__).resolve().parents[1] / "install.sh"
    for scenario in ("fresh-and-rerun", "missing-compose", "conflicting-package",
                     "partial-config", "non-ubuntu", "apt-failure", "engine-failure",
                     "missing-bundle", "existing-config", "mismatched-data", "custom-path",
                     "nonroot-existing", "nonroot-missing-docker", "startup-failure"):
        result = subprocess.run(
            ["docker", "run", "--rm", "--pull", "never", "--network", "none", "--no-healthcheck",
             "--user", "0:0", "--entrypoint", "python", "--mount",
             f"type=bind,source={installer},target=/installer/install.sh,readonly",
             "videolens:local", "-c", CONTAINER_SCRIPT, scenario],
            capture_output=True, text=True, timeout=90,
        )
        if result.returncode:
            raise SystemExit(f"FAIL: {scenario}\n{result.stdout}{result.stderr}")
        print(result.stdout.strip(), flush=True)


if __name__ == "__main__":
    main()
