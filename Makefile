.PHONY: help up down logs psql ch-sql redis-cli test lint typecheck fmt install ingest-discover ingest-clob

help:
	@echo "Common targets:"
	@echo "  make install         # install Python + Node workspaces"
	@echo "  make up              # bring up postgres + clickhouse + redis (docker compose)"
	@echo "  make down            # stop infra"
	@echo "  make logs            # tail infra logs"
	@echo "  make psql            # psql into the postgres container"
	@echo "  make ch-sql          # clickhouse-client into the clickhouse container"
	@echo "  make redis-cli       # redis-cli into the redis container"
	@echo "  make test            # pytest across all Python packages"
	@echo "  make lint            # ruff check"
	@echo "  make typecheck       # mypy"
	@echo "  make fmt             # ruff format"
	@echo "  make ingest-discover # run gamma discovery worker once"
	@echo "  make ingest-clob     # run CLOB poller worker once"

install:
	uv sync
	pnpm install

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

psql:
	docker compose exec postgres psql -U $${POSTGRES_USER:-polypredictor} -d $${POSTGRES_DB:-polypredictor}

ch-sql:
	docker compose exec clickhouse clickhouse-client --database $${CLICKHOUSE_DB:-polypredictor}

redis-cli:
	docker compose exec redis redis-cli

test:
	uv run pytest

lint:
	uv run ruff check .

typecheck:
	uv run mypy packages services

fmt:
	uv run ruff format .
	uv run ruff check --fix .

ingest-discover:
	uv run python -m ingest.workers.gamma_discovery

ingest-clob:
	uv run python -m ingest.workers.clob_poller
