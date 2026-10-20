# Production Plan: Central Airflow Development Platform

> Created: 2026-09-01
>
> Status: Proposed implementation roadmap
>
> Scope: A centrally hosted, enterprise-authenticated platform where each approved user receives an isolated Airflow development workspace after login.

## 1. Executive recommendation

Build this as a production-grade **developer platform**, not as the place where production DAG workloads execute.

Use JupyterHub as the central portal, deploy it to a managed Kubernetes cluster with the Zero to JupyterHub Helm chart, authenticate users through the enterprise OpenID Connect (OIDC) provider, and let KubeSpawner create one isolated workspace pod and persistent home volume per user. Each workspace can contain JupyterLab, VS Code, the Airflow UI, and a development scheduler.

Production DAGs should move through Git and CI/CD into a separate production Airflow deployment. That deployment should use the official Airflow Helm chart, an external PostgreSQL database, remote logging, a secrets backend, and a distributed executor. Apache Airflow explicitly treats SQLite as a testing-only backend and recommends an external database and KubernetesExecutor or CeleryExecutor for multi-node production deployments ([Airflow production guidance](https://airflow.apache.org/docs/apache-airflow/stable/administration-and-deployment/production-deployment.html)).

This separation gives users the interactive environment they want without turning notebook pods into an ungoverned production scheduler fleet.

## 2. Assumptions and decisions to confirm

This plan assumes:

- Users are employees or trusted contractors, not mutually untrusted public customers.
- The central platform is for DAG authoring, debugging, testing, and controlled access to approved development data.
- Each user gets a logically isolated workspace; users do not share an Airflow metadata database.
- Production DAG execution occurs in a separately managed Airflow environment.
- The organization already has, or will select, an OIDC provider such as Microsoft Entra ID, Okta, Keycloak, PingFederate, or another standards-compliant provider.
- A managed Kubernetes service, managed PostgreSQL, object storage, DNS, TLS certificates, and an OCI image registry are available.

Before implementation, record these architecture decisions:

| Decision | Recommended default | Alternative requiring extra design |
| :-- | :-- | :-- |
| User trust boundary | Trusted workforce in one platform namespace with strong pod/network isolation | Separate namespace or cluster per team for stronger multi-tenancy |
| Workspace state | Persistent user home; disposable runtime state | Persistent per-user Airflow run history |
| Airflow versions | A small, curated set of prebuilt images | Arbitrary runtime installation, which increases supply-chain and support risk |
| Production workloads | Separate official Airflow deployment | Running production scheduling inside user pods, which is not recommended |
| Cloud access | Workload identity and short-lived credentials | Static access keys, which should be avoided |
| Deployment | Helm plus GitOps | Hand-maintained raw manifests and imperative scripts |

If users belong to organizations with weak mutual trust, treat this as hard multi-tenancy. Kubernetes notes that namespaces alone do not provide full isolation; RBAC, quotas, network policy, workload identity, and possibly separate control planes are required ([Kubernetes multi-tenancy guidance](https://kubernetes.io/docs/concepts/security/multi-tenancy/)).

## 3. Target architecture

```text
Corporate user
  -> enterprise IdP (OIDC + MFA + conditional access)
  -> corporate DNS / WAF or load balancer
  -> TLS ingress
  -> JupyterHub
       -> OIDC authorization from IdP group claims
       -> Hub state in managed PostgreSQL
       -> KubeSpawner
            -> one user workspace pod
                 - JupyterLab
                 - VS Code behind jupyter-server-proxy
                 - Airflow development API/UI
                 - development scheduler + DAG processor
                 - non-root runtime and restricted service account
                 - per-user PVC mounted as the workspace
            -> NetworkPolicy, quota, resource limits, idle culling

Git repository
  -> CI validation, security scanning, DAG tests
  -> artifact/image registry
  -> GitOps promotion
  -> separate production Airflow deployment
       - official Airflow Helm chart
       - external PostgreSQL/PgBouncer
       - KubernetesExecutor or CeleryExecutor
       - remote task logs
       - enterprise secrets backend
       - monitoring and alerting
```

### Control plane

Deploy the Hub with the maintained Zero to JupyterHub Helm chart rather than the custom `k8s/hub.yaml`. Keep the Hub, proxy, ingress, and supporting controllers on a small, stable system node pool. Use a managed PostgreSQL database for Hub state so the Hub is not tied to a single SQLite PVC.

Do not assume that increasing the Hub Deployment replica count makes JupyterHub active-active. Design for one active Hub with automated restart, durable external state, PodDisruptionBudget, anti-affinity for supporting components, database backups, and a tested recovery procedure unless the selected JupyterHub release and architecture explicitly support another topology.

### User data plane

Use KubeSpawner to create a pod when a user starts a server. Use a separate autoscaling node pool for user pods, with taints and tolerations so user workloads cannot exhaust control-plane capacity. JupyterHub's Kubernetes guidance recommends a dedicated autoscaling user node pool, resource requests/limits, image pre-pulling, user placeholders, pod priority, and the user scheduler to improve startup and scale-down behavior ([JupyterHub optimization guidance](https://z2jh.jupyter.org/en/stable/administrator/optimization.html)).

Each user gets:

- A stable username derived from an immutable, unique OIDC claim.
- One PVC for notebooks, DAG source, editor state, and other approved files.
- A workspace pod created from a curated image selected through a profile.
- Explicit CPU, memory, ephemeral-storage, and maximum-session limits.
- A service account with no Kubernetes API permissions; disable automatic token mounting unless a profile genuinely needs it.
- Default-deny ingress and egress policies with only required destinations allowed.
- An idle timeout and optional maximum server lifetime.

Do not use node `hostPath` storage for user data in production. Use a CSI-backed encrypted StorageClass that supports dynamic provisioning, expansion, and snapshots. Define PVC retention and deletion policies, backup schedules, and a user-offboarding process. Kubernetes VolumeSnapshot support depends on the CSI driver and snapshot controller ([Kubernetes volume snapshots](https://kubernetes.io/docs/concepts/storage/volume-snapshots/)).

## 4. Enterprise OIDC design

Replace `NativeAuthenticator`, open signup, and local passwords with `oauthenticator`'s `OIDCOAuthenticator`. It uses the provider's OIDC discovery metadata and supports standard OIDC configuration ([OIDCOAuthenticator setup](https://oauthenticator.readthedocs.io/en/latest/tutorials/provider-specific-setup/providers/oidc.html)).

### Identity-provider configuration

Register one confidential web application with:

- Redirect URI: `https://<platform-domain>/hub/oauth_callback`.
- Authorization Code flow.
- `openid`, `profile`, `email`, and the provider-specific group scope.
- MFA and the organization's conditional-access policies.
- No wildcard redirect URIs.
- A short-lived client secret initially, preferably moving to a supported secretless/private-key mechanism if the IdP and authenticator support it.
- At least two enterprise groups: platform users and platform administrators. Add team/profile groups if access varies by data domain or resource tier.

### JupyterHub authentication policy

Configure:

- `c.JupyterHub.authenticator_class = "oidc"`.
- `openid_provider_url`, client ID, callback URL, scopes, and the client secret from a secret store.
- `username_claim` as an immutable, unique, non-reassignable claim. The OIDC authenticator defaults to `sub`; changing it to email should happen only if the IdP guarantees permanence and uniqueness.
- Group extraction from the appropriate token/userinfo claim.
- `manage_groups = True` if IdP groups should be authoritative in JupyterHub.
- An explicit `allowed_groups` policy. Do not use `allow_all` unless every account in the IdP tenant is meant to receive platform access.
- Least-privilege JupyterHub roles mapped from approved groups. Avoid granting full JupyterHub admin merely to support operations.
- Authentication refresh before spawning and a bounded authentication/session age so removed users do not retain long-lived access.
- Logout behavior compatible with the IdP's end-session endpoint where supported.

OAuthenticator authorization rules are additive, and `allow_all` overrides other allow restrictions. Access removal at the IdP may only prevent later logins unless sessions and the Hub user database are also handled, so offboarding and refresh behavior must be explicitly tested ([OAuthenticator access rules](https://oauthenticator.readthedocs.io/en/latest/topic/allowing.html)). JupyterHub also supports authenticator-managed groups and fine-grained role scopes ([JupyterHub authenticator-managed groups and roles](https://jupyterhub.readthedocs.io/en/stable/reference/authenticators.html)).

### Example configuration shape

Keep values secret-free in Git; the exact property names must be checked against the pinned authenticator version.

```python
c.JupyterHub.authenticator_class = "oidc"

c.OIDCOAuthenticator.openid_provider_url = os.environ["OIDC_ISSUER_URL"]
c.OIDCOAuthenticator.client_id = os.environ["OIDC_CLIENT_ID"]
c.OIDCOAuthenticator.client_secret = os.environ["OIDC_CLIENT_SECRET"]
c.OIDCOAuthenticator.oauth_callback_url = os.environ["OIDC_CALLBACK_URL"]
c.OIDCOAuthenticator.scope = ["openid", "profile", "email", "groups"]
c.OIDCOAuthenticator.username_claim = "sub"
c.OIDCOAuthenticator.auth_state_groups_key = "oauth_user.groups"
c.OIDCOAuthenticator.manage_groups = True
c.OIDCOAuthenticator.allowed_groups = {"airflow-platform-users"}

c.JupyterHub.load_roles = [
    {
        "name": "platform-operator",
        "groups": ["airflow-platform-admins"],
        "scopes": [
            "admin-ui",
            "list:users",
            "read:users",
            "read:servers",
            "servers",
        ],
    }
]
```

Do not copy this directly into production until the chosen IdP's claims, group format, token size, logout behavior, and the pinned `oauthenticator` API have been verified in staging.

## 5. Workspace image strategy

Replace the current branch-switching build process and runtime package installation with a normal CI image matrix.

### Recommended image model

- Maintain one workspace Dockerfile with build arguments or generated lock files for each supported Airflow/Python combination.
- Build a deliberately small support matrix, for example one current Airflow 3 version and one temporary migration image.
- Base on an official, supported Airflow image where practical, adding JupyterLab and code-server in a controlled layer.
- Pin operating-system packages, Python packages, Airflow providers, JupyterHub components, and extensions through lock files or immutable image digests.
- Run as a non-root UID/GID with a writable PVC and temporary directories; do not start Jupyter with `--allow-root`.
- Use `tini` or another proper init process and a supervised startup model. Replace the threaded `scheduler_wrapper.py` process launcher with managed processes, health endpoints, and graceful shutdown.
- Add startup, readiness, and liveness probes for Jupyter and the Airflow development services.
- Make the root filesystem read-only where compatible; use explicit writable volumes for home, `/tmp`, logs, and Airflow state.
- Do not put default passwords or generated credentials into the image.

### Supply-chain controls

CI must:

1. Build without checking out or stashing other branches.
2. Run unit, DAG parsing, container, and smoke tests.
3. Generate an SBOM.
4. Scan the OS and Python dependency layers.
5. Fail on an agreed severity and exploitability policy.
6. Sign images and attach provenance/attestations.
7. Push to a private OCI registry using immutable semantic tags and digests.
8. Update the Helm/GitOps configuration to reference a digest, never `latest`.
9. Promote the same digest through development, staging, and production.

The Kubernetes deployment should use registry authentication or workload identity and an appropriate pull policy. Remove `imagePullPolicy: Never` and all assumptions that images exist on local cluster nodes.

## 6. Airflow workspace behavior

### Recommended sandbox mode

The user pod is a disposable development environment:

- DAG source is persistent on the user's PVC and synchronized with Git.
- Airflow run history can be disposable, or stored in a per-user SQLite file on the PVC if the limitations are acceptable.
- The Airflow scheduler, API server/webserver, and DAG processor are for development only.
- Users cannot schedule organization-critical recurring workloads from these pods.
- Outbound access is limited to approved development systems.
- Credentials are short-lived and scoped to development resources.

Never point all workspaces at one shared Airflow metadata database. It would mix identities, DAG definitions, connections, task state, and administrative access across users.

If persistent, concurrent, or team-shared development Airflow is required, provision a separate Airflow environment per team rather than extending the per-user notebook pattern indefinitely. That environment should use external PostgreSQL and the official Airflow Helm chart.

### Production DAG path

```text
User edits/tests DAG in workspace
  -> creates pull request
  -> CI: lint + unit tests + DAG parse/import tests + policy checks
  -> review and approval
  -> artifact/image build
  -> staging Airflow deployment
  -> integration tests
  -> approved GitOps promotion
  -> production Airflow deployment
```

The production Airflow deployment should use:

- The official Airflow Helm chart, which supports automatic database migration, multiple executors, and monitoring integrations ([official Airflow Helm chart](https://airflow.apache.org/docs/helm-chart/stable/index.html)).
- Managed PostgreSQL with high availability, encryption, automated backups, point-in-time recovery, and PgBouncer where appropriate.
- KubernetesExecutor when task-level pod isolation and elastic Kubernetes execution are preferred, or CeleryExecutor when a persistent worker fleet is operationally better.
- Remote logs in object storage or the enterprise logging platform.
- A supported secrets backend and workload identity.
- Multiple schedulers/API components where supported, health probes, PodDisruptionBudgets, anti-affinity, and tested rolling upgrades.
- Metadata retention and scheduled cleanup. The Airflow Helm production guide recommends an external production database and supports periodic metadata cleanup ([Airflow Helm production guide](https://airflow.apache.org/docs/helm-chart/stable/production-guide.html)).

## 7. Kubernetes and platform hardening

### Ingress and transport security

- Replace NodePort exposure with an ingress or gateway connected to the approved internal/public load balancer.
- Require TLS 1.2 or later and redirect HTTP to HTTPS.
- Store certificates in the organization's certificate-management system or automate them with the approved controller.
- Restrict the Hub API service to cluster-internal traffic.
- Apply appropriate secure cookie, HSTS, CSP, request-size, and timeout policies after compatibility testing.
- Place a WAF and rate limiting in front of the platform where required by corporate policy.
- Ensure code-server and Airflow are reachable only through authenticated JupyterHub proxy routes, never through separate public Services.

### Pod security

Enforce the Kubernetes `restricted` Pod Security Standard where possible. It is stable through Pod Security Admission and is applied with namespace labels ([Pod Security Admission](https://kubernetes.io/docs/concepts/security/pod-security-admission/)). Workspace pods should have:

- `runAsNonRoot: true` with fixed UID/GID.
- `allowPrivilegeEscalation: false`.
- All Linux capabilities dropped.
- `seccompProfile: RuntimeDefault`.
- No privileged mode, host network, host PID/IPC, Docker socket, or hostPath.
- A read-only root filesystem where the toolchain permits it.
- Approved AppArmor/SELinux policy where supported.
- No Kubernetes service-account token unless explicitly required.

### Network isolation

Start user namespaces with default-deny ingress and egress. Kubernetes pods can otherwise communicate broadly by default, and NetworkPolicy enforcement requires a compatible CNI ([Kubernetes multi-tenancy guidance](https://kubernetes.io/docs/concepts/security/multi-tenancy/)). Allow only:

- DNS.
- JupyterHub/proxy communication.
- OIDC endpoints where the Hub requires them.
- Approved Git, registry, package mirror, object storage, secrets, observability, and development data endpoints.
- Explicit same-user or same-team traffic if the architecture requires it.

Block cloud instance metadata from user pods. The JupyterHub Helm chart includes network-policy controls intended to prevent user access to the metadata API when a supported NetworkPolicy controller is installed ([JupyterHub security guidance](https://z2jh.jupyter.org/en/latest/administrator/security.html)).

### RBAC and tenancy

- Give the Hub service account only the namespace-scoped resources and verbs KubeSpawner needs.
- Give user pods no rights to create pods, exec into other pods, list secrets, or access the Kubernetes API.
- Separate platform administration from user authorization.
- Use ResourceQuota and LimitRange to prevent noisy neighbors.
- Use separate namespaces for platform components and user workloads; use per-team namespaces if policy or trust boundaries require them.
- Apply admission policy to reject privileged pods, unapproved registries, unsigned images, missing limits, and mutable tags.

### Secrets and workload identity

- Store OIDC client credentials, database URLs, signing keys, and platform secrets in the enterprise secret manager.
- Synchronize or mount them through the approved external-secrets operator or Secrets Store CSI driver.
- Enable Kubernetes Secret encryption at rest and least-privilege RBAC. Kubernetes Secrets are base64-encoded and are not encrypted in etcd by default unless the cluster is configured accordingly ([Kubernetes secrets good practices](https://kubernetes.io/docs/concepts/security/secrets-good-practices/)).
- Use cloud workload identity for object storage, registries, and managed services.
- Never place long-lived cloud keys, OIDC secrets, Airflow Fernet keys, or database passwords in Git, Helm values, images, notebooks, or user-visible environment variables.
- Define rotation and emergency-revocation procedures.

## 8. State, backup, and disaster recovery

| State | Production target | Backup/recovery requirement |
| :-- | :-- | :-- |
| JupyterHub state | Managed PostgreSQL | Automated backups, PITR, documented restore |
| User workspace | Encrypted CSI PVC per user | Scheduled snapshots/backups, retention policy, restore test |
| DAG source | Enterprise Git | Protected branches and repository backup |
| Images | Private OCI registry | Immutable retention and replication |
| Platform configuration | GitOps repository | Protected branch, review, signed commits if required |
| Production Airflow metadata | Separate managed PostgreSQL | HA, automated backup, PITR, tested restore |
| Task logs | Object storage/log platform | Lifecycle, retention, access control |
| Secrets | Enterprise secret manager | Versioning, rotation, break-glass procedure |

Define measurable targets before launch, for example:

- Hub recovery time objective: 30 minutes or less.
- Hub state recovery point objective: 15 minutes or less.
- User workspace recovery point objective: 24 hours or less unless business requirements demand more.
- Production Airflow targets defined separately according to workload criticality.

Run restore exercises at least quarterly. A backup is not considered operational until restoration has been demonstrated in a clean environment.

## 9. Observability and operations

Collect centrally:

- Hub, proxy, ingress, KubeSpawner, and user-pod logs.
- Kubernetes events, pod lifecycle state, pending reasons, restarts, OOM kills, evictions, and PVC failures.
- OIDC login failures and authorization denials without logging tokens or secrets.
- Spawn latency, successful/failed spawn rate, active users, idle-cull results, and image-pull latency.
- CPU, memory, disk, ephemeral storage, node capacity, quota consumption, and autoscaler behavior.
- PostgreSQL availability, connection saturation, storage, replication, and backup status.
- Certificate expiry, image vulnerabilities, policy violations, and secret-rotation age.

Initial service-level objectives should include:

- Central login availability.
- Successful workspace spawn rate.
- P95 workspace startup time for warm and cold nodes.
- Time to recover a failed Hub.
- Percentage of successful scheduled backups and restore drills.

Alert on user-impacting symptoms rather than every pod restart. Maintain runbooks for login failure, IdP outage, spawn failure, exhausted quota, full PVC, image pull failure, database outage, ingress/certificate failure, and compromised credentials.

## 10. Repository changes required

### Replace or retire

| Current implementation | Production change |
| :-- | :-- |
| `k8s/hub.yaml` hand-written Hub Deployment/NodePort | Replace with pinned Zero to JupyterHub Helm dependency and environment values |
| `NativeAuthenticator` with open signup | Replace with enterprise `OIDCOAuthenticator` and group authorization |
| JupyterHub SQLite PVC | Move Hub state to managed PostgreSQL |
| `hostPath` DAG/log/plugin mounts | Use per-user CSI PVCs and approved Git/object-storage integration |
| `imagePullPolicy: Never` and local tags | Use private registry, digests, pull credentials/workload identity |
| `build.sh` branch switching and stashing | Replace with CI matrix builds from explicit sources |
| `deploy.sh` imperative `kubectl apply` | Replace with Helm/GitOps promotion and rollback |
| Root processes and `--allow-root` | Run workspace and Hub containers as non-root |
| `admin/admin`, no Jupyter token, unauthenticated code-server | Remove default Airflow credentials; expose all workspace services only through authenticated Hub routes |
| Threaded scheduler launcher | Replace with supervised processes, probes, and graceful lifecycle handling |
| Arbitrary runtime Airflow installation on `unified` | Prefer curated, scanned, immutable workspace images |
| Shared namespace with permissive networking | Add namespaces/trust boundaries, default-deny NetworkPolicies, quotas, and Pod Security Admission |

### Proposed repository layout

```text
.
├── images/
│   ├── workspace/Dockerfile
│   ├── workspace/requirements/
│   └── hub/Dockerfile              # Only if the chart Hub image needs extensions
├── charts/
│   └── airflow-dev-platform/
│       ├── Chart.yaml              # Depends on Zero to JupyterHub
│       ├── values.yaml
│       └── templates/              # Only resources not supplied by upstream chart
├── environments/
│   ├── dev/values.yaml
│   ├── staging/values.yaml
│   └── prod/values.yaml
├── policies/
│   ├── network/
│   ├── admission/
│   └── pod-security/
├── tests/
│   ├── container/
│   ├── integration/
│   ├── security/
│   └── load/
├── docs/
│   ├── architecture.md
│   ├── oidc-runbook.md
│   ├── operations.md
│   ├── disaster-recovery.md
│   └── user-guide.md
└── .github/workflows/ or equivalent CI
```

Prefer a normal trunk-based repository or release branches over using long-lived branches as different products. If multiple Airflow workspace versions remain necessary, build them as an explicit CI matrix from one maintained build definition.

## 11. Delivery phases

The estimates below assume one platform engineer, one application/Airflow engineer, and part-time identity, security, and infrastructure support. Procurement, network approvals, and IdP onboarding can dominate elapsed time.

### Phase 0 — Requirements and architecture decisions (1–2 weeks)

Deliverables:

- Confirm developer-platform versus production-workload boundary.
- Select cloud/cluster, regions, availability target, domain, registry, PostgreSQL, storage class, secret manager, ingress, and GitOps tool.
- Define user population, trust boundaries, data classifications, concurrency, workspace sizes, retention, RTO/RPO, and cost limits.
- Confirm IdP claims, groups, MFA policy, callback URL, token lifetimes, and offboarding behavior.
- Create threat model and architecture decision records.
- Define the supported Airflow/Python image matrix.

Exit criteria:

- Security, identity, platform, and Airflow owners approve the target architecture.
- Capacity assumptions and success metrics are documented.

### Phase 1 — Platform foundation and CI supply chain (2–3 weeks)

Deliverables:

- Managed Kubernetes environments for development and staging.
- System and autoscaling user node pools.
- Private registry, CI identity, image build/test/scan/sign pipeline.
- GitOps repository and promotion workflow.
- Base workspace image running non-root with pinned dependencies.
- External secret integration and managed PostgreSQL instances.

Exit criteria:

- A signed image digest is promoted to staging without local Docker builds or branch switching.
- No production secret is present in Git or an image layer.

### Phase 2 — Helm-based JupyterHub and OIDC (2–3 weeks)

Deliverables:

- Pinned Zero to JupyterHub chart and environment-specific values.
- TLS ingress and corporate DNS.
- OIDC login, group allow-listing, admin/operator role mapping, logout, refresh, and offboarding.
- Managed PostgreSQL Hub database.
- KubeSpawner profiles referencing immutable image digests.
- Per-user PVC provisioning and idle culling.

Exit criteria:

- An allowed user logs in with enterprise SSO and receives only their workspace.
- A disallowed or removed user cannot create a new session.
- No self-registration or local-password path remains.

### Phase 3 — Isolation and workspace lifecycle (2–4 weeks)

Deliverables:

- Restricted pod security context and admission policies.
- Default-deny NetworkPolicies with tested allow rules.
- Least-privilege Hub RBAC and token-free user service accounts.
- Resource requests, limits, quotas, profile entitlements, culling, and maximum age.
- Supervised Airflow development processes with probes and graceful shutdown.
- Git-based DAG workflow and controlled development credentials.
- PVC snapshot, restore, expansion, and offboarding workflows.

Exit criteria:

- Users cannot access another user's service, volume, service-account token, or Kubernetes resources.
- Workspace deletion and recreation preserves only the intended state.
- Idle environments terminate and capacity returns to the autoscaler.

### Phase 4 — Operations, reliability, and production Airflow path (2–4 weeks)

Deliverables:

- Central dashboards, alerts, SLOs, and runbooks.
- Backup and restore automation with a completed restore exercise.
- Load and burst-spawn testing at projected peak concurrency.
- Failure tests for Hub restart, node loss, database interruption, image registry interruption, and IdP outage.
- Separate staging/production Airflow deployment and DAG promotion pipeline.
- Upgrade, rollback, vulnerability response, and certificate/secret rotation procedures.

Exit criteria:

- Staging meets availability, spawn latency, recovery, security, and capacity criteria.
- A DAG is promoted from a user workspace through CI into staging Airflow without copying files manually.

### Phase 5 — Pilot and controlled production rollout (2–4 weeks)

Deliverables:

- Pilot with a small representative user group.
- Support process, user guide, platform training, and ownership/on-call model.
- Cost baseline and capacity adjustments.
- Security review, penetration test where required, and production readiness review.
- Phased onboarding by IdP group.

Exit criteria:

- Pilot issues are closed or explicitly accepted.
- Operations, security, identity, and business owners approve general availability.

Expected implementation range: roughly 10–16 weeks, excluding organizational approval or procurement delays.

## 12. Test and acceptance plan

### Identity

- Valid allowed-group user can log in.
- Valid but unauthorized tenant user is denied.
- Group removal prevents spawn after the configured refresh window.
- Administrator mapping grants only intended JupyterHub scopes.
- Callback, logout, MFA, conditional-access, clock-skew, and IdP outage behavior are tested.
- Tokens, claims, and client secrets never appear in logs.

### Isolation and security

- User A cannot reach User B's pod, service, PVC, or files.
- User pods cannot access cloud metadata or the Kubernetes API.
- Unapproved egress is blocked.
- Privileged images, root containers, mutable image tags, and missing resource limits are rejected.
- Images pass vulnerability, secret, SBOM, and signature policy.
- A compromised notebook cannot list secrets or create arbitrary workloads.

### Lifecycle and scale

- Login creates exactly one intended workspace and PVC.
- Warm and cold spawn latency meet targets.
- Concurrent burst logins at projected peak succeed without starving the Hub.
- Culling terminates inactive servers without deleting retained user data.
- Node scale-up and scale-down behave as expected.
- Full disk, OOM, eviction, registry outage, and failed image pull produce actionable user and operator messages.

### Reliability and recovery

- Hub pod restart preserves users and server state.
- Managed PostgreSQL restore meets RTO/RPO.
- User PVC restore is demonstrated.
- Node and availability-zone failure behavior is understood and documented.
- Helm/GitOps rollback restores the previous known-good release.
- Certificate and OIDC secret rotation complete without an extended outage.

### Airflow workflow

- Supported workspace images parse and test representative DAGs.
- Airflow 3 API, scheduler, execution API, and DAG processor work behind the user prefix.
- No development workspace can deploy directly to production.
- CI rejects invalid DAGs and policy violations.
- The approved artifact is promoted unchanged into staging and production Airflow.

## 13. Production readiness gate

Do not declare general availability until all items are true:

- [ ] Enterprise OIDC with MFA, group authorization, refresh, logout, and offboarding is verified.
- [ ] Open signup, `admin/admin`, root execution, NodePort exposure, and local-only images are removed.
- [ ] TLS ingress, managed DNS, private registry, immutable digests, and GitOps are operational.
- [ ] Hub and production Airflow use external managed databases with tested backups.
- [ ] Every user pod has resource limits, a restricted security context, default-deny network policy, and no unnecessary API token.
- [ ] Per-user storage is encrypted, backed up according to policy, and restorable.
- [ ] CI produces tested, scanned, signed images with SBOMs and provenance.
- [ ] Monitoring, alerts, SLOs, runbooks, ownership, and on-call escalation are in place.
- [ ] Peak-load, isolation, penetration, recovery, upgrade, and rollback tests pass.
- [ ] Production DAGs run in a separately governed Airflow deployment.
- [ ] Security, identity, platform operations, and application owners sign off.

## 14. First implementation backlog

Start with these work items in order:

1. Approve the developer-platform boundary and choose the Kubernetes/IdP/storage/database/registry stack.
2. Create a new implementation branch from the desired target architecture; do not extend the current branch-switching release model.
3. Add a pinned Zero to JupyterHub Helm chart and development/staging values.
4. Replace NativeAuthenticator with OIDCOAuthenticator in development and validate real group claims.
5. Create a non-root curated Airflow 3 workspace image and CI pipeline that pushes an immutable digest.
6. Spawn that image through KubeSpawner with a CSI-backed per-user PVC.
7. Add TLS ingress, external secrets, managed Hub PostgreSQL, NetworkPolicies, Pod Security Admission, and least-privilege RBAC.
8. Replace the scheduler wrapper's unmanaged subprocess threads with a supervised workspace lifecycle.
9. Add observability, load tests, security tests, backup/restore, and operational runbooks.
10. Build the separate production Airflow deployment and Git-based DAG promotion pipeline.
11. Pilot with a small enterprise group before broader onboarding.

## 15. Primary references

- [Zero to JupyterHub: setup and configuration](https://z2jh.jupyter.org/en/stable/jupyterhub/index.html)
- [Zero to JupyterHub: security](https://z2jh.jupyter.org/en/latest/administrator/security.html)
- [Zero to JupyterHub: scaling and optimization](https://z2jh.jupyter.org/en/stable/administrator/optimization.html)
- [OAuthenticator: OpenID Connect setup](https://oauthenticator.readthedocs.io/en/latest/tutorials/provider-specific-setup/providers/oidc.html)
- [OAuthenticator: allowing and blocking access](https://oauthenticator.readthedocs.io/en/latest/topic/allowing.html)
- [JupyterHub: authentication and authorization](https://jupyterhub.readthedocs.io/en/stable/getting-started/authenticators-users-basics.html)
- [Kubernetes: multi-tenancy](https://kubernetes.io/docs/concepts/security/multi-tenancy/)
- [Kubernetes: security](https://kubernetes.io/docs/concepts/security/)
- [Kubernetes: application security checklist](https://kubernetes.io/docs/concepts/security/application-security-checklist/)
- [Kubernetes: secrets good practices](https://kubernetes.io/docs/concepts/security/secrets-good-practices/)
- [Airflow: production deployment](https://airflow.apache.org/docs/apache-airflow/stable/administration-and-deployment/production-deployment.html)
- [Airflow: Kubernetes deployments](https://airflow.apache.org/docs/apache-airflow/stable/administration-and-deployment/kubernetes.html)
- [Airflow: official Helm chart](https://airflow.apache.org/docs/helm-chart/stable/index.html)
- [Airflow Helm chart: production guide](https://airflow.apache.org/docs/helm-chart/stable/production-guide.html)
