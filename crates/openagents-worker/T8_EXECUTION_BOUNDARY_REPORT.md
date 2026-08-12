# T8 Execution-Boundary Remediation

## Outcome

The validated OpenCode and registered-QA execution paths now enter a mandatory
Linux Landlock boundary before untrusted code runs. Candidate Git control data,
source repositories, dependency checkouts, source caches, sibling tenants, and
worker secrets are outside the writable set; post-model Git runs from rebuilt
control state with a clean environment; signing material exists only for the
minimal commit/verification interval; and OpenCode plus every QA command runs
in an isolated process group that is killed and drained on every terminal path.

The generic workflow remains available: OpenCode can edit the candidate
worktree, configured QA commands can use the candidate, isolated HOME, and
isolated TMPDIR, dependencies remain readable, ordinary QA failures continue to
later gates, cancellation remains terminal, and delivery remains one signed
child commit over the task-stable base.

## Source-To-Sink Closure

| Untrusted source | Former sink | Closure |
| --- | --- | --- |
| Generated files and candidate `.git` state | Privileged `git add`, `commit`, and `verify-commit` | The real Git directory is rebuilt as a self-contained bare clone under sibling `.openos-control`, kept read-only to OpenCode/QA, and addressed only through explicit `--git-dir` and `--work-tree`. The disposable worktree `.git` pointer is restored before trusted operations. |
| Repository config, hooks, attributes, filters, helpers, diff/textconv, pager/editor settings | Post-model Git subprocess execution | Every trusted Git command uses `env_clear`, fixed `PATH`/`HOME`, disabled system/global config and attributes, `/dev/null` hooks, disabled fsmonitor/credential helpers/external diff/prompts/editors/pagers, and command-local trusted signing configuration. The adversarial commit test proves hook/filter/helper/config payloads remain inert. |
| Worker signing configuration | OpenCode/QA environment and filesystem | The base64 key is not decoded during health or model/QA execution. It is decoded into a zeroizing buffer immediately before signing, imported through stdin into a mode-0700 temporary `GNUPGHOME`, used for the one commit and verification, then removed; its agent is killed on drop. OpenCode tool subprocesses use the managed-runtime environment scrubber covered by the focused OpenCode test. |
| OpenCode and QA filesystem access | Git control, dependency/source cache, sibling tenant, and secret paths | `openagents-sandbox` installs Landlock with hard-required compatibility. System/runtime inputs and dependency/control paths are explicit read-only rules; only candidate/runtime HOME/TMP/HACN paths are writable. All unlisted paths are denied. Exit 125 plus the sandbox-init marker is terminal. |
| OpenCode and each registered QA command | Descendant processes surviving cancellation, timeout, overflow, error, or direct-parent exit | Each command is a new process-group leader. The bounded collector selects exit/cancellation/timeout/overflow, sends `SIGKILL` to the negative process-group ID on every completion path, waits for the leader, and drains or aborts both bounded pipe readers. QA cancellation returns immediately and skips later commands; ordinary failure, timeout, and overflow produce bounded failed evidence and continue according to policy. |

## Files In Scope

- `crates/openagents-worker/src/runtime.rs`: candidate control repository,
  trusted Git environment, ephemeral signing, sandboxed OpenCode/QA launch,
  bounded process-group lifecycle, and fail-closed sandbox result handling.
- `crates/openagents-worker/src/sandbox.rs`: Linux Landlock exec wrapper with a
  hard compatibility requirement and stable initialization-failure contract.
- `crates/openagents-worker/tests/execution_boundary_linux.rs`: real Linux
  filesystem denial, QA allowed-path success, dependency immutability, and
  sandbox initialization failure evidence.
- `crates/openagents-worker/Cargo.toml` and `Cargo.lock`: sandbox, process,
  temporary signing, and zeroization dependencies plus the sandbox binary.
- `Dockerfile.worker-rust`: packages `openagents-sandbox` in the production
  image and retains the generic runtime toolchain.
- `../OpenCode/utils/sandbox/sandbox-adapter.ts`: preserved shared-worktree
  change that rethrows initialization errors when `failIfUnavailable` is true.
  No unrelated OpenCode memory changes were modified by T8.

