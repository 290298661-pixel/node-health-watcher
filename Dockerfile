# Node Health Watcher — K8s 节点健康巡检系统
# 部署为 Kubernetes Deployment（长期运行的 scheduler）
# 镜像构建: docker build -t ghcr.io/290298661-pixel/node-health-watcher:latest .

FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/290298661-pixel/node-health-watcher"
LABEL org.opencontainers.image.description="K8s node health inspection & IM alerting toolkit"

# 安装 SSH 客户端和构建依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-client \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 复制整个项目
COPY pyproject.toml .
COPY node_health_watcher/ ./node_health_watcher/
COPY config/ ./config/

# 安装 Python 依赖
RUN pip install --no-cache-dir . && \
    apt-get purge -y gcc && apt-get autoremove -y

# 非 root 运行
RUN useradd -m -s /bin/bash nhw && chown -R nhw:nhw /app
USER nhw

# 创建 SSH 目录（用于挂载密钥）
RUN mkdir -p /home/nhw/.ssh && chmod 700 /home/nhw/.ssh

ENTRYPOINT ["python", "-m", "node_health_watcher"]
CMD ["--help"]
