# 当前项目状态

核对日期：2026-10-16。基线：**v0.4**。业务源码：`implementation/`。
这是共享的当前摘要。详细证据仍在实施文档和 `implementation/var/` 的 log/XML。

## 已执行到哪里

| 工作 | 当前证据状态 |
|---|---|
| 第1—3轮与真实栈功能测试 | 既有记录。功能测试后回归 191/6，43 项复核通过 |
| 第4轮本地收尾（2026-10-02） | 216 passed / 6 skipped（var/round4-final-regression2.log）；T12 拖拽未做 |
| 2026-10-05 部署修订 | 内网卷隔离+令牌强制+端口退出（T14 两项 2 passed） |
| 2026-10-16 Linux 生产部署修订（REPORTING-LINUX-DEPLOY-20261016） | Git基线b7fda30起；§5部署修订落地；既有隔离23/23、全量229 passed / 6 skipped / 0 failed；<prod-host>最终发行 **20261016T114735Z**，部署任务已handed_off |
| 2026-10-16 部署复核与药品开发规划（REPORTING-DRUGTRACE-PLAN-20261016） | 22:29现场复核：发行哈希通过、6服务healthy、宿主22/443、迁移001—008；业务connector/destination/job等均0。发现TLS名称不符、备份/恢复静态缺口；新开发计划与用户SQL审查已交付，业务代码未改。见[本轮交接](sessions/REPORTING-DRUGTRACE-PLAN-20261016.md) |
| 2026-10-16 药品追溯离线开发（DTR-IMPLEMENT-20261016，ZCode/GLM-5.3） | W01—W09 完成：P0运维修订（SAN证书/隔离恢复演练/静止点备份/无-k验收）、drug_trace 领域插件（迁移009、整单抽取事务checkpoint、质量、冻结bill_code、证据门禁 fail-closed）、TOP适配器（签名EXT-07硬阻断）、DMZ专用转发器、/api/v1/drug-trace+Vue药品页、浏览器合成闭环+截图；全量回归 **277 passed/6 skipped/0 failed**（XML var/drugtrace-regression-20261016.xml）。未连真实HIS/DMZ/平台，EXT-01—12仍OPEN。见[本轮交接](sessions/DTR-IMPLEMENT-20261016.md) |

**最近本地自动回归是 2026-10-16 的 277/6/0（药品追溯轮，XML 在 implementation/var/drugtrace-regression-20261016.xml）**（本次解析既有XML核对235 cases、6 skipped、0 failures/errors；未重跑）。这不是HIS/码上放心真实业务终验。
期间修复四个真实缺陷：PG迁移建表拓扑序、compose 未把注册令牌传给 core（默认密钥漏洞）、
postgres initdb 健康竞态、bootstrap 测试进程污染（详见 docs/…/测试执行记录.md）。

## 可以部署的形态

- Windows 单机（SQLite）：`implementation/deploy/intranet/start.ps1`（旧路径，仍有效）。
- **Linux Compose 生产形态（新增，已隔离验证）**：发行包构建
  `implementation/deploy/release/build_release.py`；目标机安装
  `implementation/deploy/linux/install.sh`（含离线 Docker 材料 docker-offline/）；
  唯一入口 nginx TLS 443，后端 8000/8001/8002 不发布宿主机端口。
- <prod-host>基础栈已部署，`/opt/blyb/current`→`/opt/blyb/releases/20261016T114735Z`；不是仍在安装。新模块默认禁用开发方案见[药品追溯计划](../development/drug-trace-20261016/README.md)。

## 尚未满足的条件

- 真实 HIS/PIS/扫描仪/医保（B01—B09）仍 OPEN；药品追溯离线代码已完成（生产默认停用，证据门禁空表拒绝）；DMZ 转发器代码/材料已交付未部署（EXT-03/04/05/11）。
- <prod-host>临时TLS证书缺IP SAN，信任后仍名称不匹配；需可信SAN证书。两机NTP未同步。
- Docker已安装；备份文件已生成但未独立恢复。restore/backup脚本静态缺口、异机备份、重启/告警/合成业务闭环仍待修正验收。
- 22:34复测DMZ可登录、无443/8443转发监听，强制IPv4平台HTTPS443仍超时；HTTP80通。HIS1521 TCP通，不代表数据库认证，readonly登记/服务名仍未统一。
- 用户MED_TRACE_RECORD历史SQL含MAX丢多码、单据粒度/时间/变更发现等问题，已纳入[SQL追加任务](../development/drug-trace-20261016/SQL_REVIEW.md)，未执行活库SQL。
- T12 受限画布拖拽未做。

## 维护方法

新的已验证结果写到本文件，并链到 session 和 log/XML。Git 已建立（main 分支）；
凭据/运行库/患者文件不入库（.gitignore/.gitattributes 已约束）。