## Adversarial Evidence

- `sandbox_allows_candidate_edits_and_denies_control_dependency_and_siblings`
  writes the candidate while Git control, dependency, and source-cache writes
  fail and sibling/secret reads fail under real Landlock.
- `qa_shell_succeeds_for_allowed_paths_and_keeps_dependencies_read_only` runs
  the production QA shell shape (`sh -lc`) with isolated HOME/TMPDIR, writes all
  allowed paths, reads a dependency fixture, and cannot mutate that fixture.
- `opencode_sandbox_initialization_failure_is_terminal` and
  `qa_sandbox_initialization_failure_is_terminal` prove the marker/exit contract
  is terminal at both callers; the Linux integration test proves the wrapper
  emits that contract when rule setup fails.
- `trusted_git_ignores_generated_control_and_signs_one_commit` installs
  malicious hook, filter, credential-helper, and configuration payloads, then
  proves none execute while a valid signed one-child commit is produced and
  verified.
- `cancellation_kills_descendant_process_group`,
  `timeout_kills_descendant_process_group`, and
  `output_overflow_kills_descendant_process_group` prove descendant cleanup.
- QA tests prove ordinary failure/timeout/overflow continues to later gates,
  while cancellation is terminal and skips later commands.
- OpenCode `utils/subprocessEnv.test.ts` proves managed tool subprocesses do not
  inherit control-plane, provider, or signing secrets.

## Verification

All commands were run from the named repository with the required `rtk` prefix.

- `cargo fmt --all -- --check`: passed.
- `cargo clippy --workspace --all-targets --locked -- -D warnings`: passed with
  no issues on the host after the final edit.
- `cargo test --workspace --locked`: passed, 65 tests in 3 suites on the host.
- Linux Docker full `cargo test --workspace --locked`: passed, 66 worker unit
  tests and the then-current 2 real-Landlock integration tests.
- Linux Docker focused `cargo test --locked -p openagents-worker --test execution_boundary_linux`:
  passed all 3 final integration tests, including the explicit QA success test.
- `bun test utils/subprocessEnv.test.ts`: passed, 1 test and 14 assertions.
- `bun run typecheck`: passed and regenerated the SDK type file without a diff.
- OpenAgents and OpenCode `git diff --check`: passed.
- `docker buildx build --load --build-context opencode=../OpenCode --build-context opencontract=../OpenContract -f Dockerfile.worker-rust -t openos/openagents-worker:t8-boundary .`:
  passed; release worker, sandbox, and OpenCode binaries were packaged into
  image manifest list `sha256:2af652f4fc41c918416f8b42000a235b6291034f1e8643326363a216ceba668d`.

A supplemental Linux Clippy attempt against the stock `rust:1.90-bookworm`
image could not start because that image omits the Clippy component. This does
not replace or weaken the passing required strict host Clippy gate; the final
Linux-only test is rustfmt-clean and compiled/passed in the focused Docker run.

## T11 Containment Follow-up

The former process-group escape limit is closed. The Linux sandbox installs an
inherited, fail-closed seccomp filter before untrusted execution and denies
`setsid` and `setpgid` for the complete descendant tree. A real Linux
adversarial test proves both calls return `EPERM` and no descendant survives
terminal cleanup. A final cancellation check also runs after QA and candidate
verification before success can be recorded.

The follow-up passed 67 host tests, 68 Linux worker tests, strict host/Linux
Clippy, all four active Linux execution-boundary tests, the Alpine/musl
production image build, and the image sandbox-health smoke test.

## Residual Platform Proof Limits

- Landlock enforcement is Linux-kernel dependent. The worker health check fails
  when `openagents-sandbox --check` cannot install an enforced ruleset, and the
  production image plus one Docker Linux kernel were exercised, but deployment
  kernels still require the same startup health proof.
- Every deployment kernel and architecture must still pass sandbox startup and
  the adversarial Linux boundary suite. Unsupported Linux architectures fail
  closed rather than running untrusted commands without syscall containment.
