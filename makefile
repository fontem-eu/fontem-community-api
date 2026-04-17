SONAR_URL   ?= http://sonarqube.sonarqube.svc.cluster.local:9000
SONAR_TOKEN ?= $(shell cat /config/.sonarqube-token 2>/dev/null)
SCANNER     ?= /config/.local/sonar-scanner/bin/sonar-scanner
JAVA_HOME   ?= $(shell dirname $$(dirname $$(readlink -f $$(which java))))
export JAVA_HOME

TAG     := $(shell git rev-parse --short HEAD)
IMAGE   := contribute.void42.internal/golden/gmr-community-api
PROJECT := gmr-community-api
SRC     := src
TESTS   := tests

all: build release deploy

# ── Quality ──────────────────────────────────────────────────
test:
	python3 -m pytest $(TESTS) \
		--cov=$(SRC) \
		--cov-report=xml:coverage.xml \
		--cov-config=.coveragerc \
		--junitxml=test-results.xml \
		-q
	python3 -m pylint $(SRC) $(TESTS) \
		--output-format=parseable \
		--reports=no \
		> pylint-report.txt 2>&1 || true
	@tail -1 pylint-report.txt

analyze: test
	$(SCANNER) \
		-Dsonar.projectKey=$(PROJECT) \
		-Dsonar.sources=$(SRC) \
		-Dsonar.tests=$(TESTS) \
		-Dsonar.language=py \
		-Dsonar.python.coverage.reportPaths=coverage.xml \
		-Dsonar.python.xunit.reportPath=test-results.xml \
		-Dsonar.python.pylint.reportPaths=pylint-report.txt \
		-Dsonar.host.url=$(SONAR_URL) \
		-Dsonar.token=$(SONAR_TOKEN) \
		-Dsonar.scm.provider=git \
		'-Dsonar.coverage.exclusions=src/infra/postgres/**/*'
	@echo "Dashboard: $(SONAR_URL)/dashboard?id=$(PROJECT)"

# ── Deploy ───────────────────────────────────────────────────
build:
	docker build -t $(IMAGE):$(TAG) .

release:
	docker push $(IMAGE):$(TAG)

deploy:
	helm upgrade --install gmr-community-api ./deployment --set-string version=$(TAG)
	@echo "Deploying..."
	kubectl -n gmr rollout restart deployment gmr-community-api
	@echo "Waiting for deployment to become ready..."
	kubectl -n gmr rollout status deployment/gmr-community-api --timeout=300s
	@echo "Deployment is ready!"

# Build + push + commit new tag to dev/gmr-community-api.yaml in gitops.
# ArgoCD's gmr-community-api-dev Application picks it up. Hot-loop dev flow.
GITOPS_REPO  ?= http://oauth2:$(GITOPS_TOKEN)@gitea-http.dev-tools.svc.cluster.local:3000/golden/gitops.git
GITOPS_EMAIL ?= $(USER)@local
GITOPS_NAME  ?= $(USER) (local dev)

deploy-dev: build release
	@if [ -z "$(GITOPS_TOKEN)" ]; then echo "GITOPS_TOKEN not set (Gitea PAT for gitops push)"; exit 1; fi
	@tmp=$$(mktemp -d) && \
	  git clone --depth=1 $(GITOPS_REPO) $$tmp >/dev/null 2>&1 && \
	  cd $$tmp && \
	  sed -i 's/version: ".*"/version: "$(TAG)"/' dev/gmr-community-api.yaml && \
	  git config user.email "$(GITOPS_EMAIL)" && \
	  git config user.name "$(GITOPS_NAME)" && \
	  git add dev/gmr-community-api.yaml && \
	  (git diff --cached --quiet && echo "dev/gmr-community-api.yaml already at $(TAG)") || \
	  (git commit -m "dev: deploy gmr-community-api $(TAG) (local)" && git push origin main) ; \
	  rm -rf $$tmp
	@echo "gmr-community-api:$(TAG) deployed to gmr-dev"

# Run mutation tests in an isolated temp copy to avoid source contamination.
# mutmut modifies files on disk during its run -- never run it in the real tree.
mutation:
	@MUTDIR=$$(mktemp -d) && \
	echo "Running mutmut in isolated copy: $$MUTDIR" && \
	cp -a . "$$MUTDIR/" && \
	cd "$$MUTDIR" && rm -f .mutmut-cache && \
	python3 -m mutmut run --paths-to-mutate=src/domain/,src/services/ ; \
	STATUS=$$? ; \
	python3 -m mutmut results 2>/dev/null ; \
	rm -rf "$$MUTDIR" ; \
	exit $$STATUS

.PHONY: all test analyze mutation build release deploy deploy-dev security

DTRACK_URL  ?= http://dependency-track.dependency-track.svc.cluster.local:8080
DTRACK_KEY  ?= $(shell cat /config/.dtrack-api-key 2>/dev/null)

# ── Security & SBOM ─────────────────────────────────────────
audit:
	pip-audit -r requirements.txt --desc 2>&1 || true
	@echo ""
	@echo "=== Renovate Dependency Report ==="
	LOG_LEVEL=warn npx renovate --platform=local --dry-run 2>&1 | grep -E "dependency|update|→|->|current|new" | head -30 || true

sbom:
	cyclonedx-py requirements requirements.txt --of json -o sbom.json
	curl -s -X POST "$(DTRACK_URL)/api/v1/bom" \
		-H "X-Api-Key: $(DTRACK_KEY)" \
		-H "Content-Type: multipart/form-data" \
		-F "autoCreate=true" \
		-F "projectName=$(PROJECT)" \
		-F "projectVersion=main" \
		-F "bom=@sbom.json" > /dev/null
	@echo "SBOM uploaded to Dependency-Track"

.PHONY: audit sbom security

security:
	pip-audit -r $(firstword $(wildcard requirements.txt Requirements.txt)) --desc
