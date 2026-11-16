# Operations

## Workspace sizing

The default is **1 CPU and 16 GB per user pod**, with request equal to limit
so every workspace lands in the Guaranteed QoS class — a workspace cannot be
evicted by a busier neighbour, and a runaway DAG cannot starve the node.

Four values control it, and nothing else reads them:

```yaml
# charts/airflow-dev-platform/values.yaml, or environments/<env>.values.yaml
jupyterhub:
  singleuser:
    cpu:
      limit: 1
      guarantee: 1
    memory:
      limit: 16G
      guarantee: 16G
```

Three ways to change it, in increasing order of permanence:

```bash
# 1. One deploy
scripts/deploy.sh --env prod --domain ... --image ... --cpu 2 --memory 32G

# 2. Directly
helm upgrade ... --set jupyterhub.singleuser.cpu.limit=2 \
                 --set jupyterhub.singleuser.cpu.guarantee=2 \
                 --set jupyterhub.singleuser.memory.limit=32G \
                 --set jupyterhub.singleuser.memory.guarantee=32G

# 3. Committed: edit environments/prod.values.yaml
```

Resizing takes effect on the **next spawn**, not on running workspaces.

Two things to check when you raise it:

- `platform.resourceQuota.hard` must still fit the expected concurrency. At
  24 CPU / 340Gi the namespace holds about 20 workspaces at 1 CPU / 16G, and
  about 10 at 2 CPU / 32G. Exceeding the quota produces a clear
  `exceeded quota` event on a failed spawn rather than a pod that pends
  forever — but it is still a failed spawn.
- `platform.limitRange.max` (8 CPU / 64Gi) is a hard ceiling; a larger
  workspace than that is rejected by admission.

`16G` is 16 × 10⁹ bytes. Write `16Gi` if you mean 16 × 2³⁰ (≈17.2 GB); both
are valid and the difference is about 7%.

### Letting users choose a size

Populate `singleuser.profileList` and a picker appears at spawn time. A
profile's `kubespawner_override` wins over the defaults above:

```yaml
jupyterhub:
  singleuser:
    profileList:
      - display_name: "Standard — 1 CPU, 16 GB"
        description: "Default development workspace"
        default: true
        kubespawner_override: {}
      - display_name: "Large — 4 CPU, 64 GB"
        description: "Wide backfills and large local datasets"
        kubespawner_override:
          cpu_limit: 4
          cpu_guarantee: 4
          mem_limit: 64G
          mem_guarantee: 64G
```

If you gate the large profile, do it by IdP group with
`allowed_groups` on the profile entry — `profileList` entries support it in
kubespawner 7.

## Image references: tag or digest

z2jh builds the workspace image string as `{name}:{tag}`, and its image
pre-puller DaemonSets interpolate the same two fields with no handling for an
empty tag. That makes a digest reference (`repo@sha256:...`, which must sit
alone in `name`) incompatible with pre-pulling: the DaemonSet would render
`repo@sha256:...:` and fail to parse.

The chart refuses that combination outright rather than letting you discover
it as an `ImagePullBackOff`.

So you have two options:

**Immutable tag (recommended).** Create the Artifact Registry repository with
`--immutable-tags`. A tag can then never be repointed at different bytes,
which is the property the digest was for, and pre-pulling keeps working —
worth a lot when a cold-node spawn otherwise waits on a multi-gigabyte pull.

```bash
scripts/deploy.sh --env prod --domain ... \
  --image europe-west1-docker.pkg.dev/proj/airflow/airflow-workspace:3.3.2-a1b2c3d4e5f6
```

**Digest.** `scripts/deploy.sh` accepts one and turns the pre-puller off,
telling you it has done so.

```bash
scripts/deploy.sh --env prod --domain ... \
  --image europe-west1-docker.pkg.dev/proj/airflow/airflow-workspace@sha256:...
```

The default tag `build-image.sh` generates is `<airflow-version>-<git-sha>`,
so a running pod always traces back to a commit.

## Upgrading

### Airflow

Edit `AIRFLOW_VERSION` in `VERSIONS.env`, rebuild, deploy. Each workspace
runs `airflow db migrate` at startup, so a user's SQLite metadata database
migrates itself the next time their pod starts.

