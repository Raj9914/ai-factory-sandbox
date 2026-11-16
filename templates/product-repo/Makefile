# Ship48 product Makefile contract (stack-agnostic).
#
# CI, agents and humans only ever call these four targets:
#   make install            install dependencies from the lockfile (no unpinned installs)
#   make test               run every test; must exit non-zero on any failure
#   make build              build the deployable artefact into dist/
#   make smoke URL=<url>    check a deployed preview/beta URL responds
#
# Replace the placeholder recipes below with the product's stack commands,
# e.g. `npm ci` / `npm test` / `npm run build`, or `pip install -r requirements.lock` / `pytest`.
# Keep the target names, the dist/ output folder and the URL= argument unchanged.

DIST := dist

.PHONY: install test build smoke clean

install:
	@echo "install: no stack configured yet; replace this recipe with the lockfile install command"

test:
	@echo "test: no stack configured yet; checking the repository contract only"
	@for f in README.md AGENTS.md CLAUDE.md docs/PRD.md experiments/README.md TEMPLATE_VERSION; do \
		test -f "$$f" || { echo "missing $$f"; exit 1; }; \
	done
	@echo "test: contract files present"

build:
	@mkdir -p $(DIST)
	@echo "build: no stack configured yet; build output belongs in $(DIST)/"

smoke:
	@test -n "$(URL)" || { echo "usage: make smoke URL=<base-url>"; exit 2; }
	@bash scripts/smoke.sh "$(URL)"

clean:
	@rm -rf $(DIST)
