# 药品追溯来源接入与部署实测（2026-10-22）

> 用户最新要求：非必需先忽略。下文正式上线清单继续保留；一次试报按[新分类](../drug-trace-trial-prerequisites-20261022/README.md)后移正式CA/持续NTP及书面回执。本轮仅修订文档，尚未修改线上发送门禁。

本轮已完成开发、<prod-host> 升级、专用账号绑定、真实单张采集、映射和冻结。
**当前是「可以取数、校验、建包；未真实上传」**，不要再要求重建已生效的 HIS 用户/视图。
本轮由 Codex / unknown 执行；依据用户“请你继续开发完成”“继续”的授权推进。

## 现场结果

| 项目 | 实际结果 |
|---|---|
| <prod-host> 当前发行 | `/opt/blyb/releases/src-20261022T103000Z`，六容器 healthy；最终复查持续运行约4小时 |
| 来源连接 | worker 实际登录 `<his-host>:1521/his`，用户 `BLYB_DRUGTRACE` |
| 权限 | CREATE SESSION、PHARMACY 两视图 SELECT、不带转授权、无角色 |
| 来源配置 | `PUBLISHED`，连接器 `ENABLED`；仅手工 batch，没有开启连续调度 |
| 来源版本 | 新建 `blyb_drugtrace_views@20261022-r10`；旧 sjzc broad source 的 medical_dict_push 登记不改 |
| 正式采集 | run `1bce9518-ea05-4574-aa71-93f1e3a1da27`，DONE，1张/4码/VALID/0冲突 |
| 字典 | 三类新 revision `r10-normal-20261022-r1`；保留旧批准项，补原始状态→321/A、实际药房→已存医院 ref_ent_id |
| 冻结 | submission `7ed95aaa-ee66-4510-b80d-630f6c9dc381`，PREPARED；package revision `93fc0afb-b068-471a-bd3a-4b03f72799a1` |
| 业务参数干跑 | 五必填字段齐全，4个原码顺序/内容保留，医院标识一致；DRY_RUN，无 client、发送 job、attempt、quota |
| 真实只读平台查询 | 新 worker→严格mTLS→DMZ→官方平台成功；本系统新稳定单号查得 total_num=0 |
| 真实上传 | **0**；上述查无新单号不证明原厂商未以其他单号报过同一追溯码 |
| 目的地/DMZ | 真实目的地配置仍为原 PUBLISHED rev1，对象 DRAFT；DMZ 仍只允许 query.billstatus，库存开关关闭 |
| 源库写入 | **0**，本轮无 HIS DDL/DML；真实码只在远端业务进程/受控应用数据库中处理 |

采集窗口只取了已实际验证唯一一张的1秒窗口，没有跑用户最初的全历史区间。
质量仅有 `QD-LEVEL-UNCONFIRMED` 警告；这不是未报、未拆零或全业务覆盖的证明。

## 已交付的程序改进

1. Oracle 码明细空游标首屏修复已部署；新增主单窗口起点的空字符串/NULL边界修复。
2. r10 为独立受限来源事实版本，要求固定对象、专用 DSN 环境引用、batch 模式；驱动逐连接核对实际 reader。
   r1/r2/r7 不改标认证成功；未知分支仍 WAITING，退药、住院、拆零、晚到变更未整体验收。
3. 新增来源配置发布/禁用、目的地启停 API。经过角色、租户、插件、配置版本及证据门禁；
   配置新建 revision，旧版退役保留；发布与审计同事务，密码/DSN明文不能进入来源 JSON。
4. 真实目的地启用要求完整发送证据；当前没有补造发送证据或配额。
5. 修复恢复脚本的路径通配符展开错误：实际目录存在时原检查可能“too many arguments”后误通过。
   改用 case 路径前缀匹配；相等、祖先、子孙反例均拒绝。

新增接口（前缀 `/api/v1`）：

| 方法/路径 | 权限和行为 |
|---|---|
| POST `/drug-trace/sources/{id}/config-revisions` | editor可存草稿/校验；publish、enable须admin；Oracle启用查collect证据 |
| POST `/drug-trace/sources/{id}/disable` | admin；页边界会停止在途采集 |
| POST `/drug-trace/destinations/{id}/activation` | admin；`enabled=true`需有效配置和真实send证据；false可停用 |

配置示例仅含 SecretRef 环境变量名，**不填密码或完整 DSN**：

```json
{"content":{"mode":"oracle","source_profile":"blyb_drugtrace_views@20261022-r10","dsn_env":"BLYB_DRUGTRACE_ORACLE_DSN","collection_mode":"batch","objects":{"bill_view":"PHARMACY.V_DRUG_TRACE_BILL","code_view":"PHARMACY.V_DRUG_TRACE_CODE"}},"publish":true,"enable":true}
```

## 测试与证据

命令在 `implementation/` 执行，Git Bash 使用已安装的 `D:\Program Files\Git`，不装新依赖。

