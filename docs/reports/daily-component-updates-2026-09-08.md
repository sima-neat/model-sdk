# Daily component updates — implementation and test report

Date: 2026-09-08

## Scope and branches

- Implementation: `codex/daily-component-updates`, isolated worktree based on `origin/develop` at `3531427925aae9f9f1f8d83ec9d5f2151aae14a0`.
- GitHub candidate build: `codex/daily-component-updates-e2e`, commit `a3ba4c9`, with the ten resolved component versions applied.
- Existing exact pins remain unchanged on the implementation branch. The new policy explicitly manages the ten requested Python distributions. Five target the 3.0.0 develop family; the others retain their respective bases while selecting develop.
- MLA and moving LLiMa snap references are excluded from the explicit allowlist. Immutable LLiMa discovery remains separate work; this change does not complete all of issue #53.

## Implemented behavior

- `component-updates.python-packages.<normalized-name>.version-prefix` authorizes exact-prefix discovery independently of the current pin. Explicit configuration can cross a base/channel once; subsequent runs only advance numerically in that prefix.
- Absent policy retains legacy behavior. An empty policy manages no components. Invalid prefixes, unknown policy entries and conflicting duplicate pins fail clearly.
- Normalized duplicate package pins update atomically; URL/file pins and other fields remain untouched. Reports are bound to the source manifest hash.
- ARM64 scans can stop at the newest compatible wheel. Network/authentication failures fail the scan instead of masquerading as no updates.
- `daily` is refreshed from the scanned develop commit with an explicit expected-SHA lease. Unchanged candidates produce no extra commits; stale develop and concurrent branch updates fail safely.
- Push credentials explicitly clear checkout's authorization headers and set the GitHub App header. PR processing shares updater concurrency, checks current SHA/base, commit marker/author and changed-file scope, and verifies that the manifest contains only permitted version updates.
- Midnight UTC schedule and manual dry-run remain supported. Feature-branch `source_ref` is accepted only for dry runs. Normal updates always use develop.

## Local verification

| Check | Result |
|---|---|
| Full repository unittest suite | PASS — 89 tests |
| Configured prefix transition and numeric selection | PASS, including build zero, wrong family/channel and no downgrade |
| Live Artifactory ARM64 scan using final implementation | PASS — all ten configured components resolved |
| CLI merge and independently generated PR summary | PASS — 14 exact pin occurrences updated |
| Live rescan of resolved candidate | PASS — `changed=false`; output byte-identical |
| Local bare-Git end-to-end branch test | PASS — create, repeat unchanged, refresh, stale develop rejection |
| Concurrent remote branch creation | PASS — explicit lease rejects overwrite |
| Git HTTP authentication integration test | PASS — local server receives only new App header, not persisted checkout header |
| Candidate policy validation | PASS — unmanaged manifest edits rejected |
| Workflow actionlint, shell syntax, git diff whitespace checks | PASS |

