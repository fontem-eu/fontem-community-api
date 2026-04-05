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
		-Dsonar.scm.provider=git
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

.PHONY: all test analyze build release deploy
