SHELL := /bin/sh

LOCAL_COMPOSE = docker compose --env-file .env -f compose.yaml -p starkbank-trial-local
TEST_COMPOSE = docker compose -f compose.test.yaml -p starkbank-trial-test
VPS_COMPOSE = docker compose --env-file /etc/starkbank-trial/vps.env -f compose.vps.yaml -p starkbank-trial-vps
IMAGE_REPOSITORY ?= docker.io/gemanueldev/starkbank-backend-challenge
SOURCE_SHA ?= $(shell git rev-parse HEAD)
IMAGE_TAG ?= sha-$(SOURCE_SHA)

.DEFAULT_GOAL := help

.PHONY: help env-init starkbank-keygen validate-env build image-push up down restart health ps logs logs-webhook \
	test check tunnel tunnel-url tunnel-down sandbox-check webhook-setup webhook-list webhook-cleanup \
	smoke-invoice smoke-batch trial-start trial-status transfer-status-sync reset vps-config-check vps-secrets vps-pull \
	vps-deploy vps-up vps-down vps-health vps-status vps-trial-status vps-logs \
	vps-auth-pull vps-release vps-verify-image vps-backup vps-restore vps-rollback \
	vps-smoke-batch vps-trial-start vps-transfer-status-sync vps-live-enable vps-live-disable vps-live-status

help:
	@printf '%s\n' \
		'Local: env-init starkbank-keygen validate-env build up down restart health ps logs logs-webhook' \
		'Image: image-push CONFIRM_PUSH=yes [IMAGE_REPOSITORY=...]' \
		'Checks: test check' \
		'Tunnel: tunnel tunnel-url tunnel-down' \
		'Sandbox: sandbox-check webhook-setup webhook-list webhook-cleanup smoke-invoice smoke-batch trial-start trial-status' \
		'VPS: vps-config-check vps-secrets vps-pull vps-deploy vps-up vps-down vps-health' \
		'     vps-status vps-trial-status vps-logs vps-release vps-verify-image' \
		'     vps-auth-pull vps-backup vps-restore vps-rollback' \
		'     vps-smoke-batch vps-trial-start vps-live-enable vps-live-disable'

env-init:
	@test ! -e .env || { printf '.env already exists; refusing to overwrite it.\n' >&2; exit 1; }
	cp .env.example .env
	@printf 'Created .env. Set credentials before make up.\n'

starkbank-keygen:
	./infra/local/starkbank-keygen.sh

validate-env:
	./infra/local/validate-env.sh app
	@$(LOCAL_COMPOSE) config --quiet

build:
	docker build --target runtime --tag starkbank-trial:local .

image-push:
	@test "$(CONFIRM_PUSH)" = yes || { printf 'Pass CONFIRM_PUSH=yes to publish an image.\n' >&2; exit 1; }
	@test -z "$$(git status --porcelain)" || { printf 'Working tree must be clean before publishing.\n' >&2; exit 1; }
	@test "$$(printf '%s' "$(SOURCE_SHA)" | wc -c | tr -d ' ')" -eq 40 || { printf 'SOURCE_SHA must contain 40 characters.\n' >&2; exit 1; }
	@case "$(SOURCE_SHA)" in *[!0-9a-f]*) printf 'SOURCE_SHA must be lowercase hexadecimal.\n' >&2; exit 1 ;; esac
	docker buildx build --platform linux/amd64 --target runtime \
		--label "org.opencontainers.image.revision=$(SOURCE_SHA)" \
		--label "org.opencontainers.image.source=https://github.com/GleisonEm/starkbank-backend-challenge" \
		--tag "$(IMAGE_REPOSITORY):$(IMAGE_TAG)" --push .

up: validate-env
	$(LOCAL_COMPOSE) up --detach --wait --wait-timeout 180 postgres migrate api worker scheduler

down:
	$(LOCAL_COMPOSE) --profile tunnel down --remove-orphans
	@docker volume rm starkbank-trial-local_runtime_secrets >/dev/null 2>&1 || true

restart: down up

