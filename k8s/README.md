# k8s manifests

Kubernetes equivalent of `docker-compose.yml`'s 13 app services (the observability/
infra containers — postgres, redis, redpanda, rag-postgres, prometheus, grafana,
jaeger — are not included here; deploy those separately under the same DNS names
referenced in `configmap.yaml` and `<service>-secret.yaml`, or point the config at
managed equivalents). Each service gets a `Deployment` + `ClusterIP` `Service` in
`k8s/<service>.yaml`, port 8000 (matches every Dockerfile's `EXPOSE`/`CMD`), image
`ticketflow-<service>:latest`. Shared non-sensitive env (postgres host/port/user,
redis/kafka URLs, inter-service URLs) lives in `configmap.yaml`; per-service secrets
(JWT key, Fernet key, webhook secret, internal shared secret) live in
`<service>-secret.yaml` with `CHANGE_ME_IN_PROD` placeholders — replace those before
applying to any real cluster. All resources live in the `ticketflow` namespace
(`namespace.yaml`).

To deploy against a real cluster:

```
kubectl apply -f k8s/
```

**Not deployed or tested against a live cluster in this session.** Validation here
was limited to `python -c "import yaml; yaml.safe_load(...)"` against every file
(all 28 parsed cleanly) plus an attempted `kubectl apply --dry-run=client`, which
this machine's `kubectl` client could not complete (no configured cluster context
to fetch the OpenAPI schema against, even for client-side validation). Treat this
as validated-but-undeployed scaffolding, not a proven-working deployment.
