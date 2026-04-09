IMAGE := contribute.void42.internal/golden/gmr-community-api:latest

.PHONY: test gate mutation build deploy

test:
	python3 -m pytest tests/ -q

gate: test

mutation:
	python3 -m mutmut run --paths-to-mutate=src/domain/,src/services/

build:
	docker build -t gmr-community-api:latest .

deploy: build
	docker tag gmr-community-api:latest $(IMAGE)
	docker push $(IMAGE)
	kubectl set image deployment/gmr-community-api -n gmr api=$(IMAGE)
	kubectl rollout status deployment/gmr-community-api -n gmr --timeout=60s
