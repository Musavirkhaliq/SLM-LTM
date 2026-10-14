# 11 - Security Checklist

Scope: controls for the SLM Studio platform and the data it handles. Priority: **P0** before real tenant data, **P1** before GA, **P2** later.
Statuses use: **Built** (exists in the implemented services per the contract), **Design** (specified here, not yet implemented), **Operator** (the party running the platform must do it).
Implemented services: gateway, auth, registry. Everything else is design-stage, so "Built" claims below are limited to what CONTRACT.md and `0001_init.sql` already define.

## 1. Assets, boundaries, top threats

| Asset | Where | Primary threats |
|---|---|---|
| Tenant prompts/completions (may contain PHI/PII) | In flight through gateway and vLLM; **not persisted by design** | Logging leaks, cross-tenant leakage via shared batching/caches, interception |
| Tenant datasets, RAG documents, embeddings | S3 buckets, vector DB | Cross-tenant retrieval, over-broad credentials, backups |
| Fine-tuned weights/adapters | `models` bucket | Exfiltration, tampering, unsafe deserialisation |
| Credentials: passwords, API keys, refresh tokens, JWT keys, DB/S3 creds | Postgres (hashed), secret manager | Theft, brute force, replay, key confusion |
| Audit and billing records | `audit_logs`, `usage_logs`, `cost_records` | Tampering, deletion |
| GPUs | Cluster | Abuse (crypto-mining via tenant-supplied code), noisy neighbour, denial of service |

Trust boundaries: Internet to ingress; gateway to services; services to data stores; services to Kubernetes API; tenant-supplied artifacts (models, datasets, documents) to the compute that parses them.

## 2. Access control (RBAC)

Roles (CONTRACT.md section 2): `platform_admin` > `org_admin` > `ml_engineer` > `developer` > `viewer`. Enforcement is `slm_common.rbac.require_role(min_role)` as a
route dependency, ordered comparison, role taken from the verified JWT claim `role` (never from the request).

| ID | Control | Pri | Status |
|---|---|---|---|
| RB1 | Every control-plane route declares `require_role`; a CI test enumerates all routes and fails if one lacks it (allow-list for public auth routes) | P0 | Design (test), Built (dependency) |
| RB2 | Role in JWT is re-validated against `org_memberships` at refresh and org switch; a demoted user's old access token is valid for at most 15 min (accepted) or until `jti` denylist (Proposed for removals) | P0 | Design |
| RB3 | Last `org_admin` cannot be removed or demoted | P0 | Design (API 409) |
| RB4 | `platform_admin` is only set by operator SQL; MFA required; every platform-admin action audited; acting inside a tenant is explicit and logged ("act as") | P0 | Design |
| RB5 | API keys carry scope (`organization`/`project`); a project key can reach only that project's aliases/resources (**Proposed** enforcement at gateway) | P1 | Design |
| RB6 | Service-to-service identity: services accept control calls only from the gateway or documented callers (NetworkPolicy now; mTLS/SPIFFE identity later) | P1 | Design |
| RB7 | Database roles: `slm_app` non-owner, no `BYPASSRLS`; migration role separate; per-service DB users once bypass is removed. Today the gateway uses `bypass=True` for key lookup, route resolution and usage inserts ([02](02-database-schema.md) section 3) | P0 | Design |
| RB8 | Kubernetes RBAC: namespaced Roles only; `deployment`/`finetune` may manage workloads in `slm-serving`/`slm-train` only; no `secrets` read | P0 | Design |
| RB9 | Least privilege for object storage: per-service credentials, per-job scoped credentials for trainers | P1 | Design |
| RB10 | Quarterly access review of platform admins, DB superuser, cluster admins | P1 | Operator |

## 3. Authentication and sessions

