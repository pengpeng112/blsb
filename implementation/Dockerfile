# BLYB 后端镜像（W14修订：DEP-A12/A13、D1/D4 整改）。
# - 删除无代码读取的 BLYB_FACTORY 环境变量（D4）；工厂由 uvicorn --factory 显式声明
#   （本机 Uvicorn 0.44.0 已验证零参工厂可自动探测，显式参数仅消除告警并明确意图）
# - 固定运行账户 UID/GID=10001：core/storage/worker/exchange 共享同一镜像与对象卷
# - PYTHONPATH 显式包含 packages/plugins（容器内不依赖入口模块先 import _bootstrap）
# - 构建上下文由 implementation/.dockerignore 裁剪（DEP-A13：var/SQLite/凭据绝不入镜像）
# 构建：docker build -t blyb/backend:<release-id> --build-arg PYTHON_IMAGE=python:3.14-slim -f Dockerfile .
# （正式发行由 deploy/release/build_release.py 执行并记录 digest；禁止绕过 .dockerignore 构建）
ARG PYTHON_IMAGE=python:3.14-slim
FROM ${PYTHON_IMAGE}

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app:/app/packages:/app/plugins \
    BLYB_STORAGE_EMBEDDED_WORKER=0

WORKDIR /app

# 依赖层（最小生产锁；离线构建可用 --no-index --find-links 替换）
COPY requirements-linux.txt /app/requirements-linux.txt
RUN pip install --no-cache-dir -r /app/requirements-linux.txt

# Oracle Instant Client 19c（DPL-P01）：HIS 为旧 Oracle 11g，oracledb 必须
# Thick 模式；19c 是官方互操作矩阵支持 11.2.0.4 服务端的最新客户端线。
# zip 来自 Oracle 公开下载（1932000/19.32.0.0.0dbru，构建前哈希核对）；
# 解包到稳定路径 /opt/oracle/instantclient（软链），libaio 由发行版包提供。
# （bookworm=libaio1 / trixie=libaio1t64，两代基镜像都兼容）
ARG IC_ZIP=deploy/oracle/instantclient-basiclite-linuxx64-19.32.0.0.0dbru.zip
COPY ${IC_ZIP} /tmp/instantclient.zip
RUN set -eu; \
    apt-get update; \
    { apt-get install -y --no-install-recommends unzip libaio1t64 \
      || apt-get install -y --no-install-recommends unzip libaio1; }; \
    mkdir -p /opt/oracle; \
    unzip -q /tmp/instantclient.zip -d /opt/oracle; \
    rm -f /tmp/instantclient.zip; \
    ln -sfn /opt/oracle/instantclient_19_* /opt/oracle/instantclient; \
    ln -sfn libclntsh.so.19.1 /opt/oracle/instantclient/libclntsh.so; \
    # trixie 的 libaio1t64 实际文件名是 libaio.so.1t64；Instant Client 按
    # soname libaio.so.1 dlopen——在 IC 目录（LD_LIBRARY_PATH 首位）放等价
    # 软链；bookworm 的 libaio1 本名即 libaio.so.1 时跳过。
    AIO="$(ldconfig -p | grep -o '/[^ ]*libaio\.so\.1t64' | head -1)"; \
    if [ -n "$AIO" ] && [ ! -e /lib/x86_64-linux-gnu/libaio.so.1 ]; then \
      ln -sfn "$AIO" /opt/oracle/instantclient/libaio.so.1; \
    fi; \
    rm -rf /var/lib/apt/lists/*
ENV LD_LIBRARY_PATH=/opt/oracle/instantclient \
    BLYB_DRUGTRACE_ORACLE_CLIENT_LIB_DIR=/opt/oracle/instantclient

# 应用层：核心目录布局（_bootstrap 仍保留，兼容宿主/测试入口）
COPY _bootstrap.py /app/_bootstrap.py
COPY apps/ /app/apps/
COPY packages/ /app/packages/
COPY plugins/ /app/plugins/
COPY profiles/ /app/profiles/
COPY migrations/ /app/migrations/

# 前端构建产物（构建机 npm run build 后随镜像分发；缺失则 /ui 不挂载，不影响API）
COPY apps/admin_web/dist/ /app/apps/admin_web/dist/

# 专用低权限运行账户；/data 预建并归 blyb，使全新命名卷继承该属主
RUN groupadd -g 10001 blyb && useradd -u 10001 -g 10001 -r -m -d /app -s /usr/sbin/nologin blyb \
    && mkdir -p /data/volume1 /data/exchange \
    && chown -R blyb:blyb /app /data
USER blyb

# 数据/交换/对象卷挂载点（compose 挂载同名卷；sentinel 由卷注册流程写入）
VOLUME ["/data/volume1", "/data/exchange"]

ENV BLYB_EXCHANGE_DB=/data/exchange/exchange.sqlite3

# 默认入口=core API（--factory 显式声明；迁移/初始化由 compose 的 migrate/bootstrap
# 一次性服务先执行，编号迁移，不依赖 create_all）
CMD ["python", "-m", "uvicorn", "apps.core_api.app:get_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
