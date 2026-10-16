# syntax=docker/dockerfile:1.6
# iClass Agent（openclaw-fastapi）在线构建版：CI 流水线构建 + 推送 Harbor。
# 基础镜像通过 REGISTRY_PREFIX 指向私有仓库（GitLab CI 注入 <registry>/build/ 前缀）。
# 注意：docling 模型不打进镜像（体积大），运行时以只读卷挂载到 /opt/docling-models。
#
# COPY 路径相对构建上下文（仓库根 BUILD_CONTEXT=.）解析：
#   requirements.runtime.txt / app/ / static/ / templates/ / skills/ 均在仓库根。
ARG REGISTRY_PREFIX=""

# ===== Stage 1: 依赖安装（requirements.runtime.txt 为锁定版本清单） =====
FROM ${REGISTRY_PREFIX}python:3.11-slim AS builder
WORKDIR /workspace
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"
RUN python -m venv /opt/venv
# requirements 放在 app/build/ 下（与 Dockerfile 同目录）；COPY 相对构建上下文（仓库根）解析
COPY app/build/requirements.runtime.txt ./requirements.runtime.txt
RUN pip install --no-cache-dir -r requirements.runtime.txt

# ===== Stage 2: 运行时 =====
FROM ${REGISTRY_PREFIX}python:3.11-slim AS runtime
WORKDIR /app

# docling / onnxruntime 的系统级动态库依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxcb1 \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

ENV TZ=Asia/Shanghai \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OPENCLAW_DATA_DIR=/data \
    OPENCLAW_TIMEZONE=Asia/Shanghai \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

COPY --from=builder /opt/venv /opt/venv
COPY app ./app
COPY static ./static
COPY templates ./templates
COPY skills ./skills

EXPOSE 8000

ENTRYPOINT ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