health:
	@set -a; . ./.env; set +a; \
	curl --fail --silent --show-error --max-time 10 "http://127.0.0.1:$${API_PORT:-8787}/health/ready"; \
	printf '\nLocal readiness check passed.\n'

ps:
	$(LOCAL_COMPOSE) --profile tunnel ps

logs:
	$(LOCAL_COMPOSE) --profile tunnel logs --follow --tail=200

logs-webhook:
	$(LOCAL_COMPOSE) exec -T api sh -c 'tail -n 200 -F /app/logs/starkbank-trial.jsonl | grep --line-buffered webhook'

test:
	@set -eu; \
	cleanup() { $(TEST_COMPOSE) down --volumes --remove-orphans >/dev/null 2>&1 || true; }; \
	trap cleanup EXIT HUP INT TERM; \
	$(TEST_COMPOSE) up --build --abort-on-container-exit --exit-code-from test test

check:
	@set -eu; \
	cleanup() { $(TEST_COMPOSE) down --volumes --remove-orphans >/dev/null 2>&1 || true; }; \
	trap cleanup EXIT HUP INT TERM; \
	$(TEST_COMPOSE) run --build --rm test sh -c \
		'uv lock --check && ruff format --check src tests migrations && ruff check src tests migrations && mypy src && basedpyright src tests && starkbank-trial db upgrade && pytest -p no:cacheprovider --cov=starkbank_trial --cov-report=term-missing && python -m build --outdir /tmp/dist && pip-audit'

tunnel: validate-env
	$(LOCAL_COMPOSE) --profile tunnel up --detach cloudflared-quick

tunnel-url:
	./infra/local/tunnel-url.sh

tunnel-down:
	-$(LOCAL_COMPOSE) --profile tunnel stop cloudflared-quick
	-$(LOCAL_COMPOSE) --profile tunnel rm --force cloudflared-quick

sandbox-check:
	./infra/local/validate-env.sh sandbox

webhook-setup: sandbox-check
	$(LOCAL_COMPOSE) run --rm scheduler starkbank-trial provider setup-webhook

webhook-list: validate-env
	$(LOCAL_COMPOSE) run --rm scheduler starkbank-trial provider list-webhooks

webhook-cleanup: sandbox-check
	@test "$(CONFIRM_SANDBOX)" = yes || { printf 'Pass CONFIRM_SANDBOX=yes.\n' >&2; exit 1; }
	$(LOCAL_COMPOSE) run --rm scheduler starkbank-trial provider cleanup-webhooks --confirm-sandbox

smoke-invoice: sandbox-check
	@test "$(CONFIRM_SANDBOX)" = yes || { printf 'Pass CONFIRM_SANDBOX=yes.\n' >&2; exit 1; }
	$(LOCAL_COMPOSE) run --rm scheduler starkbank-trial provider smoke-invoice --confirm-sandbox

smoke-batch: sandbox-check
	@test "$(CONFIRM_SANDBOX)" = yes || { printf 'Pass CONFIRM_SANDBOX=yes.\n' >&2; exit 1; }
	$(LOCAL_COMPOSE) run --rm scheduler starkbank-trial provider smoke-batch --count "$${COUNT:-8}" --reference "$${REFERENCE:-smoke-batch-1}" --confirm-sandbox

trial-start: sandbox-check
	@test "$(CONFIRM_SANDBOX)" = yes || { printf 'Pass CONFIRM_SANDBOX=yes.\n' >&2; exit 1; }
	$(LOCAL_COMPOSE) run --rm scheduler starkbank-trial trial start

trial-status: validate-env
	$(LOCAL_COMPOSE) run --rm scheduler starkbank-trial trial status

transfer-status-sync: validate-env
	$(LOCAL_COMPOSE) run --rm scheduler starkbank-trial provider sync-transfer-statuses

reset:
	@test "$(CONFIRM_RESET)" = yes || { printf 'Pass CONFIRM_RESET=yes; this deletes local volumes.\n' >&2; exit 1; }
	$(LOCAL_COMPOSE) --profile tunnel down --volumes --remove-orphans

