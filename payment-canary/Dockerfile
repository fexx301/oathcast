FROM node:22-slim

ARG OATHCAST_RELEASE_ID=unreleased
ARG OATHCAST_SOURCE_SHA256=unrecorded

LABEL org.opencontainers.image.title="OathCast Application Payment Sidecar" \
      org.opencontainers.image.version="${OATHCAST_RELEASE_ID}" \
      org.opencontainers.image.revision="${OATHCAST_SOURCE_SHA256}"

WORKDIR /app/payment-canary

COPY --chown=node:node payment-canary/package.json payment-canary/package-lock.json ./
RUN npm ci

COPY --chown=node:node payment-canary/tsconfig.json ./tsconfig.json
COPY --chown=node:node payment-canary/src ./src

# The payment signer is injected at runtime. The image never contains a key.
USER node

CMD ["npm", "run", "sidecar"]
