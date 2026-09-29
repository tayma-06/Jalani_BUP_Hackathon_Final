"""Exercise actual shell deployment control flow with a fake Docker command.

No Docker, official simulator, SSH or real host is exercised by these tests.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

PACK = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'posix', 'Linux shell deployment tests require POSIX paths, symlinks and flock')
class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / '.env').write_text('TEST_ONLY=true\n')
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        fake = self.bin / 'docker'
        fake.write_text('''#!/usr/bin/env python3
import json, os, sys
with open(os.environ['DOCKER_CALL_LOG'], 'a') as out:
    out.write(json.dumps(sys.argv[1:]) + '\\n')
if 'exec' in sys.argv:
    print('-- fake test database backup')
if 'image' in sys.argv and 'inspect' in sys.argv:
    digit = sys.argv[-1].split('sha256:')[-1][0]
    print(json.dumps([{'Id':'sha256:'+digit*64,'Config':{'Labels':{'org.opencontainers.image.revision':digit*40}}}]))
if 'up' in sys.argv and any('/candidate/' in a for a in sys.argv) and os.environ.get('FAIL_CANDIDATE') == '1':
    raise SystemExit(1)
''')
        fake.chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ['PATH'],
                        DOCKER_CALL_LOG=str(self.root / 'docker-calls.jsonl'))
        self.previous = self.release('old', 'a')
        self.candidate = self.release('candidate', 'b')
        (self.root / 'current').symlink_to(self.previous)

    def tearDown(self):
        self.temp.cleanup()

    def release(self, name, digit):
        release = self.root / 'releases' / name
        (release / 'scripts').mkdir(parents=True)
        for script in ('deploy.sh', 'manifest.py', 'rollback.sh', 'verify_images.py'):
            shutil.copy2(PACK / 'scripts' / script, release / 'scripts' / script)
        (release / 'compose.yml').write_text('services: {}\n')
        manifest = {'version': 'v1.0.0', 'git_sha': digit * 40,
                    'backend_image': 'ghcr.io/test/backend@sha256:' + digit * 64,
                    'frontend_image': 'ghcr.io/test/frontend@sha256:' + digit * 64}
        (release / 'manifest.json').write_text(json.dumps(manifest))
        (release / 'release.env').write_text('TEST_ONLY=true\n')
        (release / 'scripts' / 'host_smoke.py').write_text('''import json,sys
from pathlib import Path
manifest=json.loads((Path(sys.argv[2])/'manifest.json').read_text())
out=Path(sys.argv[3]);out.parent.mkdir(parents=True,exist_ok=True)
out.write_text(json.dumps({'status':'passed','git_sha':manifest['git_sha']}))
''')
        return release

    def invoke(self, env=None):
        return subprocess.run(['bash', str(self.candidate / 'scripts/deploy.sh'), str(self.root)],
                              env=env or self.env, capture_output=True, text=True)

    def test_success_promotes_only_after_checks_and_keeps_previous(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / 'current').resolve(), self.candidate)
        self.assertEqual((self.root / 'previous').resolve(), self.previous)
        self.assertTrue(list((self.root / 'backups').glob('*.sql')))
        self.assertTrue((self.root / 'evidence' / ('b' * 40) / 'deployed.json').is_file())

    def test_failed_candidate_restores_previous_but_exits_failed(self):
        result = self.invoke(dict(self.env, FAIL_CANDIDATE='1'))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual((self.root / 'current').resolve(), self.previous)
        report = json.loads((self.root / 'evidence' / ('b' * 40) / 'rollback.json').read_text())
        self.assertEqual(report['git_sha'], 'a' * 40)
        calls = [json.loads(line) for line in (self.root / 'docker-calls.jsonl').read_text().splitlines()]
        self.assertTrue(any('up' in c and any('/old/' in v for v in c) for c in calls))
        self.assertFalse(any('down' in c or 'reset' in c for c in calls))

    def test_mutable_manifest_fails_before_docker_mutation(self):
        path = self.candidate / 'manifest.json'
        manifest = json.loads(path.read_text())
        manifest['backend_image'] = 'ghcr.io/test/backend:latest'
        path.write_text(json.dumps(manifest))
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / 'docker-calls.jsonl').exists())
        self.assertEqual((self.root / 'current').resolve(), self.previous)

    def docker_calls(self):
        return [json.loads(line) for line in (self.root / 'docker-calls.jsonl').read_text().splitlines()]

    def up_calls(self, release=None):
        """The service list of each `docker compose up`, optionally for one release only."""
        calls = []
        for call in self.docker_calls():
            if call[0] != 'compose' or 'up' not in call:
                continue
            if release is not None and not any(release in value for value in call):
                continue
            calls.append(call[call.index('up') + 1:])
        return calls

    def test_monitoring_ships_with_a_release_that_carries_its_config(self):
        (self.candidate / 'observability').mkdir()
        (self.candidate / 'observability' / 'alertmanager.yml').write_text('route: {}\n')
        # The entrypoint substitutes the receiver token, so a release missing it would start
        # Alertmanager against a config path that does not exist.
        shutil.copy2(PACK / 'observability' / 'alertmanager-entrypoint.sh',
                     self.candidate / 'observability' / 'alertmanager-entrypoint.sh')
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        started = self.up_calls()
        self.assertTrue(started)
        # A release that ships the config must also start the services that consume it.
        self.assertIn('alertmanager', started[0])
        self.assertIn('prometheus', started[0])

    def test_rollback_target_without_monitoring_config_still_deploys(self):
        (self.candidate / 'observability').mkdir()
        (self.candidate / 'observability' / 'alertmanager.yml').write_text('route: {}\n')
        result = self.invoke(dict(self.env, FAIL_CANDIDATE='1'))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual((self.root / 'current').resolve(), self.previous)
        rollback = self.up_calls('/old/')
        self.assertTrue(rollback)
        # A release from before monitoring existed must not be asked for services it lacks.
        self.assertNotIn('alertmanager', rollback[0])
        self.assertIn('backend', rollback[0])


class ReleaseConfigTests(unittest.TestCase):
    def test_receiver_token_is_never_committed_and_is_required_by_the_deploy_stack(self):
        config = (PACK / 'observability' / 'alertmanager.yml').read_text()
        self.assertIn('@@ALERT_WEBHOOK_TOKEN@@', config)
        self.assertNotIn('demo-alert-webhook-token', config)
        deploy = (PACK / 'deploy' / 'compose.yml').read_text()
        alertmanager = deploy[deploy.index('  alertmanager:'):deploy.index('  prometheus:')]
        self.assertIn('ALERT_WEBHOOK_TOKEN: ${ALERT_WEBHOOK_TOKEN:?required}', alertmanager)
        self.assertIn('alertmanager-entrypoint.sh', alertmanager)
        self.assertIn('ALERT_WEBHOOK_TOKEN', (PACK / 'deploy' / 'host.env.example').read_text())

    def test_publish_release_bundles_the_alertmanager_entrypoint(self):
        # Publishing pushes images; this check only inspects the mandatory file list.
        source = (PACK / 'scripts' / 'publish_release.py').read_text()
        for name in ('alertmanager.yml', 'alertmanager-entrypoint.sh'):
            self.assertIn(name, source, f'{name} is not bundled into the release')


if __name__ == '__main__':
    unittest.main()
