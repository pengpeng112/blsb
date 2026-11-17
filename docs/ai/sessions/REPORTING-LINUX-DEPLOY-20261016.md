# REPORTING-LINUX-DEPLOY-20261016 交接

任务ID/起止时间：REPORTING-LINUX-DEPLOY-20261016，2026-10-16 18:24—（UTC+8）。
用户目标：严格按 docs/deployment/reporting-20261015/NEXT_AI_PROMPT.md 执行：§5 部署修订、离线发行、隔离验证、<prod-host> 基础平台部署。
工具/真实模型：ZCode / GLM-5.3。
协作session_id：20261016T102424Z-d9c53d96（范围 implementation、docs/ai、docs/deployment/reporting-20261015）。
前置协调：TEST-20261012（grok）失联4天无checkpoint，按协议 recover→handed_off（docs/ai/sessions/TEST-20261012.md）。

## 源码基线

- Git 仓库新建（此前无 Git）：基线提交 b7fda30（451文件，提交前扫描四组密码零命中）；
  快照哈希清单 snapshots/baseline-20261016/SHA256SUMS（995文件）。
- .gitattributes 固定部署脚本 LF；.gitignore 排除原始资料/归档/离线二进制/发行产物。
- 关键提交：1321bce（§5修订）→ a32cfcb（P2全绿）→ 后续发行与文档提交。

## 修改范围/文件（摘要）

- 新增：apps/bootstrap.py、implementation/.dockerignore、requirements-linux.txt、
  deploy/compose/nginx/nginx.conf、deploy/compose/test-override.yml、
  deploy/linux/{lib,install,status,backup,restore,upgrade,rollback}.sh+README、
  deploy/linux/docker-offline/{install-docker-offline.sh,README,docker-29.9.1.tgz,
  docker-compose-linux-x86_64,tar-*.rpm,SHA256SUMS}、deploy/release/{build_release.py,
  whitelist.txt,verify_isolated.sh}、tests/integration/test_deploy_gaps.py、
  docs/deployment/reporting-20261015/deploy_remote.py。
- 修改：Dockerfile（删BLYB_FACTORY/--factory/PYTHONPATH/UID10001/CMD uvicorn）、
  deploy/compose/docker-compose.yml（重写：无后端直映/nginx/bind mount/bootstrap服务/
  健康门禁/env anchor含注册令牌/pg_isready TCP）、apps/core_api/app.py（enroll fail-closed）、
  apps/worker/engine.py（心跳）、apps/admin_web/src/{api.ts,views/Login.vue}（同源默认/不预填dev令牌）、
  packages/core_db/migrate.py + plugins/domains/pathology/models.py（建表拓扑序）、
  requirements.txt（+psycopg）、四类实施文档+STATE/BACKLOG/HANDOFF。

## 实现后的行为

- 生产形态唯一入口 nginx TLS 443；core/storage/exchange 不发布宿主机端口；单 worker。
- enroll：禁用dev令牌且未配置注册令牌→401（修复了"令牌只在bootstrap、core回落dev默认"漏洞）。
- bootstrap：最小幂等（租户/卷/引导管理员），fail-closed，不建演示配置。
- 迁移建表拓扑序（修复 PG 上字母序 FK 失败）；pg_isready 强制 TCP（修复 initdb 竞态）。

## 数据库编号迁移/历史数据处理

无新编号迁移（008 为既有最新）；001/002 建表顺序调整不影响 schema 结果，SQLite/PG 行为一致。

## 真实验证：命令、退出码、结果、证据路径

