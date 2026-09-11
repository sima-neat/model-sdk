import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


I = load('component_inventory')
N = load('daily_build_notification')


class InventoryTests(unittest.TestCase):
    def test_resolved_versions_come_from_artifacts_with_arch_override(self):
        source = {'dependency_overrides': {'sima-frontend': '3.0.0.dev0+develop.1'},
                  'aarch64': {'dependency_overrides': {'sima-frontend': '3.0.0.dev0+develop.2'}},
                  'binary-packages': [{'name': 'mla/toolchain/mla-toolchain', 'version': 'v3.0.0-3609-develop.453'}]}
        files = list(map(Path, ['sima_frontend-3.0.0.dev0+develop.2-py3-none-any.whl',
                              'mla-toolchain-v3.0.0-3609-develop.453-aarch64-ubuntu.zip',
                              'sqlitedict-2.1.0.tar.gz']))
        rows = I.inventory(files, source, 'aarch64')
        frontend = next(row for row in rows if row['name'] == 'sima-frontend')
        self.assertEqual(frontend['version'], '3.0.0.dev0+develop.2')
        self.assertEqual(frontend['requested'], frontend['version'])
        self.assertEqual(next(row for row in rows if row['name'] == 'mla-toolchain')['version'], 'v3.0.0-3609-develop.453')
        rendered = I.summary(rows, version='3.0.0', arch='aarch64', provenance={'sima_lmm': {'resolved-commit': 'abc123'}})
        self.assertIn('### SiMa components', rendered)
        self.assertIn('sqlitedict', rendered)
        self.assertIn('abc123', rendered)

    def test_binary_extensions_match_downloaded_archives(self):
        version = 'v3.0.0-3609-develop.453'
        for options in ({'extension': 'zip'}, {'extension': '.zip'},
                        {'extension': ' zip '}, {'extension': '', 'archive-type': 'zip'},
                        {'archive-type': 'zip'}, {}):
            for arch, suffix in (('aarch64', 'aarch64'), ('x86_64', 'x86')):
                with self.subTest(options=options, arch=arch):
                    filename = f'mla-toolchain-{version}-{suffix}-ubuntu.zip'
                    source = {'binary-packages': [
                        {'name': 'mla/toolchain/mla-toolchain', 'version': version, **options},
                    ]}
                    rows = I.inventory([Path(filename)], source, arch)
                    self.assertEqual(rows, [{'name': 'mla-toolchain', 'version': version,
                                            'requested': version, 'kind': 'binary', 'artifact': filename}])

    def test_non_zip_binary_uses_archive_type_without_mla_zip_suffix(self):
        filename = 'mla-toolchain-v3.0.0.tar.gz'
        source = {'binary-packages': [{'name': 'mla/toolchain/mla-toolchain',
                  'version': 'v3.0.0', 'extension': '', 'archive-type': 'tar.gz'}]}
        row = I.inventory([Path(filename)], source, 'aarch64')[0]
        self.assertEqual((row['name'], row['version'], row['kind']), ('mla-toolchain', 'v3.0.0', 'binary'))

    def test_markdown_cells_are_escaped(self):
        text = I.summary([{'name': 'sima-<script>|x', 'version': '1', 'requested': 'a\nb', 'kind': 'wheel'}],
                         version='3.0.0', arch='x86_64', provenance={})
        self.assertNotIn('<script>', text)
        self.assertIn('&#124;', text)
        self.assertIn('a b', text)

    def test_metadata_and_artifact_inventory_agree(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'sima_frontend-3.0.0.dev0+develop.2269-py3-none-any.whl').write_bytes(b'fixture')
            (root/'install_modelsdk_wheels.sh').write_text('#!/bin/sh\n')
            (root/'source.json').write_text(json.dumps({'sdk_version': '3.0.0'}))
            subprocess.run([sys.executable, str(ROOT/'scripts/generate_metadata.py'),
                            '--artifacts-dir', temp, '--output', str(root/'metadata.json'),
                            '--version', '3.0.0.neat+daily.abc123', '--target-arch', 'aarch64'],
                           check=True, capture_output=True)
            metadata = json.loads((root/'metadata.json').read_text())
            offline = json.loads((root/'metadata-offline.json').read_text())
            inventory = json.loads((root/'component-versions.json').read_text())
            self.assertEqual(metadata['component-versions'], inventory)
            self.assertEqual(offline['component-versions'], inventory)
            self.assertEqual(inventory[0]['version'], '3.0.0.dev0+develop.2269')
            self.assertIn(metadata['version'], (root/'component-versions.md').read_text())


class NotificationTests(unittest.TestCase):
    def run_data(self, conclusion):
        return {'head_branch': 'daily', 'status': 'completed', 'repository': {'full_name': 'sima-neat/model-compiler'},
                'id': 123, 'run_number': 4, 'head_sha': 'a'*40, 'run_attempt': 2, 'conclusion': conclusion}

    def test_success_contains_build_summary_link(self):
        payload = N.compose(self.run_data('success'), [], 'C123')
        self.assertEqual(payload['channel'], 'C123')
        self.assertIn('succeeded', payload['text'])
        self.assertIn('123#summary', json.dumps(payload))

    def test_failure_and_cancellation_report_jobs_without_mentions(self):
        for conclusion in ('failure', 'cancelled', 'timed_out'):
            payload = N.compose(self.run_data(conclusion), [
                {'name': 'compile <!channel>', 'conclusion': conclusion},
                {'name': 'good', 'conclusion': 'success'},
            ], 'C123')
            encoded = json.dumps(payload)
            self.assertIn('&lt;!channel&gt;', encoded)
            self.assertNotIn('<!channel>', encoded)
            self.assertIn(conclusion, payload['text'])

    def test_non_daily_running_or_missing_channel_rejected(self):
        for change, channel in [({'head_branch': 'develop'}, 'C123'), ({'status': 'in_progress'}, 'C123'), ({}, '')]:
            with self.assertRaises(ValueError):
                N.compose({**self.run_data('success'), **change}, [], channel)
