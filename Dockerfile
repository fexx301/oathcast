FROM python:3.12-slim

ARG OATHCAST_RELEASE_ID=unreleased
ARG OATHCAST_SOURCE_SHA256=unrecorded

LABEL org.opencontainers.image.title="OathCast Miner" \
      org.opencontainers.image.version="${OATHCAST_RELEASE_ID}" \
      org.opencontainers.image.revision="${OATHCAST_SOURCE_SHA256}"

WORKDIR /app

COPY pyproject.toml .
COPY src ./src
COPY miners ./miners
COPY scripts ./scripts

ENV PYTHONPATH=/app/src
ENV OATHCAST_HOST=0.0.0.0
ENV OATHCAST_REQUIRE_AUTH=true
ENV OATHCAST_RECEIPT_DB=/data/oathcast/receipts.sqlite3
ENV OATHCAST_RELEASE_ID=${OATHCAST_RELEASE_ID}
ENV OATHCAST_SOURCE_SHA256=${OATHCAST_SOURCE_SHA256}

# Run as a non-root account. A compromise through the request path should not
# start with root in the container.
#
# The UID/GID are pinned to 1000:1000 to match the `ec2-user` that owns the
# durable host directory bind-mounted at /data/oathcast. This is not cosmetic:
# a bind mount keeps the *host* ownership, so a container running as any other
# UID cannot write receipts, and every forecast fails at persistence time while
# /healthz and /readyz still report healthy. Changing this UID requires
# chown-ing the host directory in the same change.
RUN groupadd --gid 1000 oathcast \
    && useradd --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin oathcast \
    && mkdir -p /data/oathcast \
    && chown -R oathcast:oathcast /data/oathcast /app

USER 1000:1000

EXPOSE 8080 8787

# /healthz is served before the auth check, so the probe needs no credentials
# and never carries a token. urllib is already present; this avoids installing
# curl into the runtime image. A non-2xx status raises and exits non-zero.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).status == 200 else 1)"]

CMD ["python", "-m", "oathcast.service"]
