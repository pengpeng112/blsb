# INF-DESIGN-20261027 交接 · 传染病病历数据上报设计与视图草稿

工具/模型：ZCode / GLM-5.3。日期：2026-10-27。续任务 INF-VERIFY-20261027（同日，sjzc 元数据逐项核对并修订 v0.2）。

## v0.2 核对修订（INF-VERIFY-20261027）

对 6 张视图引用的全部表/列用 sjzc 平台元数据逐项核对（仍零 live）：
- **修正 3 处硬错误**：PAT_VISIT 医师列实为 `ATTENDING_DOCTOR`（草稿误写 ATTENDING_PHYSICIAN）；PAT_VISIT 无 `OPERATOR` 列（改主治医师兼操作人）；CDA 死亡表死因实列为 `DIRECT_DEATH_REASON_CODE/NAME`（草稿误用三甲视图输出列名）。
- **诊断源切换**：HIS.DIAGNOSIS（三甲 V_EMR_ICH_DIAGNOSIS 实际来源，TYPE 1/2/3/7/8）替代嘉和 diagnosis；编码经 `CDA.CDA_DICTIONARY` 院标→国标 ICD10 映射。
- **补实值**：过敏（ALERGY_DRUGS）、病理号（PATHOLOGY_NO）、死亡诊疗过程/死亡诊断（CDA 实列）；死亡兜底=三甲验证口径（出院时间+主诊断）。
- 全部核对结论与给 DBA 的执行提示见 FIELD_MAPPING §8。**6 张 v0.2 SQL 可交 DBA 建视图**（替换 2 处占位：起始窗口、机构 ID）。

## 改了什么

1. **新增** `数据上报/4、传染病病历数据上报/README.md`（按模板；总索引 `数据上报/README.md` 同步加行+更新日期）。
2. **新增** `docs/development/inf-report-20261027/`：
   - `FIELD_MAPPING.md`：住院 5 类（病案首页/首次病程/日常病程/死亡信息/检查报告主+子）规范字段↔ODS 源字段映射，含全局约定、值域清单（检查类别 10/11/99、诊疗活动类型 1-9 等）、§7 待核对 6 项。
   - `sql/01..06-v_inf_*.sql`：6 张取数视图草稿（ODS <ods-host>:1521/orcl，命名 V_INF_* 与三甲 V_EMR_* 区分），**均本机草稿未部署**。
   - `spec-fields/`：两份 PDF 全文 txt + 6 张规范表字段拆分参考件。

## 用户已拍板的口径（2026-10-27）

