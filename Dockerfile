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

RUN mkdir -p /data/oathcast

EXPOSE 8080 8787

CMD ["python", "-m", "oathcast.service"]
