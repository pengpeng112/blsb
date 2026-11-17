# r10执行后实际核查（2026-10-22 17:28 CST）

**HIS视图修订已生效，一张正常门诊单据4码按现质量规则为VALID；目前尚不能经现网系统上传。**
无需重建账号或再次修改本轮视图。当前阻断位于<prod-host>部署/取数配置和DMZ方法白名单，以及实际首张发送准备。
实际上传0，HIS写0，远端配置修改0。本轮Codex / unknown使用已授权只读核验，没有替用户执行新DDL。

## 实测通过

证据：[reader-r1.json](evidence/reader-r1.json)。新专用账号BLYB_DRUGTRACE实际登录his服务，
PHARMACY两视图均VALID、17/18列投影可用，16项限量SELECT成功。

- 当天20头样本中15张PATIENT_DISPENSE、5张住院UNKNOWN；前15张的完成/作废字段已从空值变为候选规则规定值，
  其余5张仍保持未知，不再误报“所有单据都是UNKNOWN”。
- 正常门诊的100码样本均20位数字串，IS_DELETED原值均0、无空码；不是全院/全期质量统计。
- 选择其中一张真实整单：1头4码，无重复记录或码，主/码revision一致、没有触发行数保护。
- 使用当前本地quality.py中的实际评估函数，在受控远端进程内对这张单据评估：
  **VALID、阻断规则0**，仅QD-LEVEL-UNCONFIRMED警告；没有把实际码/单据键/患者字段带入AI或报告。
  该评估没有运行生产采集、没有建包/冻结/写入应用数据库，也没有证明既往未报或全部行未拆零。
- 两视图实际定义与r10草案的SQL token比对结果见[local-validation.json](evidence/local-validation.json)。

前轮“正常门诊完成字段待落库”的事项本次已经通过用户执行回执和活库事实确认，不再让用户重复建视图。
这不自动补齐应用中尚未登记的collect/send证据、字典、来源配置或发送范围。

## 目前卡在哪里

现场证据：[application-r2.json](evidence/application-r2.json)、[dmz-r1.json](evidence/dmz-r1.json)。

| 位置 | 当前实测状态 | 实际影响/接续动作 |
|---|---|---|
| <prod-host>应用发行 | 仍为fb-20261021T164800Z，6容器healthy | 本地已通过110项回归的Oracle首屏修复尚未构建/部署新发行 |
| Oracle码明细首屏 | 部署worker的NULL安全分支为false；同一真实单据旧谓词0条、NULL安全谓词4条 | 有码也会读成无码，必须部署修复，不能绕过采集器直接手拼上报 |
| 新只读账号接入 | worker BLYB_DRUGTRACE_ORACLE_DSN为空；来源DRAFT、无active config | 只读账号已可用，但生产服务还没有绑定它；按既有配置生命周期发布来源 |
| 医院药房映射 | drugtrace.ref_ent_id仅空草稿 | 为选定药房建立到已登记本院ref_ent_id的映射，无需用户再提供医院标识 |
| 原始状态字典 | 已批准材料只包含PATIENT_DISPENSE→321/A；现视图原始状态键是PRESC_SOURCE=0;FLAG=1 | 按前轮明确差异新建revision保留旧项并补原始状态映射；不得篡改来源原值或已批准字典 |
| 目的地对象 | DRAFT；真实配置虽为PUBLISHED且is_mock=false | 配置发布与对象启用是不同步骤，应正常启用目的地 |
| DMZ <relay-net-host> | allowed_top_methods仅query.billstatus | 上传方法未开放；这是转发器应用配置，不是再申请一个TCP端口 |
| 发送准备 | collect/send证据MISSING；业务单据/采集/submission/配额0 | 依据真实已完成证据正常登记，采集/冻结一张已确认未报的单据、限定max_bills=1后才发 |

现有平台凭据、医院标识与签名资格已有证据，不重复索取AppKey/AppSecret。
系统send状态中的缺项不能一律解释为用户未提供这些资料，必须区分已有实测事实与应用证据尚未登记。

## 网络和时间的准确结论

- <prod-host> worker→DMZ→官方状态查询实测成功1次、total_num=0，是随机探针，未查询本次真实候选业务单。
- DMZ→eco.taobao.com:443 HTTPS HEAD200，时钟17:28:28 CST与同轮官方09:28:29 GMT接近。
- NTP长期同步仍no，证书链仍TEST-ONLY，未在本轮更改或声称正式链已交付；沿既有真实发送证据流程处理。
- 从<jump-host>跳板首次连<prod-host>:22得到Connection refused（application-r1失败保留），紧接r2已SSH0/worker0成功。
  因此不把一次拒绝解释为持续断网，也不关闭主机校验来试。

## 下一步可以直接接续的工作

1. 使用[前轮已测首屏修复](../drug-trace-oracle-first-page-20261022/README.md)构建新不可变发行，按已有备份/升级流程部署到<prod-host>。
   不直接修改现网旧发行目录，不清数据、不重复建用户/视图。
2. 安全注入已存新账号，绑定并发布来源配置；本机DPAPI文件位置见
   [账号视图记录](../drug-trace-view-verify-20261022/README.md)，不要打印或向用户再索密码。
3. 按[字段契约/字典差异](../drug-trace-content-contract-20261022/README.md)新建所需映射revision，正常启用来源和真实目的地。
4. 将用户执行r10与本轮字段/质量实测作为真实来源证据材料，不要求用户再次确认已执行这一步。
   其他尚缺的正式链、长期时钟控制、权限/字典/未报范围等按实际情况登记，不编造人工终态。
5. 选定一张确认尚未由旧系统上报的正常门诊整单，核对完整码列表及首批未拆零条件，正常采集/质量VALID/冻结预览。
   上述4码候选仅用于核查，不能把随机候选自动等同于院方确认的未报测试单。
6. DMZ只新增必要uploadretail方法，按正常发送证据与max_bills=1额度，真实发送一次→查终态→逐码核对。
   HTTP200/受理不能报告业务处理成功，UNKNOWN先查不重发。

目前不需要再次生成HIS建用户/建视图脚本；后续重点是服务部署和配置，以及具体未报首张范围。

## 命令与边界

本轮以数据资产backend Python执行：

```powershell
& 'F:\python\数据资产\backend\.venv\Scripts\python.exe' 'docs/development/drug-trace-r10-readiness-20261022/check_reader.py' r1
# 退出0，16项SELECT成功，视图生效、1单4码、纯质量评估VALID。

& 'F:\python\数据资产\backend\.venv\Scripts\python.exe' 'docs/development/drug-trace-r10-readiness-20261022/check_runtime.py' application r2
# 退出0，SSH0/worker0，正式链路只读状态查询成功。

& 'F:\python\数据资产\backend\.venv\Scripts\python.exe' 'docs/development/drug-trace-r10-readiness-20261022/check_runtime.py' dmz r1
# 退出0，DMZ配置/时间/HEAD只读取证。
```

回执采用独占创建，不覆盖旧结果；未新增业务代码/运行测试套件/构建或升级现网。
