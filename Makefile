# The same paths as CI's lint job (.github/workflows/ci.yml): only the code this
# repository owns, never the third-party submodules under modules/.
LINT_PATHS = packages tests check_registry.py scripts
# The files mypy gates on in CI (the rest of packages/ has pre-existing errors).
MYPY_PATHS = packages/huri-events/huri_events \
	packages/huri-module-tts/huri_tts packages/huri-module-vad/huri_vad \
	packages/huri-module-stt/huri_stt packages/huri-module-tag/huri_tag \
	packages/huri-hub/huri_hub check_registry.py

# mypy reads huri-core's types from the installed package (it ships py.typed),
# but cannot see an editable install made by setuptools' import hook: with
# HURI_CORE_PATH=../huri-core (as for ./install.sh), it reads that checkout.
ifdef HURI_CORE_PATH
export MYPYPATH := $(HURI_CORE_PATH)
endif

# Run every check even if an earlier one fails, then fail at the end if any did.
lint:
	@rc=0; \
	for cmd in "black --check $(LINT_PATHS)" "isort --check-only $(LINT_PATHS)" \
		"flake8 $(LINT_PATHS)" \
		"mypy --check-untyped-defs --follow-imports=silent $(MYPY_PATHS)"; do \
		echo "==> $$cmd"; \
		$$cmd || rc=1; \
	done; \
	exit $$rc

# Rewrites the files lint checks.
format:
	black $(LINT_PATHS)
	isort $(LINT_PATHS)

test:
	pytest

# Run lint and test even if lint fails, then fail at the end if either did.
check:
	@rc=0; \
	$(MAKE) --no-print-directory lint || rc=1; \
	$(MAKE) --no-print-directory test || rc=1; \
	exit $$rc

.PHONY: lint format test check
