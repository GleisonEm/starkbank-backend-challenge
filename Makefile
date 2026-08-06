SHELL := /bin/sh

LOCAL_COMPOSE = docker compose --env-file .env -f compose.yaml -p starkbank-trial-local
TEST_COMPOSE = docker compose -f compose.test.yaml -p starkbank-trial-test
VPS_COMPOSE = docker compose --env-file /etc/starkbank-trial/vps.env -f compose.vps.yaml -p starkbank-trial-vps

.DEFAULT_GOAL := help

.PHONY: help env-init keygen validate-env build up down restart health ps logs logs-webhook \
	test check tunnel tunnel-url tunnel-down sandbox-check webhook-setup webhook-list \
	smoke-invoice trial-start trial-status reset vps-config-check vps-secrets vps-pull \
	vps-deploy vps-up vps-down vps-health vps-status vps-trial-status vps-logs \
	vps-release vps-verify-image vps-backup vps-restore vps-rollback

help:
	@printf '%s\n' \
		'Local: env-init keygen validate-env build up down restart health ps logs logs-webhook' \
		'Checks: test check' \
		'Tunnel: tunnel tunnel-url tunnel-down' \
		'Sandbox: sandbox-check webhook-setup webhook-list smoke-invoice trial-start trial-status' \
		'VPS: vps-config-check vps-secrets vps-pull vps-deploy vps-up vps-down vps-health' \
		'     vps-status vps-trial-status vps-logs vps-release vps-verify-image' \
		'     vps-backup vps-restore vps-rollback'

env-init:
	@test ! -e .env || { printf '.env already exists; refusing to overwrite it.\n' >&2; exit 1; }
	cp .env.example .env
	@printf 'Created .env. Set credentials before make up.\n'

keygen:
	./infra/local/keygen.sh

validate-env:
	./infra/local/validate-env.sh app
	@$(LOCAL_COMPOSE) config --quiet

build:
	docker build --target runtime --tag starkbank-trial:local .

up: validate-env
	$(LOCAL_COMPOSE) up --detach --wait --wait-timeout 180 postgres migrate api worker scheduler

down:
	$(LOCAL_COMPOSE) --profile tunnel down --remove-orphans

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

smoke-invoice: sandbox-check
	@test "$(CONFIRM_SANDBOX)" = yes || { printf 'Pass CONFIRM_SANDBOX=yes.\n' >&2; exit 1; }
	$(LOCAL_COMPOSE) run --rm scheduler starkbank-trial provider smoke-invoice --confirm-sandbox

trial-start: sandbox-check
	@test "$(CONFIRM_SANDBOX)" = yes || { printf 'Pass CONFIRM_SANDBOX=yes.\n' >&2; exit 1; }
	$(LOCAL_COMPOSE) run --rm scheduler starkbank-trial trial start

trial-status: validate-env
	$(LOCAL_COMPOSE) run --rm scheduler starkbank-trial trial status

reset:
	@test "$(CONFIRM_RESET)" = yes || { printf 'Pass CONFIRM_RESET=yes; this deletes local volumes.\n' >&2; exit 1; }
	$(LOCAL_COMPOSE) --profile tunnel down --volumes --remove-orphans

vps-config-check:
	./infra/vps/config-check.sh

vps-secrets: vps-config-check
	./infra/vps/validate-secrets.sh

vps-pull: vps-secrets
	$(VPS_COMPOSE) pull

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
