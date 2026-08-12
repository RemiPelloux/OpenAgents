# T12 Trusted Delivery Signature Closure

## Outcome

Fixed. Trusted delivery now accepts a candidate only when the exact claimed
commit has a valid OpenPGP signature made by the explicitly configured worker
public key and primary fingerprint. Coding retains private signing material in
zeroizing storage after removing its source environment entry, while delivery
rejects private material and receives only public trust configuration.

## Revalidated Paths

### Private signing path

- Source: `OPENAGENTS_GIT_SIGNING_KEY_B64` enters `Config::from_env` for a
  coding worker.
- Boundary: `take_secret_env` removes the process environment entry immediately
  and stores the value as `Arc<Zeroizing<String>>` so cloned configuration does
  not create additional ordinary `String` copies.
- Sink: `commit_changes` borrows the encoded key only while creating an
  ephemeral mode-0700 `GNUPGHOME`; decoded bytes are held in `Zeroizing<Vec<u8>>`
  only through import, and the agent/home are destroyed on drop.
- Role invariant: coding rejects delivery public-key/fingerprint inputs;
  delivery rejects the private signing input.

### Delivery trust path

- Source: `OPENAGENTS_GIT_SIGNING_PUBLIC_KEY_B64` and
  `OPENAGENTS_GIT_SIGNING_FINGERPRINT` are required for the delivery role.
- Boundary: bounded base64 input is decoded, imported into a fresh mode-0700
  `GNUPGHOME`, required to contain exactly one primary public key matching the
  normalized configured fingerprint, and rejected if any secret key is present.
- Sink: after the candidate HEAD is proven equal to the signed delivery claim,
  `git verify-commit --raw <claimed-commit-sha>` runs with only that ephemeral
  trust home. Exactly one `VALIDSIG` record is required, and its signer or
  primary-key fingerprint must match the configured fingerprint.
- Health invariant: delivery health requires GnuPG plus successful trust-key
  import and fingerprint validation, so missing, malformed, wrong, or
  unavailable trust configuration fails closed.
- Cleanup: `gpg-agent` is killed before the temporary trust home is destroyed.

### Runtime and deployment separation

- Local Compose and AWS OpenBrain Compose run distinct coding and delivery
  services. Coding receives the private key only; delivery receives the public
  key and fingerprint only and has no LLM/OpenCode/private signing variables.
- The generic AWS ECS stack now has a distinct delivery service consuming only
  the public trust secrets. It resolves the existing configured GitHub token
  through a tenant-scoped Secrets Manager connector and a resource-scoped task
  role permission; no credential value is fabricated or embedded.
- Secret declarations, activation, Terraform variables/examples, lifecycle
  contracts, and provisioning documentation carry the public key and expected
  fingerprint end to end.
- T11's final cancellation check remains after QA and candidate verification,
  before a coding run can return terminal success.

## Regression Proof

`candidate_signature_requires_the_configured_public_key_and_fingerprint`
generates two real OpenPGP identities and a real signed Git candidate. The
configured signer passes, a missing key fails with
`DELIVERY_GIT_TRUST_REQUIRED`, and a different valid key/fingerprint fails
signature verification.

Configuration and deployment contracts additionally prove:

- the private source environment entry is absent immediately after ingestion;
- delivery cannot start with private signing material;
- delivery requires public trust material;
- coding and delivery job capabilities remain disjoint;
- local Compose, AWS OpenBrain Compose, and generic AWS ECS preserve role and
  key separation.

## Verification

Passed:

- `cargo test --workspace --locked` (host): 70 tests.
- `cargo test --locked -p openagents-worker candidate_signature_requires_the_configured_public_key_and_fingerprint -- --nocapture` (host): 1 test.
- The same generated-key regression in `rust:1.90-bookworm` with GnuPG: 1 test.
- Full Linux workspace run performed for this shared patch: 71 unit tests and
  4 active execution-boundary integration tests; 2 helper tests are intentionally ignored.
- `cargo clippy --workspace --all-targets --locked -- -D warnings` on host.
- The same strict Clippy command in `rust:1.90-bookworm`.
- `cargo fmt --all -- --check`.
- `bash scripts/mesh/tests/lifecycle-test.sh`.
- `bash infra/aws/pipeline/tests/terraform-contract-test.sh`.
- `bash infra/aws-openbrain/tests/pipeline-contract-test.sh`.
- `bash -n` for all modified shell scripts and tests.
- `terraform -chdir=infra/aws fmt -check -recursive`.
- `terraform -chdir=infra/aws-openbrain fmt -check -recursive`.
- `terraform -chdir=infra/aws-openbrain validate`.
- AWS OpenBrain Compose `config --quiet --no-interpolate`.
- Production `openos/openagents-worker:t12` image build.
- Production image GnuPG, AWS CLI, and `openagents-sandbox --check` smoke test.
- Production image startup rejects missing trust material and private delivery
  signing material.
- Root and OpenAgents `git diff --check`.

Coordinator integration follow-up:

- `terraform -chdir=infra/aws validate` now passes. The OpenBrain CodeBuild
  scan runs Gitleaks without a repository baseline, removing the invalid
  cross-checkout file dependency without suppressing findings.
- The repository-supported merged lifecycle Compose test passes, and the full
  merged profile expands successfully with `.openos/local.env`.
- Runtime LLM base URL and model values are environment-owned; Compose and W8
  setup contain no provider endpoint or model fallback.

## Changed Files

- `crates/openagents-worker/src/config.rs`
- `crates/openagents-worker/src/delivery.rs`
- `crates/openagents-worker/src/runtime.rs`
- `crates/openagents-worker/T12_TRUSTED_SIGNATURE_REPORT.md`
- `compose.local.yml`
- `docs/ENGINEERING-WORKSPACES.md`
- `infra/aws-openbrain/OPENPRO-DEPLOYMENT.md`
- `infra/aws-openbrain/README.md`
- `infra/aws-openbrain/compose/docker-compose.yml`
- `infra/aws-openbrain/scripts/activate-release.sh`
- `infra/aws-openbrain/secrets.tf`
- `infra/aws-openbrain/tests/pipeline-contract-test.sh`
- `infra/aws/openagents.tf`
- `infra/aws/pipeline/tests/terraform-contract-test.sh`
- `infra/aws/secrets.tf`
- `infra/aws/terraform.tfvars.example`
- `infra/aws/tests/stack.tftest.hcl`
- `infra/aws/variables.tf`
- `scripts/mesh/tests/lifecycle-test.sh`

## Residual Risk

The private key must still enter the coding process once through the deployment
environment because this repository has no native external signer. Its exposure
is limited to coding, removed from the live process environment immediately,
held in zeroizing shared storage, decoded only for ephemeral import, and never
passed to delivery. Operational rotation and access policy for the externally
provisioned OpenPGP key pair remain deployment responsibilities.