Migrations are one-way. A user who needs to go back needs their PVC restored
from a snapshot taken before the upgrade — take one first if the jump is a
minor version or larger.

### The platform chart

`Z2JH_CHART_VERSION` in `VERSIONS.env` is documentation; the binding pin is
`dependencies[0].version` in `charts/airflow-dev-platform/Chart.yaml`. Change
both together.

Before bumping z2jh, re-read `charts/airflow-dev-platform/values.yaml`: the
OIDC block targets oauthenticator 17.x trait names, and
`hub.extraConfig.03-workspace-security` sets kubespawner traits directly.

```bash
scripts/deploy.sh --env prod --domain ... --image ... --diff   # needs helm-diff
scripts/deploy.sh --env prod --domain ... --image ... --dry-run
```

## Day-to-day

```bash
# Who is running something
kubectl -n airflow-prod get pods -l component=singleuser-server \
  -L hub.jupyter.org/username

# Hub, including login and spawn failures
kubectl -n airflow-prod logs -f deploy/hub

# One workspace
kubectl -n airflow-prod logs -f <pod>
kubectl -n airflow-prod exec -it <pod> -- bash

# Airflow service state inside a workspace, without the browser
kubectl -n airflow-prod exec <pod> -- curl -s localhost:8999/healthz

# Capacity
kubectl -n airflow-prod describe resourcequota airflow-dev-platform
kubectl top pods -n airflow-prod
```

## Observability

Google Cloud only, off by default (`observability.enabled`). An OpenTelemetry
Collector (`otel-collector`, one pod on the core node) scrapes the Hub's
`/hub/metrics` every 60 seconds and exports to Google Managed Service for
Prometheus. `observability/gke-dashboard.json` is a Cloud Monitoring dashboard
over that data and over what GKE already collects.

| Section | Shows | Comes from |
| :-- | :-- | :-- |
| Cluster | CPU use per node, pods waiting to be scheduled | GKE built-in |
| Pods | Phase, restarts, memory of each pod in the platform namespace | GKE built-in |
| Hub | Running workspaces, users active in 24 hours, workspace start time (p50, p95) | the Hub, through the collector |
| Users | Minutes since each user's last activity, and whether their workspace is running | the Hub, through the collector (`hub.extraConfig.06-activity-metrics`) |

The collector exports only what the dashboard charts, plus `up` (a failing
scrape shows as 0). The Hub's request-duration histograms alone are hundreds of
series that nothing reads, and Google bills per sample.
`tests/test_chart_render.py` fails if a metric the dashboard reads is not
allowed through. To chart more, add its name to the `filter` processor in
`templates/otel-collector.yaml`.

**Cost.** Hub series: about 55 fixed (the start-time histogram dominates) plus
3 per user, so roughly 85 for ten users. At 60 seconds that is about 3.7
million samples a month, a few tenths of a dollar at Google's published
Managed Service for Prometheus rate ($0.06 per million samples when this was
written; check the current price). The built-in cluster and pod metrics are
already enabled on the cluster, and this adds no collection for them.

**Privacy.** The `user` label is the Hub username, an email address on the
`waas` environment. That is acceptable only because its OAuth audience is
Internal. Do not enable this in an environment where usernames are opaque
identifiers a viewer should not see, or where people outside the company can
sign in.

**The dashboard is written for `waas` only.** Its queries are scoped to the
cluster `airflow-waas` and the namespace `airflow-waas`, because the project is
shared with other teams' clusters. For another environment, copy
`observability/gke-dashboard.json` and change those two names before creating it;
a second Hub exporting to the same project would otherwise add a second series
to every Hub widget.

**Turn it on** (the chart side is per environment; `waas` already has its values):

