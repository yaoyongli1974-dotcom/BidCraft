# syntax=docker/dockerfile:1

# ---- web client build -------------------------------------------------------
FROM node:22-slim AS webbuild
WORKDIR /src
COPY web ./web
RUN cd web && (npm ci || npm install) && npm run build

# ---- application ------------------------------------------------------------
FROM python:3.13-slim

# uv 用于按 uv.lock 复现依赖（见根目录 uv.lock）。
COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BIDCRAFT_HOME=/data \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH"

# LibreOffice (PDF field/TOC refresh) + CJK fonts + optional scanned-PDF OCR.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libreoffice-writer python3-uno fonts-noto-cjk \
        poppler-utils tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

# 非 root 运行用户（固定 uid，便于挂载卷权限规划）。
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin bidcraft \
    && mkdir -p /app /data \
    && chown -R bidcraft:bidcraft /data

WORKDIR /app

# 依赖层：只依赖清单，源码变更可复用缓存。
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra api --no-install-project

# 源码层：复制后再安装项目自身（含 resources 与 Web 构建产物）。
COPY bidcraft ./bidcraft
COPY --from=webbuild /src/bidcraft/web_dist ./bidcraft/web_dist
RUN uv sync --frozen --no-dev --extra api \
    && chown -R bidcraft:bidcraft /app

USER bidcraft

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

ENTRYPOINT ["bidcraft"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
