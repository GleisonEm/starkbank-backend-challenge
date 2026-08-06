ARG PYTHON_VERSION=3.13.14
FROM python:${PYTHON_VERSION}-slim-bookworm@sha256:9d7f287598e1a5a978c015ee176d8216435aaf335ed69ac3c38dd1bbb10e8d64 AS runtime

ARG TARGETARCH
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:${PATH}"

RUN pip install --no-cache-dir uv==0.11.6 \
    && SUPERCRONIC_ARCH="${TARGETARCH:-$(dpkg --print-architecture)}" \
    && case "${SUPERCRONIC_ARCH}" in \
        amd64) SUPERCRONIC_SHA256="88c1b66b94c486f972fdd1a4d1f901e3e75ff04f749cddd60c5db573e3a33c6c" ;; \
        arm64) SUPERCRONIC_SHA256="50ae8755e04fa72812d0a1bc47a112a856811cc91cce7b6c875c378a850788bc" ;; \
        *) exit 1 ;; \
    esac \
    && SUPERCRONIC_URL="https://github.com/aptible/supercronic/releases/download/v0.2.48/supercronic-linux-${SUPERCRONIC_ARCH}" \
    && python -c 'import hashlib, pathlib, sys, urllib.request; payload = urllib.request.urlopen(sys.argv[1], timeout=60).read(); assert hashlib.sha256(payload).hexdigest() == sys.argv[2]; pathlib.Path(sys.argv[3]).write_bytes(payload)' \
        "${SUPERCRONIC_URL}" "${SUPERCRONIC_SHA256}" /usr/local/bin/supercronic \
    && chmod 0755 /usr/local/bin/supercronic

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY migrations migrations
COPY src src
COPY infra/supercronic infra/supercronic
RUN uv sync --frozen --no-dev \
    && useradd --create-home --uid 10001 app \
    && mkdir -p /app/logs \
    && chown app:app /app/logs

USER app
EXPOSE 8000
CMD ["gunicorn", "--bind=0.0.0.0:8000", "--workers=2", "--threads=4", "--timeout=30", "starkbank_trial.http:create_app()"]

FROM runtime AS test

USER root
COPY tests tests
COPY typings typings
RUN uv sync --frozen
USER app
CMD ["pytest", "--cov=starkbank_trial", "--cov-report=term-missing"]
