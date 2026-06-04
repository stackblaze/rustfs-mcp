# rustfs-mcp

A per-add-on **RustFS (S3) object-storage admin MCP** for the kubero chat. When a
user selects a RustFS add-on in the chat's `@`-context selector, the kubero server
(the feature-MCP broker) surfaces this server's tools namespaced `s3__*`, gated by
the add-on selection. The 5th feature MCP after postgres/redis/mysql/kafka.

RustFS is an S3-compatible object store (operator CR kind `Tenant`, group
`rustfs.com`); in-cluster its S3 API is `http://<instance>-io:9000`, plain HTTP
with path-style addressing.

## Architecture

ONE long-lived ClusterIP Deployment (`rustfs-mcp:3010` in ns `kubero-mcp`) serves
**every** RustFS add-on in the workload cluster. The target endpoint + credentials
+ access mode arrive **per request** as HTTP headers set by the kubero broker:

| Header | Value |
|---|---|
| `X-Kubero-DB-URI` | `s3://<accesskey>:<secretkey>@<host>:9000` (creds URL-encoded) |
| `X-Kubero-Access-Mode` | `restricted` (production phase, read-only) or `unrestricted` |

`--connection-from-request` reads these via the FastMCP lowlevel `request_ctx`
contextvar; a missing/invalid access mode ⇒ **restricted** (fail-safe). The kubero
server reaches the service via apiserver port-forward — no public ingress.

## Tools

Reads (always available): `list_buckets`, `list_objects`, `stat_object`,
`bucket_usage`, `get_object_text`, `get_bucket_policy`.

Writes (refused under restricted mode): `create_bucket`, `delete_bucket`,
`put_object_text`, `delete_object`, `set_bucket_policy`.

## Run

```bash
pip install -r requirements.txt

# Production (in-cluster): per-request connection from headers.
python main.py --transport streamable-http --connection-from-request \
  --streamable-http-host 0.0.0.0 --streamable-http-port 3010

# Local (stdio): binds one endpoint from S3_ENDPOINT_URL / AWS_* env (.env.template).
python main.py
```

## CI / deploy

Mirrors the other stackblaze feature MCPs: `auto-tag-on-main` → `docker-release`
(amd64, self-hosted `kubero` runner, `ghcr.io/stackblaze/rustfs-mcp`) → `deploy.yaml`
rolls the ClusterIP Deployment to the workload cluster (gated on
`KUBECONFIG_B64_SHARED`). Repo secrets: `CHAIN_PAT`, `KUBECONFIG_B64_SHARED`. Seed a
`v0.1.0-sb.0` tag once so auto-tag can increment.
