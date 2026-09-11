# Model Compiler Bundle Builder

**Documentation:** [English](docs/guides/index.md) | [한국어](docs/i18n/ko/guides/index.md) | [日本語](docs/i18n/ja/guides/index.md) | [繁體中文](docs/i18n/zh-Hant/guides/index.md) | [Українська](docs/i18n/uk/guides/index.md)

This repository builds a distributable Model Compiler bundle for `sima-cli`.

The bundle contains:
- curated Python wheels for the Model Compiler package set
- direct internal wheel dependencies required by those packages
- binary package artifacts such as the MLA toolchain
- an installer script for the target host
- generated `metadata.json` for `sima-cli`

Use this repository to build a repeatable Model Compiler installation for the
Neat SDK or an Ubuntu host, with a predictable package set and a self-contained
extension layout.

## Installation

Install and authenticate `sima-cli` first. See the
[sima-cli documentation](https://github.com/sima-neat/sima-cli) for setup
instructions.

Then install Model Compiler inside the Neat SDK or on an Ubuntu 22.04/24.04
host:

```bash
# amd64 host
sima-cli install -v 2.0.0 tools/model-compiler/amd64
# arm64 host
sima-cli install -v 2.1.2 tools/model-compiler/arm64
```

## Repository Layout

- [scripts/source.json](scripts/source.json): bundle manifest
- [scripts/build_modelsdk_bundle.sh](scripts/build_modelsdk_bundle.sh): end-to-end bundle builder
- [scripts/download_modelsdk_wheels.sh](scripts/download_modelsdk_wheels.sh): downloads Python and binary artifacts
- [scripts/install_modelsdk_wheels.sh](scripts/install_modelsdk_wheels.sh): installs the bundle on a target host
- [scripts/generate_metadata.py](scripts/generate_metadata.py): generates `metadata.json`
- [docs/generated/index.md](docs/generated/index.md): generated Model Compiler API reference entrypoint
- `dist/`: default output directory for built bundles

## API Reference

Generated API reference docs live under [docs/generated](docs/generated). Start
with [docs/generated/index.md](docs/generated/index.md), which links to the
generated AFE API pages.

## Manifest Format

[scripts/source.json](scripts/source.json) defines the bundle contents.

Example:

```json
{
  "sdk_version": "2.0.0",
  "python_version": "3.10",
  "system_dependencies": {
    "ubuntu": [
      "build-essential",
      "curl",
      "libllvm18",
      "libopenblas0-pthread"
    ]
  },
  "dependency_overrides": {
    "onnx": "1.17.0",
    "onnxruntime": "1.21.1",
    "protobuf": "4.25.7"
  },
  "python-packages": [
    { "name": "sima-frontend", "version": "2.0.0.dev0+master.371" }
  ],
  "binary-packages": [
    {
      "name": "mla/toolchain/mla-toolchain",
      "version": "v2.1.3560-develop.409",
      "extension": ".zip"
    }
  ]
}
```

Fields:
- `sdk_version`: used when constructing the bundle version string
- `python_version`: target interpreter version for the installed virtual environment
- `system_dependencies.ubuntu`: apt packages installed on the target host before Python and virtual environment setup
- `dependency_overrides`: exact versions to rewrite into downloaded wheel metadata when needed
- `python-packages`: top-level Python packages to include in the bundle; entries may include `file` to download a wheel from `sima-pypi/<package-name>/`, or `url` for a full direct wheel URL, when the wheel is not exposed by the configured Python index
- `binary-packages`: non-wheel artifacts fetched from Artifactory and installed into the Model Compiler virtual environment; the MLA toolchain derives its `x86` or `aarch64` archive suffix from the build target
- `aarch64`: optional architecture-specific overrides for fields that genuinely differ on ARM64; `dependency_overrides` entries are merged over the global map, so list only packages whose ARM64 pins differ

Native source builds triggered during installation, such as
`llama_cpp_python`, use the build backend's default parallelism. Set
`MODELSDK_BUILD_PARALLEL_LEVEL` to limit or override build parallelism on
resource-constrained machines.

### Automated component updates

The `Daily Component Update` workflow checks `develop`'s `scripts/source.json`
every four hours (00:17, 04:17, 08:17, 12:17, 16:17, and 20:17 UTC).
A small `Daily Component Update` wrapper on the default branch calls the
updater workflow on `develop`; updater scripts and the manifest are checked out
from the same resolved `develop` commit. GitHub may delay scheduled runs.
Exact versions remain the build inputs. The optional
`component-updates` block explicitly lists packages managed by automation:

```json
"component-updates": {
  "python-packages": {
    "sima-frontend": {"version-prefix": "3.0.0.dev0+develop."}
  },
  "binary-packages": {
    "mla/toolchain/mla-toolchain": {
      "version-prefix": "v3.0.0-",
      "channel": "develop"
    }
  }
}
```

The scanner chooses the greatest numeric build suffix in that exact prefix.
A prefix may differ from the current pin, explicitly authorizing the initial
base/channel transition. Subsequent runs advance only within that prefix.
Editing an exact pin does not change the configured prefix. Different components
may use different base versions. Package names are normalized, and matching
pins in `dependency_overrides` and `python-packages` update together.
URL/file-pinned packages are excluded. Conflicting duplicate pins are rejected.

Only listed components are managed when the block is present; an empty block
manages none. Older manifests without the block retain pin-derived discovery.
The supplied policy manages ten Python packages and the MLA toolchain. Moving
LLiMa snap references are not included. MLA uses `version-prefix` for the release
and `channel` for the branch: `v3.0.0-` plus `develop` matches
`v3.0.0-3609-develop.453`. Discovery orders the numeric revision first, then the
numeric channel build, allowing both to advance within 3.0.0 develop. Other
releases, PRs, and commit-only archives are excluded. An explicit policy allows
migration from a legacy 2.1 pin. Legacy binary prefixes ending in `.` retain
their existing suffix-only behavior. Architecture scans select only matching
`x86` or `aarch64` Ubuntu ZIPs; merging two scans requires a common version.

The private macOS/ARM64 runner checks Python 3.12 ARM64 or universal wheels.
A changed candidate refreshes `daily` from the scanned `develop` commit using
an explicit force-with-lease. Identical existing candidates
leave the branch untouched. A new develop commit refreshes daily even if no
package pins changed, so code and bundle-version changes are included. GitHub App credentials trigger the ordinary Build,
which packages, installs, and smoke-tests both architectures. These tests gate
cross-component compatibility; individual artifact availability does not.
A successful current manifest-only updater commit opens or refreshes one PR
from `daily` to `develop`, with old/new versions, prefixes and the Build link.

Manual dry runs are available through `workflow_dispatch`. The optional
`source_ref` input can select a feature-branch manifest only with `dry_run=true`;
normal updates always read `develop`. Artifactory access
uses the private runner's existing netrc credentials. Pushes use
`NEAT_RELEASES_APP_ID` and `NEAT_RELEASES_APP_PRIVATE_KEY`.
GitHub executes scheduled and `workflow_run` workflows from the default branch:
deploy the workflow and helper changes to `main`, and the policy to `develop`,
before unattended updates can use the new configuration.

## Building a Bundle

Before you build, configure `~/.netrc` with credentials for
`artifacts.eng.sima.ai`. The build downloads wheels and binary artifacts from
Artifactory, so it requires valid access tokens.

Example:

```netrc
machine artifacts.eng.sima.ai
  login <your-username-or-token-name>
  password <your-artifactory-access-token>
```

Restrict the file permissions when needed:

```bash
chmod 600 ~/.netrc
```

Default build:

```bash
./scripts/build_modelsdk_bundle.sh
```

Typical explicit build:

```bash
./scripts/build_modelsdk_bundle.sh \
  --source-json ./scripts/source.json \
  --output-dir ./dist \
  --index-url https://artifacts.eng.sima.ai/artifactory/api/pypi/sima-pypi-group/simple \
  --extra-index-url https://pypi.org/simple
```

Build a bundle for a specific architecture:

```bash
./scripts/build_modelsdk_bundle.sh --target-arch aarch64
```

By default, the metadata version comes from the exact git checkout. If `HEAD`
has a release tag such as `v1.0.0`, the generated `metadata.json` uses
`1.0.0`. Otherwise, it falls back to
`sdk_version.neat+branch.git-short-hash`. Pass `--bundle-version` to override
this behavior.

The LLiMa Vulcan entry uses `policy: snap` for development builds. It resolves
the latest artifact from the matching Model Compiler branch. A feature branch
without a matching LLiMa artifact falls back to `develop`; `develop`, `main`,
and release branches fail instead of crossing channels.

The Release workflow requires the intended LLiMa version and replaces snap
policy on the Model Compiler release branch with a commit-qualified ref such as
`v0.4.0:<commit>` before creating the Model Compiler tag. Tag builds reject
snap policy and non-commit-qualified refs. Generated metadata records the
requested ref, resolved commit, wheel version, and wheel filename.

The build creates a self-contained archive by default and performs these steps:
1. Read the package manifest from `source.json`.
2. Download every wheel in the target architecture's dependency closure.
3. Download binary package archives such as the MLA toolchain.
4. Copy the installer and source manifest into the output directory.
5. Generate `manifest.txt` with the bundled wheel filenames.
6. Generate the ZIP archive plus `metadata.json` and `metadata-offline.json`.

The release workflow builds the full dependency closure into one archive.
`metadata.json` downloads that archive, extracts it into a temporary directory,
runs the installer locally, and removes the extracted directory afterward.
`metadata-offline.json` references the same archive but provides manual
distribution instructions for transferring the ZIP to another environment and
running its included installer there. It is available from Linux, macOS, and
Windows hosts so the archive can be downloaded before transfer; the installer
inside the archive remains Linux-only.

Output files in `dist/` typically include:

- `model-compiler-<arch>.zip`
- `metadata.json`
- `metadata-offline.json`

## Testing a Local Bundle

Test a freshly built bundle from your local machine before you publish it.

From this repository:

```bash
cd dist
python3 -m http.server
```

Then install the bundle from an Ubuntu host or a Neat SDK environment:

```bash
sima-cli install -m http://<ip>:8000/metadata.json
```

Replace `<ip>` with the IP address of the machine that serves the `dist/`
directory.

This validates the Model Compiler bundle against a local metadata source.

## Authentication and Package Sources

The scripts download internal Python packages and binary artifacts from SiMa
Artifactory.

Python wheels:
- primary index: Artifactory
- fallback index: public PyPI via `--extra-index-url`

Binary packages:
- fetched from `https://artifacts.eng.sima.ai/artifactory/...`

If your environment requires authentication, configure Artifactory credentials
with `.netrc` or your shell environment before you run the scripts.

## Installing a Built Bundle

After you build the bundle, copy the architecture-specific ZIP to the target
Linux machine, extract it, and run the included installer. For example, for an
ARM64 target:

```bash
unzip -q model-compiler-arm64.zip -d model-compiler-arm64
cd model-compiler-arm64
bash ./install_modelsdk_wheels.sh
```

Use `model-compiler-amd64.zip` for an amd64 target. Alternatively, install
through `metadata.json` with `sima-cli`, which extracts the same archive into a
temporary directory and runs this installer automatically.

The installer performs these steps:
1. Read `source.json`.
2. Install required Ubuntu system packages from `system_dependencies.ubuntu`.
3. Find or install the required Python version, using `pyenv` when needed.
4. Read `manifest.txt` to identify the bundled Model Compiler wheels.
5. Create a Model Compiler virtual environment.
6. Install bundled binary packages into that virtual environment.
7. Install top-level package specs from `python-packages`, including extras such
   as `sima_lmm[sdk]`. It uses manifest-listed wheels as local `--find-links`
   inputs with `--no-index`.
8. Update shell startup files with the Model Compiler virtual environment
   `PATH`.
9. Leave the extracted archive available for reuse; remove it manually if it is
   no longer needed.

## Install Location

The installer creates the Model Compiler virtual environment in one of these
locations:

- `/sdk-extensions/model-compiler` if `/sdk-extensions` exists and is writable
- `/sdk-add-on/model-compiler` as a backward-compatible fallback
- `~/sdk-extensions/model-compiler` otherwise

Binary package contents, such as the MLA toolchain, are installed into the same
virtual environment under:
- `bin/`
- `include/`
- `lib/`

The downloaded MLA toolchain zip is sanitized during bundle creation so only
its `bin/` payload is preserved.

The installer also restores executable permissions for binaries copied into `model-compiler/bin`.

## Shell Environment Updates

The installer adds Model Compiler environment setup to one of these files:
- `~/.bashrc` when it exists, otherwise
- `~/.bash_profile`

It appends an idempotent block that exports:

```bash
PATH=<venv>/bin:$PATH
```

After installation, reload your shell:

```bash
source ~/.bashrc
```

or:

```bash
source ~/.bash_profile
```

You can also log out and back in.

## Post-Install Smoke Tests

After installing and activating the Model Compiler extension, run the fast smoke test:

```bash
activate-model-compiler
python /path/to/model-sdk/scripts/smoke_test_modelsdk.py --tier basic
```

The `basic` tier is intended for every CI/CD extension-install job. It checks:
- the active Python is the Model Compiler venv
- Model Compiler `bin/` is on `PATH`
- core MLA tools such as `mla-nm`, `mla-size`, `mla-readelf`, and `mla-isim` are runnable
- `afe-replay-compile`, `onnxsim`, and `llima-compile` entry points are runnable
- required Python modules including `afe`, `onnx`, `torch`, `sima_lmm`, `gguf`, `llama_cpp`, and `safetensors` are importable

Heavier tiers are available for scheduled or pre-release jobs:

```bash
# Export a synthetic ResNet50 ONNX model with torchvision.
python scripts/smoke_test_modelsdk.py --tier resnet-export

# Export or reuse ResNet50, audit it, simplify it, and run quantize-only.
python scripts/smoke_test_modelsdk.py --tier resnet-quantize

# Same as resnet-quantize, but also runs the compile step.
python scripts/smoke_test_modelsdk.py --tier resnet-compile

# Download YOLOv8n ONNX, simplify/audit it, and run quantize+compile.
python scripts/smoke_test_modelsdk.py --tier yolo

# Run all long-form smoke cases and print a final result summary.
python scripts/smoke_test_modelsdk.py --tier all

# Reuse a cached YOLO ONNX model and optionally verify model-to-pipeline references.
python scripts/smoke_test_modelsdk.py \
  --tier yolo \
  --yolo-model /path/to/yolo.onnx \
  --model-to-pipeline-dir /path/to/tool-model-to-pipeline
```

The ONNX operator audit is informational by default. Add `--strict-audit` when
you want the smoke test to fail on unknown or unsupported operators in the
bundled support database.

When `--work-dir` is omitted, model tiers create temporary work directories
under `~/tmp`. If `--work-dir` is supplied, model tiers create a fresh per-run
subdirectory under that path for intermediate and compiled artifacts. This
avoids collisions with stale files from previous smoke-test runs.
If an explicit `--work-dir` exists but is not writable, the runner falls back
to a per-user sibling such as `~/tmp/modelsdk-smoke-$USER`.

The `all`, `resnet-compile`, and `yolo` tiers also collect lightweight
compiled-artifact metrics. They report package counts/sizes and run MLA
toolchain checks such as `mla-size` and `mla-readelf` on ELF files packaged in
the generated MPK archive when those files are present.

Use `activate-model-compiler` to enter the installed environment and
`deactivate-model-compiler` to leave it.

## Local Model Compiler Containers

After creating a bundle, install it into an Ubuntu 24.04 container with:

```bash
# x86_64 host and bundle
./scripts/build_modelsdk_container.sh \
  --bundle-dir dist/amd64/package \
  --target-arch amd64 \
  --image model-compiler:local \
  --smoke-test

# ARM64 host and bundle
./scripts/build_modelsdk_container.sh \
  --bundle-dir dist/arm64/package \
  --target-arch arm64 \
  --image model-compiler:arm64-local \
  --smoke-test
```

The bundle directory must already contain `install_modelsdk_wheels.sh`,
`source.json`, `manifest.txt`, and the downloaded wheel and binary artifacts.
The container helper does not build or download the bundle. It passes the local
directory to BuildKit as a temporary build context, so package credentials and
the multi-gigabyte bundle are not copied into an image layer.

For example, on `macstudio`, download and extract the official ARM64 bundle
before invoking the container helper:

```bash
mkdir -p model-compiler-arm64 && cd model-compiler-arm64
sima-cli neat download model-compiler/arm64
unzip -q model-compiler-arm64.zip -d bundle
```

Open an interactive shell:

```bash
docker run --rm -it -v "$PWD:/workspace" model-compiler:local
```

The container starts Bash as a login shell. pyenv is initialized automatically,
and the Model Compiler virtual environment is active without running
`activate-model-compiler` manually. The same environment is applied to direct
container commands. The prompt includes the image version, for example
`[model-compiler feature/container:deadbee]`. Development builds use
`branch:short-git-hash`; an exact official release tag such as `v2.1.3` is used
on a tagged commit. `/etc/sdk-release` records that version, the SDK and Python
versions, the target architecture, the source branch and commit, the UTC image
build time, and component versions resolved from the bundle's `source.json`.
The source manifest is also preserved at
`/usr/local/share/model-compiler/source.json` for detailed inspection.

Run a compile smoke test and keep its artifacts on the host:

```bash
mkdir -p modelsdk-smoke
docker run --rm \
  -v "$PWD/modelsdk-smoke:/workspace/modelsdk-smoke" \
  model-compiler:local \
  python /opt/model-compiler-tests/scripts/smoke_test_modelsdk.py \
    --tier resnet-compile \
    --work-dir /workspace/modelsdk-smoke
```

Build natively on a matching host when possible: `linux/amd64` on `ll2` and
`linux/arm64` on `macstudio`.

## Branch Container Images

After the `Build` workflow succeeds for a pushed branch, GitHub Actions builds
the amd64 and arm64 containers from that run's package artifacts and publishes
a multi-architecture image to a branch-scoped GHCR package. Branch names are
lowercased, characters such as `/` are replaced with `-`, and a stable hash of
the original branch name prevents normalized-name collisions. For example,
`fix/container-build` publishes:

```text
ghcr.io/sima-neat/model-compiler-fix-container-build-f492aeaada4d:latest
```

The full source commit is also published as an immutable image tag. Deleting a
branch deletes its branch-scoped package. A daily reconciliation run handles
any cleanup event that was missed, and the cleanup workflow supports a manual
dry run.

Container builds use architecture-specific Buildx registry caches stored as
`buildcache-amd64` and `buildcache-arm64` tags in the branch package. A branch
also imports the matching `develop` cache when available. Deleting the branch
package therefore removes both its images and its build caches.

On ARM systems, activation enables the JAX compilation path with the NEON CPU
ISA by default. Use `--no-jax` as a compatibility or debugging fallback:

```bash
activate-model-compiler --no-jax
```

This explicitly disables the JAX compilation path for the activation while
preserving unrelated `XLA_FLAGS`. Deactivation restores the environment values
that were present before activation.

## Cleanup Behavior

After a successful install, the installer removes downloaded bundle resources
from the bundle directory, including:
- manifest-listed wheel files
- binary package archives such as the MLA toolchain zip
- extracted binary package directories if present

It keeps installer and metadata files such as:
- `install_modelsdk_wheels.sh`
- `source.json`
- `manifest.txt`
- `metadata.json`

## Notes and Troubleshooting

- If a sample or test script fails with missing Python modules, confirm that it
  uses the installed Model Compiler virtual environment and not a separate local
  `.env`.
- If a compiled package fails at runtime with missing shared libraries, check that:
  - the required Ubuntu packages from `system_dependencies.ubuntu` were installed
- If GitHub push fails from an automated environment, verify that the git remote has usable credentials.
- If a wheel is missing from Artifactory, provide `--extra-index-url` so the
  build can fall back to public PyPI for Python packages.

## Regenerating a Package

The archive contains the installer and every dependency, so regenerate it and
its matching metadata together with `build_modelsdk_bundle.sh` rather than
editing metadata independently.

## Status

This repository currently focuses on:
- curated host-side Model Compiler installation
- binary package inclusion for the MLA toolchain
- `sima-cli`-style bundle metadata generation

If you add more extensions later, use the existing extension-style install root
under `sdk-extensions/`.


### Build component metadata and daily notifications

Every architecture's Build summary lists the actual selected SiMa component
versions, including MLA, and an expandable table of all other bundled packages.
The inventory is derived from downloaded artifact filenames, not just requested
pins. `component-versions.json` and `component-versions.md` are included in the
build artifacts; the same inventory is stored in `metadata.json` and
`metadata-offline.json` under `component-versions`. LLiMa's resolved commit is
reported separately.

Completed `daily` Build runs report success or failure (including cancellation)
to `neat-vulcan-events`, using the organization's `SLACK_BOT_TOKEN` secret and
`SLACK_VULCAN_EVENT_CHANNEL_ID` variable. Notifications link to the build and its
component tables. The completion listener must exist on default branch `main`;
it calls the protected develop worker and executes trusted notification code
from develop, never code from a build artifact. Feature-branch and develop builds do not send these notifications.

The periodic updater only rebuilds when the resolved candidate or develop
commit changes. To repeat validation of an unchanged candidate, manually run
Build on `daily`. The test-only `codex/daily-component-updates-e2e` branch is no
longer the publication target.

The three default-branch entry points (`update-components.yml`,
`daily-build-notify.yml`, and `open-component-update-pr.yml`) are kept in one
isolated commit: merge into develop first, then cherry-pick that commit onto
main. Each entry point calls its corresponding `*-worker.yml@develop`. Worker
logic and helper scripts are maintained only on develop for this rollout.
