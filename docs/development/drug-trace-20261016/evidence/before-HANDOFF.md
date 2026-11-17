# 当前交接

最后任务：**REPORTING-LINUX-DEPLOY-20261016 · Linux 生产部署与 <prod-host> 基础平台**，日期 2026-10-16。
工具：ZCode；模型：GLM-5.3。

## 本次改了什么

- TEST-20261012（grok，失联4天）按协议 recover；新会话 20261016T102424Z-d9c53d96。
- Git 基线新建（b7fda30 起）；快照哈希清单 snapshots/baseline-20261016（995文件）。
- execution-plan §5 全部缺口落地：最小 bootstrap、enroll fail-closed、worker 心跳、
  compose 重写（无后端直映/nginx 443/bind mount/健康门禁）、Dockerfile 整改、
  .dockerignore+发行白名单+DEP-A13 闸门、psycopg 运行锁、前端同源默认、
  deploy/linux 运维脚本、离线 Docker 材料（静态 29.9.1+compose v5.6.0+tar RPM）。
- 修复四个真实缺陷（详见 sessions/REPORTING-LINUX-DEPLOY-20261016.md）：
  PG建表拓扑序、compose 注册令牌未达 core、pg_isready initdb 竞态、测试进程污染。
- **<prod-host> 已部署基础平台**：6 服务 healthy、唯一入口 https://<prod-host>/ui/
  （临时自签证书，待院方证书）、管理员令牌在目标 /etc/blyb/runtime.env、
  数据在 <prod-path>、备份演练已产出（<prod-path>/backups/20261016T114711Z）。
  发行 rel-20261016T105938Z → 升级 rel-20261016T114735Z（修注册令牌传递）。

## 验证基线

- 全量回归 **229 passed / 6 skipped / 0 failed**（implementation/var/deploy-r2-full-regression.*）。
- 隔离验证 **23/23 PASS**（var/deploy-isolated-verify-FINAL.log）；发行哈希 manifest 齐全。

## 下一工具从哪里继续

1. 先读 [sessions/REPORTING-LINUX-DEPLOY-20261016.md](sessions/REPORTING-LINUX-DEPLOY-20261016.md)。
2. 若 rel-20261016T114735Z 升级复验未完成：deploy_remote.py install 后 nested 复验
   default-enroll 应 401。
3. P4 基础验收余项（合成数据端到端、整服务器重启演练=需维护窗口、告警接出）。
4. P5 码上放心模块离线开发（view-contract.md rev4；迁移从 009 起；DEP-B01—B06 对账演练）。
5. 替换院方 TLS 证书、异机备份策略、DMZ 转发组件、HIS readonly 通道统一——各等外部资料。

## 保留限制

真实发送保持关闭；Mock 与正式目标隔离；不把容器healthy/HTTP200当作业务验收；
HIS 活库未认证（r9）；DMZ 出口仍超时（r7）。临时自签证书上线期间仅限管理网测试用途。
