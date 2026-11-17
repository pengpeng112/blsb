# DTR-SJZC-VERIFY-20261027 交接（sjzc 四项核查结论）

> **追加：关联错位复核（2026-10-27 深夜，session `20261027T113552Z-db471fbc`）。**
> 用户业务反证"都扫码了、退药也扫了"后追加三轮只读核查：①单号无窗诊断发现**处方号
> 周期性复用**（同号历史行=不同单据），修正此前表述；②050102 当日 1085 单覆盖对照：
> **904 VALID 全有当日行 / 181 WAITING 全无**（任何格式）；③050121 同（1088/215，
> 退药 3 张无当日行、当日仅 OUT 方向）。④20 位复合号假设零命中。
> **结论：三键关联无误（VALID 100% 命中证明）；WAITING 单无以处方号为键的当日行；
> 若确已扫码则以其他键写入或未达 MED_TRACE_RECORD**——交 DBA 按
> [sql-for-user.md](../../operations/drug-trace-sjzc-verify-20261027/sql-for-user.md)
> 当日全量切片（CREATE_TIME 无索引）比对实际写入单号/数量。缺失按小时 08–14 点
> 17–20%、15–17 点近零（非单一中断窗）。过程修复两处：连接器拒注释（未触库）、
> GROUP BY 别名 ORA-00933（探针定位）。累计只读 SELECT 约 160 条，源写入 0。

> **追加：授权执行阶段（2026-10-27 晚，session `20261027T112332Z-f99d1373`）。**
> 用户明确指令「授权执行」后，按 10-23 同通道（<relay-jump> 容器只读连接器、身份 READY_HIS@his、
> 本机不读凭据）完成 30 样本核查（`run_verify.py --authorized`）：
> **SQL01 主单全部唯一且在 10-26 发药日（原值 0/1、1/0、0/2 与分组一致）；SQL02 精确三键全部 0 行；
> SQL03 同单号 ±24h 全部 0 行**——归因「样本内源库无任何扫码记录」，排除迟到码/关联不一致/仅F-S。
> 住院 7、退药 3 同样无码 → 口径决策项非数据缺失。94 条只读 SELECT 共 0.43s 零错误；
> 首轮 60 条因 SQL `--` 注释被连接器客户端拒绝（未触库，失败回执保留），剥注释重跑成功。
> 完整键值联表在受限目录 verify-with-keys-20261027T112336Z.json；共享 evidence 仅 grp/tile 级。
> 判读边界：不上升为全部 260 张/全天未扫码；「为何无扫码」交药学部/HIS 厂家（050102 占 68% 首查）。
> 详见[README 授权执行结果节](../../operations/drug-trace-sjzc-verify-20261027/README.md)。

- 执行：ZCode / GLM-5.3，2026-10-27 晚，session `20261027T110327Z-20039f79`。
- 触发：用户列 10-27 核查四项人工协调事项，问能否经 sjzc 确定。
- 结果：[任务 README](../../operations/drug-trace-sjzc-verify-20261027/README.md)。

## 做了什么（全部只读）

1. 加载 sjzc 技能、读数据资产 AGENTS live 条款；定位 10-27 核查落档
   （recovery-stability 的 SOURCE-REVIEW：3 条 SQL 已备、live 因 write_policy 门禁未执行）。
2. 平台元数据新鲜快照（`check_metadata.py`→evidence/source-metadata-20261027T110338Z.json）：
   登记仍仅 `his_source_<his_host_id>`（medical_dict_push/enabled），无同地址 readonly 源，
   相关状态列值域无 confirmed——**第 4 项现状确定**。
3. ODS 元数据关键词检索（追溯/上报/码上放心）全 0 命中——**第 3 项已证不可经数据平台判定**。
4. <prod-host> 应用库只读事务提取 30 张样本三键（`extract_sample_keys.py`，jump+nested 固定指纹通道）：
   分组 260/1079/3 与核查数完全一致；样本 20/7/3；完整键值（无患者字段）存
   `.ai/private/drug-trace-sjzc-verify-20261027/sample-keys-20261027T111347Z.json`
   （ACL 收紧仅当前用户+SYSTEM；过程中一条 icacls 参数被 shell 吞产生坏 ACE，已 /reset 修复并重新收紧，终态已核验）。
5. 向用户发起 HIS live 当次授权问询（同 10-23 READY_HIS 通道先例）**未获答复**——按 HIS 只读技能
   门禁维持 BLOCKED，**未执行任何 HIS 查询**（business_source_calls=0）。
6. 落档 README + [DBA 执行单](../../operations/drug-trace-sjzc-verify-20261027/dba-package.md)；
   更新 STATE/HANDOFF/BACKLOG。

## 新增事实

- 缺码 260 张药房分布：050102=176（68%）、050101=62、050121=19、050117=2、050116=1。
- 1/0 分支 1079：050103=401、050107=334、050121=193、050110=63、050109=42、050101=41、050102=5。
- 0/2 退药 3 张全在 050121。

## 未完成 / 下一步

- **P0 二选一**：①用户明确授权→按 10-23 同通道（<relay-jump> 容器只读连接器+平台托管 READY_HIS）
  执行 SQL 包（规则：单条 15s、累计≤5min、≤50 行）并按 SOURCE-REVIEW 处置表归因；
  ②DBA 按 dba-package.md 执行回传，AI 归因。
- 205 比对清单等药学部/原厂家材料（人工 Excel/平台上传记录），不盲重发。
- 平台管理员侧：登记合规只读源（或调整 write_policy）后，sjzc live 门禁自然解除。

## 边界

- HIS 活库查询 0 条、平台业务调用 0、<prod-host> 只读事务零写入零重启。
- 未改任何生产配置/视图/驱动/代码。
