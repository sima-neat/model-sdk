"""Configured prefix and local Git end-to-end regression tests."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('policy_updater', ROOT / 'scripts/update_component_versions.py')
M = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = M
SPEC.loader.exec_module(M)


class PolicyTests(unittest.TestCase):
    def source(self):
        return {
            'dependency_overrides': {'mpk-parser': '2.1.3.dev0+master.38'},
            'python-packages': [{'name': 'mpk_parser', 'version': '2.1.3.dev0+master.38'}],
            'component-updates': {'python-packages': {'mpk-parser': {'version-prefix': '3.0.0.dev0+develop.'}}},
        }

    def test_transition_numeric_order_and_atomic_duplicates(self):
        doc = self.source()
        c = M.collect_components(doc, 'aarch64')[0]
        reports = [{'target_arch': 'aarch64', 'components': {c.component_id: {'available': [
            '3.0.0.dev0+develop.9', '3.0.0.dev0+develop.100',
            '3.1.0.dev0+develop.999', '3.0.0.dev0+master.999',
        ]}}}]
        updates = M.select_updates(doc, reports)
        self.assertEqual(updates[c.component_id], '3.0.0.dev0+develop.100')
        updated = json.loads(M.apply_updates_preserving_format(json.dumps(doc), doc, {c.component_id: c}, updates))
        self.assertEqual(updated['dependency_overrides']['mpk-parser'], updated['python-packages'][0]['version'])
        self.assertEqual(updated['component-updates'], doc['component-updates'])

    def test_build_zero_is_valid_after_transition(self):
        c = M.collect_components(self.source(), 'aarch64')[0]
        self.assertEqual(M.component_family(c).build, -1)
        report = {'target_arch': 'aarch64', 'components': {c.component_id: {'available': ['3.0.0.dev0+develop.0']}}}
        self.assertEqual(M.select_updates(self.source(), [report])[c.component_id], '3.0.0.dev0+develop.0')

    def test_empty_policy_manages_none(self):
        doc = self.source(); doc['component-updates'] = {}
        self.assertEqual(M.collect_components(doc, 'aarch64'), [])

    def test_unknown_or_conflicting_policy_fails(self):
        doc = self.source(); doc['component-updates']['python-packages']['unknown'] = {'version-prefix': '3.0.0.dev0+develop.'}
        with self.assertRaisesRegex(M.UpdateError, 'no editable pin'):
            M.collect_components(doc, 'aarch64')
        doc = self.source(); doc['python-packages'][0]['version'] = '2.1.3.dev0+master.39'
        with self.assertRaisesRegex(M.UpdateError, 'conflicting duplicate'):
            M.collect_components(doc, 'aarch64')

    def test_malformed_prefix_fails(self):
        for prefix in ['3.0.0.dev0+develop.*', '3.0.0.dev0+develop', '', 5]:
            doc = self.source(); doc['component-updates']['python-packages']['mpk-parser']['version-prefix'] = prefix
            with self.assertRaises(M.UpdateError):
                M.collect_components(doc, 'aarch64')

    def test_url_pin_is_preserved(self):
        doc = self.source(); doc['python-packages'][0]['url'] = 'https://example.invalid/pinned.whl'
        c = M.collect_components(doc, 'aarch64')[0]
        updated = json.loads(M.apply_updates_preserving_format(json.dumps(doc), doc, {c.component_id: c}, {c.component_id: '3.0.0.dev0+develop.100'}))
        self.assertEqual(updated['python-packages'], doc['python-packages'])

    def test_same_family_no_downgrade(self):
        doc = self.source()
        for entry in (doc['dependency_overrides'],): entry['mpk-parser'] = '3.0.0.dev0+develop.100'
        doc['python-packages'][0]['version'] = '3.0.0.dev0+develop.100'
        c = M.collect_components(doc, 'aarch64')[0]
        report = {'target_arch': 'aarch64', 'components': {c.component_id: {'available': ['3.0.0.dev0+develop.9', c.current]}}}
        self.assertEqual(M.select_updates(doc, [report]), {})

    def test_network_failure_is_not_no_update(self):
        for stderr in ['ERROR: No matching distribution found', 'WARNING: Retrying 401\nERROR: No matching distribution found']:
            result = subprocess.CompletedProcess([], 1, '', stderr)
            with patch.object(M.subprocess, 'run', return_value=result):
                args = dict(target_arch='aarch64', python_version='312', index_url='https://example.invalid')
                if '401' in stderr:
                    with self.assertRaises(M.UpdateError): M.wheel_is_available('pkg', '1', **args)
                else:
                    self.assertFalse(M.wheel_is_available('pkg', '1', **args))

    def test_scan_falls_back_to_newest_compatible_wheel(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)/'source.json'; source.write_text(json.dumps(self.source()))
            output = Path(temp)/'scan.json'
            with patch.object(M, 'python_index_versions', return_value=[
                '3.0.0.dev0+develop.102', '3.0.0.dev0+develop.101', '3.0.0.dev0+develop.100',
            ]), patch.object(M, 'wheel_is_available', side_effect=[False, True]) as check:
                M.scan(source, target_arch='aarch64', output=output,
                       index_url='https://example.invalid/simple', artifactory_url='https://example.invalid',
                       max_candidates=0, newest_only=True)
            self.assertEqual(check.call_count, 2)
            entry = next(iter(json.loads(output.read_text())['components'].values()))
            self.assertEqual(entry['available'], ['3.0.0.dev0+develop.101'])
            self.assertEqual(entry['version_prefix'], '3.0.0.dev0+develop.')

    def test_cli_merge_and_summary_reject_unmanaged_changes(self):
        import hashlib
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp); source = p/'source.json'; source.write_text(json.dumps(self.source()))
            c = M.collect_components(self.source(), 'aarch64')[0]
            report = {'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'target_arch': 'aarch64', 'components': {c.component_id: {'available': ['3.0.0.dev0+develop.100']}}}
            (p/'scan.json').write_text(json.dumps(report))
            subprocess.run([sys.executable, str(ROOT/'scripts/update_component_versions.py'), 'merge', '--source-json', str(source), '--report', str(p/'scan.json'), '--output', str(p/'updated.json'), '--summary', str(p/'summary.md')], check=True)
            M.summarize(source, p/'updated.json', p/'summary.md')
            self.assertIn('3.0.0.dev0+develop.*', (p/'summary.md').read_text())
            bad = json.loads((p/'updated.json').read_text()); bad['sdk_version'] = '99'
            (p/'updated.json').write_text(json.dumps(bad))
            with self.assertRaisesRegex(M.UpdateError, 'outside managed'):
                M.summarize(source, p/'updated.json', p/'summary.md')


class BranchEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.repo = self.root/'work'
        self.run_git(self.root, 'init', '--bare', 'origin.git')
        self.run_git(self.root, 'clone', str(self.root/'origin.git'), str(self.repo))
        self.run_git(self.repo, 'config', 'user.name', 'Test')
        self.run_git(self.repo, 'config', 'user.email', 'test@example.invalid')
        self.run_git(self.repo, 'checkout', '-b', 'develop')
        (self.repo/'scripts').mkdir(); (self.repo/'scripts/source.json').write_text('{"version": 1}\n')
        self.run_git(self.repo, 'add', '.')
        self.run_git(self.repo, 'commit', '-m', 'base')
        self.run_git(self.repo, 'push', '-u', 'origin', 'develop')
        self.sha = self.run_git(self.repo, 'rev-parse', 'HEAD')

    def run_git(self, cwd, *args):
        return subprocess.check_output(['git', *args], cwd=cwd, stderr=subprocess.DEVNULL, text=True).strip()

    def refresh(self, expected=0):
        result = subprocess.run(['bash', str(ROOT/'scripts/refresh_daily_branch.sh')], cwd=self.repo, env={**os.environ, 'SOURCE_SHA': self.sha}, capture_output=True, text=True)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def test_create_repeat_no_change_and_refresh(self):
        self.refresh()
        initial = self.run_git(self.repo, 'rev-parse', 'HEAD')
        self.assertIn(initial, self.run_git(self.repo, 'ls-remote', '--heads', 'origin', 'daily'))
        self.run_git(self.repo, 'checkout', '--detach', self.sha)
        (self.repo/'scripts/source.json').write_text('{"version": 2}\n'); self.refresh()
        first = self.run_git(self.repo, 'rev-parse', 'HEAD')
        self.assertEqual(self.run_git(self.repo, 'rev-parse', 'HEAD^'), self.sha)
        self.run_git(self.repo, 'checkout', '--detach', self.sha)
        (self.repo/'scripts/source.json').write_text('{"version": 2}\n'); self.refresh()
        self.assertIn(first, self.run_git(self.repo, 'ls-remote', '--heads', 'origin', 'daily'))
        (self.repo/'scripts/source.json').write_text('{"version": 3}\n'); self.refresh()
        self.assertNotEqual(first, self.run_git(self.repo, 'rev-parse', 'HEAD'))

    def test_no_pin_change_still_follows_develop_without_repeated_commits(self):
        self.refresh()
        first = self.run_git(self.repo, 'rev-parse', 'HEAD')
        self.run_git(self.repo, 'checkout', '--detach', self.sha)
        self.refresh()
        self.assertIn(first, self.run_git(self.repo, 'ls-remote', '--heads', 'origin', 'daily'))
        self.run_git(self.repo, 'checkout', 'develop')
        (self.repo/'code.txt').write_text('new code')
        self.run_git(self.repo, 'add', 'code.txt')
        self.run_git(self.repo, 'commit', '-m', 'advance develop')
        self.run_git(self.repo, 'push', 'origin', 'develop')
        self.sha = self.run_git(self.repo, 'rev-parse', 'HEAD')
        self.refresh()
        self.assertEqual(self.run_git(self.repo, 'rev-parse', 'HEAD^'), self.sha)
        self.assertNotEqual(first, self.run_git(self.repo, 'rev-parse', 'HEAD'))

    def test_app_auth_replaces_checkout_header(self):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        received = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                received.extend(self.headers.get_all("Authorization", []))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"001e# service=git-upload-pack\n00000000")
            def log_message(self, *args):
                pass
        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}"
        key = f"http.{url}/.extraheader"
        self.run_git(self.repo, 'config', key, 'Authorization: basic OLD_CHECKOUT_TOKEN')
        try:
            subprocess.run(['git', 'ls-remote', url + '/repo.git'], cwd=self.repo,
                env={**os.environ, 'GIT_CONFIG_COUNT': '2', 'GIT_CONFIG_KEY_0': key,
                     'GIT_CONFIG_VALUE_0': '', 'GIT_CONFIG_KEY_1': key,
                     'GIT_CONFIG_VALUE_1': 'Authorization: basic NEW_APP_TOKEN'},
                capture_output=True, timeout=10)
            thread.join(timeout=5)
            self.assertEqual(received, ['basic NEW_APP_TOKEN'])
        finally:
            server.server_close()

    def test_explicit_lease_rejects_concurrent_branch_creation(self):
        import shlex
        hook = self.repo/'.git/hooks/pre-push'
        remote = shlex.quote(str(self.root/'origin.git'))
        hook.write_text(f'#!/bin/sh\ngit --git-dir={remote} update-ref refs/heads/daily {self.sha}\n')
        hook.chmod(0o755)
        (self.repo/'scripts/source.json').write_text('{"version": 2}\n')
        self.refresh(expected=1)
        self.assertIn(self.sha, self.run_git(self.repo, 'ls-remote', '--heads', 'origin', 'daily'))

    def test_stale_develop_is_rejected(self):
        (self.repo/'new').write_text('advance'); self.run_git(self.repo, 'add', '.')
        self.run_git(self.repo, 'commit', '-m', 'advance'); self.run_git(self.repo, 'push')
        self.run_git(self.repo, 'checkout', '--detach', self.sha)
        (self.repo/'scripts/source.json').write_text('{"version": 2}\n')
        self.refresh(expected=1)
        self.assertEqual(self.run_git(self.repo, 'ls-remote', '--heads', 'origin', 'daily'), '')


if __name__ == '__main__': unittest.main()
