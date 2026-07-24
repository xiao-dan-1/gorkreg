# Grok 纯协议注册 — 精简镜像（无浏览器 / 无 Playwright）
# 依赖：curl_cffi + PyYAML；密钥/代理走 env 与 volume
# 基础镜像若直连 Docker Hub 失败，可先：
#   docker pull docker.m.daocloud.io/library/python:3.11-slim-bookworm
#   docker tag  docker.m.daocloud.io/library/python:3.11-slim-bookworm python:3.11-slim-bookworm
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 不跑 apt（避免 deb.debian.org 超时）；协议路径只靠 pip wheel
COPY requirements.txt /app/requirements.txt
# 协议路径（curl_cffi+PyYAML）；浏览器打码另装 requirements-browser.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY main.py /app/main.py
COPY config.example.yaml /app/config.example.yaml
COPY config.docker.yaml /app/config.docker.yaml
# 默认用 docker 专用样例（host.docker.internal）；可挂载覆盖
COPY config.docker.yaml /app/config.yaml
COPY .env.example /app/.env.example
COPY grokreg /app/grokreg

RUN mkdir -p /app/output /app/cpa_export /data \
    && useradd -m -u 1000 grok \
    && chown -R grok:grok /app /data
USER grok

# 宿主机代理（Windows/Mac Docker Desktop；Linux 用 host-gateway）
ENV HTTPS_PROXY=http://host.docker.internal:10808 \
    HTTP_PROXY=http://host.docker.internal:10808 \
    NO_PROXY=localhost,127.0.0.1

VOLUME ["/app/output", "/app/cpa_export", "/data"]

ENTRYPOINT ["python", "main.py"]
CMD ["--help"]
