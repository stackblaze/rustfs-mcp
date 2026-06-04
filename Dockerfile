FROM python:3.12-slim

LABEL org.opencontainers.image.title="rustfs-mcp"
LABEL org.opencontainers.image.description="Stackblaze rustfs-mcp — per-add-on RustFS (S3) object-storage admin MCP for the kubero chat"
LABEL org.opencontainers.image.source="https://github.com/stackblaze/rustfs-mcp"
LABEL org.opencontainers.image.vendor="Stackblaze"

WORKDIR /app

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

# Non-root runtime (the deploy securityContext drops all caps + no-privesc).
# NB the uid is numeric so runAsNonRoot can verify it (a non-numeric USER fails
# with CreateContainerConfigError).
RUN useradd -m -u 1001 app && chown -R app /app
USER app

# The k8s Deployment passes args (--transport streamable-http
# --connection-from-request --streamable-http-host 0.0.0.0
# --streamable-http-port 3010). Default (no args) = stdio for local use.
ENTRYPOINT ["python", "main.py"]
CMD []
