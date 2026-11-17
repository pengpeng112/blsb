# G01—G31 验收矩阵：整改收尾轮逐项对照（2026-10-01）

按主报告第10节矩阵逐项给出**本轮真实证据**（通过=有可复核测试/日志；跳过/未执行=如实注明原因）。
命令执行目录：`implementation/`。日志：`var/round3-*.log`。

| ID | 结论 | 证据 |
|---|---|---|
| G01 两租户/Agent/collector隔离 | 通过（API级） | round2 R04/R13 + A09；两租户双Agent否定矩阵在round2测试内 |
| G02 reviewer并发CAS | 通过 | round2 R12（428/412/一胜一败零副作用） |
| G03 enroll一次性/轮换 | 通过 | round2 R27；旧凭据401、新随机秘密 |
| G04 事件去重/新代次/双目标 | 通过 | round2 R08/R09 + C01；revision×target独立run |
| G05 租约抢占fencing | 通过 | round2 R39 + W14 `test_w14_finish_job_cas_rejects_stale_epoch`（并发claim恰一胜者） |
| G06 分支/等待/取消/重启 | 通过 | round2 R18；取消=W12 `test_w12_cancel_blocks_new_side_effects`（取消后tick零新副作用） |
| G07 dry-run零副作用 | 通过 | round2 R34 + W12 dry标记进run上下文；UI脱敏模拟实测（浏览器） |
| G08 未知plugin/超时正则/无能力 | 通过 | round2 R19/R20 + W09 fake驱动CAPABILITY错误 |
| G09 三病例发布+更正+旧URL | 通过 | round2 R03/R06 + W08 `test_w08_exchange_old_url_window_real_http`（真实HTTP旧批次路由可读） |
| G10 缺成员/坏Blob/卷离线阻断 | 通过 | round2 R05/R42 + W07挂载丢失清扫（`test_w07_mount_lost_blocks_writes_and_sweep_restores`） |
| G11 回执族分离 | 通过 | round2 R01/R02 + D02/D06 |
| G12 对账固定范围 | 通过（子集） | D02/fault对账用例；范围外差异与目标不可查矩阵在round2 |
| G13 2session预留竞争/重复释放 | 通过 | W07 `test_w07_quota_race_two_sessions_one_capacity` + `test_w07_release_exactly_once_across_paths`（CANCEL/EXPIRE/FAIL/COMPLETE/RECOVERY并发恰一次） |
| G14 各阶段kill/restart/重复recover | 通过 | W07 kill矩阵7崩溃点（真实子进程os._exit137）+恢复幂等二次no-op；`tests/fault/test_w07_recovery.py` |
| G15 去重/隔离/恢复副本 | 通过 | round2 R41/R42 + W07恢复路径 |
| G16 条件请求/断开协议 | 通过 | W08 4用例（真实uvicorn+raw socket：断开ABORTED实际bytes、206字节精确、HEAD无body、If-Range匹配/错配）+ kill无伪COMPLETE |
| G17 同刻游标/盘点分页/断页 | 通过 | round2b R10/R11（keyset+末页证明+回卷覆盖） |
| G18 报告子表/补片/字典修订/源不可达 | 通过 | round2 R29/R31 + F14；失败扫描不删（B02族） |
| G19 前导零/多候选/缺号/无bridge/跨机构 | 通过 | round2 R30/R32/R33 + F03/F07—F10 |
| G20 重扫/补报告/身份纠正/切Pack | 通过 | round2 G20子集（F16更正+digest不变）；闭包切换W10 `test_w10_old_run_restart_keeps_old_pack` |
| G21 旧run中途切配置restart | 通过 | W10 `test_w10_old_run_restart_keeps_old_pack` + release固定 `test_w10_release_pins_pack_for_new_runs_and_rollback`（回放/新run分轨） |
| G22 生产守卫矩阵 | 通过 | round2 R38守卫矩阵（Pack/workflow/destination/submit各入口按OPEN阻断；文件归集不受影响） |
| G23 metrics-only独立进程 | 通过 | W11 `test_w11_metrics_only_process_no_pathology`（子进程PATHOLOGY_LOADED=0、无病理表）+ `test_w11_metrics_only_csv_to_dual_targets`（CSV→映射→校验→冻结→PUSH Mock+视图发布双目标）；`apps/run_metrics_only.py` |
| G24 04 API目录逐项 | 通过（矩阵+缺口登记） | `docs/04-API目录对照矩阵.md`：已实现/等价ADR/登记缺口三类逐行；workflow-runs/config-releases为本轮新增且有契约测试（W12） |
| G25 关Vite构建版浏览器闭环 | 通过（核心闭环） | 真实浏览器（IAB）对 `http://127.0.0.1:8000/ui/`：登录/深链接#刷新渲染/资源0×404；有限DAG编辑（节点选择/条件边）→校验（含端口不兼容拒绝）→保存→批准发布→执行→dry-run→失败定位（步骤级错误+闭包）→固定版本回放；取消以API测试固化（浏览器内运行已终态）。**剩余**：连接器→映射预览→字典→Pack/Release完整向导串联（各页面已有，向导流未串）；拖拽画布未做（受限表单编辑） |
| G26 两设备/ACK丢失/队列损坏/目录放行 | 通过（本地矩阵） | H-10队列损坏保留副本；W13目录候选人工放行（登记hash快照→放行复核→证据→同队列；CONTENT_CHANGED拒绝；symlink越根拒绝）；命令幂等重发+超期EXPIRED；`test_w13_agent.py` 7用例 |
| G27 PG空库/旧库升级/并发/2GB | **部分**：SQLite隔离通过，**PG实测未执行（无PG环境）** | 迁移001—006幂等带数据重跑（W14 `test_w14_migrations_idempotent_rerun_with_data`）；BigInteger DDL编译BIGINT+>2GiB往返（W07）；PG专属实测跳过（`test_w07_pg_over_2gb_real` 需 BLYB_PG_URL） |
| G28 清洁环境/容器安装 | **未执行**（无docker） | Dockerfile/compose补齐（migrate服务前置/共享卷/worker挂卷/storage无内嵌worker/healthcheck）；离线材料已生成（lock 147包/wheelhouse 24文件/sha256manifest/sbom/licenses）；清洁安装演练未执行 |
| G29 新根恢复 | 通过 | W15 `test_w15_new_root_restore_remaps_and_reads_from_new_volume`：backup API快照→新根恢复→重映射root_ref+全部physical_key→旧根改名不可访问→资产从新卷读、digest一致→exchange generation/route一致→孤儿只登记不删 |
| G30 600MB流式/万级队列/并发 | 通过（既有） | A01 600MB（RSS≈95MB）；C-22万级队列0.1ms取队；并发/限流/取消在本轮W08/W14矩阵；峰值资源记录见测试执行记录 |
| G31 50GB/Win7/真实源/WSI/医保联调 | **未执行** | 磁盘窗口不足（需≥150GiB）、无Win7构建链、B01—B09全OPEN；如实登记 |

本地必需项结论：G01—G26、G29、G30 具备真实通过证据；G27/G28 的 PG 与清洁容器形态
**未执行**（环境缺失，已给可执行材料与跳过原因）；G31 外部条件独立登记。
按主报告门槛：**PG/容器未实测，本地验收为"除G27(PG实测)/G28/G31外通过"**，不冒称完整阶段E验收。
