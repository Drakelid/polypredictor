FROM node:20-slim

ENV PNPM_HOME="/pnpm" \
    PATH="/pnpm:${PATH}" \
    NEXT_TELEMETRY_DISABLED=1

WORKDIR /app

RUN corepack enable

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json ./apps/web/package.json

RUN pnpm install --frozen-lockfile

COPY apps/web ./apps/web

RUN pnpm --filter @polypredictor/web build

EXPOSE 3000

# Next.js honours the PORT env var natively; passing --port via pnpm
# leaks the literal `--` separator into next's argv (which then tries to
# treat `--port` as a positional project directory).
CMD ["sh", "-c", "PORT=${PORT:-3000} pnpm --filter @polypredictor/web start"]
