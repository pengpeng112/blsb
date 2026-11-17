# 当前项目状态

核对日期：2026-10-17。基线：**v0.4**。业务源码：`implementation/`。
这是共享的当前摘要。详细证据仍在实施文档和 `implementation/var/` 的 log/XML。

## 已执行到哪里

| 工作 | 当前证据状态 |
|---|---|
| 第1—3轮与真实栈功能测试 | 既有记录。功能测试后回归 191/6，43 项复核通过 |
| 第4轮本地收尾（2026-10-02） | 216 passed / 6 skipped（var/round4-final-regression2.log）；T12 拖拽未做 |
| 2026-10-05 部署修订 | 内网卷隔离+令牌强制+端口退出（T14 两项 2 passed） |
| 2026-10-16 Linux 生产部署修订（REPORTING-LINUX-DEPLOY-20261016） | Git基线b7fda30起；§5部署修订落地；既有隔离23/23、全量229 passed / 6 skipped / 0 failed；<prod-host>最终发行 **20261016T114735Z**，部署任务已handed_off |
| 2026-10-16 部署复核与药品开发规划（REPORTING-DRUGTRACE-PLAN-20261016） | 22:29现场复核：发行哈希通过、6服务healthy、宿主22/443、迁移001—008；业务connector/destination/job等均0。发现TLS名称不符、备份/恢复静态缺口；新开发计划与用户SQL审查已交付，业务代码未改。见[本轮交接](sessions/REPORTING-DRUGTRACE-PLAN-20261016.md) |
| 2026-10-16 药品追溯离线开发（DTR-IMPLEMENT-20261016，ZCode/GLM-5.3） | 已交领域/009迁移/合成采集/Mock适配器/DMZ代码/API/UI及历史回归 **277 passed/6 skipped/0 failed**（283 cases）。旧“W01—W09全部完成、签名硬阻断、三方对账”结论已被10-17代码反例限制，须先整改；原交接保留 |
| 2026-10-17 二次代码复核（DTR-REVIEW2-20261017） | Git0ca47c6；原4组局部测试重跑 **47 passed/0 failed/0 skipped**，另16个离线反例均复现问题：TOP字段/响应、部分误确认、签名豁免、配置未固定、Oracle类型/游标、UNKNOWN重发/暂停失效、查询不再执行、无远端的对账。无远程登录/业务源码改动，正式上报不可启用。见[复核与完善计划](../development/drug-trace-20261016/review-2-20261017/README.md) |
| 2026-10-17 药品追溯第二轮整改（DTR-FIX2-20261017，ZCode/GLM-5.3） | FIX2-00—08全部可离线项完成：官方协议两profile/证据精确绑定+EXT-05/10/12门禁/Mock类型隔离/Oracle规范化+持久重取/停发CAS+查询代次+回执历史/平台快照对账/DMZ白名单与材料口径/备份全写入口+恢复路径保护/父子码+tenant隔离/UI修复；迁移010（真实隔离PG验证）。新增反例转换测试26项+转发器2项；全量回归见fix2-20261017/evidence/full-regression.log。EXT-01—12未齐，正式上报仍阻断。见[整改交付](../development/drug-trace-20261016/fix2-20261017/README.md) |

**最近全量自动回归是2026-10-17 FIX2 整改后本轮**（见 fix2-20261017/evidence/full-regression.log 与 var/fix2-full-regression.xml；含新增 26+2 项反例转换/转发器测试）。10-16的277/6/0与10-17复核的47/0/0为历史基线。所有本地通过均不等于真实上报通过。
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

- 真实HIS/PIS/扫描仪/医保（B01—B09）仍OPEN；药品已有离线实现但R2-01—16代码问题待修，不得仅补EXT证据就开启上报。DMZ转发代码未部署、真实client未配mTLS证书、3.6运行材料不兼容实际代码。
- <prod-host>临时TLS证书缺IP SAN，信任后仍名称不匹配；需可信SAN证书。两机NTP未同步。
- Docker已安装；备份文件已生成但未完整独立恢复。新版backup/restore已有fake docker测试，仍需修冻结写入口/路径/项目保护，补真实PG全链路恢复与异机备份等现场验收。
- 22:34复测DMZ可登录、无443/8443转发监听，强制IPv4平台HTTPS443仍超时；HTTP80通。HIS1521 TCP通，不代表数据库认证，readonly登记/服务名仍未统一。
- 用户MED_TRACE_RECORD历史SQL含MAX丢多码、单据粒度/时间/变更发现等问题，已纳入[SQL追加任务](../development/drug-trace-20261016/SQL_REVIEW.md)，未执行活库SQL。
- T12 受限画布拖拽未做。

## 维护方法

新的已验证结果写到本文件，并链到 session 和 log/XML。Git 已建立（main 分支）；
凭据/运行库/患者文件不入库（.gitignore/.gitattributes 已约束）。
