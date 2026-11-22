# AI Intelligence Hub — common local commands. Requires: uv, pnpm (Node 20+), PostgreSQL 15+.
BACKEND := backend
FRONTEND := frontend
UV := cd $(BACKEND) && uv run

.PHONY: setup build migrate owner serve worker start dev test lint typecheck check backup doctor status

setup:            ## install backend + frontend dependencies and build the UI
	cd $(BACKEND) && uv sync
	cd $(FRONTEND) && pnpm install --frozen-lockfile
	$(MAKE) build

build:            ## build the web UI into frontend/dist (served by the backend)
	cd $(FRONTEND) && pnpm build

migrate:          ## apply database migrations and seed the source registry
	$(UV) hub migrate

owner:            ## create the single owner account (interactive)
	$(UV) hub create-owner

serve:            ## web server only (http://127.0.0.1:8000)
	$(UV) hub serve

worker:           ## background collection only (keeps running with the browser closed)
	$(UV) hub worker

start:            ## server + worker with automatic restart
	$(UV) hub start

dev:              ## Vite dev server with /api proxied to the backend (run `make start` too)
	cd $(FRONTEND) && pnpm dev

test:             ## backend + frontend tests
	$(UV) pytest -q
	cd $(FRONTEND) && pnpm test

lint:
	$(UV) ruff check hub tests
	cd $(FRONTEND) && pnpm lint

typecheck:
	$(UV) mypy hub
	cd $(FRONTEND) && pnpm typecheck

check: lint typecheck test build

backup:
	$(UV) hub backup

doctor:
	$(UV) hub doctor

status:
	$(UV) hub status
