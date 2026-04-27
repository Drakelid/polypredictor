.PHONY: help up down logs psql ch-sql redis-cli test lint typecheck fmt install ingest-discover ingest-clob ingest-features ingest-smart-money ingest-arb ingest-external ingest-microstructure-signals ingest-rss ingest-reddit ingest-x ingest-scheduled-events ingest-deribit-iv ingest-spot-validation ingest-macro-series ingest-perp-funding ingest-onchain-metrics retention-archive-plan drift-monitor signal-ablation resolution-risk-corpus dp-aggregates kol-credibility alert-outcome-audit m1-audit m2-audit m3-audit m4-audit m7-audit m8-audit ensemble-retrain source-failure-audit eol-monitor decision-time-monitor

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
	@echo "  make ingest-features # run market feature snapshot worker once"
	@echo "  make ingest-smart-money # run smart-money refresh worker"
	@echo "  make ingest-arb      # run sibling/no-arb checker once"
	@echo "  make ingest-external # run external venue divergence checker once"
	@echo "  make ingest-microstructure-signals # run large-print + book-shock worker once"
	@echo "  make ingest-rss      # run RSS/news ingestion worker once"
	@echo "  make ingest-reddit   # run Reddit ingestion worker once"
	@echo "  make ingest-scheduled-events # run scheduled catalyst ingestion once"
	@echo "  make ingest-deribit-iv # run Deribit IV surface poller once"
	@echo "  make ingest-spot-validation # run spot price cross-validation once"
	@echo "  make ingest-macro-series # run FRED/BLS macro series ingestion once"
	@echo "  make ingest-perp-funding # run Binance/Coinbase perp funding+basis poller once"
	@echo "  make ingest-onchain-metrics # run Glassnode/Dune on-chain ingestion once"
	@echo "  make retention-archive-plan # write cold-storage archive manifest"
	@echo "  make drift-monitor    # run nightly drift metrics + auto-disable driver once"
	@echo "  make signal-ablation # run monthly per-signal ablation + archive driver once"
	@echo "  make resolution-risk-corpus # build the resolution-risk training corpus once"
	@echo "  make dp-aggregates   # build the latest differentially private label aggregates once"
	@echo "  make kol-credibility # compute rolling per-KOL credibility from KOL-tagged posts vs resolutions"
	@echo "  make alert-outcome-audit # compute false-positive alert rate from signal_events vs market resolutions"
	@echo "  make m1-audit        # run the M1 active-type coverage + threshold IV spot-check audit once"
	@echo "  make m2-audit        # run the M2 typed-render + per-cell conformal coverage + journal v0 audit once"
	@echo "  make m3-audit        # run the M3 smart-money ablation + arb replay + signal-feed density audit once"
	@echo "  make m4-audit        # run the M4 social+event-time+resolution-risk exit audit once"
	@echo "  make m7-audit        # run the M7 journal+tuning exit audit once"
	@echo "  make m8-audit        # run the M8 per-cell Brier-skill + conformal-coverage exit audit once"
	@echo "  make ensemble-retrain # PIT-replay resolved markets and refit per-type ensembles"

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

ingest-features:
	uv run python -c "import asyncio; from ingest.workers.feature_snapshots import run_once; count = asyncio.run(run_once()); print(f'wrote {count} market feature snapshots')"

ingest-smart-money:
	uv run python -m ingest.workers.smart_money_refresh

ingest-arb:
	uv run python -c "import asyncio; from ingest.workers.arb_checker import run_once; count = asyncio.run(run_once()); print(f'wrote {count} arb signal events')"

ingest-external:
	uv run python -c "import asyncio; from ingest.workers.external_divergence import run_once; count = asyncio.run(run_once()); print(f'wrote {count} external divergence events')"

ingest-microstructure-signals:
	uv run python -c "import asyncio; from ingest.workers.microstructure_signals import run_once; count = asyncio.run(run_once()); print(f'wrote {count} microstructure signal events')"

ingest-rss:
	uv run python -c "import asyncio; from ingest.workers.rss_ingest import run_once; count = asyncio.run(run_once()); print(f'wrote {count} external news events')"

ingest-reddit:
	uv run python -c "import asyncio; from ingest.workers.reddit_ingest import run_once; count = asyncio.run(run_once()); print(f'wrote {count} external reddit events')"

ingest-x:
	uv run python -m ingest.workers.x_ingest


ingest-scheduled-events:
	uv run python -c "import asyncio; from ingest.workers.scheduled_events_ingest import run_once; count = asyncio.run(run_once()); print(f'wrote {count} scheduled catalyst events')"

ingest-deribit-iv:
	uv run python -c "import asyncio; from ingest.workers.deribit_iv_surface import run_once; count = asyncio.run(run_once()); print(f'wrote {count} Deribit IV surface rows')"

ingest-spot-validation:
	uv run python -c "import asyncio; from ingest.workers.spot_price_validation import run_once; count = asyncio.run(run_once()); print(f'wrote {count} validated spot price rows')"

ingest-macro-series:
	uv run python -c "import asyncio; from ingest.workers.macro_series_ingest import run_once; count = asyncio.run(run_once()); print(f'wrote {count} macro series rows')"

ingest-perp-funding:
	uv run python -c "import asyncio; from ingest.workers.perp_funding_basis import run_once; count = asyncio.run(run_once()); print(f'wrote {count} perp funding rows')"

ingest-onchain-metrics:
	uv run python -c "import asyncio; from ingest.workers.onchain_metrics import run_once; count = asyncio.run(run_once()); print(f'wrote {count} onchain metric rows')"

retention-archive-plan:
	uv run python -m ingest.retention --manifest artifacts/retention/archive-manifest.json

drift-monitor:
	uv run python -m api.drift_monitor

signal-ablation:
	uv run python -m api.signal_ablation

resolution-risk-corpus:
	uv run python -m api.resolution_risk_corpus

dp-aggregates:
	uv run python -m api.dp_aggregates

kol-credibility:
	uv run python -m api.kol_credibility

alert-outcome-audit:
	uv run python -m api.alert_outcome_audit

m1-audit:
	uv run python -m api.m1_audit

m2-audit:
	uv run python -m api.m2_audit

m3-audit:
	uv run python -m api.m3_audit

m4-audit:
	uv run python -m api.m4_audit

m7-audit:
	uv run python -m api.m7_audit

m8-audit:
	uv run python -m api.m8_audit

ensemble-retrain:
	uv run python -m api.ensemble_retrain

source-failure-audit:
	uv run python -m api.source_failure_audit

eol-monitor:
	uv run python -m api.eol_monitor

decision-time-monitor:
	uv run python -m api.decision_time_monitor
