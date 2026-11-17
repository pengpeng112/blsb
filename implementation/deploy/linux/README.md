# Linux 生产部署（<prod-host> 基础平台）

对应 `docs/deployment/reporting-20261015/execution-plan.md` §5/§7 P3/§9。
前置：发行包（`deploy/release/build_release.py` 产物，含 images/*.tar、implementation/、
manifest.json、SHA256SUMS）；目标机已装 Docker + Compose v2；`/home` 大分区；
TLS 证书已放 `/etc/blyb/certs/tls.crt|tls.key`（院方证书；不关闭校验）。

## 目录约定（§6）

```text
/opt/blyb/releases/<release-id>/    不可变发行目录（install.sh 归位）
/opt/blyb/current                   → releases/<release-id> 符号链接
/etc/blyb/runtime.env               密钥/镜像tag（600；install.sh 生成，不入发行包）
<prod-path>/postgres|exchange|objects|backups/
/var/log/blyb/
```

## 命令

| 动作 | 命令 |
|---|---|
| 安装/重装（幂等，不清库不轮换密钥） | `./install.sh <release-dir-or-tarball>` |
| 状态/健康/端口/水位 | `./status.sh` |
| 备份（PG+交换库+配置；对象卷需静止窗口） | `./backup.sh [--with-objects]` |
| 空目标恢复演练 | `./restore.sh <backup-dir>` |
| 升级（先自动备份再切发行） | `./upgrade.sh <new-release-dir-or-tarball>` |
| 回滚（应用层；数据库不自动降级） | `./rollback.sh <old-release-id>` |

安装/升级/回滚统一走显式健康门禁（db→migrate→bootstrap→全栈，超时即停），
不依赖 compose 对 `run`/`service_healthy` 的默认等待语义（计划 D5）。

## 边界

- 不运行 `down -v`、`docker system prune`、通配删除；不结束未知进程。
- 迁移失败禁止跳到后续步骤；不删库重来。
- 密钥只在目标机生成/保存；`runtime.env` 不回显、不进日志与发行包。
- exchange 不发布宿主机端口 ≠ 停用；PULL 经 nginx `/exchange/` 批准路径提供。
- 单 worker；`docker compose scale worker` 禁止（副作用幂等未证明）。
