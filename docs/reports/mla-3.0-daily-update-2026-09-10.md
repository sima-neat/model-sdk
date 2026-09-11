# MLA 3.0 daily component update and revalidation

## Changes

The candidate branch `codex/daily-component-updates-e2e` now pins
`mla/toolchain/mla-toolchain` to `v3.0.0-3609-develop.453`, replacing
`v2.1.3560-develop.409`. The ten previously resolved Python component pins are
unchanged, preserving the comparison with the earlier ResNet failures.

Both the implementation and candidate branches include this policy:

```json
"binary-packages": {
  "mla/toolchain/mla-toolchain": {
    "version-prefix": "v3.0.0-",
    "channel": "develop"
  }
}
```

This block belongs under `component-updates`. The scanner orders MLA's numeric
revision and channel build, filters release/channel and architecture, and permits
the explicit migration from the existing 2.1 pin. PR and commit-only archives do
not match this policy. The implementation branch retains its original exact
component pins; the policy authorizes the automated transition. No public core
API is changed.

The packaging workflow now sets `TMPDIR` to `runner.temp`, placing dependency
staging on disk instead of the x86 bridge runner's quota-limited 3.6 GB `/tmp`
tmpfs. The disk had approximately 194 GB free when inspected.

## Local and live updater validation

- Full repository unittest suite: 100 tests passed.
- Workflow actionlint and whitespace validation: passed.
- Live Artifactory discovery: `v3.0.0-3609-develop.453` exists for x86 and aarch64.
- Live two-architecture scan/merge with an MLA-only policy: migrated the legacy
  pin, generated and independently validated the summary, and retained policy.
- Rescan of the updated manifest: no update, byte-identical merged output.
- Regression coverage includes numeric revision/build ordering, architecture
  filtering, release/channel rejection, common-version selection, no downgrade,
  invalid policy errors, summary validation, and the repository policy itself.

## Hosted build and compile validation

The [first 3.0 toolchain build](https://github.com/sima-neat/model-compiler/actions/runs/34563428487)
used candidate `b0f6b847ba8922bf28345a95458b5da11e411126`.
ARM64 packaging passed. AMD64 packaging failed during pip dependency download
with `OSError: [Errno 122] Disk quota exceeded`; installation/compilation jobs
were skipped because packaging did not pass on both architectures.

The [rebuild with disk-backed staging](https://github.com/sima-neat/model-compiler/actions/runs/34563727447)
uses candidate `93f34df20b98b08e25de4b382e216cea2f5a3c1c`. Both architecture
build, installation, and smoke-test jobs passed; downstream publication is
still in progress at the time of this report.
The x86 retry was scheduled on `bench31-pc1-modalix-pcie-linux-x64-753kyx`;
therefore its result is not a direct rerun on the original bridge runner.
Both packages built, validated, uploaded, and installed successfully. The x86
artifact was approximately 5.3 GB and its upload took about 13 minutes. ARM64
post-install validation passed: ResNet INT8 (134.0 s), ResNet BF16 (160.8 s),
and Qwen3/LLiMa ONNX, quantization, compilation, and quantized-part execution.
The ARM64 tools report `v3.0.0-3609-gd4b4b343`. The previous ARM64 INT8
simulator crash did not recur. AMD64 also passed all smoke tests using the same
MLA version; the previous BF16 `ofm_chk.mlc` failure did not recur.

| Architecture | ResNet INT8 | ResNet BF16 | Qwen3/LLiMa |
|---|---|---|---|
| ARM64 | PASS — 134.0 s | PASS — 160.8 s | PASS |
| AMD64 | PASS — 142.8 s | PASS — 187.4 s | PASS |

- [ARM64 installation and smoke tests](https://github.com/sima-neat/model-compiler/actions/runs/34563727447/job/103155626728)
- [AMD64 installation and smoke tests](https://github.com/sima-neat/model-compiler/actions/runs/34563727447/job/103155626768)

The ten exact Python component pins were held constant for this comparison.
LLiMa retains its pre-existing moving snap policy, so this is not a fully
immutable comparison of every bundled dependency.

## Full policy scan

The full eleven-component policy was also scanned successfully for ARM64 and
x86_64. Both scans found the same three additional Python updates:

| Component | Candidate pin | Newest available |
|---|---|---|
| sima-ev-transforms | 3.0.0.dev0+develop.66 | 3.0.0.dev0+develop.67 |
| sima-frontend | 3.0.0.dev0+develop.2263 | 3.0.0.dev0+develop.2269 |
| sima-mlc | 3.0.0.dev0+develop.1097 | 3.0.0.dev0+develop.1101 |

A combined manifest and independently verified summary were generated. These
updates are held separately while the toolchain-only comparison runs. No newer
3.0.0 develop MLA archive was found for either architecture.

The [hosted updater dry run](https://github.com/sima-neat/model-compiler/actions/runs/34564521946)
passed source resolution, ARM64 scanning, and candidate preparation. Its scan
artifact explicitly includes MLA with `version_prefix: v3.0.0-` and
`channel: develop`. This exercised the feature-branch workflow with
`dry_run=true`; it did not update `daily` or create a PR.

## Bundle base version

At the user's request, `sdk_version` is now `3.0.0` on both branches. The candidate
at `b19e6052e23e` resolves to
`3.0.0.neat+codex-daily-component-updates-e2e.b19e6052e23e`.
This manifest-only version change was pushed with CI skipped to preserve the
already-running compilation comparison. That run tests the same component pins
but its packages were created before the bundle base version bump.
