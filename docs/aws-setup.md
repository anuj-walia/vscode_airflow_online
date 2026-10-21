# AWS (EKS) setup

Everything the platform needs from AWS, in the order you need it. It is the
EKS counterpart of [gcp-setup.md](gcp-setup.md): same chart, same workspace
image, different cloud plumbing.

> **Status.** The chart renders and validates for AWS (`tests/test_chart_render.py`)
> and `scripts/deploy.sh --cloud aws` is checked against a local cluster, but
> this guide has **not** yet been run against a real EKS cluster. Expect to
> correct a command or two on the first pass, and please fix this doc when you do.

Values in `ALL_CAPS` are yours to choose.

```bash
export AWS_REGION=eu-west-1
export CLUSTER=airflow-platform
export DOMAIN=airflow.example.com
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
source VERSIONS.env        # EKS_KUBERNETES_VERSION, chart versions
```

## 0. Prerequisites

| Tool | Used for |
| :-- | :-- |
| `aws` CLI v2, authenticated (`aws sts get-caller-identity` works) | everything below |
| `eksctl` | creating the cluster, node groups and add-ons |
| `kubectl`, `helm` 3.x | `scripts/deploy.sh` checks for both |
| `docker` with BuildKit | `scripts/build-image.sh` |

The identity you run `eksctl` as needs to create CloudFormation stacks, VPC and
EC2 resources, EKS clusters and IAM roles; an administrator role is the simple
answer for a first install. You also need a domain whose DNS you control
(Route 53 is assumed below, any DNS provider works) and an OIDC identity
provider ([oidc-setup.md](oidc-setup.md)).

## 1. EKS cluster

`infra/aws/eksctl-cluster.yaml` defines the cluster. Four things in it are
load-bearing rather than preference; the file explains each in a comment.

