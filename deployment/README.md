# Deploying to a remote cluster (registry-based)

This is the registry-based counterpart to [`../k8s/`](../k8s/README.md).
`k8s/` assumes images are built and loaded directly into a local Minikube
(`minikube image load`) — fine when everything runs on one machine. This
folder instead **pushes both images to a container registry**, then deploys
by pulling them back down on whatever cluster `kubectl` is currently pointed
at. That's the shape needed to build here, push to a registry, and deploy
into a Kubernetes namespace on AWS that you reach through a KASM workspace.

It also assumes you may not have full admin rights in that namespace — see
[Restricted permissions](#restricted-permissions) below. If you only know
you can create a Deployment/Pod there and nothing else is confirmed yet,
that's the expected starting point; you don't need to sort out the rest of
your RBAC grants before running this.

It reuses the exact same Kubernetes design as `k8s/` (documented there in
detail): frontend and backend run as two containers in one Pod, talking over
`127.0.0.1`, because the frontend's API proxy refuses plain HTTP to any
non-loopback host. Only the image source and a few names are templated here.

```
deployment/
  .env.example          copy to .env and fill in
  lib/env.sh             shared config resolution, sourced by the scripts below
  build-and-push.sh      docker build + push (run wherever docker + registry auth live)
  render-manifests.sh    fills in manifests/*.yaml.tmpl -> manifests/rendered/
  deploy.sh              kubectl apply against whatever cluster kubectl points at
  manifests/*.yaml.tmpl  templates (namespace, PVC, Deployment, Service)
```

## The two phases

**Phase 1 — build and push.** Run this wherever `docker` is installed and
already authenticated to your registry (`docker login`, or for ECR:
`aws ecr get-login-password --region <region> | docker login --username AWS
--password-stdin <account>.dkr.ecr.<region>.amazonaws.com`). This repo's
working directory is the build context either way.

```bash
cd deployment
cp .env.example .env
# edit .env: set REGISTRY (and anything else you want to override)
./build-and-push.sh
```

**Phase 2 — deploy.** Run this from inside the KASM workspace, where
`kubectl` is already pointed at the target AWS namespace. Copy or clone this
repo into that workspace — or just the `deployment/` and `deploy/` folders
plus the two root Dockerfiles if you'd rather keep it minimal — carrying
over the same `.env` (or at least the same `REGISTRY`/`IMAGE_TAG`) from
phase 1.

```bash
cd deployment
kubectl config current-context   # confirm this really is the target cluster
./deploy.sh
```

`deploy.sh` renders the manifests, then attempts (in order) the namespace,
an optional registry pull secret, the `primrose-backend-env` secret from
`../deploy/backend.env` (see below), the PVC, and finally the
Deployment/Service — printing a pass/fail summary at the end. See
[Restricted permissions](#restricted-permissions) for what happens when one
of those isn't something you're allowed to do.

## Backend configuration secret

The backend refuses to start without `PRIMROSE_BACKEND_TOKEN` (32+ chars),
and the frontend needs the same token to authenticate its proxied requests.
This reuses the repo's existing `deploy/backend.env` convention — the same
file the Docker Compose owner deployment and the plain `k8s/` flow use:

```bash
cp ../deploy/backend.env.example ../deploy/backend.env
# edit: set PRIMROSE_BACKEND_TOKEN. Model endpoint vars are optional —
# omit them and extraction/GraphRAG/target-development fall back to
# deterministic behavior instead of failing.
```

If that file is missing when `deploy.sh` runs, it prints a warning and
deploys anyway — the Pod will sit in `CreateContainerConfigError` until you
create the file and re-run `./deploy.sh` (or create the secret by hand).

## Registry authentication for the cluster

Push-time auth (`docker login`) and cluster pull-time auth are separate. If
your registry needs credentials to be *pulled from* (most private
registries; not usually needed for a public ECR repo, or for ECR when the
cluster's own node role already has ECR pull permissions — common on EKS),
set `REGISTRY_USERNAME`/`REGISTRY_PASSWORD` in `.env`. `deploy.sh` then
creates a `kubernetes.io/dockerconfigjson` secret and attaches it to the
namespace's default `ServiceAccount`, so every Pod in the namespace can pull
without each Deployment needing its own `imagePullSecrets` entry.

## Restricted permissions

`deploy.sh` doesn't assume you have full control of the namespace — only
that you can create a Deployment there (everything else is a separate RBAC
grant: `Namespace` is even a cluster-scoped resource, so a namespace-scoped
Role can't cover it at all). Namespace, the optional pull secret, the
backend secret, and the PVC are each attempted but **fail with a warning
instead of aborting the script** if they're forbidden — the script carries
on and still applies the Deployment and Service. At the end it prints a
summary like:

```
=== Summary ===
  namespace:      FAILED (likely no permission to manage Namespace objects)
  pull_secret:    not requested (REGISTRY_USERNAME/REGISTRY_PASSWORD unset)
  backend_secret: FAILED (deploy/backend.env not found)
  pvc:            FAILED (likely no permission to create PersistentVolumeClaims)
  deployment:     ok
  service:        FAILED (likely no permission to create Services)
  rollout:        did not become ready in time
```

That tells you exactly what you're missing (here: Secrets, PVCs and
Services all need to exist before the Pod can actually run, and someone
with more access — or a different role bound to you — needs to create
them, or grant you the ability to). Once you *do* know which of these you
don't have, set the matching `MANAGE_NAMESPACE` / `MANAGE_PVC` /
`MANAGE_SECRETS` / `MANAGE_SERVICE_ACCOUNT` toggle to `false` in `.env` to
skip that step (and its warning) outright rather than re-discovering it
every run.

The Deployment/Service apply itself is the one thing treated as required —
if that fails, `deploy.sh` exits non-zero, since nothing is running at that
point.

## Notes for an AWS-managed Kubernetes namespace

- `SERVICE_TYPE` defaults to `ClusterIP` — it needs no more permission than
  any other Service, provisions no AWS resources, and always works via
  `kubectl -n <namespace> port-forward svc/primrose 8080:3000`. If your
  cluster has real load-balancer integration (typical on EKS) and you want
  a direct external endpoint, `LoadBalancer` will provision one — but that's
  a real, billable AWS resource, so switch to it deliberately rather than
  by default. `NodePort` sits in between (no new AWS resource, but needs
  the node reachable on that port, which a managed/shared cluster may not
  allow).
- `STORAGE_CLASS` is empty by default (defers to the cluster's default
  StorageClass). A freshly created cluster may not have one marked default;
  if the PVC sits `Pending` indefinitely, that's almost always why — ask
  whoever manages the cluster which StorageClass to set.
- If you want model features (extraction/GraphRAG/target-development) to
  reach an LLM endpoint running on your own machine rather than in AWS,
  `host.docker.internal`/`host.minikube.internal` won't resolve to *your*
  laptop from inside an AWS cluster — point those vars at a reachable
  endpoint instead (something on the AWS side, or a tunnel you set up
  yourself).
- Set `IMAGE_TAG` to something immutable (git sha, semver) once you're past
  active iteration, and switch `IMAGE_PULL_POLICY` to `IfNotPresent` —
  `Always` (the default here) exists so a mutable `dev` tag actually gets
  re-pulled on redeploy, but it means every Pod (re)start hits the registry.

## Redeploying after a change

```bash
cd deployment
./build-and-push.sh        # phase 1, wherever docker lives
# then, in the KASM workspace:
./deploy.sh                 # re-applies; with IMAGE_PULL_POLICY=Always this
                             # picks up the new image on rollout
kubectl -n "$K8S_NAMESPACE" rollout restart deployment/primrose  # if the
                             # tag didn't change and you need to force it
```
