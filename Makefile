# mythosCircle — the single standard entry points (see README.md).
#
# Toolchain is project standard: uv (backend: ruff, mypy, pytest) and
# npm (frontend: eslint, prettier, vue-tsc, vitest). Checks run under
# `uv run`; the dev launcher uses the pinned backend venv directly.

UV ?= uv
NPM ?= npm

.PHONY: help setup dev test lint format typecheck

help:
	@echo "mythosCircle — standard entry points"
	@echo "  setup      provision backend venv (uv) and frontend node_modules (npm ci)"
	@echo "  dev        run backend and frontend with writable local data paths"
	@echo "  test       backend pytest + frontend vitest"
	@echo "  lint       ruff check (backend) + eslint (frontend)"
	@echo "  format     ruff format (backend) + prettier --write (frontend)"
	@echo "  typecheck  mypy (backend) + vue-tsc (frontend)"

setup:
	$(UV) sync --directory backend
	$(NPM) ci --prefix frontend

dev:
	bash scripts/dev.sh

test:
	$(UV) run --directory backend pytest -q
	$(NPM) run test --prefix frontend

lint:
	$(UV) run --directory backend ruff check .
	$(UV) run --directory backend ruff format --check .
	$(NPM) run lint --prefix frontend

format:
	$(UV) run --directory backend ruff format .
	$(UV) run --directory backend ruff check --fix .
	$(NPM) run format --prefix frontend

typecheck:
	$(UV) run --directory backend mypy
	$(NPM) run typecheck --prefix frontend