| ID | Control | Pri | Status |
|---|---|---|---|
| AU1 | Passwords hashed with argon2id (`argon2-cffi`); parameters tuned to ~50-100 ms on production CPUs (library defaults follow RFC 9106 low-memory profile); rehash on login if parameters change | P0 | Built (hash), Design (tuning) |
| AU2 | Password policy: >= 12 characters, check against a breached-password list, no composition rules (NIST SP 800-63B style); registration/login/refresh rate-limited per IP and per account; generic error (no user enumeration) | P0 | Design |
| AU3 | JWT: **verifier fixes the algorithm from configuration, never from the token header** (prevents `alg` confusion; the contract allows RS256 or HS256, so if both exist in the codebase they must use distinct keys and a single configured choice per environment); `kid` header and a JWKS/public-key set to allow rotation; validate `exp`, `iat`, `jti`; 15 min lifetime; prefer RS256 so only `auth` holds the private key | P0 | Design (verify against `slm_common.security`) |
| AU4 | Refresh tokens: opaque, stored as sha256, rotated on every use, reuse of a rotated token revokes the whole `family_id`, absolute lifetime (e.g. 30 d, **Assumption**) | P0 | Built (schema + contract) |
| AU5 | Web: refresh token in `HttpOnly; Secure; SameSite=Strict` cookie via a same-origin route handler; access token in memory only; CSRF protection on cookie-authenticated routes | P0 | Design |
| AU6 | OAuth/OIDC: authorization-code flow with PKCE, `state` and `nonce` validated, redirect URIs allow-listed, email claim trusted only when `email_verified` is true; link by `(provider, subject)` not by email (account takeover guard) | P0 | Design |
| AU7 | MFA: TOTP secret encrypted at rest (`mfa_secret_enc`, AES-256-GCM, key from KMS, versioned format `ver|kid|nonce|ct`), recovery codes hashed; mandatory for `platform_admin` and `org_admin` (plan-configurable) | P1 | Design |
| AU8 | Email verification and password reset flows with single-use, short-lived, hashed tokens | P1 | Gap (no schema) |
| AU9 | Session listing and "log out everywhere" (revoke all families for a user) | P1 | Design |
| AU10 | SSO enforcement per org (disable password login for SSO orgs) | P2 | Gap (no org SSO table) |

## 4. API keys

Format `slm_<8 char prefix>_<40 char secret>`; only `sha256(full key)` is stored (the SQL comment saying "sha256(secret)" is stale, see [02](02-database-schema.md)).
A 40-character secret from a CSPRNG over base62 carries about 238 bits, so an unsalted fast hash is appropriate (no brute-force concern as with passwords).

| ID | Control | Pri | Status |
|---|---|---|---|
| K1 | Generate with `secrets` CSPRNG (`slm_common.security.generate_api_key`); show plaintext once; never log it; never place in audit `detail` | P0 | Built |
| K2 | Lookup by hash with constant-time comparison where any comparison happens in application code; unique index on `key_hash` | P0 | Built (index) |
| K3 | Rotation: new key row with `rotated_from`; old key valid until `grace_until`; default grace 24 h, max 30 d; both visible in UI with countdown | P0 | Built (schema) |
| K4 | Revocation (`revoked_at`) takes effect within the gateway cache TTL (60 s). **Proposed:** push invalidation over Redis pub/sub so revocation of a leaked key is near-instant | P1 | Design |
| K5 | Expiry (`expires_at`) enforced at gateway; UI default expiry suggestion 90 days | P1 | Built (column) |
| K6 | Per-key rate limit (`rate_limit_rpm`) and `last_used_at` updated at most once/minute (avoid write amplification) | P1 | Built/Design |
| K7 | Secret-scanning: register the `slm_[A-Za-z0-9]{8}_[A-Za-z0-9]{40}` pattern with GitHub secret scanning partner program / add gitleaks custom rule so leaked keys are detected | P1 | Design |
| K8 | Optional IP allow-list and allowed-alias list per key | P2 | Design |
| K9 | Rotation reminders: alert org admins for keys older than 90 days or unused for 60 | P2 | Design |
| K10 | Keys cannot call the control plane; JWTs cannot call `/v1/*` (separation of credentials) unless the playground design (OQ-4 in [14](14-roadmap.md)) decides otherwise | P0 | Design |

## 5. Encryption

### 5.1 In transit

