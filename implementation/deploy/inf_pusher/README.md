# 传染病前置软件推送器（inf_pusher）

独立部署的取数-推送工具：ODS 六张 `V_INF_*` 视图 → 前置机 `http://<frontend-host>/hclient/emr/receive/*`。
设计决定与审查结论见 `docs/development/inf-report-20261027/`（FIELD_MAPPING §17/终报告）。

## 文件

| 文件 | 用途 |
|---|---|
| `pusher.py` | 主程序（stdlib + oracledb，无 Web 框架） |
| `tables.py` | 6 表注册：视图/端点/字段映射（SNAKE→camel）/必填集/水位策略 |
| `config.template.json` | 配置模板（复制为 config.json；凭据只走环境变量） |
| `selftest.py` | 自测（无 DB/网络依赖，26 项断言） |

## 部署要求（目标主机需同时可达 ODS <ods-host>:1521 与前置机 <frontend-host>）

1. Python ≥3.9 + `pip install oracledb`。
2. **ODS 是 Oracle 11g，oracledb 必须 thick 模式**：安装 instantclient 并在启动脚本先
   `oracledb.init_oracle_client(lib_dir=...)`（或设 `PATH` 后在 pusher.py 顶部取消对应注释）。
3. DBA 建专用只读账号（仅 SELECT 六张 V_INF_* 视图），凭据写入环境变量：
   `INF_ODS_USER / INF_ODS_PASSWORD / INF_ODS_DSN`（DSN 如 `<ods-host>:1521/orcl`）。
4. `cp config.template.json config.json`，workdir 指向持久目录（水位/回执都在这里）。

## 运行（严格按序）

```bash
python selftest.py                                   # 1. 部署后先跑自测（26 项）
python pusher.py --config config.json --table all --mode mock    # 2. 全表演练（零真实副作用）
python pusher.py --config config.json --table death --mode real --limit 3   # 3. 小批量真实（死亡表3条，需 config.enabled=true）
python pusher.py --config config.json --table death --mode real             # 4. 放量
# 5. 逐表开闸（顺序已内建）：admission → exam(+item) → first_course → daily_course
python pusher.py --config config.json --table all --mode real              # 6. 全量常态
```

真实模式双重门槛：`--mode real` **且** `config.enabled=true`（MOCK 先行约定）。

## 状态文件（workdir）

- `watermark.json`：每表水位；增量窗=水位-3 天重叠回扫；首次运行回扫 full_days（death/admission 180 天，病程 30 天）。
- `sent.jsonl`：id→operationTime 已发清单（次序保护：旧版重试不覆盖已发送新版；重扫幂等跳过）。
- `receipts-YYYYMMDD.jsonl`：逐条回执（result/errorCode/desc/attempt/mode）——对账与质控自评依据。
- `last-run-<mode>.json`：本次统计（rows/sent/skipped_same/skipped_invalid/failed/truncated）。

## 内建保护（对应 multi-review 终报告）

- 目标白名单硬编码：仅 `http://<frontend-host>`（写错配置直接拒绝启动）。
- 必填客户端预检（与规范必填集一致）——缺必填记 skipped_invalid 不发送。
- errorCode 01/99 → 重试（3 次退避）；02/03/04 → 记录不重试（人工处置）；网络错误重试。
- 病程 2000 字截断触顶计数（truncated 统计，GBK 库 SQL 层上限，全文二期 CLOB 直读）。
- 主子表顺序：exam 先于 exam_item（注册表 depends_on 自动排序）。
- 日批建议 cron `30 8 * * *`（避开 drug_trace 06:10-08:30 维护窗）；月末如重试堆积自动退避。

## PT-01（2026-11-17）加固后的新增状态文件与保护

| 文件/机制 | 说明 |
|---|---|
| `pending-wait.json` | **持久等待队列**（零患者隐私：仅表名/ID/父ID/源时间/原因）。父未受理子项先落盘再计等待——即使水位越过 overlap_days 或进程重启，下一班按 ID 定向重查自动补回（`recovered_from_wait` 统计）；源端已删行移入 `wait_expired` 不死锁。**解决"数据问题清单 Sheet2 视图外 382 单"类积压的自动恢复路径**（父单补录受理后子项自动补发，无需人工重放）。 |
| `manifest-state-<id>.json` | 定向重放逐项进度（PENDING/SUCCEEDED/FAILED/VERSION_DRIFT）。同一清单二跑对已成功项**零重复 HTTP**。 |
| 父受理门禁 `ParentAcceptanceTracker` | 命名空间键 `父表:父ID`；本班次父失败（缺必填/02/03/04/重试耗尽）严格阻断子项，绝不被 sent.jsonl 旧成功、其他表同 ID 或 Mock 回执绕过；同版本历史父跨日复用。 |
| 原子写 | `watermark.json`/`pending-wait.json`/`last-run-*`/`reconcile-*`/`manifest-state-*` 全部临时文件+fsync+os.replace（进程被杀不再产生空文件引发全量重扫）。 |
| 流式取数 | `fetch_rows` 改 `fetchmany(2000)` 分批（防百万行 fetchall 内存暴涨）；重放/待办重查走参数化 IN 定向查询（每批≤500）。 |
| reconcile 恒等式 | `reconcile-<D>.json` 每表含 `window_rows/repushed/recovered_wait/parent_wait/skipped_invalid/failed`；`parent_wait>0` 写 `RECONCILE-PARENT-WAIT` 告警行（`failed=0` 不再掩盖未完成子项）。 |

## 升级部署流程（<prod-host> 受控；PT-06 手册总纲，本节为 dry-run 操作卡）

> 红线：部署须用户在维护窗口授权执行；`deploy_8156_infpusher.py` 已保 `*.sh` 上传后 `chmod 755`（run.sh 丢执行位事故根因修复）。

1. 本地/宿主预检：`python selftest.py`（69 checks）；`python -m pytest tests/unit/test_pt01_inf_pusher_reliability.py -q`（47 用例，含 12/12 行为场景）。
2. 上传（deploy 脚本自动 chmod：run.sh=755、*.py=644）→ 容器内 `python3 selftest.py` 复跑 → SHA256 与本地比对。
3. **重放 dry-run（零副作用，强烈建议首步）**：按 `replay-manifest.template.json` 填一张小清单（如 3 条 ID），执行
   ```bash
   python pusher.py --config config.json --table ex_lab --mode real \
     --replay-manifest replay-manifest.json        # 不加 --apply = dry-run
   ```
   预期：打印 `{"dry_run": true, ...}` 预览（含源端命中/缺失探针）后退出 0；workdir **零新增** sent/watermark/receipts/manifest-state 文件。目标缺失会列在 `source_missing`（apply 时则非零退出并打印缺失清单）。
4. 灰度：`--table ex_lab --mode real --limit 3` → 观察回执全 true。
5. 全量：恢复 cron 常态班次；观察一班 `last-run-real.json` 的 `recovered_from_wait`（首批会把历史 parent_wait 等待子项自动补回）与 `pending-wait.json` 收敛。
6. 回退：恢复备份 `pusher.py.bak-*`（旧版不识别 pending-wait.json，无害——等待子项回到 PT-01 前行为，不丢数据）。
