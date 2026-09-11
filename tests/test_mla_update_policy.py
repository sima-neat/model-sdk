"""MLA release/channel discovery, migration, and merge regressions."""
import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from test_update_component_versions import MODULE


class MlaUpdatePolicyTests(unittest.TestCase):
    def source(self, current="v2.1.3560-develop.409"):
        return {
            "component-updates": {"binary-packages": {
                "mla/toolchain/mla-toolchain": {
                    "version-prefix": "v3.0.0-", "channel": "develop",
                },
            }},
            "binary-packages": [{
                "name": "mla/toolchain/mla-toolchain", "version": current,
            }],
        }

    def test_migration_discovers_only_release_channel_and_architecture(self):
        component = MODULE.collect_components(self.source(), "aarch64")[0]
        versions = [
            "v3.0.0-9-develop.99", "v3.0.0-10-develop.2",
            "v3.0.0-10-develop.10", "v3.0.0-0-develop.0",
            "v3.0.1-999-develop.999", "v3.0.0-999-master.999",
            "v3.0.0-999-PR-494.4", "v3.0.0-999-gabc123",
        ]
        listing = {"files": [
            {"uri": f"/mla-toolchain-{version}-aarch64-ubuntu.zip"}
            for version in versions
        ] + [{"uri": "/mla-toolchain-v3.0.0-999-develop.999-x86-ubuntu.zip"}]}
        with patch.object(MODULE, "curl_text", return_value=json.dumps(listing)):
            found = MODULE.binary_index_versions(
                component, target_arch="aarch64", artifactory_url="https://example.invalid",
            )
        self.assertEqual(found, [
            "v3.0.0-10-develop.10", "v3.0.0-10-develop.2",
            "v3.0.0-9-develop.99", "v3.0.0-0-develop.0",
        ])

    def test_merge_requires_common_version_and_never_downgrades(self):
        doc = self.source("v3.0.0-3609-develop.453")
        component = MODULE.collect_components(doc, "aarch64")[0]
        shared = ["v3.0.0-3610-develop.454", "v3.0.0-3608-develop.999"]
        reports = [{"target_arch": arch, "components": {
            component.component_id: {"available": shared + extra},
        }} for arch, extra in [
            ("aarch64", ["v3.0.0-3611-develop.455"]), ("x86_64", []),
        ]]
        updates = MODULE.select_updates(doc, reports)
        self.assertEqual(updates, {component.component_id: shared[0]})
        updated = json.loads(MODULE.apply_updates_preserving_format(
            json.dumps(doc), doc, {component.component_id: component}, updates,
        ))
        self.assertEqual(updated["component-updates"], doc["component-updates"])
        for report in reports:
            report["components"][component.component_id]["available"] = [
                component.current, "v3.0.0-3608-develop.999",
            ]
        self.assertEqual(MODULE.select_updates(doc, reports), {})

    def test_summary_accepts_migration_and_rejects_wrong_channel(self):
        doc = self.source()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base, updated, summary = (root / name for name in ("base.json", "updated.json", "summary.md"))
            base.write_text(json.dumps(doc))
            doc["binary-packages"][0]["version"] = "v3.0.0-3609-develop.453"
            updated.write_text(json.dumps(doc))
            MODULE.summarize(base, updated, summary)
            self.assertIn("v3.0.0-*-develop.*", summary.read_text())
            doc["binary-packages"][0]["version"] = "v3.0.0-3609-master.999"
            updated.write_text(json.dumps(doc))
            with self.assertRaisesRegex(MODULE.UpdateError, "violates update policy"):
                MODULE.summarize(base, updated, summary)

    def test_new_format_without_policy_preserves_release_and_channel(self):
        family = MODULE.binary_family("v3.0.0-3609-develop.453")
        self.assertEqual(family.parse_candidate("v3.0.0-3610-develop.454"), (3610, 454))
        self.assertIsNone(family.parse_candidate("v3.0.1-3610-develop.454"))
        self.assertIsNone(family.parse_candidate("v3.0.0-3610-master.454"))

    def test_invalid_release_channel_policy_fails(self):
        for prefix, channel in [
            ("v3.0.0-*", "develop"), ("v3.0.0-", None),
            ("v3.0.0-", "develop.*"), ("v3.0.0-", ""),
        ]:
            with self.subTest(prefix=prefix, channel=channel):
                doc = self.source()
                doc["component-updates"]["binary-packages"]["mla/toolchain/mla-toolchain"] = {
                    "version-prefix": prefix, "channel": channel,
                }
                with self.assertRaises(MODULE.UpdateError):
                    MODULE.collect_components(doc, "aarch64")

    def test_repository_policy_manages_mla(self):
        from pathlib import Path
        doc = json.loads((Path(__file__).resolve().parents[1] / "scripts/source.json").read_text())
        for arch in MODULE.SUPPORTED_ARCHES:
            mla = [c for c in MODULE.collect_components(doc, arch) if c.kind == "binary"]
            self.assertEqual(len(mla), 1)
            self.assertEqual(mla[0].channel, "develop")
            self.assertEqual(mla[0].version_prefix, "v3.0.0-")