| Hop | Control | Pri |
|---|---|---|
| Client to ingress | TLS 1.2+ (prefer 1.3), modern cipher list, HSTS, certificates by cert-manager (ACME or internal CA) | P0 |
| Gateway/services to Postgres, Redis, S3, Qdrant, MLflow | TLS enabled (`sslmode=verify-full`, Redis TLS, HTTPS endpoints); CA distributed via secret | P0 for Postgres/S3, P1 for the rest |
| Gateway to vLLM and service-to-service inside the cluster | **Plaintext HTTP inside NetworkPolicy-isolated namespaces is an accepted risk in the MVP.** mTLS via service mesh or cert-manager-issued certs is the Phase 3 control. For HIPAA tenants treat this as an *addressable* gap to close or to document as a compensating control (private network + policies) | P1 |
| WebSocket | `wss://` only | P0 |

### 5.2 At rest

| Data | Control | Pri |
|---|---|---|
| Node disks / PVs | Full-disk or volume encryption. DGX Spark's NVMe is documented as self-encrypting; confirm and decide who holds the keys, otherwise use LUKS with keys you control. Cloud: provider KMS-encrypted volumes | P0 |
| PostgreSQL | Volume encryption (community PostgreSQL has no TDE); application-level encryption for `mfa_secret_enc`; encrypted backups (pgBackRest AES-256 or Barman encryption) | P0 |
| Object storage | SSE-KMS (cloud) or MinIO SSE with KES/Vault; bucket policies deny unencrypted PUT; optional per-org keys for enterprise (Phase 4) | P0 |
| Vector DB | Volume encryption; snapshots encrypted when exported | P0 |
| Redis | Holds no durable secrets: only hashed-key cache entries, counters, route cache; no persistence | P0 |
| Logs/metrics/traces | Encrypted volumes/buckets; no prompt content (section 8) | P0 |
| Backups | Encrypted, off-site, access separated from production credentials | P0 |

Key management: a KMS (cloud KMS, Vault transit, or HSM-backed) holds data-encryption keys; rotate keys annually; DEK/KEK envelope; access logged.

## 6. Secrets management

