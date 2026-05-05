# Convenience targets for local development. None of these are required —
# everything still works with `python3 setup.py` and `python3 bot.py`.

PYTHON ?= python3
VENV   ?= .venv
PIP    := $(VENV)/bin/pip
PY     := $(VENV)/bin/python3

.PHONY: help install setup doctor run docker-build docker-up update clean lint test

help:
	@echo "discord-llm-bot — make targets"
	@echo ""
	@echo "  make install     create venv + install deps (with --pre)"
	@echo "  make setup       run the interactive setup wizard"
	@echo "  make doctor      read-only health check"
	@echo "  make run         start the bot"
	@echo "  make docker-up   build + run with docker compose"
	@echo "  make update      git pull + reinstall deps"
	@echo "  make clean       remove venv, caches, .env backups"
	@echo "  make test        smoke-import every module"

$(VENV)/bin/activate:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip

install: $(VENV)/bin/activate
	$(PIP) install --pre -r requirements.txt

setup: install
	$(PY) setup.py

doctor: $(VENV)/bin/activate
	$(PY) setup.py doctor

run: $(VENV)/bin/activate
	$(PY) bot.py

docker-build:
	docker compose build

docker-up:
	docker compose up --build

update:
	git pull --ff-only
	$(PIP) install --pre -r requirements.txt

clean:
	rm -rf $(VENV) __pycache__ .pytest_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type f -name '.env.bak.*' -delete

test: install
	$(PY) -c "import ast, pathlib; \
		[ast.parse(pathlib.Path(f).read_text()) for f in ['bot.py', 'voice_manager.py', 'elevenlabs_client.py', 'setup.py']]; \
		print('ok parse')"
	$(PY) -c "import importlib; \
		[importlib.import_module(m) for m in ['config', 'mcp_config', 'providers', 'personalities', 'context_manager', 'search_client']]; \
		print('ok import')"
