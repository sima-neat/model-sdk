import ast
import unittest
import sys
import types
from unittest.mock import patch
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "quantize_compile" / "scripts" / "quantize_compile.py"


def load_function(name):
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"), filename=str(SCRIPT))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == name
    )
    namespace = {}
    exec(
        compile(ast.Module(body=[function], type_ignores=[]), str(SCRIPT), "exec"),
        namespace,
    )
    return namespace[name]


def load_manifest_builder():
    return load_function("build_quantization_manifest")


class QuantizationManifestTests(unittest.TestCase):
    def test_bf16_weights_imply_bf16_activations(self):
        manifest = load_manifest_builder()(
            bf16_activations=False,
            bf16_weights=True,
            device="modalix",
        )

        self.assertEqual(manifest["activation_precision"], "bfloat16")
        self.assertEqual(manifest["weight_precision"], "bfloat16")

    def test_int8_configuration_remains_int8(self):
        manifest = load_manifest_builder()(
            bf16_activations=False,
            bf16_weights=False,
            device="modalix",
        )

        self.assertEqual(manifest["activation_precision"], "int8")
        self.assertEqual(manifest["weight_precision"], "int8")


class TargetCompatibilityTests(unittest.TestCase):
    def resolve(self, device, *, gen1_available):
        afe = types.ModuleType("afe")
        apis = types.ModuleType("afe.apis")
        defines = types.ModuleType("afe.apis.defines")
        defines.gen2_target = self.gen2
        if gen1_available:
            defines.gen1_target = self.gen1
        afe.apis = apis
        apis.defines = defines
        with patch.dict(sys.modules, {
            "afe": afe, "afe.apis": apis, "afe.apis.defines": defines,
        }):
            return load_function("resolve_target")(device)

    def setUp(self):
        self.gen1 = object()
        self.gen2 = object()

    def test_modalix_on_sdk_without_deprecated_gen1(self):
        self.assertIs(self.resolve("modalix", gen1_available=False), self.gen2)

    def test_modalix_on_legacy_sdk(self):
        self.assertIs(self.resolve("modalix", gen1_available=True), self.gen2)

    def test_mlsoc_on_legacy_sdk(self):
        self.assertIs(self.resolve("mlsoc", gen1_available=True), self.gen1)

    def test_mlsoc_on_new_sdk_reports_unsupported_target(self):
        with self.assertRaisesRegex(ValueError, "does not support the deprecated MLSoC"):
            self.resolve("mlsoc", gen1_available=False)

    def test_unknown_device_never_silently_selects_gen1(self):
        with self.assertRaisesRegex(ValueError, "Unsupported device"):
            self.resolve("unknown", gen1_available=True)


if __name__ == "__main__":
    unittest.main()