| ID | Control | Pri |
|---|---|---|
| SE1 | No secret in Git, images, Helm values or CI logs; gitleaks in CI; `.env.local`/`.env` ignored (the repo's `web/.gitignore` must cover them) | P0 |
| SE2 | Secrets from an external manager (Vault, cloud secret manager) via External Secrets/CSI, or sealed secrets; file mounts preferred over env vars for private keys | P0 |
| SE3 | Rotation schedule and runbook, below | P0 |
| SE4 | Kubernetes secrets encrypted at rest in etcd (k3s: `--secrets-encryption`; managed K8s: envelope encryption with KMS) | P0 |
| SE5 | Different credentials per environment and per service; compromise of one service's DB/S3 credential does not grant another's | P1 |
| SE6 | Break-glass credentials sealed offline, use triggers an alert | P1 |

| Secret | Rotation | Method |
|---|---|---|
| JWT signing key | 90 days | New `kid` published, sign with new, accept old until max token age (15 min + skew) elapses |
| DB application/migration passwords | 90 days or on staff change | CNPG managed roles / secret manager rotation, rolling restart |
| S3 service credentials | 90 days | Dual-key overlap |
| OAuth client secrets | 12 months | Per IdP |
| HF token, W&B key | 12 months or on staff change | Scoped read-only tokens |
| Tenant API keys | Tenant-controlled; recommended 90 days | `rotate` endpoint with grace |
| TLS certificates | Automatic (<= 90 days) | cert-manager |

## 7. Audit log and tamper evidence

Details of the schema and hash-chain specification are in [02](02-database-schema.md) section 5.

| ID | Control | Pri | Status |
|---|---|---|---|
| AL1 | All control-plane mutations call `slm_common.audit.audit(...)` in the same transaction; auth events logged: login success/failure, refresh-reuse detection, MFA changes, key create/rotate/revoke, role changes, org switch, platform-admin actions | P0 | Built (helper), Design (coverage test) |
| AL2 | Append-only enforced by trigger (UPDATE/DELETE) plus `REVOKE TRUNCATE`/statement trigger; `slm_app` has `INSERT, SELECT` only on `audit_logs` | P0 | Built (trigger), Design (rest) |
| AL3 | Hash chain per org: `row_hash = SHA-256(prev_hash || canonical(record))`, appends serialised by advisory lock | P1 | Built in `slm_common.audit`; gaps (ip/request_id not hashed, no verifier, no anchor) in [02](02-database-schema.md) s5 |
| AL4 | Nightly chain verification; mismatch is a P1 incident | P1 | Design |
| AL5 | Daily anchoring of the latest hash per chain to Object Lock (compliance-mode) storage or an external timestamp authority, which defeats a privileged user rewriting the chain | P1 | Design |
| AL6 | Audit reads are themselves audited; audit export for SIEM (syslog/OTLP/webhook) | P2 | Design |
| AL7 | `detail` never contains secrets or prompt content; schema-checked per action type | P0 | Design |
| AL8 | `ip` stored truncated (/24, /48) or encrypted per user for GDPR; decision recorded | P1 | Design |
| AL9 | Retention >= 6 years where HIPAA applies, otherwise per policy; archived partitions keep anchors | P1 | Operator |
| AL10 | Audited client IP must be trustworthy: `slm_common.audit` currently takes the first `X-Forwarded-For` value unconditionally (the gateway's own `trust_forwarded_for` defaults to false). Accept the header only from trusted proxies or pass the verified IP from the gateway; hash `ip` and `request_id` into the chain | P0 | Gap found in code review |

Limitation to state to customers: this is **tamper-evident**, not tamper-proof: a database superuser can alter data; the chain plus external anchors make that detectable.

## 8. Prompt, data and model handling

| ID | Control | Pri | Status |
|---|---|---|---|
| DH1 | **Prompts and completions are not persisted by the platform.** `usage_logs` has no content columns (only ids, tokens, latency, status). Application logs must not include request/response bodies; a log-scrubbing test sends a canary string and greps all log sinks | P0 | Built (schema), Design (test) |
| DH2 | Optional per-org "debug capture" for prompts is off by default, requires `org_admin` opt-in, is encrypted, TTL <= 7 d, audited; not built | P2 | Design |
| DH3 | Tenant data is used only to serve that tenant: fine-tuning on org datasets produces org-owned adapters; no cross-tenant training, no platform-model improvement from tenant data (state in terms) | P0 | Policy |
| DH4 | Traces/spans carry token counts and ids, never message text; attribute allow-list in the OTel pipeline | P0 | Design |
| DH5 | Safe model formats: accept `safetensors`/`gguf`/HF repos with safetensors; **reject pickle-based checkpoints** (`.bin`, `.pt`, `.pkl`) which execute code on load; `trust_remote_code` disabled by default and allowed only for reviewed model repos; pin HF revisions; verify `artifact_sha256` | P0 | Design |
| DH6 | Tenant-supplied models/adapters run in `private`/`dedicated` pods with no egress and a read-only root filesystem; optional sandbox runtime (gVisor/Kata) for untrusted architectures | P1 | Design |
| DH7 | Dataset/document parsing (CSV, Parquet, PDF, DOCX) in resource-limited sandboxed workers: size, row, decompression-ratio limits, timeouts; no network | P0 | Design |
| DH8 | SSRF: no server-side URL fetch from tenant input except an allow-listed HF pull path; if URL ingestion is added, block link-local/metadata/private ranges and re-resolve DNS | P0 | Design |
| DH9 | Vector DB filter (`org_id`, `index_id`) is added server-side, never client-controlled; embeddings are treated as personal data (they can leak source text under inversion) | P0 | Design |
| DH10 | Prompt-injection posture: retrieved RAG text is untrusted input to the model; the platform never executes model output, never lets model output select tools, URLs or SQL; RAG answers carry citations; judge prompts isolate candidate output as data | P0 | Design |
| DH11 | Abuse/DoS: `max_tokens` clamp, `max_model_len`, body size limit, concurrency cap per org, TPM limits; no weights-download endpoint (limits model theft to inference-based extraction, which rate limits and monitoring address) | P0 | Design |
| DH12 | Shared-endpoint isolation caveat (batch + prefix cache timing side channel, [01](01-architecture.md) section 8) documented; per-org cache salt on shared deployments if supported; `private`/`dedicated` for sensitive tenants | P1 | Design |
| DH13 | Retention: datasets, documents, checkpoints deleted on org deletion and per lifecycle rules; erasure covers S3, vector DB, MLflow, caches | P1 | Design |
| DH14 | Content safety: toxicity evaluation metric exists for evaluation; runtime input/output moderation is optional per org (not built) | P2 | Roadmap |

OWASP Top 10 for LLM applications, mapped: prompt injection (DH10), insecure output handling (DH10), model DoS (DH11), supply chain (DH5, DH6, section 10),
sensitive information disclosure (DH1, DH3, DH12), insecure plugin design (no tool execution in platform), excessive agency (none), overreliance and misinformation
(evaluation: faithfulness/hallucination, [15](15-mlops-and-rag.md)), model theft (DH11).

## 9. Application and infrastructure hardening

| ID | Control | Pri |
|---|---|---|
| AP1 | All SQL parameterised (asyncpg `$n`); ruff `S` rules enabled in CI (note `S608` is ignored repo-wide, so reviewers must check string-built SQL manually) | P0 |
| AP2 | Request validation with closed pydantic schemas for `vllm_args`, `hyperparams` (unknown keys rejected; never build shell strings) | P0 |
| AP3 | CORS restricted to the web origin; security headers (CSP, `X-Content-Type-Options`, `frame-ancestors none`, Referrer-Policy); cookies Secure + HttpOnly | P0 |
| AP4 | Error responses never echo internals; `request_id` is the correlation handle; validate/sanitise a client-provided `X-Request-ID` | P0 |
| AP5 | Pod security: non-root, read-only rootfs, drop all capabilities, `seccompProfile: RuntimeDefault`, PSA `restricted` (`baseline` for GPU namespaces) | P0 |
| AP6 | Images: minimal base, digest-pinned, scanned, signed; admission verification (Phase 3) | P1 |
| AP7 | NetworkPolicies default-deny, egress allow-lists ([06](06-infrastructure.md) section 4) | P0 |
| AP8 | Node hardening: CIS benchmark baseline, unattended security updates for the host OS (DGX OS updates managed by NVIDIA channel), SSH keys only, no password SSH | P0 |
| AP9 | Rate limits and lockouts on auth endpoints; WAF/edge protection for the public surface optional | P1 |
| AP10 | Dependency and base-image CVE SLA (critical 7 d, high 30 d: **Assumption**) | P1 |
| AP11 | Kubernetes API server audit logging enabled; GPU device access only for pods requesting it | P1 |
| AP12 | Clock sync everywhere (chrony); JWT skew tolerance <= 60 s | P0 |

## 10. Supply chain

Models are third-party artifacts: record `hf_repo`, **pinned revision hash**, licence and `artifact_sha256` in `model_versions`; mirror to the `models` bucket; scan with a pickle scanner for any legacy files; prefer safetensors.
Container images: SBOM + provenance + cosign signatures ([08](08-cicd.md)). Python/npm lockfiles committed (`uv.lock`, `package-lock.json`); weekly audit job.

## 11. HIPAA-ready control mapping

**"HIPAA-ready" means the platform provides technical controls that can support a covered entity's or business associate's HIPAA Security Rule obligations. HIPAA has no product certification;
nothing here makes an installation "HIPAA compliant". Compliance is a property of the operating organisation's policies, processes and agreements.**
Applicability: the SLM Studio operator is typically a business associate of the covered entity (or a covered entity itself); self-hosted open models mean prompts are not sent to a third-party model API, which removes one common disclosure path but does not remove the operator's duties.

| HIPAA reference | Requirement (abridged) | Platform control | Gap / what the operator must do |
|---|---|---|---|
| 164.308(a)(1)(ii)(A) | Risk analysis | Threat model summary (section 1), this checklist | **Operator**: formal documented risk analysis of the deployed environment, repeated periodically |
| 164.308(a)(1)(ii)(B) | Risk management | Prioritised controls with owners ([10](10-production-checklist.md)) | **Operator**: risk treatment decisions, residual-risk sign-off |
| 164.308(a)(1)(ii)(D) | Information system activity review | `audit_logs`, `usage_logs` (metadata), dashboards, hash-chain verification | **Operator**: scheduled review, evidence of reviews |
| 164.308(a)(3), (a)(4) | Workforce security; information access management | RBAC roles, per-project keys, quarterly access review procedure | **Operator**: onboarding/offboarding, sanctions policy, minimum-necessary role assignment |
| 164.308(a)(5) | Security awareness training | - | **Operator** |
| 164.308(a)(6) | Security incident procedures | Alerting, runbooks, audit trail, key revocation | **Operator**: incident response plan, breach assessment/notification process (Breach Notification Rule 164.400-414) |
| 164.308(a)(7) | Contingency plan: backup, DR, emergency mode, testing | Backup/DR design ([06](06-infrastructure.md) s7), restore drills | **Operator**: implement, test, document; emergency-mode procedure |
| 164.308(a)(8) | Evaluation | Production checklist, pen test scope (section 13) | **Operator**: periodic technical and non-technical evaluation |
| 164.308(b), 164.314(a) | Business associate contracts | - | **Operator**: sign BAAs with covered-entity customers and with every sub-processor that can touch ePHI (cloud, backup, support tooling) |
| 164.310(a)-(d) | Physical safeguards: facility, workstation, device/media controls | - (a DGX Spark is a desktop-class device: lock it in a controlled room/rack, asset-tag it, sanitise media on disposal) | **Operator** |
| 164.312(a)(1), (a)(2)(i) | Access control; unique user identification | Per-user accounts, SSO, no shared logins, JWT `sub` | Disable shared accounts operationally |
| 164.312(a)(2)(ii) | Emergency access procedure | Break-glass `platform_admin` procedure (RB4, SE6) | **Operator**: define and test |
| 164.312(a)(2)(iii) | Automatic logoff (addressable) | 15-minute access tokens, UI idle timeout, refresh absolute lifetime | Configure org policy |
| 164.312(a)(2)(iv) | Encryption/decryption (addressable) | Section 5.2 at-rest encryption | **Operator**: enable and manage keys; prove encryption on every store, including Spark NVMe and backups |
| 164.312(b) | Audit controls | Tamper-evident audit log of administrative actions; usage metadata; **no content logging** | Content-level access logging is not provided because content is not stored; if the customer requires per-request audit of PHI use, usage metadata plus request ids is what exists |
| 164.312(c)(1), (c)(2) | Integrity; mechanism to authenticate ePHI | Artifact checksums, audit hash chain, TLS | |
| 164.312(d) | Person or entity authentication | argon2id, MFA, SSO, API keys | Enforce MFA by policy |
| 164.312(e)(1), (e)(2) | Transmission security; encryption (addressable) | TLS at the edge; internal TLS/mTLS (Phase 3) | Until internal mTLS ships, document the compensating controls (isolated network, NetworkPolicies) in the risk analysis |
| 164.316(b)(2) | Documentation retention 6 years | Audit retention >= 6 years (AL9), policy docs in Git | **Operator** |
| 164.502(b) | Minimum necessary | Per-project scoping, short retention, no content storage | Customer must avoid sending unnecessary PHI in prompts; provide guidance |

Additional platform rules for PHI tenants (**Proposed**): `private` or `dedicated` serving class; `data_region` fixed; prompt/debug capture disabled and enforced by plan; no third-party telemetry
that could carry content; HF Hub egress limited to model-only pulls; evaluation datasets treated as ePHI storage.

## 12. GDPR-ready control mapping

**"GDPR-ready" means the platform provides features that support a controller's/processor's compliance; there is no GDPR certification of software.** The tenant is normally the controller and the platform operator the processor (Art. 28);
if the operator also decides purposes (for example uses data to improve its own models), it becomes a controller for that processing, which DH3 forbids.

| GDPR reference | Requirement | Platform control | Operator / customer must still |
|---|---|---|---|
| Art. 5(1)(c),(e) Minimisation, storage limitation | Collect and keep only what is needed | No prompt/completion persistence; retention schedule per table ([02](02-database-schema.md) s6); lifecycle rules on buckets | Define retention per customer contract; configure |
| Art. 5(1)(f), Art. 32 Security of processing | Appropriate technical measures | Sections 2-10 | Document the measures; maintain them; test them |
| Art. 6, 9 Lawful basis, special categories | | Not a platform function | Customer determines basis; special-category data requires additional safeguards (consider `dedicated` class) |
| Art. 12-15 Transparency and access | Provide data subject access | `GET /api/v1/me`, audit export, usage export per user/org; a "data subject export" job is **Design** | Operate the DSR process and identity verification |
| Art. 16 Rectification | | User profile PATCH (**gap**: no endpoint in contract) | |
| Art. 17 Erasure | Delete personal data without undue delay | `users`: anonymise + `deleted_at` (schema comment); org deletion job removes Postgres rows (cascade), S3 prefixes, vector DB points, MLflow experiments ([02](02-database-schema.md) s6); backups age out per retention window (document the window; data is "put beyond use" until then); audit `ip`/actor handling (AL8) | Execute and evidence erasure; handle legal-hold exceptions (Art. 17(3)); tell customers backups persist until expiry |
| Art. 18-21 Restriction, portability, objection | | Export of datasets/models (owned artifacts) via S3 presigned URLs; account disable (`is_active`) | Process |
| Art. 22 Automated decisions | | Evaluation tooling (accuracy, hallucination) supports oversight | Customer: legal assessment, human review where required |
| Art. 25 Data protection by design/default | | Tenant isolation (RLS), no content storage, default-deny network, short default retention, `data_region` | |
| Art. 28 Processor obligations | DPA with controller; sub-processor control; deletion/return at end | Sub-processor inventory is a documented artifact (cloud, backup, observability) | **Operator**: DPA, sub-processor list and notification, audit rights |
| Art. 30 Records of processing | | Component inventory and data-flow diagrams ([01](01-architecture.md)) feed the record | **Operator**: maintain the record |
| Art. 33-34 Breach notification | 72 h to authority; inform data subjects when high risk | Detection (alerts, audit chain, anomaly), forensics via audit and logs | **Operator**: incident and notification procedure, contact points |
| Art. 35 DPIA | Required for high-risk processing (LLMs on personal data often qualify) | This documentation provides inputs | **Operator/customer**: perform and sign the DPIA |
| Art. 37-39 DPO | | | Appoint if required |
| Ch. V (Art. 44-49) International transfers | | `organizations.data_region` pins an org to a region cell; no tenant data leaves the cell by design; HF Hub egress carries model pulls only | Choose regions, SCCs/transfer impact assessments if any cross-border admin access exists (support staff outside the region count) |
| Art. 4(1), recital 26 Personal data scope | | Embeddings and fine-tuned weights may encode personal data; they are deleted with the org (DH13); fine-tuned weights trained on personal data may themselves be personal data | Legal view per use case |

Adjacent regimes not covered here: EU AI Act duties (for example obligations when fine-tuning and placing a general-purpose model on the market), sector rules (PCI DSS, FedRAMP), national data-localisation laws.

## 13. "Ready is not certified": what the operator must still do

| Item | HIPAA | GDPR | Owner |
|---|---|---|---|
| Sign agreements (BAA / DPA) with customers and sub-processors | Yes | Yes | Legal |
| Formal risk analysis / DPIA / records of processing | Yes (164.308(a)(1)) | Yes (Art. 30, 35) | Compliance |
| Independent penetration test and remediation (scope below) | Strongly expected | Art. 32(1)(d) testing | Security |
| Policies, training, sanctions, incident response, breach notification | Yes | Yes | Compliance/HR |
| Appoint security official / DPO and EU representative where required | Security official | DPO/Art. 27 rep | Management |
| Physical security of Spark/H100 hardware and media disposal | Yes (164.310) | Art. 32 | Facilities |
| Vendor management for every cloud/SaaS in the data path | Yes | Yes | Procurement |
| Operate the controls (reviews, access recertification, restore drills) and keep evidence | Yes | Yes | Operations |
| Independent assurance if customers demand it: SOC 2 Type II, ISO 27001/27701, HITRUST | Optional | Optional | Management |
| Model-licence compliance and AI-specific governance (acceptable-use terms, EU AI Act analysis) | n/a | n/a | Legal/Product |

**Pen test scope (minimum):** tenant isolation (RLS bypass attempts through every list/detail endpoint, FK-reference oracle, cursor tampering), JWT attacks (`alg` confusion, `kid` injection, replay after logout), refresh-family reuse, API-key brute force and cache poisoning,
OAuth flow abuse, SSE/WebSocket auth, SSRF via model pull, malicious model/dataset/document uploads (pickle, zip bombs, parser exploits), vector-DB filter injection, prompt-injection-to-data-exfiltration via RAG, container escape/lateral movement from vLLM or trainer pods, Kubernetes RBAC misconfiguration, secrets exposure in logs/traces.
