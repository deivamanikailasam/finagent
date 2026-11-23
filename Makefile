# GNU Make 3.81 compatible: no .ONESHELL, no !=
FINAGENT_HMAC_KEY ?= 0000000000000000000000000000000000000000000000000000000000000000

.PHONY: install test lint fmt ingest eval scenarios test-llm run-dev schema

install:
	uv sync --locked

test:
	uv run pytest -q

lint:
	uv run ruff check .
	uv run ruff format --check .

fmt:
	uv run ruff check --fix .
	uv run ruff format .

ingest:
	@FINAGENT_HMAC_KEY='$(FINAGENT_HMAC_KEY)' uv run finagent ingest

eval:
	@FINAGENT_HMAC_KEY='$(FINAGENT_HMAC_KEY)' uv run finagent eval

scenarios:
	uv run pytest -m scenarios -q

test-llm:
	uv run --env-file .env pytest -m llm -q

run-dev:
	@FINAGENT_HMAC_KEY='$(FINAGENT_HMAC_KEY)' uv run finagent $(ARGS)

schema:
	@uv run python -c "import json; from finagent.domain.results import FinalResult; print(json.dumps(FinalResult.model_json_schema(), indent=2))"
