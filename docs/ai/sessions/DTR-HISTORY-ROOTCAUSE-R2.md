# rootcause-r2 历史核查执行交接

任务 DTR-HISTORY-ROOTCAUSE-R2；session `20261023T104200Z-35a7e519`；工具 ZCode，模型 GLM-5.3；2026-10-23。
用户以 NEXT_AI_PROMPT 指示按 EXECUTION_TASK Q00—Q06 一次接力完成核查并交付可审查成果。

## 完成与证据

[RESULT.md](../../development/drug-trace-history-rootcause-r2/RESULT.md)、
[CANDIDATE_CHANGES.md](../../development/drug-trace-history-rootcause-r2/CANDIDATE_CHANGES.md)、
[EXTERNAL_INPUTS.md](../../development/drug-trace-history-rootcause-r2/EXTERNAL_INPUTS.md)。

- Q00：25 份原件 SHA 一致；BLYB_DRUGTRACE 身份/授权（仅两视图）再实测不变；两视图定义 token=r10 且与 r1 逐字节相同。
- Q01：5077 条视图侧两时点零变化（369 码可见/162 不见/0 精确旧键入视图）；2330 行同码同日其他明细可见（最强"同事件异标识"提示，非证明）；
  S01—S03 按规则选样（part_1 行39/40、part_10 行1175）；绑定包+531 码+4974 旧单清单入受限目录；
  **源表 SQL 未执行（无审计身份）→ 全部 U05，守恒 5077=U05**。
- Q02：1396 条三态 46/23/1327 与 r1 逐对零迁移；411 主单；拦截保留。
- Q03：31 组 → **11 张不同主单**，整单无截断取全，现有纯质量函数判定 **11/11 INVALID/QD-DUP-CODE**；
  每张单全部有效码均恰 2 次（整单双扫模式），其余门槛全过；成因待源表 SPLIT_FLAG。
- Q04：第二层如实标"未完成"；SQL05（按日 JOIN 覆盖，PRESC_DATE 索引支撑）+ SQL06（索引确认）交付。
- Q05：C01—C07 候选/保留成文；A01—A05 逐项代码检查（A03 ignore_part_success 全仓 0 命中未实现；A02 视图恒空；A05 部分）。
- Q06：五问答案在 RESULT 第一节；持续自动推送不具备条件。

## 实际命令与结果（退出码均为 0，除标注外）

```powershell
python scripts/ai_session.py status            # 0 活动会话
python scripts/ai_session.py start --task DTR-HISTORY-ROOTCAUSE-R2 --tool ZCode --model GLM-5.3 --scope docs/ai --scope docs/development/drug-trace-history-rootcause-r2 --scope 数据上报/1、码上放心平台/README.md --scope 数据上报/1、码上放心平台/历史上报数据/README.md
# Q00 元数据（平台元数据通道，无 live）
python C:/Users/Administrator/.zcode/skills/sjzc/scripts/sjzc_query.py columns HIS MED_TRACE_RECORD DRUG_USER
python C:/Users/Administrator/.zcode/skills/sjzc/scripts/sjzc_query.py columns HIS DRUG_PRESC_MASTER PHARMACY
# 在线两视图核查（37 SELECT、0.876s、零错误零触顶）
python docs/development/drug-trace-history-rootcause-r2/run_r2.py --approved-daytime
python docs/development/drug-trace-history-rootcause-r2/run_q01b.py --approved-daytime
# 本机分析与交付
python docs/development/drug-trace-history-rootcause-r2/analyze_r2.py
python docs/development/drug-trace-history-rootcause-r2/build_workbook_r2.py
python docs/development/drug-trace-history-rootcause-r2/verify_r2.py
```

在线通道与 r1 相同：本机 DPAPI reader 凭据 → SSH <relay-jump> data-asset-api 容器 OracleConnector.execute_readonly，
身份 BLYB_DRUGTRACE（脚本内断言授权集合未变，否则中止）。源表/宽源通道未使用：
平台登记 his_source_<his_host_id> write_policy=medical_dict_push（非 readonly），按 DATABASE_ACCESS 门禁停用，未读托管凭据、未改登记、未索要密码。

## 改动与保留

新增 `docs/development/drug-trace-history-rootcause-r2/`（RESULT/README/CANDIDATE_CHANGES/EXTERNAL_INPUTS/
private-artifact-index/validation、sql/04—06、evidence/ 5 份、6 个可复跑脚本）与仓库外受限目录
`%LOCALAPPDATA%/BLYB/history-rootcause-r2/live-20261023T104928Z/`（差异明细、DBA 包、531 码/4974 旧单、
新工作簿 r2）。更新共享 STATE/HANDOFF/BACKLOG 与两处业务 README。原 25 份 Excel、旧 SQLite、
r1 证据/工作簿/脚本均未覆盖未删除（verify_r2.py 断言）。HIS 写 0、生产改动 0、平台调用 0、上传 0。

过程记录（如实）：私有目录初次创建继承了 BLYB 父目录的宽 ACL；首次 icacls 收紧命令在 Git Bash 中
`/T` 被误解析为路径导致部分文件短暂出现空 DACL，随即用显式逐文件授权修复，最终 Get-Acl 全树
验证仅当前用户+SYSTEM（9 项），并回读文件确认可读。此为本次新建文件的本地瞬态，未影响 r1 成果。

## 未完成与下一步

1. E01/E02/E03：DBA 执行 SQL01—04（S01—S03 小样 → 531 码分批）回传后，5077 条 U05 归位为 U01—U04；
   若用户批准专用受限审计账号（仅三表 SELECT），本机受控连接器可直接执行，执行器包装按 run_r2.py 同机制生成。
2. E04：DBA 先跑 SQL06 确认索引，再决定 CREATE_TIME 口径反向方案或 SQL05 逐日 JOIN 覆盖统计。
3. E05/E06：20 位旧明细号业务来源、退款/退药口径由 HIS 厂商/药学部给字段级证据。
4. A02/A03/A05 产品完善按 CANDIDATE_CHANGES C06 差距另立开发任务（不在本核查任务内）。
5. 原表根因未闭环、同窗口反向全量未做——RESULT 已如实标注，不冒充完成。
