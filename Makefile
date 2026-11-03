# Agent Trust Layer. Owned by L0.
# make check | make demo [SIM=1] [AUTO=10] [AGENT=scripted] [RAILWAIT=600] | make verify RUN=<run_id> | make secret-scan | make bench | make reset

.PHONY: check lint test demo verify secret-scan bench bench-run bench-replay bench-report reset setup

DEMO_FLAGS := $(if $(SIM),--sim,) $(if $(AUTO),--auto-approve-after $(AUTO),) $(if $(RAILWAIT),--rail-auth-wait $(RAILWAIT),)

check: lint test

lint:
	uv run ruff check .

test:
	uv run pytest -q

# AGENT=scripted selects the labeled scripted MCP driver (pipeline test only, never agent evidence).
demo:
	$(if $(AGENT),ATL_AGENT=$(AGENT) ,)uv run atl demo $(DEMO_FLAGS)

verify:
	@test -n "$(RUN)" || (echo "usage: make verify RUN=<run_id>" && exit 2)
	uv run python -m verify.verify_run $(RUN)

secret-scan:
	uv run python scripts/secret_scan.py

bench:
	uv run python -m eval.bench

# HW7: ATL vs baseline on eval/scenarios.json (simulated rail, real Claude Code agent).
# make bench-run [SPLIT=tuning] [REPS=3] [JOBS=3] [EPISODES=T01,F02] | make bench-replay | make bench-report DIR=...
bench-run:
	uv run python -m eval.harness --split $(or $(SPLIT),all) --reps $(or $(REPS),3) --jobs $(or $(JOBS),3) $(if $(EPISODES),--episodes $(EPISODES),)

bench-replay:
	uv run python -m eval.replay $(if $(SPLIT),--split $(SPLIT),)

bench-report:
	@test -n "$(DIR)" || (echo "usage: make bench-report DIR=reports/bench/<bench_id>" && exit 2)
	uv run python -m eval.report $(DIR)

reset:
	uv run atl reset

setup:
	uv run python scripts/sandbox_setup.py
