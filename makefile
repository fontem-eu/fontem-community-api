TAG := $(shell git rev-parse --short HEAD)
IMAGE := contribute.void42.internal/golden/gmr-community-api

all: build release deploy

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

test:
	python3 -m pytest tests/unit/ -v

.PHONY: all build release deploy test