Commands:

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
actionlint .github/workflows/update-components.yml .github/workflows/open-component-update-pr.yml
bash -n scripts/refresh_daily_branch.sh
python3 scripts/update_component_versions.py scan --source-json scripts/source.json --target-arch aarch64 --newest-only --output scan.json
python3 scripts/update_component_versions.py merge --source-json scripts/source.json --report scan.json --output candidate.json --summary summary.md
python3 scripts/update_component_versions.py summarize --base scripts/source.json --updated candidate.json --output verified-summary.md
```

## Resolved candidate

## Component version updates

| Component | Previous | Updated | Update prefix |
|---|---|---|---|
| `mpk-parser` | `2.1.3.dev0+master.38` | `2.1.3.dev0+develop.128` | `2.1.3.dev0+develop.*` |
| `neural-compressor` | `2.0.0.dev0+master.3` | `2.0.0.dev0+develop.9` | `2.0.0.dev0+develop.*` |
| `sima-accelerator-mode` | `2.1.3.dev0+master.17` | `2.1.3.dev0+develop.88` | `2.1.3.dev0+develop.*` |
| `sima-ev-transforms` | `2.1.3.dev0+master.18` | `3.0.0.dev0+develop.66` | `3.0.0.dev0+develop.*` |
| `sima-frontend` | `2.1.3.dev0+master.392` | `3.0.0.dev0+develop.2263` | `3.0.0.dev0+develop.*` |
| `sima-ml-kernels` | `2.1.3.dev0+master.45` | `3.0.0.dev0+develop.260` | `3.0.0.dev0+develop.*` |
| `sima-mlc` | `2.1.3.dev0+master.187` | `3.0.0.dev0+develop.1097` | `3.0.0.dev0+develop.*` |
| `sima-mppe` | `2.1.0.dev0+master.25` | `2.1.0.dev0+develop.96` | `2.1.0.dev0+develop.*` |
| `sima-tvm` | `1.4.0.dev0+master.19` | `1.4.0.dev0+develop.101` | `1.4.0.dev0+develop.*` |
| `sima-utils` | `2.1.3.dev0+master.37` | `3.0.0.dev0+develop.132` | `3.0.0.dev0+develop.*` |

## Hosted end-to-end validation

- [Updater dry-run](https://github.com/sima-neat/model-compiler/actions/runs/34279736965): BLOCKED — source resolution succeeded, but the ARM64 scan job remained queued for over 12 minutes. The run was cancelled after the runner-availability limit was established.
- [Resolved candidate Build](https://github.com/sima-neat/model-compiler/actions/runs/34279762788): AMD64 packaging failed because the private runner could not resolve `artifacts.eng.sima.ai`. ARM64 packaging was cancelled after the AMD64 failure made the downstream install/compile jobs unreachable, to free the runner for scanner validation. The Build conclusion is cancelled; the AMD64 job conclusion is failure.
- [Baseline Build](https://github.com/sima-neat/model-compiler/actions/runs/34279737026): AMD64 failed with the same DNS error using the original `sima-frontend==2.1.3.dev0+master.392` pin. The resolved candidate failed attempting `3.0.0.dev0+develop.2263`; this is infrastructure failure, not evidence that either version is missing. The redundant baseline runs were cancelled/superseded after this failure was established.

The full dual-architecture install/compile gate and hosted scanner execution cannot be reported as passing. The private runner pool did not schedule the scan job during this validation window; organization runner inspection returned HTTP 403 for the available credentials. Restore artifact DNS/network access on the AMD64 private runner, then rerun the candidate Build. Individual wheel availability and local tests do not establish compatibility of the mixed component set with the currently pinned MLA toolchain.

## Rollout and remaining validation

1. Review/merge implementation and policy into develop.
2. Deploy updater/PR workflows and their helper scripts to default branch main; scheduled and workflow_run events execute there.
3. Run an updater dry-run against develop, then a normal dispatch to create daily using the App credentials.
4. After restoring AMD64 runner access, require a successful current dual-architecture Build before creating the daily → develop PR.

Production branch mutation and privileged PR creation were not exercised live: they depend on deploying the new default-branch workflows. Branch mutation was exercised against a temporary bare Git remote; credentials were checked using a local HTTP server. No production daily branch or update PR was created by these tests. No merges were performed.

The final report/regression-test commit uses `[skip ci]` to avoid launching another redundant packaging run against the known DNS failure. Runtime changes are in commits `bf9ea7c` and `979f2a7`; all 89 tests were run locally after the final regression additions.

## Bridge runner correction

The AMD64 private packaging labels were changed from `self-hosted, Linux, X64, issue-triage` to `self-hosted, Linux, X64, bridge` following the runner-access correction. `actionlint` passes for `build.yml`.

[Candidate revalidation](https://github.com/sima-neat/model-compiler/actions/runs/34281751544) uses candidate commit `360fc87` and scheduled AMD64 packaging on `sima-bridge-2-linux-x64-ipqf2d`. Revalidation completed with a failure in the compilation smoke tests. Both architecture packages built successfully, and both installation steps succeeded. The earlier DNS failure describes the retired runner selection and was resolved by using bridge.


## Bridge revalidation result: SDK API compatibility failure

Both ARM64 and AMD64 passed packaging, package installation, `pip check`, and smoke preflight. Both failed ResNet INT8 and BF16 compilation at the same import in `skills/quantize_compile/scripts/quantize_compile.py:30`:

```text
ImportError: cannot import name 'gen1_target' from 'afe.apis.defines'
```

The candidate installs `sima-frontend==3.0.0.dev0+develop.2263`. That SDK artifact does not expose `gen1_target`, but the utility imports it unconditionally alongside `gen2_target`. The tests request `--device modalix`, so only `gen2_target` is required; the import fails before target selection or compilation. This is an SDK/script API incompatibility, not an installation or architecture-specific failure.

Next correction: resolve the target for the requested device without requiring Gen1 support during Modalix runs. Retain Gen1 support where the installed SDK exposes it, and issue a clear unsupported-target error otherwise. Rerun both precision tests after that correction; the early import error does not establish that the rest of compilation is compatible.

- [ARM64 install/smoke job](https://github.com/sima-neat/model-compiler/actions/runs/34281751544/job/102251538882)
- [AMD64 install/smoke job](https://github.com/sima-neat/model-compiler/actions/runs/34281751544/job/102251538912)

No runtime fix for this SDK API incompatibility is included in this diagnostic update.

## SDK target compatibility correction

Removed the unconditional Gen1 import. Target selection now resolves only the requested device: Modalix uses Gen2 without needing the deprecated API, while MLSoC remains available with SDKs that expose Gen1 and otherwise raises a clear unsupported-target error. Unknown device values are rejected.

The complete local suite passes: **94 tests**, including five target-compatibility cases covering new and legacy SDKs. The resolved-candidate Build is being rerun to verify actual compilation beyond the previously failing import.
