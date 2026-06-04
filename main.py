#!/usr/bin/env python3
"""
rustfs-mcp CLI entrypoint.

Adds transport selection + connection-from-request so ONE long-lived Deployment
can serve every RustFS (S3) add-on: the kubero chat broker passes the target
endpoint + creds + access mode per request as HTTP headers (X-Kubero-DB-URI =
s3://ak:sk@host:9000, X-Kubero-Access-Mode). Default (no flags) = stdio + the
S3_ENDPOINT_URL / AWS_* env vars for local use.
"""

import os

import click


@click.command()
@click.option(
    "--transport",
    type=click.Choice(["stdio", "sse", "streamable-http"]),
    default="stdio",
    help="MCP transport (default stdio).",
)
@click.option(
    "--connection-from-request",
    is_flag=True,
    help=(
        "Per-request connection mode: read the target endpoint + creds from the "
        "X-Kubero-DB-URI header (s3://ak:sk@host:port) + access mode from "
        "X-Kubero-Access-Mode, instead of binding one endpoint at boot. "
        "Requires an HTTP transport."
    ),
)
@click.option("--streamable-http-host", default="127.0.0.1", help="HTTP bind host")
@click.option("--streamable-http-port", default=8000, type=int, help="HTTP bind port")
def cli(transport, connection_from_request, streamable_http_host, streamable_http_port):
    if connection_from_request:
        os.environ["S3_CONNECTION_FROM_REQUEST"] = "1"

    # Import AFTER the env is set so the connection layer sees the flag.
    from server import mcp

    if transport in ("streamable-http", "sse"):
        mcp.settings.host = streamable_http_host
        mcp.settings.port = streamable_http_port

    mcp.run(transport=transport)


if __name__ == "__main__":
    cli()