**VPC CNI with network policy enabled.** Workspace isolation is enforced by
NetworkPolicy: it is what stops a workspace reaching the instance metadata
service (and minting credentials for the node's IAM role) and what stops one
workspace reaching another. On a cluster whose CNI does not enforce policy, the
policies are accepted by the API server and then quietly ignored.

**Node labels and a taint.** The chart sets `matchNodePurpose: require`, so
pods stay Pending until nodes carry `hub.jupyter.org/node-purpose`. That keeps a
user's DAG off the node the Hub runs on. The `workspaces` group is tainted so
nothing else schedules there.

**IMDS locked down.** `disableIMDSv1` and `disablePodIMDS` on every node group
are a second layer behind the NetworkPolicy.

**EBS CSI driver.** Every workspace home volume is an EBS `gp3` volume.

```bash
# Fill in the placeholders and the pinned Kubernetes version. The generated
# file is also what you pass to `eksctl delete cluster` later; keep it out of Git.
sed -e "s/CLUSTER_NAME/${CLUSTER}/g" -e "s/AWS_REGION/${AWS_REGION}/g" \
    -e "s/version: \"1.35\"/version: \"${EKS_KUBERNETES_VERSION}\"/" \
    infra/aws/eksctl-cluster.yaml > eksctl-cluster.generated.yaml
eksctl create cluster -f eksctl-cluster.generated.yaml

aws eks update-kubeconfig --name "$CLUSTER" --region "$AWS_REGION"
kubectl config current-context      # arn:aws:eks:<region>:<account>:cluster/<name>
kubectl get nodes -L hub.jupyter.org/node-purpose
```

Creation takes about 15–20 minutes. The `workspaces` group starts at zero nodes
on purpose; it scales up when someone spawns.

Pods use **EKS Pod Identity** for AWS permissions, which needs no OIDC provider
and no role trust-policy editing. Only the load balancer controller and the
cluster autoscaler get a role; workspace pods get none.

## 2. AWS Load Balancer Controller

It turns the chart's `Ingress` into an Application Load Balancer. Without it
the Ingress is created and never gets an address; `scripts/deploy.sh` checks for
it first.

```bash
helm repo add eks https://aws.github.io/eks-charts && helm repo update eks

VPC_ID=$(aws eks describe-cluster --name "$CLUSTER" --region "$AWS_REGION" \
  --query cluster.resourcesVpcConfig.vpcId --output text)

helm upgrade --install aws-load-balancer-controller eks/aws-load-balancer-controller \
  --namespace kube-system \
  --version "$AWS_LB_CONTROLLER_CHART_VERSION" \
  --set clusterName="$CLUSTER" \
  --set region="$AWS_REGION" \
  --set vpcId="$VPC_ID" \
  --set serviceAccount.create=false \
  --set serviceAccount.name=aws-load-balancer-controller

kubectl -n kube-system rollout status deploy/aws-load-balancer-controller
kubectl get ingressclass alb
```

The service account and its Pod Identity role were created by `eksctl` in
step 1. If the controller logs `AccessDenied` for an API call, the chart is newer
than eksctl's built-in policy; attach the policy file published for your
controller version
(`docs/install/iam_policy.json` in the `kubernetes-sigs/aws-load-balancer-controller`
repository) to that role.

The ALB needs public subnets tagged `kubernetes.io/role/elb=1`. `eksctl`
tags the subnets it creates; if you bring your own VPC, tag them yourself.

## 3. Cluster Autoscaler

GKE autoscales node pools for you; **EKS does not.** Without this, a workspace
spawn waits forever on a zero-node `workspaces` group.

```bash
helm repo add autoscaler https://kubernetes.github.io/autoscaler && helm repo update autoscaler

helm upgrade --install cluster-autoscaler autoscaler/cluster-autoscaler \
  --namespace kube-system \
  --version "$CLUSTER_AUTOSCALER_CHART_VERSION" \
  --set autoDiscovery.clusterName="$CLUSTER" \
  --set awsRegion="$AWS_REGION" \
  --set rbac.serviceAccount.create=false \
  --set rbac.serviceAccount.name=cluster-autoscaler

kubectl -n kube-system rollout status deploy/cluster-autoscaler-aws-cluster-autoscaler
```

It finds the node groups by their `k8s.io/cluster-autoscaler/*` tags (set in the
eksctl file) and scales the `workspaces` group from zero by reading the label and
taint from the same tags. The chart's autoscaler version should stay within a
minor of `EKS_KUBERNETES_VERSION`.

## 4. ECR repository

```bash
aws ecr create-repository \
  --repository-name airflow-workspace \
  --region "$AWS_REGION" \
  --image-tag-mutability IMMUTABLE \
  --image-scanning-configuration scanOnPush=true

aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
```

`IMMUTABLE` is what lets you deploy a *tag* and still get the rollback
guarantee of a digest: once pushed, `airflow-workspace:3.3.2-<sha>` can never be
repointed at different bytes. That matters because z2jh's image pre-puller cannot
template a digest reference; see
[operations.md](operations.md#image-references-tag-or-digest).

Managed node groups already carry `AmazonEC2ContainerRegistryReadOnly`, so the
nodes can pull from an ECR repository in the same account without more setup.

## 5. Build and push the workspace image

```bash
scripts/build-image.sh \
  --registry "${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com" \
  --push
```

The script builds `linux/amd64` by default (the `m6i`/`r6i` node groups are x86),
runs a smoke test inside the image, pushes it, and prints the pushed digest. The
tag is `<airflow version>-<git sha>`, with `-dirty` appended if the working tree
has uncommitted changes, so build from a clean commit for anything you deploy.

For Graviton node groups (`m7g`/`r7g`), pass `--platform linux/arm64` and change
the instance types in `infra/aws/eksctl-cluster.yaml`. On an Apple Silicon laptop
the default `amd64` build is emulated and slow; build it in CI or on an `amd64`
host.

## 6. ACM certificate

TLS terminates at the ALB with a certificate from AWS Certificate Manager. It
must be in the **same region as the cluster** and in the `ISSUED` state before
you deploy.

```bash
CERT_ARN=$(aws acm request-certificate \
  --domain-name "$DOMAIN" \
  --validation-method DNS \
  --region "$AWS_REGION" \
  --query CertificateArn --output text)

# The DNS record ACM wants you to create to prove you own the domain:
aws acm describe-certificate --certificate-arn "$CERT_ARN" --region "$AWS_REGION" \
  --query 'Certificate.DomainValidationOptions[0].ResourceRecord'
```

Create that `CNAME` in your DNS zone (in Route 53 the console's "Create records
in Route 53" button does it in one click), then wait:

```bash
aws acm wait certificate-validated --certificate-arn "$CERT_ARN" --region "$AWS_REGION"
echo "$CERT_ARN"
```

## 7. OIDC credentials

See [oidc-setup.md](oidc-setup.md) for the identity-provider side. The redirect
URI to register is `https://$DOMAIN/hub/oauth_callback`. Once you have an issuer
URL, a client ID and a client secret:

```bash
scripts/create-oidc-secret.sh \
  --namespace airflow-prod \
  --issuer https://login.example.com/oauth2/default \
  --client-id 0oa1b2c3d4e5f6g7h8i9
```

## 8. Deploy

```bash
scripts/deploy.sh --cloud aws \
  --domain "$DOMAIN" \
  --certificate-arn "$CERT_ARN" \
  --image "${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/airflow-workspace:3.3.2-<sha>"
```

`--cloud aws` selects `environments/aws-prod.values.yaml` and the namespace
`airflow-prod`. Before installing, `deploy.sh` checks that the cluster is
reachable, the OIDC secret exists, the `alb` IngressClass and the EBS CSI driver
are installed, and the VPC CNI's network policy agent is running. It stops if
any of those is missing, and only warns when it can't confirm that network
policy is enforced.

Use the **tag** from step 5 here. Because the repository is immutable it gives
the guarantee a digest would, and it keeps the image pre-puller working so the
first spawn on a warm node is fast. You can pass the `...@sha256:...` digest that
`build-image.sh` prints instead; `deploy.sh` accepts it but must switch the
pre-puller off, so the first spawn on a cold node waits for the image pull.

Use `--dry-run` first to render and validate without applying, and `--diff` to
see what an upgrade would change.

## 9. DNS

The ALB takes a few minutes to appear. When the Ingress has an address, point
your domain at it:

```bash
kubectl -n airflow-prod get ingress
ALB_DNS=$(kubectl -n airflow-prod get ingress -o jsonpath='{.items[0].status.loadBalancer.ingress[0].hostname}')
```

In Route 53, create an **alias** `A` record for `$DOMAIN` targeting `$ALB_DNS`
(the console's "Alias to Application and Classic Load Balancer" option fills in the
hosted zone for you). With any other DNS provider, use a `CNAME` to `$ALB_DNS`.

To keep the same ALB hostname across an uninstall and reinstall, uncomment
`alb.ingress.kubernetes.io/group.name` in `environments/aws-prod.values.yaml`.

Then check the login, a spawn, and the isolation that the whole design rests on:

1. Open `https://$DOMAIN`, sign in, and start a workspace (the first spawn also
   waits for the autoscaler to add a node, a few minutes).
2. In JupyterLab, run a DAG task. This exercises the scheduler and the
   Execution API, not just the UI.
3. From a workspace terminal, confirm the instance metadata service is
   unreachable. This must **time out**:

   ```bash
   curl -m 3 -sS http://169.254.169.254/latest/meta-data/ ; echo "exit=$?"
   ```

   If it returns data, NetworkPolicy is not being enforced and workspaces can
   read the node's credentials. Do not put the platform in front of users until it
   times out; check step 1's `enableNetworkPolicy` and `disablePodIMDS`.

## 10. Optional: Hub state in RDS

`hub.db.type: sqlite-pvc` keeps JupyterHub's user and server records on a single
EBS volume, which lives in one availability zone. That is adequate while the
volume is backed up, but zone loss is an outage with a manual recovery. For an
availability target, move Hub state to RDS PostgreSQL.

This changes nothing about **Airflow's** metadata database: each workspace keeps
its own SQLite file on its own volume, by design.

Create a Multi-AZ PostgreSQL instance in private subnets of the cluster's VPC,
with a security group that allows port 5432 from the node security group only,
and a `jupyterhub` database. Then in `environments/aws-prod.values.yaml`:

```yaml
jupyterhub:
  hub:
    db:
      type: postgres
      url: postgresql+psycopg2://hub@RDS_ENDPOINT:5432/jupyterhub
    extraEnv:
      PGPASSWORD:
        valueFrom:
          secretKeyRef: { name: hub-db-password, key: password }
```

Create the `hub-db-password` Secret in the namespace first. The database password
stays out of the values file; keep it in AWS Secrets Manager as the source of
truth and copy it into the cluster Secret.

## 11. Backups

| State | Where it lives | How to protect it |
| :-- | :-- | :-- |
| Hub users and servers | `hub-db-dir` EBS volume, or RDS | A DLM snapshot policy on the volume, or RDS automated backups with point-in-time recovery |
| Workspace contents (DAGs, notebooks, Airflow SQLite) | one EBS volume per user | The same DLM policy; a workspace is also expected to push DAGs to Git |
| OIDC client secret | `airflow-platform-oidc` Secret | Keep the source of truth in AWS Secrets Manager; the cluster Secret is a copy |
| Platform configuration | this repository | Git |

The `gp3` StorageClass has `reclaimPolicy: Delete`, so deleting a user's PVC
deletes their volume. Snapshot first.

The EBS CSI driver tags every volume it creates with `ebs.csi.aws.com/cluster=true`,
so one Data Lifecycle Manager policy covers them all:

```bash
aws dlm create-default-role
aws dlm create-lifecycle-policy \
  --description "airflow-platform daily EBS snapshots" \
  --state ENABLED \
  --execution-role-arn "arn:aws:iam::${ACCOUNT_ID}:role/AWSDataLifecycleManagerDefaultRole" \
  --policy-details '{
    "PolicyType": "EBS_SNAPSHOT_MANAGEMENT",
    "ResourceTypes": ["VOLUME"],
    "TargetTags": [{"Key": "ebs.csi.aws.com/cluster", "Value": "true"}],
    "Schedules": [{
      "Name": "daily",
      "CreateRule": {"Interval": 24, "IntervalUnit": "HOURS", "Times": ["02:00"]},
      "RetainRule": {"Count": 14},
      "CopyTags": true
    }]
  }'
```

A backup counts as working only once you have restored from it into a clean
namespace and logged in.

## 12. Tearing down

Delete the release first so the load balancer controller removes the ALB. If you
delete the cluster with the Ingress still present, the ALB and its security
groups are orphaned and keep billing.

```bash
helm -n airflow-prod uninstall airflow-platform
kubectl -n airflow-prod get ingress      # wait until empty
eksctl delete cluster -f eksctl-cluster.generated.yaml
```

Workspace volumes are deleted with their PVCs, so snapshot anything you want
to keep (step 11) before this.
