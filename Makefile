UV ?= UV_CACHE_DIR=.uv-cache uv
REPOSITORY ?= pypi
PUBLISH_DIST_DIR ?= .build-publish-dist

ifeq ($(REPOSITORY),pypi)
PUBLISH_URL := https://upload.pypi.org/legacy/
CHECK_URL := https://pypi.org/simple/
endif

ifeq ($(REPOSITORY),testpypi)
PUBLISH_URL := https://test.pypi.org/legacy/
CHECK_URL := https://test.pypi.org/simple/
endif

.PHONY: test build build-publish

test:
	$(UV) run --extra dev python -m pytest

build:
	$(UV) build --no-sources

build-publish:
	@set -a; \
	if [ -f .env ]; then . ./.env; fi; \
	set +a; \
	: "$${PYPI_API_TOKEN:=$(PYPI_API_TOKEN)}"; \
	test -n "$$PYPI_API_TOKEN" || { \
		printf "\033[1;31merror:\033[0m missing PYPI_API_TOKEN environment variable. Set it in .env or export it in your shell.\n"; \
		exit 1; \
	}; \
	test -n "$(PUBLISH_URL)" || { \
		printf "\033[1;31merror:\033[0m unsupported REPOSITORY '%s'. Use 'pypi' or 'testpypi'.\n" "$(REPOSITORY)"; \
		exit 1; \
	}; \
	printf "\033[1;36m==>\033[0m \033[1mBuilding Aptitude Publisher distributions\033[0m\n"; \
	printf "\033[0;36m  Output:\033[0m %s\n" "$(PUBLISH_DIST_DIR)"; \
	printf "\033[0;36m  Target:\033[0m %s\n\n" "$(REPOSITORY)"; \
	$(UV) build --no-sources --clear --out-dir "$(PUBLISH_DIST_DIR)"; \
	printf "\033[1;36m==>\033[0m \033[1mPublishing Aptitude Publisher distributions\033[0m\n"; \
	printf "\033[0;36m  Upload:\033[0m %s\n" "$(PUBLISH_URL)"; \
	printf "\033[0;36m  Check:\033[0m  %s\n\n" "$(CHECK_URL)"; \
	UV_PUBLISH_TOKEN="$$PYPI_API_TOKEN" \
	$(UV) publish \
		--trusted-publishing never \
		--publish-url "$(PUBLISH_URL)" \
		--check-url "$(CHECK_URL)" \
		"$(PUBLISH_DIST_DIR)"/*