```bash
# 1. Let the collector write metrics. Workload Identity, no key, no Google
#    service account. The member is the Kubernetes ServiceAccount.
gcloud projects add-iam-policy-binding <project-id> --role roles/monitoring.metricWriter \
  --member "principal://iam.googleapis.com/projects/<project-number>/locations/global/workloadIdentityPools/<project-id>.svc.id.goog/subject/ns/<namespace>/sa/otel-collector" \
  --condition None

# 2. Deploy as usual (scripts/deploy.sh). Check the collector:
kubectl -n <namespace> get pod -l app.kubernetes.io/name=otel-collector
kubectl -n <namespace> logs deploy/otel-collector

# 3. Create the dashboard once.
scripts/create-dashboard.sh --project <project-id> --labels lab=gcp-pca
```

Series appear within about five minutes. Workspace start time stays empty until
a workspace has started after the deploy.

**Turn it off:** set `observability.enabled: false` and redeploy; delete the
dashboard with the command the script printed; remove the IAM grant with
`gcloud projects remove-iam-policy-binding` and the same arguments.

## Runbook

### A spawn fails or hangs

```bash
kubectl -n airflow-prod get events --sort-by=.lastTimestamp | tail -30
kubectl -n airflow-prod describe pod <jupyter-pod>
```

| What you see | What it means |
| :-- | :-- |
| `Insufficient cpu` / `Insufficient memory`, pod Pending | The user node pool is at its autoscaling maximum, or no machine type can fit a 1 CPU / 16 GB Guaranteed pod. Check the pool's `--max-nodes` and machine type. |
| `exceeded quota` | `platform.resourceQuota.hard` is full. Raise it *and* the node pool together. |
| Pending with no scheduling message | Nodes are missing `hub.jupyter.org/node-purpose=user`. `matchNodePurpose: require` means no label, no scheduling. |
| `ImagePullBackOff` | The node service account lacks `artifactregistry.reader`, or the tag/digest does not exist. |
| Spawn times out after ~10 min | A cold node pulling the image. Raise `singleuser.startTimeout`, or keep `prePuller` on and `userPlaceholder.replicas` above zero. |

### Airflow inside a workspace misbehaves

Open **Airflow Services** from the JupyterLab launcher. It shows scheduler,
DAG processor and triggerer state, restart counts, and the tail of each log,
and lets the user restart one without restarting the pod.

- **A DAG file does not appear.** The DAG processor is the component that
  parses files in Airflow 3 — check that it is running, not just the
  scheduler.
- **Deferrable tasks never resume.** The triggerer is stopped.
- **Tasks fail immediately with a 403 or the scheduler dies after one task.**
  The Execution API URL is wrong. Workers must reach `127.0.0.1:9091` with
  the `${JUPYTERHUB_SERVICE_PREFIX}airflow-api/execution/` path prefix
  intact; routing them through port 8888 hits the JupyterHub auth layer, and
  the resulting `HTTPStatusError` is unpicklable, which takes the scheduler
  down with it. `images/workspace/bin/start.sh` derives both URLs — compare
  them against the log line it prints at startup.
- **A service shows `failed`.** It exited non-zero more than
  `WORKSPACE_MAX_RESTARTS` times. The log tail on that tab has the reason.

### The Hub will not start

`kubectl -n airflow-prod logs deploy/hub` and match against the table in
[oidc-setup.md](oidc-setup.md#troubleshooting). Almost every case is the OIDC
guard doing its job.

### Rolling back

```bash
helm -n airflow-prod history airflow-platform
helm -n airflow-prod rollback airflow-platform <revision>
```

This rolls back the Hub, the proxy and the *configuration*, including which
workspace image future spawns use. It does not touch running workspaces or
any PVC — including an Airflow metadata database that a newer Airflow has
already migrated.

## Cost

Per concurrent workspace: 1 vCPU + 16 GB for the duration of the session,
plus 20 GB of `premium-rwo` per user that persists whether or not they are
logged in.

The levers, most to least effective:

- `cull.timeout` (1 hour idle by default) — the single biggest one.
- `cull.maxAge` (12 hours) — bounds a forgotten session.
- `scheduling.userPlaceholder.replicas` — each warm slot costs a full
  workspace's worth of idle capacity in exchange for a fast spawn.
- User node pool `--min-nodes 0` so the pool scales to zero overnight.
- `singleuser.storage.capacity` — PVCs are the cost that does not go away.
  Have an offboarding process that snapshots and deletes them.
