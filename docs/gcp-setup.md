# GCP setup

Everything the platform needs from Google Cloud, in the order you need it.
Values in `ALL_CAPS` are yours to choose.

```bash
export PROJECT_ID=my-project
export REGION=europe-west1
export CLUSTER=airflow-platform
export DOMAIN=airflow.example.com
gcloud config set project "$PROJECT_ID"
```

## 1. Enable the APIs

```bash
gcloud services enable \
  container.googleapis.com \
  artifactregistry.googleapis.com \
  certificatemanager.googleapis.com \
  secretmanager.googleapis.com \
  compute.googleapis.com
```

## 2. GKE cluster

Two things here are load-bearing rather than preference.

**Dataplane V2.** Workspace isolation in this chart is enforced by
NetworkPolicy: it is what stops a workspace reaching the GCE metadata server
(and minting tokens for the node's service account) and what stops one
workspace reaching another. On a cluster without a NetworkPolicy-enforcing
dataplane those policies are accepted by the API server and then quietly
ignored. `--enable-dataplane-v2` is not optional for a production deployment.

**Workload Identity.** Removes node-level service-account keys from the
picture, and is the prerequisite for a Cloud SQL Hub database later.

```bash
gcloud container clusters create "$CLUSTER" \
  --region "$REGION" \
  --release-channel regular \
  --enable-dataplane-v2 \
  --enable-ip-alias \
  --workload-pool "${PROJECT_ID}.svc.id.goog" \
  --enable-shielded-nodes \
  --enable-autoupgrade \
  --enable-autorepair \
  --num-nodes 1 \
  --machine-type e2-standard-4 \
  --node-labels hub.jupyter.org/node-purpose=core \
  --addons HttpLoadBalancing
```

### Node pools

The chart sets `scheduling.corePods.nodeAffinity.matchNodePurpose: require`
and the same for user pods, which means **pods will stay Pending until nodes
carry the matching labels**. That is deliberate: it keeps a user's DAG from
sharing a node with the Hub. The default node pool above is labelled `core`;
add a separate autoscaling pool for workspaces.

A 1 CPU / 16 GB workspace needs a machine with a high memory-to-CPU ratio.
`e2-highmem-4` (4 vCPU / 32 GB) fits two workspaces per node with room for
system daemons; `n2-highmem-8` fits four.

```bash
gcloud container node-pools create workspaces \
  --cluster "$CLUSTER" --region "$REGION" \
  --machine-type e2-highmem-4 \
  --enable-autoscaling --min-nodes 0 --max-nodes 10 \
  --node-labels hub.jupyter.org/node-purpose=user \
  --node-taints hub.jupyter.org_dedicated=user:NoSchedule \
  --enable-autoupgrade --enable-autorepair \
  --disk-type pd-balanced --disk-size 100
```

z2jh tolerates that taint out of the box, so nothing else in the system will
schedule onto the pool.

If you would rather not label nodes at all, set both
`scheduling.corePods.nodeAffinity.matchNodePurpose` and
`scheduling.userPods.nodeAffinity.matchNodePurpose` to `prefer`
(`environments/dev.values.yaml` already does).

## 3. Artifact Registry

```bash
gcloud artifacts repositories create airflow \
  --repository-format docker \
  --location "$REGION" \
  --immutable-tags \
  --description "Airflow developer workspace images"

gcloud auth configure-docker "${REGION}-docker.pkg.dev"
```

`--immutable-tags` is what lets you deploy a *tag* and still get the
rollback guarantees of a digest: once pushed, `workspace:3.3.2-<sha>` can
never be repointed at different bytes. That matters here because z2jh's image
pre-puller cannot template a digest reference — see
[operations.md](operations.md#image-references-tag-or-digest).

Grant the cluster's node service account read access:

```bash
NODE_SA=$(gcloud container clusters describe "$CLUSTER" --region "$REGION" \
  --format='value(nodeConfig.serviceAccount)')
gcloud artifacts repositories add-iam-policy-binding airflow \
  --location "$REGION" \
  --member "serviceAccount:${NODE_SA}" \
  --role roles/artifactregistry.reader
```

## 4. Static IP and DNS

The Google-managed certificate cannot be issued until the domain resolves to
the load balancer, and an ephemeral ingress IP changes every time the ingress
is recreated. Reserve one up front:

```bash
gcloud compute addresses create airflow-platform-ip --global
gcloud compute addresses describe airflow-platform-ip --global --format='value(address)'
```

Create an `A` record for `$DOMAIN` pointing at that address, then uncomment
the `kubernetes.io/ingress.global-static-ip-name` annotation in
`charts/airflow-dev-platform/values.yaml` (or set it per environment).

## 5. Build and push the workspace image

```bash
scripts/build-image.sh \
  --registry "${REGION}-docker.pkg.dev/${PROJECT_ID}/airflow" \
  --push
```

The script builds `linux/amd64` by default, runs a smoke test inside the
image, and prints the reference to deploy. On an Apple Silicon machine the
cross-build is emulated and slow; a CI runner is the better place for it.

## 6. OIDC credentials

See [oidc-setup.md](oidc-setup.md) for the identity-provider side. Once you
have an issuer URL, a client ID and a client secret:

```bash
scripts/create-oidc-secret.sh \
  --namespace airflow-prod \
  --issuer https://login.example.com/oauth2/default \
  --client-id 0oa1b2c3d4e5f6g7h8i9
```

## 7. Deploy

```bash
scripts/deploy.sh \
  --env prod \
  --domain "$DOMAIN" \
  --image "${REGION}-docker.pkg.dev/${PROJECT_ID}/airflow/airflow-workspace:3.3.2-<sha>"
```

Then wait for the certificate. This is the slowest step and the one most
often mistaken for a failure — 15 to 20 minutes is normal, and it cannot
start until DNS resolves:

```bash
kubectl -n airflow-prod describe managedcertificate airflow-dev-platform-cert
# Status: Provisioning -> Active
```

## 8. Optional: Hub state in Cloud SQL

`hub.db.type: sqlite-pvc` keeps JupyterHub's user and server records on a
single zonal disk. That is adequate while the disk is backed up, but it makes
zone loss an outage with a manual recovery. For an availability target, move
Hub state to Cloud SQL.

Note this changes nothing about **Airflow's** metadata database: each
workspace keeps its own SQLite file on its own volume, by design. The two are
unrelated.

```bash
gcloud sql instances create airflow-hub \
  --database-version POSTGRES_16 \
  --region "$REGION" --tier db-custom-2-7680 \
  --availability-type REGIONAL \
  --no-assign-ip --network default \
  --backup --enable-point-in-time-recovery
gcloud sql databases create jupyterhub --instance airflow-hub
```

Then in `environments/prod.values.yaml`:

```yaml
jupyterhub:
  hub:
    db:
      type: postgres
      url: postgresql+psycopg2://hub@127.0.0.1:5432/jupyterhub
    extraContainers:
      - name: cloud-sql-proxy
        image: gcr.io/cloud-sql-connectors/cloud-sql-proxy:2.14.1
        args: ["--private-ip", "--structured-logs", "PROJECT:REGION:airflow-hub"]
        securityContext:
          runAsNonRoot: true
          allowPrivilegeEscalation: false
          capabilities: { drop: ["ALL"] }
        resources:
          requests: { cpu: 100m, memory: 128Mi }
          limits: { cpu: 500m, memory: 256Mi }
    serviceAccount:
      annotations:
        iam.gke.io/gcp-service-account: airflow-hub@PROJECT.iam.gserviceaccount.com
```

The database password goes in `hub.db.password`, sourced from a Secret — or
skip it entirely by granting the Hub's Google service account
`roles/cloudsql.client` and using IAM database authentication.

## 9. Backups

| State | Where it lives | How to protect it |
| :-- | :-- | :-- |
| Hub users and servers | `hub-db-dir` PVC, or Cloud SQL | Compute Engine snapshot schedule on the PD, or Cloud SQL automated backups + PITR |
| Workspace contents (DAGs, notebooks, Airflow SQLite) | one PVC per user | Snapshot schedule on the `premium-rwo` disks; a workspace is also expected to push DAGs to Git |
| OIDC client secret | `airflow-platform-oidc` Secret | Secret Manager is the source of truth; the cluster Secret is a copy |
| Platform configuration | this repository | Git |

```bash
gcloud compute resource-policies create snapshot-schedule airflow-daily \
  --region "$REGION" --max-retention-days 14 \
  --daily-schedule --start-time 02:00 \
  --on-source-disk-delete keep-auto-snapshots
```

A backup counts as working only once you have restored from it into a clean
namespace and logged in.