- 范围：住院 5 类，T+0；电子传报卡**不做**；门急诊/检验/医嘱/生命体征不做；伴生表（patient_info/activity_info/base_user/base_dept）暂不做。
- 取数基于**三甲复审视图模式**（`D:\...\三甲复审\...\03、当前上报视图\`），不用 CDA 互联互通表新鲜度核验（用户暂缓）。
- ODS 同步延迟满足"当日 24 点前"（用户确认）。
- 前置机 IP/端口、国家分配机构 ID、license：**用户晚点提供**（当前最大外部阻断）。

## 关键技术决定与依据

- 全部视图集中 ODS 一库（HIS/JHEMR/CDA 镜像齐），避免双库连接；检查报告不用三甲的 HIS 源库版而用 ODS 镜像同表。
- 首次/日常病程：三甲清单无此两类视图 → 按嘉和模式新写（jhmr_file_index TOPIC 匹配 + jhmr_file_content_text CLOB 正文 + pat_visit/pat_master_index 关联，NVL(DELETE_FLAG,0)=0）。正文进建议填字段（course/present_illness_his），结构化拆分（CDA TT_ESBHL_FIRST_COU_ROD / JHMR_FILE_CONTENT_DG）列为二期。
- 死亡：驱动=pat_visit 离院方式 5（148 号口径，PAT_VISIT.DEATH_DATE_TIME 源端不填是已知陷阱）；死因初版取 CDA.TT_ESBHL_DEATH_ROD，落点核验后可切换嘉和死亡证明。
- TOPIC 过滤为排除法保守写法（首次：`LIKE '%首次病程记录%' AND NOT LIKE '%术后%'`；日常：`NOT 首次/术后`），**真实 TOPIC 分布需抽样收紧**。

## 真实命令与结果

- 仅 sjzc 元数据查询（systems/tables/columns，约 20 条，零 live、零写入、零上传）；pypdf 提取两份 PDF 文本到 spec-fields/；openpyxl 读三甲视图统计表。
- 本会话未跑测试、未连任何业务库、未部署任何 SQL。

## 未完成与下一步

1. 等用户提供：前置机 IP/端口、机构 ID（是否=49557032X）、license → 填视图常量+推送配置。
2. DBA/授权抽样核验 FIELD_MAPPING §7：① MEDREC 诊断镜像是否在 ODS；② EXAM_ITEM_CODE 非空率；③ 死亡时间/直接死因真实落点；④ 病程 TOPIC 分布；⑤ PAT_VISIT 医师列名与离院方式列名。
3. 视图部署（DBA 受控流程，参考 his-ddl-clock 结构：precheck→create→grant→verify→rollback）+ dry-run 抽样。
4. 推送插件（复用 drug_trace 骨架，MOCK 通道先行，新域 domains/inf_report）。

## 恢复命令

接手先读：`数据上报/4、传染病病历数据上报/README.md` → `docs/development/inf-report-20261027/FIELD_MAPPING.md` → 本文件。无运行中进程。

## v0.3 活库实测与修复（INF-VIEW-VERIFY-20261027，2026-10-27 晚）

用户建 5 张视图后实测（<relay-jump> 连接器，~15 条聚合 SELECT，零写入）：
- 死亡视图 VALID 且数据健康（319 例，死亡时间/死因/科室 100%）；但发现 **PAT_VISIT.INP_NO 全空**（住院号全部失效）→ 全部视图住院号改 `NVL(pv.INP_NO, pmi.INP_NO)`。
- 检查两张 VALID 但 0 行：PATIENT_SOURCE 值域实测 NULL/'1'门诊/'2'住院（非 I/O）→ 改 JOIN PAT_VISIT 判定住院，加"已出报告才推"过滤；修正后 7 天 7315 例、项目编码 100% 真实填充。
- 首次/日常病程 INVALID（ORA-00942）：ODS 缺 JHEMR 两表直接授权（无嘉和视图先例）→ 待 GRANT+COMPILE。
- 病案首页未建成：真因=老 Oracle 关联不能穿透聚合内联视图（LISTAGG 双层嵌套 ORA-00904，已复现），非字段过长；v0.3 改单层关联+字典预聚合（实测通过）。字段超长实际在项目子表 31 字符列，规范为 EXAM_QUANT_UNIT。
- 详见 FIELD_MAPPING §9（含 DBA 一次到位清单）。SQL 全部升 v0.3。

## 二轮复核（INF-VIEW-RECHECK-20261027）

21:23 部署后复核：死亡/检查主/检查项目三张全绿（住院号100%）；病案首页 VALID 但门急诊诊断仅 40%（TYPE=1 编码只填 47%、名称 100%）→ v0.4 加"名称映射+TYPE2 兜底"两级链，实测 99.8%；两张病程仍 ORA-00942，根因=本人 v0.3 文件 JOIN 行 JHEMR 误写 JHMR（授权已到位）。剩余动作：重跑 01(v0.4)/02/03。

## 三轮复核（INF-VIEW-FINAL-20261027）

21:35 重跑后 6 张全 VALID；首页门急诊诊断 99.8%（v0.4 兜底生效）；首次/日常病程行数与住院号 100%。
新发现：正文 3900 字在 GBK 库触发 Oracle SQL 层 VARCHAR2 4000 字节上限（ORA-06502，日常病程因内容短未暴露），
02/03 已改 2000 字（实测全周通过，15.5% 触顶截断，全文二期 CLOB 直读）。剩：重跑 02/03 收官。

## 收官（INF-VIEW-DONE-20261027，21:45）

02/03 重跑（2000字修正）后终验：6 张全 VALID；首次病程 1131/日常 1341，住院号/正文/科室全 100%。视图层完工，等前置机信息启动推送插件。

## 伴生表与 serial 实证（INF-COMPANION-20261028，2026-10-28）

用户拍板（伴生表补做/serial 查现有视图/死亡全量）后 live 实证（HIS 源+ODS，约 20 条聚合 SELECT 零写入）：
- **FXHIS schema 已有传染病规范同名视图群**（patient/activity/admission/discharge/vital+V_SYS_USER_DEPT_INFO+
  COMM.INFECT_DEPT_DICT 412行），不含我方 5 类文书——伴生表改复用；用户表 196/196 覆盖我方操作人工号。
- FXHIS 住院 serial=唯一8位数字，非病案号/患者ID/PAT_VISIT 任何号列；号源在其 12KB 视图文本内，DBA 导出后统改 6 视图。
- CDA 区 2024-08 停更；嘉和作者ID=HIS工号 100%。详见 FIELD_MAPPING §13。

## 前置机端点测试（INF-ENDPOINT-TEST-20261028）

<frontend-host> 实测（<relay-jump> 跳板）：GET 405=Hclient 服务在；POST {} 返回 04+必填清单；POST 合成完整记录字段校验全过、
止于"医疗机构编码不属于配置范围内"。上传链路验证完成，待机构编码登记（国家 ID 或配置 49557032X）。证据 §14+evidence/。

## crbsb 对齐与上传成功（INF-ALIGN-CRBSB-20261028）

用户提供 crbsb.emr_order：①370104000 端点 POST result:true+DELETE 清理，上传双向打通；②serial=病案号||次数实锤
（crbsb 59.5万行+FXHIS 双证）；③CRBSB 全集 20 表视图与我方 5 类重叠。我方 6 视图 v0.5 对齐完毕（近期数据 serial
唯一/科室映射 100% 实测），待 DBA 重建；emr_order 审查 2🔴2🟠3🟡；并存决策待用户（建议读 CRBSB、V_INF 退役）。

## 上报启动清单（INF-GOLIVE-CHECKLIST-20261028）

用户拍板统一走 V_INF_* v0.5。§17 清单：DBA 侧=重建 6 视图+专用只读账号；我方=推送插件（可立即开工）+MOCK+小批量+放量；
协调=crbsb 双推禁止+伴生表归属。关键路径 1→2→(4 并行)→5→6→7。

## 推送器交付（INF-PUSHER-BUILD-20261028）

`implementation/deploy/inf_pusher/`（pusher.py/tables.py/config 模板/README/selftest）独立工具：MOCK 默认+real 双门槛、
水位+3天重叠回扫+full_days 对账、次序保护、必填预检、回执 JSONL、错误分类重试、白名单硬编码、截断统计、主子表排序。
26/26 自测通过。待：部署主机（双可达，建议<prod-host>）+专用账号+cron；首推 death --limit 3。

## 推送器部署 <prod-host>（INF-PUSHER-DEPLOY-20261028）

经 <jump-host>:<ssh-port> 跳板（复用 reporting-20261015 模块，base64 分块+sha256）部署至 <deploy-path> 与
<prod-path>/exchange/inf-pusher（容器挂载）；双可达 OPEN（ODS/前置机）；<worker-container> 容器内自测 26/26；
thick 自动初始化、config(enabled=false)、run.sh(750, source inf.env) 就位。剩：DBA 写 inf.env → mock → death real 3。

## 首跑负向验证（INF-FIRST-RUN-20261028）

inf.env 未写入（项目凭据库亦无 ODS 账号，平台凭据不外发）→ 执行链已验证到缺凭据为止：修 10001 属主权限后
docker exec → run.sh → pusher → Oracle 层全通，报错精确友好（缺 INF_ODS_*）。dba-setup.sql 已备（建号+授权+inf.env 模板）。
待 DBA 30 秒执行后即可 mock → death real 3。

## 首次全表 MOCK（INF-GO-20261028；登记与 DTR-SCOPE-DEPLOY 并行会话范围重叠，文件不相交）

平台 ODS 账号注入 inf.env（stdin+^D 配方零回显）；MOCK：死亡199全过/首页97%/病程过万，检查两张全拦（EXAM_SUBDEPT、
CONFIRM_DOCT 空值）→ 三视图兜底修正（预验证100%）；推送器次序键分表 bug 修复。待 DBA 重跑 01/05/06 后重跑 MOCK。

## 正式上线（INF-TIMEDPUSH-20261028；会话登记与 DTR-SCOPE-DEPLOY 并行重叠，实际文件不相交）

垫片四件套+MOCK状态污染修复+次序键分表；死亡表 VARCHAR 日期强转坑修复（locale 无关归一化）；
逐表首推受理→全量回填 128,625 条（~200/s，docker exec -d）；T+0 cron 装妥（30 8,11,14,17,20 + 55 23，flock）。
余项：视图侧根治四张、伴生表协调、首月质控观察。

## 档案体系（INF-ARCHIVE-20261028）

业务目录立档：01 过程与决策/02 流程与运维/03 接口总览/interfaces 20 接口逐档（6 详+14 简），README 重写导航，
总索引与 STATE 同步。逐表终态：death 196/admission 31242/exam 42893/item 43203/first 5644/daily 5429。

## 维护铁律设立（INF-CONVENTION-20261028）

用户令"后续统一按此体系记录"→ 业务 README 首节立 6 条铁律（改动必同步档案/六步法/双推红线/口径全表联动/会话纪律/
对账依据），D10 入决策记录，STATE 顶部声明约束生效。后续会话接手即受约束：未同步档案不得标 completed。

## 首日巡检（INF-CHECK-20261029）

发现 cron PATH 故障（docker 不在 cron 环境）→ crontab 绝对路径修复 → 补推增量 89 条全受理、水位推进至 10-29 凌晨。
铁律执行：01/02/STATE 已同步。今起自动班恢复。

## T+0 强化计划编制（INF-T0-PLAN-20261031）

用户令"尽快完成 T+0"→方案一+二定稿并写成自包含执行计划（plans/20261031-T0强化执行计划.md：0 接手须知/红线，
1 ODS 延迟实测，2 crontab 四行，3 pusher --since+reconcile 幂等补推+ALERT+空窗演练，4 守护进程观察项，5 铁律回写，
6 回退，7 验收清单）。README 导航加 plans/，STATE/BACKLOG 挂待执行。
