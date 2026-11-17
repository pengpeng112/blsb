# 源表核查执行交接（5077条根因闭环）

任务 DTR-HISTORY-SOURCE-AUDIT-20261023；session `20261023T141649Z-10513fda`；工具 ZCode，模型 GLM-5.3；2026-10-23晚。
用户在本会话明确授权"授权你执行"，指执行前轮交接中列出的源表核查 SQL01—06。

## 完成与结论

[RESULT.md 源表核查（r2b）一节](../../development/drug-trace-history-rootcause-r2/RESULT.md)、
[classification-final.json](../../development/drug-trace-history-rootcause-r2/evidence/classification-final.json)。

- **5077 条根因闭环（全部 U02，守恒）**：(码,旧明细号) 记录全部存在于 MED_TRACE_RECORD，
  全部 SPLIT_FLAG=1（拆零）、STORE_CODE=050121、TRACE_BILLNO 20 位数字、OUT、未删除；
  主单表窗口 PRESC_NO 仅 1—5 位（E1，20 位号不存在）、050121 是最大药房（E2，排除药房失配）、
  S01—S03 按（号+日期）不带药房查主单 0 行（E3）。判定：拆零扫码流单号不在处方主单编号空间，
  r10 三键 JOIN 按设计不可命中；非正常门诊漏报；r10 不改不并入不自动补报。
- **31 组重复成因**：每组恰 2 条源记录（OUT/未删/SPLIT_FLAG=0/JOIN 命中），创建间隔全部<1小时
  ＝真实双扫；11 张整单保持 INVALID/QD-DUP-CODE；同码在其他药房另有独立正常记录。
- **逐日覆盖（主单侧 81 天）**：主单 245,794 张、142,080 张有 OUT 码（484,567 行）≈ r1 视图
  窗口行数 484,539（交叉一致，视图未丢已 JOIN 记录）；103,714 张门诊主单无 OUT 码（A02 完整性
  待解）；住院主单（PRESC_SOURCE=1，90,024 张）窗口内零 OUT 码。
- **未做**：T 侧全量 JOIN_MISS/CREATE_TIME 反向（无索引，SQL06 实证；需 DBA 夜间窗口/临时索引）；
  平台历史终态（无授权即不查）。

## 实际命令与结果（退出码均 0）

```powershell
python scripts/ai_session.py start --task DTR-HISTORY-SOURCE-AUDIT-20261023 --tool ZCode --model GLM-5.3 --scope docs/ai --scope docs/development/drug-trace-history-rootcause-r2 --scope 数据上报/1、码上放心平台/README.md --scope 数据上报/1、码上放心平台/历史上报数据/README.md
python docs/development/drug-trace-history-rootcause-r2/run_source_audit.py --authorized   # 13.571s
python docs/development/drug-trace-history-rootcause-r2/run_daily_fix.py --authorized     # 7.144s（TRUNC 修正重跑）
python docs/development/drug-trace-history-rootcause-r2/run_stage_e.py --authorized       # 判别聚合
python docs/development/drug-trace-history-rootcause-r2/classify_source.py
python docs/development/drug-trace-history-rootcause-r2/build_workbook_r2b.py
```

通道：本机 SSH→<relay-jump> data-asset-api 容器 OracleConnector.execute_readonly；**平台托管只读凭据
（连接身份 READY_HIS，服务 his）由容器内 resolve() 解析，本机全程未读取凭据内容**；授权依据为
用户 2026-10-23 会话明确指令，范围限本业务只读核查。查询全部走既有索引（T_TRACE_CODE、主单 PK），
每条 30 秒超时、批量≤100 码、行上限 10000、零触顶零 ORA 错误。合计约 181 条 SELECT、在线约 23 秒；
源写入 0、上传 0、生产改动 0、平台业务调用 0。

过程记录（如实）：前两次远程装配失败（漏 `from sqlalchemy import select` 前缀），未执行任何数据库
查询即崩溃，回执保留；首版逐日 SQL 按 `M.PRESC_DATE`（含时间）分组导致每日结果被 50 行上限截断、
聚合无效——已用 `TRUNC(PRESC_DATE)` 分组重跑全部 81 天（旧回执标注 superseded 保留，未删除）。
最小化调试探针（1 条 identity SELECT）也已计入总数。

## 改动与保留

新增：run_source_audit.py / source_audit_remote.py / run_daily_fix.py / run_stage_e.py /
classify_source.py / build_workbook_r2b.py、evidence/source-audit-*（含失败尝试与修正记录）、
classification-final.json、EXTERNAL_INPUTS 重写（E01—E03 关闭，余 E05'—E08'）、RESULT 追加源表核查节、
README/CANDIDATE_CHANGES（C02/C04 闭环）更新。受限目录新增 source-audit-* 三个子目录（原始行、
分类明细、判别输出、**新工作簿《历史上报根因核查_源端归因_r2b.xlsx》**），ACL 显式收紧并全树验证
（16 项全部仅当前用户+SYSTEM）；私有索引已更新。共享 STATE/HANDOFF/BACKLOG 与两处业务 README 同步。
r1/r2 全部证据与工作簿未覆盖（r2 工作簿仍在、校验通过）。既有 1 单 4 码成功不撤销不重发。

## 下一步

1. E05'：HIS 厂商/药学部确认拆零流（050121、SPLIT_FLAG=1、20 位单号）业务名与是否上报；
   确认后按 C02 另立来源 profile/视图 revision 开发（含回归与部署，另行授权）。
2. E06'：药学部定双扫 11 张整单处理规则（源更正或可审计豁免）；E07' 退款/退药口径不变。
3. E04'：如需 T 侧全量计数，DBA 评估夜间窗口/临时索引后另行执行。
4. A02/A03/A05 产品完善另立开发任务；本核查不自动包含任何 DDL/部署/发送。
