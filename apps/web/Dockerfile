FROM node:20-slim AS deps

ENV PNPM_HOME="/pnpm" \
    PATH="/pnpm:${PATH}"

WORKDIR /app

RUN corepack enable

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json ./apps/web/package.json

RUN pnpm install --frozen-lockfile

FROM node:20-slim AS builder

ENV PNPM_HOME="/pnpm" \
    PATH="/pnpm:${PATH}" \
    NEXT_TELEMETRY_DISABLED=1

WORKDIR /app

RUN corepack enable

COPY --from=deps /app/node_modules ./node_modules
COPY --from=deps /app/apps/web/node_modules ./apps/web/node_modules
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web ./apps/web

RUN pnpm --filter @polypredictor/web build

FROM node:20-slim AS runtime

ENV PNPM_HOME="/pnpm" \
    PATH="/pnpm:${PATH}" \
    NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1

WORKDIR /app

RUN corepack enable

COPY --from=builder /app/package.json /app/pnpm-lock.yaml /app/pnpm-workspace.yaml ./
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/apps/web ./apps/web

EXPOSE 3000

CMD ["sh", "-c", "pnpm --filter @polypredictor/web start -- --port ${PORT:-3000}"]