| 检查 | 结果/证据 |
|---|---|
| 来源/就绪及Oracle边界两文件 | 41 passed，`evidence/source-delivery-targeted-r2.xml` |
| 药品相关六文件回归 | 132 passed，`evidence/source-delivery-regression.xml` |
| 全量首轮 `python -m pytest tests -q -ra` | 424 passed / **8 failed** / 7 skipped；保留 full.xml/log，不能写成单轮全绿 |
| 运维文件首轮及新增路径反例 | 6 passed /10 failed；Windows默认GBK解码UTF8输出导致子进程结果为空，失败保留 |
| 同一运维文件设置进程 `PYTHONUTF8=1` 后复验 | **16 passed /0failed**，含新增3个路径反例；断言未删改 |
| 同一安装测试文件设置UTF8后复验 | **9 passed /0failed**；全量首轮剩余1个编码/grep失败已消除 |
| 容器镜像 | 模块导入成功、Oracle Thick成功；依赖freeze与现网底包完全相同 |
| 实际Oracle/编排/干跑 | 见 `verify_worker-100520.json`、`setup-100554.json`、`collect-100656.json`、`prepare-100737.json`、`prepare_worker-100757.json`、`dryrun_worker-135445.json` |

全量首轮8个失败均在上述两文件，UTF8后25项复验全部通过；未重复声称另一轮完整全量绿灯。
窗口起点、跨租户、越权、凭据拒绝、幂等/换代、来源禁用、真实目的地缺证据拒绝等已实际覆盖。
[汇总](evidence/validation-summary.json)、[相对现网旧发行的完整增量](release-delta.patch)（含前轮首屏修复；change-review.patch仅为本轮本地增量）、[发行清单](release-manifest.json)。

## 备份、回退与凭据

- 升级前一致性备份 `<prod-path>/backups/20261022T095541Z`：PG、交换库、对象、配置；哈希通过。
- 新恢复脚本已现场 `--validate-only` 通过；**不是实际恢复数据库**。显式使用生产根以外的
  `--data-root <prod-path>-restore-check-20261022`，见 `restore-check-100140.json`。
  旧默认演练目录位于生产数据根下面，新保护会正确拒绝；运维必须显式给独立根。
- 旧发行 `fb-20261021T164800Z` 保留；本次无数据库迁移/依赖升级。回退前先用新API禁用来源，
  停止药品作业后按现有 rollback.sh 切旧版；保留本次采集及冻结记录，不能清库或重编平台单号。
- 旧候选 `src-20261022T101000Z` 仅曾传入staging，**未安装**；恢复保护修复后改建新revision103000Z。
- reader 本机仍由 `.ai/private/drug-trace-view-verify-20261022/his-reader.credential.clixml` DPAPI保存，
  接口凭证沿用[既有受控登记](../platform-credentials-20261020/README.md)，无需再索取。
- 目标仅worker注入DSN/AppSecret；`/etc/blyb/runtime.env`为root:root/600，core两项均无。
  临时reader JSON与引导token已清理；受限回退配置 `/root/srcdeploy-runtime-before-bind.env` 保留。
  单据真实业务键仅在受控采集进程/<prod-host> root文件 `/root/srcdeploy-candidate.json` 与应用数据库中，不进共享报告。

## 还缺什么，下一AI如何接续

| 事项 | 已有事实 | 还需落实 |
|---|---|---|
| 正式证书 EXT-04 | DMZ严格mTLS可用，但CA明示TEST-ONLY；<prod-host>管理证书无SAN | 院方CA服务端链/SAN/serverAuth、worker clientAuth及私钥受控轮换；按既有OPERATIONS流程验证 |
| NTP EXT-05 | DMZ当前与官方HTTPS Date相差约2秒 | chrony仍Not synchronised、既有203.107.6.88 Reach0；院内可达时间源/UDP123路由并实证同步 |
| 平台权限/口径 EXT-06/08 | AppKey/Secret/ref_ent_id已有；查询成功；服务期此前实测至2028-12-31；当前沿用批准321 | 确认该AppKey医院授权含uploadretail；321/323两官方资料差异由平台确认，变更另建字典revision |
| 未报首单 EXT-10/12 | 系统中有一张已冻结候选，未发送 | 药学/原厂商核对是否已报、是否适合作为测试首单；明确药房/窗口，max_bills=1。不能以新DTRT单号查无记录替代旧厂商码级核对 |
| DMZ方法 | query正常 | 前述条件满足后，仅增加 `alibaba.alihealth.drugtrace.top.yljg.uploadretail`，保留query及库存关闭，不必重开网络三端口 |

接续顺序：

1. 读取本文件和当前STATE，不重跑旧r7/r8/r10 DDL、不重索凭据、不重新部署旧包。
2. 收齐有效正式链、可持续校时、平台权限口径、**具体未报首单**事实；支持其他正常单作为首单，
   不强迫使用本轮随机候选。此次collect证据有效1天，过期应重新核验后经API续登，不能手改数据库。
3. 配置/证书需要换代时走已有配置发布API；旧冻结包不改，按原prepare接口生成新代并明确退役未发旧代。
4. 新API正常启用目的地前，按实际材料登记send证据，`destination_id`和`config_release_id`精确绑定；
   scope为已确认单的半开时间窗、实际药房列表、`max_bills=1`。本轮未提供此send证据。
5. 通过新activation接口启用目的地，受控轮换DMZ方法白名单并验证mTLS/平台443；不开放uploadinoutbill。
6. 只发已确认的一张：原send接口显式confirm+reason；HTTP受理后查平台终态、逐码核对。
   UNKNOWN/SENDING先查，不盲重发；失败尝试也可能占唯一配额，不通过重建证据偷偷重置额度。
7. 留真实终态/逐码及来源核对证据。住院/退药/拆零和连续晚到变更单独开发验收，本期不自动扩大。

执行工具 `deploy_source.py` 已留作复盘；不要不看状态重复执行backup/upgrade/bind或生产采集。
`build_patch_release.py` 的输出为不可变发行，重复构建需新release_id。
