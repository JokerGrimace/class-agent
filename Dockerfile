ARG REGISTRY_PREFIX=""
FROM ${REGISTRY_PREFIX}python:3.11-slim AS builder

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock ./
COPY app ./app

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --upgrade pip \
    && python -m pip install --prefix=/install .

FROM ${REGISTRY_PREFIX}python:3.11-slim AS runtime

WORKDIR /app

ENV TZ=Asia/Shanghai
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV OPENCLAW_DATA_DIR=/data
ENV OPENCLAW_TIMEZONE=Asia/Shanghai

COPY --from=builder /install /usr/local
COPY app ./app
COPY static ./static
COPY templates ./templates
COPY skills ./skills

EXPOSE 8000

ENTRYPOINT ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
