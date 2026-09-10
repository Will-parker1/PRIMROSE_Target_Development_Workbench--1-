# Running PRIMROSE on Minikube

Two images, one Pod: `frontend` (the vinext/React UI) and `backend` (the
Python knowledge-graph server) run as two containers in a single Pod, talking
over `127.0.0.1`. They are not split into separate Deployments because the
frontend's API proxy (`app/api/[...path]/route.ts`) deliberately refuses
plain HTTP to any backend host except `127.0.0.1`/`localhost` — only HTTPS is
trusted otherwise. Sharing a Pod satisfies that check with zero code changes
and keeps the backend's port off the network entirely, which matches the
project's existing "never expose the raw backend port" model.

## 1. Build the images into Minikube

Start Minikube if it isn't running, then build both images and load them so
Kubernetes can find them without a registry:

```bash
minikube start   # skip if already running

docker build -f Dockerfile.backend -t primrose-backend:dev .
docker build -f Dockerfile.frontend -t primrose-frontend:dev .

minikube image load primrose-backend:dev
minikube image load primrose-frontend:dev
```

(`minikube image load` works regardless of driver. If you're on the `docker`
driver you can instead `eval $(minikube docker-env)` before the two `docker
build` commands to build straight into Minikube's daemon and skip the load
step — rerun that `eval` in any new shell.)

The Deployment sets `imagePullPolicy: Never` on both containers, so re-running
`minikube image load` after a rebuild is how you ship a new version; `kubectl
rollout restart deployment/primrose -n primrose` picks it up.

## 2. Configure the backend

The backend refuses to start without `PRIMROSE_BACKEND_TOKEN` (32+ chars),
and the frontend needs the same token to authenticate its proxied requests.
Everything else (model endpoints) is optional — omit it and extraction/
GraphRAG/target-development narratives fall back to deterministic behavior
instead of failing.

```bash
cp deploy/backend.env.example deploy/backend.env
# edit deploy/backend.env: set PRIMROSE_BACKEND_TOKEN, and if you want model
# features, point KG_LM_STUDIO_URL / PRIMROSE_GEMMA4_BASE_URL /
# TARGET_DEVELOPMENT_BASE_URL at http://host.minikube.internal:<port>/v1
# instead of host.docker.internal — that's the Minikube equivalent for
# reaching a server running on your Mac (e.g. LM Studio).

kubectl apply -f k8s/namespace.yaml
kubectl create secret generic primrose-backend-env \
  --namespace primrose \
  --from-env-file=deploy/backend.env
```

Re-run the `kubectl create secret` command with `--dry-run=client -o yaml |
kubectl apply -f -` instead if you need to update it later.

## 3. Deploy

```bash
kubectl apply -f k8s/pvc.yaml -f k8s/deployment.yaml -f k8s/service.yaml
kubectl -n primrose rollout status deployment/primrose
```

## 4. Open it

```bash
minikube service primrose -n primrose
```

That prints (and opens) the reachable URL. Behind it: `GET /` redirects to
the static workbench UI, and everything under `/api/*` is proxied
server-side to the backend container with the bearer token attached — the
browser never sees `PRIMROSE_BACKEND_TOKEN`.

## Troubleshooting

- `kubectl -n primrose logs deploy/primrose -c backend` /
  `-c frontend` — logs for each container.
- `kubectl -n primrose describe pod -l app=primrose` — check
  `ImagePullBackOff` (means the image wasn't loaded into Minikube — repeat
  step 1) or probe failures.
- The backend's `/healthz` is unauthenticated by design (container health
  checks only); every other backend route requires the bearer token, which
  is why the frontend readiness probe hits `/api/health` (through the proxy)
  rather than the backend directly.