vps-config-check:
	./infra/vps/config-check.sh

vps-secrets: vps-config-check
	./infra/vps/validate-secrets.sh

vps-pull: vps-secrets
	$(VPS_COMPOSE) pull

vps-auth-pull:
	@test -n "$(RELEASE_IMAGE)" || { printf 'Pass RELEASE_IMAGE=ghcr.io/...@sha256:...\n' >&2; exit 1; }
	@test -n "$(GHCR_USER)" || { printf 'Pass GHCR_USER=<github-user>.\n' >&2; exit 1; }
	./infra/vps/pull-private-image.sh "$(RELEASE_IMAGE)" "$(GHCR_USER)"

vps-deploy:
	./infra/vps/release.sh

vps-up: vps-secrets
	$(VPS_COMPOSE) up --detach --wait --wait-timeout 180 postgres migrate api worker scheduler caddy

vps-down: vps-config-check
	$(VPS_COMPOSE) down --remove-orphans

vps-health:
	./infra/vps/health.sh

vps-status: vps-config-check
	$(VPS_COMPOSE) ps

vps-trial-status: vps-config-check
	$(VPS_COMPOSE) run --rm scheduler starkbank-trial trial status

vps-smoke-batch: vps-config-check
	@test "$(CONFIRM_SANDBOX)" = yes || { printf 'Pass CONFIRM_SANDBOX=yes.\n' >&2; exit 1; }
	$(VPS_COMPOSE) run --rm scheduler starkbank-trial provider smoke-batch --count "$${COUNT:-8}" --reference "$${REFERENCE:-smoke-batch-1}" --confirm-sandbox

vps-trial-start: vps-config-check
	@test "$(CONFIRM_SANDBOX)" = yes || { printf 'Pass CONFIRM_SANDBOX=yes.\n' >&2; exit 1; }
	$(VPS_COMPOSE) run --rm scheduler starkbank-trial trial start

vps-transfer-status-sync: vps-config-check
	$(VPS_COMPOSE) run --rm scheduler starkbank-trial provider sync-transfer-statuses

vps-webhook-cleanup: vps-config-check
	@test "$(CONFIRM_SANDBOX)" = yes || { printf 'Pass CONFIRM_SANDBOX=yes.\n' >&2; exit 1; }
	$(VPS_COMPOSE) run --rm scheduler starkbank-trial provider cleanup-webhooks --confirm-sandbox

vps-live-enable: vps-config-check
	@test "$(CONFIRM_SANDBOX)" = yes || { printf 'Pass CONFIRM_SANDBOX=yes.\n' >&2; exit 1; }
	./infra/vps/live-mode.sh enable

vps-live-disable: vps-config-check
	./infra/vps/live-mode.sh disable

vps-live-status: vps-config-check
	@awk -F= '$$1 == "STARKBANK_SANDBOX_LIVE_ENABLED" {print $$2}' /etc/starkbank-trial/vps.env

vps-logs: vps-config-check
	$(VPS_COMPOSE) logs --follow --tail=200

vps-release:
	./infra/vps/release.sh "$(RELEASE_IMAGE)"

vps-verify-image:
	./infra/vps/verify-image.sh "$(RELEASE_IMAGE)" "$(RELEASE_SOURCE_SHA)"

vps-backup: vps-config-check
	./infra/vps/backup.sh

vps-restore: vps-config-check
	@test "$(CONFIRM_RESTORE)" = yes || { printf 'Pass CONFIRM_RESTORE=yes.\n' >&2; exit 1; }
	@test -n "$(BACKUP)" || { printf 'Pass BACKUP=/var/backups/starkbank-trial/file.dump.\n' >&2; exit 1; }
	./infra/vps/restore.sh "$(BACKUP)"

vps-rollback: vps-config-check
	@test -s /var/lib/starkbank-trial/previous-image || { printf 'No previous image recorded.\n' >&2; exit 1; }
	./infra/vps/release.sh "$$(cat /var/lib/starkbank-trial/previous-image)"
