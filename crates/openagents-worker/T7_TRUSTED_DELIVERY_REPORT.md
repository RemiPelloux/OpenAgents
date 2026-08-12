# T7 Trusted Delivery Executor

## Outcome

OpenAgents now claims `engineering.delivery` as a distinct trusted worker phase. The phase consumes the persisted local candidate without invoking OpenCode or QA, resolves a tenant-scoped connector only after all claim and candidate checks pass, performs an exact non-force branch delivery, creates or reuses one draft pull request through a provider adapter, and submits the signed provider callback expected by OpenOrchestrator.

Deployment uses disjoint `coding` and `delivery` roles. The coding role cannot claim delivery jobs and has no connector mount. The delivery role cannot claim coding jobs, receives no LLM/OpenCode or Git commit-signing configuration, and sees candidate worktrees and connectors read-only with locks isolated on tmpfs.

The pre-existing local-candidate, redaction, H-ACN, and Landlock changes in the working tree were preserved.

## Accepted Contract

- Job type and capability must both be `engineering.delivery`.
- `inputs.request` must be a current Ed25519-signed `openos.worker/v1` Claim from `OpenOrchestrator` to `OpenAgents`.
- Envelope organization, correlation, idempotency key, and expiry must match the leased job.
- Plan, task, ticket, organization, deterministic `delivery/<source_job_id>` remote branch, and callback route must match the signed subject.
- Commit, diff, candidate, and approval digests must be exact lowercase-compatible 40/64-hex values. Candidate and approval digests are recomputed locally using the OpenOrchestrator algorithms.
- Every signed delivery requirement must be true; only the registered GitHub adapter is currently accepted.
- The canonical candidate must equal `OPENOS_MANAGED_WORKTREE_ROOT/<organization>/<plan>/<safe_repository_name>`, contain its own `.git` directory with no alternates or external gitdir, remain on the deterministic OpenAgents local branch, have the exact signed HEAD, a clean index/worktree, a valid signed commit, and the exact persisted diff digest. Materialization copies objects without hardlinks so OpenSec and delivery remain functional without the source-cache mount.
- Local Git configuration that can dispatch helpers, hooks, filters, alternate URLs, credentials, proxies, or custom signature programs is rejected before status/diff/signature commands.
- The registered remote must be credential-free and resolve to the signed GitHub repository identity.

## Connector And Provider Boundary

- Local references resolve only as `local-connector://orgs/<organization>/github/...` below `OPENAGENTS_CONNECTOR_ROOT` (default `/run/openos/connectors`), with canonical containment and a 16 KiB file bound.
- AWS references resolve only from the approved EU region set and `orgs/<organization>/github/...` secret namespace through an `env_clear` AWS CLI subprocess with explicit workload-identity variables.
- Connector material is zeroized on drop, never included in logs, artifacts, callbacks, command arguments, Git configuration, or pull-request content.
- Every Git subprocess uses `env_clear`, fixed locale/PATH/config variables, and an explicit GnuPG home. Authenticated Git uses a 0700 askpass helper whose script contains no credential; local credential helpers and hooks are disabled/rejected.
- Push uses the exact commit-to-branch refspec with no force option. An existing branch is reused only at the exact commit; a different commit is a hard conflict.
- The GitHub adapter looks up the exact open base/head subject, accepts exactly one draft PR, creates only when absent, treats a concurrent GitHub 422 as a lookup race, and authoritatively re-reads cardinality and identity.

## Callback Contract

OpenAgents signs a Complete envelope to `POST /v1/deliveries/<delivery_id>/provider` with idempotency key `delivery:<delivery_id>:provider:<approval_subject_digest>`. The payload binds the current job lease and worker, repository/base/branch/commit/diff/candidate/approval/provider identities, exact draft PR URL/number/status, and all seven required verification flags.

Transient callback failures reuse one signed subject. If the callback commits but its response is lost, OpenAgents reads the delivery and accepts success only when `provider_result` is the exact persisted, valid OpenAgents-signed envelope; lease heartbeats tolerate settlement conflicts only after callback settlement has started.

## Verification

- `cargo test --workspace --locked`: passed, 59 tests across both worker binaries.
- `cargo clippy -p openagents-worker --all-targets --locked -- -D warnings`: passed.
- `cargo fmt --all -- --check`: passed.
- `git diff --check`: passed.
- Focused tests cover malformed, forged, stale, wrong-tenant, and wrong-approval claims; role and capability separation; path escape; wrong HEAD and local branch; dirty state; wrong remote and connector identity; rejection of HTTPS userinfo and unsafe SSH identities; self-contained candidate survival after source-cache removal; credential-free askpass construction; bounded OpenCode, QA, and provider subprocesses; existing/conflicting branch decisions; concurrent and existing PR reuse; callback retry failure; wrapped callback recovery; adversarial secret redaction; and lost-response confirmation from the exact signed persisted callback.

## Residual Live Risks

- No real tenant connector, approved delivery, or disposable live GitHub repository was available, so a live push/PR/OpenOrchestrator trace remains unexecuted. The provider boundary is covered with real local HTTP and Git subprocesses plus deterministic fakes, not a production GitHub call.
- The GitHub adapter currently supports `github.com`; GitHub Enterprise hosts require a separately registered adapter/host contract.
- AWS Secrets Manager resolution depends on runtime ECS or web-identity credentials and the installed AWS CLI; that live workload-identity path was not available for validation.
- Repository provisioning must mount a tenant connector file below `/run/openos/connectors/orgs/<organization>/github/...`. Root local and AWS Compose definitions now provide a dedicated delivery role with read-only connector and candidate mounts.
