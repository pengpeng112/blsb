# 传染病影子插件（LT-05 B 段）

平台内按标准插件形态实现的传染病上报**影子**：取数映射（tables.py 复制自
生产 pusher）+ generic_http_json Mock 发送。**生产仍由 `deploy/inf_pusher`
+ 容器 crontab 执行，本插件不接管、不修改任何生产链路。**

## 组成

- `tables.py`：复制自 `deploy/inf_pusher/tables.py` @ 0882ede143aa（源 sha256
  前 12 位）；parity 由 `tests/unit/test_inf_tables_parity.py` 守护。
  **生产变更后只更新影子，禁止反改生产来通过 parity。**
- `mapping.py`：只读加载 pusher.py 复用纯函数（camel/norm_date_str/
  apply_view_shims/validate_required——oracledb 为函数内延迟导入，顶层仅
  stdlib，spec 加载无副作用）；等价性由 `test_inf_payload_equivalence.py`
  以**真实分支**（run_table + 冻结时钟 + monkeypatch fetch_rows/post_json）
  驱动证明，不用 Mock 分支（其在 operationTime 重写前退出）。
- `send.py`：幂等键=源业务键+源版本（冻结 digest），上传时刻不参与身份；
  同 id 旧源版本显式拦截；只能 is_mock=True 目的地（真实发送在
  perform_push 闸门必拒：inf_cdc_frontend runtime_kind=EXTERNAL）。
- `hooks.py`：external_ledger summary（ACCEPTED≠CONFIRMED；观测截至/导入
  时间分开；无批次 data_source=none）+ ledger_search（游标 v2 绑定上下文）。
- 水位/已发账存 PUBLISHED ConfigRevision（config_type=plugin_state,
  owner_id=infectious.shadow），不建新表。

## 文书类型编码 ↔ 表（03-接口总览）

| 表 key | endpoint | 编码 |
|---|---|---|
| death | emrDeathInfo | 07 |
| admission | emrAdmissionRecord | 08 |
| exam | emrExClinical | 09 |
| exam_item | emrExClinicalItem | 11 |
| first_course | emrFirstCourse | 12 |
| daily_course | emrDailyCourse | 17 |

## 从 pusher 切到平台的前提（未执行；LT-05 B5）

1. **影子并行 ≥7 天对比**：同一时间窗，平台 Mock 报文集合与 pusher 实际回执
   id 集合一致率 100%。对比工具：`compare_shadow_vs_ledger(target, date)`
   （tests/integration/test_inf_shadow.py 内实现，输入=外部台账记录与平台
   Submission）。**本批未执行七天对比，不得视为已签认。**
2. ODS 只读账号在平台 worker 的配置（凭据引用名，不写值）。
3. 切换草案（须独立授权，不能仅改 Target 字段）：停 crontab 对应行 →
   ReportTarget 改 runtime_kind=PLATFORM、lifecycle=PILOT（过闸门还需
   sole_pusher=CONFIRMED+real_send_evidence_ref）→ Destination is_mock=False
   指真实地址 → 启用 inf.poll 调度 → 观察 → PRODUCTION；回退反向。
4. 切换窗口避开 */15 班次与 23:58/00:05/00:27 迟班；须用户在场。

## 已知差异待验证

SQLite 合成视图用 TEXT 代替 Oracle CLOB/DATE；真实库类型差异在切换前用
ODS 只读账号验证。