| 项 | 结果 | 证据 |
|---|---|---|
| 单测（部署缺口） | 12 passed | tests/integration/test_deploy_gaps.py |
| 局部回归 T1—T14 | 20 passed | tests/integration/test_local_gaps.py |
| 发行构建 | exit 0，118文件哈希 | implementation/deploy/release/dist/rel-20261016T114735Z |
| 隔离验证（本机Docker，真PG） | **23 PASS / 0 FAIL** | implementation/var/deploy-isolated-verify-FINAL.log |
| 全量回归 | **229 passed / 6 skipped / 0 failed**（428s） | implementation/var/deploy-r2-full-regression.{log,xml} |
| 前端同源构建 | exit 0 | build_release 内置（VITE_CORE_DEFAULT=''、VITE_STORAGE_DEFAULT='/storage'） |
| 目标传输 | 366MB 经跳板中继（SFTP→scp），目标 SHA256 全过 | deploy_remote.py transfer 输出 |
| 目标安装 | install exit 0；6服务 healthy；唯一监听443 | deploy_remote.py install 输出 |
| 目标A06/A07 | bootstrap令牌200、dev令牌401、/ui/ 200、/storage/health 200 | nested 命令输出 |
| 备份演练 | backup.sh 完成（pg_dump+交换库快照+配置600） | <prod-path>/backups/20261016T114711Z |
| 二次发行 | rel-20261016T114735Z（修 compose env-anchor 后重建），重传升级中 | deploy/release/dist/ |

## 发现的问题（已修复也保留）

1. PG 建表字母序 FK 失败（SQLite 不校验故从未暴露）→ 拓扑序。
2. compose 注册令牌只配 bootstrap、core 回落 dev 默认密钥（隔离 A06 抓出 404≠401）→ env anchor；**首次部署的发行包建于修复前，目标上 default-enroll 曾 404，已重建 rel-20261016T114735Z 重传升级**。
3. postgres initdb 期 pg_isready（unix socket）误报 healthy → 强制 -h 127.0.0.1。
4. bootstrap 测试进程级污染 BLYB_DISABLE_DEV_TOKENS（一次性42 failed）→ monkeypatch。
5. 目标机无 tar（openEuler 最小安装）→ 发行版 tar RPM 入 docker-offline。
6. 跳板禁 direct-tcpip → 中继方案（SFTP→跳板→scp），全程主机密钥固定值校验（keyscan 多类型逐行比对）。
7. 白名单首版格式歧义把 var/agent-config.json 扫进闸门命中 → 重设计 +/- 格式（闸门本身工作正常）。

## 未执行验证及原因

- DEP-B01—B06（码上放心离线功能/对账演练）：P5 模块未开发（下一个任务）。
- 目标机外部客户端浏览器实走（管理网另一台电脑）：本轮无法从本机直达 <prod-host>:443（仅跳板可达），
  已在跳板/目标本机 curl 验证；待现场管理网客户端复核。
- 恢复演练（restore.sh）：备份已产出，空目标恢复未在目标机演练（隔离环境已验证同脚本逻辑的备份件）。
- Docker Desktop 升级目标内核兼容性长跑/院方异机备份策略：外部项。

## 已更新的STATE/BACKLOG/业务详细文档

STATE.md（2026-10-16 全量重写）、BACKLOG.md（+本任务行）、HANDOFF.md（本次）、
implementation/docs/实施状态矩阵.md（+Linux部署修订节）、接口与设计差异.md（+部署修订差异）、
测试执行记录.md（+2026-10-16验证）、runbooks/离线发行材料.md（+Linux容器发行）、runbooks/备份与恢复.md（+容器形态）。

## 尚未完成的任务/失败

- P4 完整验收余项（合成数据端到端流程、整服务器重启演练=需维护窗口、告警接出）。
- P5 码上放心模块（含 DEP-B01—B06 对账演练）、P6 真实接入。
- DMZ 转发组件、HIS 只读通道统一（服务名三口径）、院方TLS证书替换、异机备份策略。
- 管理网另一台电脑的浏览器实走复核（本轮从跳板/目标本机已验证 200）。

## 已完成的目标侧最终复验（升级 rel-20261016T114735Z 后）

release=20261016T114735Z；bootstrap-token 200；dev-admin-token 401；
default-enroll 401；/ui/ 200；/storage/health 200；宿主机 443/8000/8001/8002 监听数=1（仅nginx）；
6 服务全部 healthy；跳板侧外部访问 /health 与 /ui/ 均 200。

## 下一步精确函数/文件/命令

1. P5 开发入口：view-contract.md revision 4 + execution-plan §7 P5；独立 profile/Schema/迁移编号从 009 起。
2. P4 余项按 execution-plan §7 P4 清单。
3. 院方证书到位后：替换 /etc/blyb/certs/{tls.crt,tls.key} → `docker compose -p blyb ... restart nginx`。

结果：handed_off。
