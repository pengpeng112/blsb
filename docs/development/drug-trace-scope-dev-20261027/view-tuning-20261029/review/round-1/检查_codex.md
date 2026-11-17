**结论：这个函数索引有合理的技术依据，值得验证；但现稿不能按“确定生效、结果零影响、日批从约 6 小时降到分钟级”通过。**它很可能改善 r10 门诊的逐单查询，是否足以稳定解决 `collect_timeout`，现有证据不足，而且仓库里已有证据表明还存在大量未解释的耗时。

本次仅阅读仓库文件、分析已有回执并核对 Oracle 官方文档，**没有修改文件、连接生产数据库、执行 DDL/DML 或重新进行性能测试**。下面的“实测”均指仓库已有记录。

1. **最重要的发现：生产超时耗时，只有约一半被 Oracle 查询计时覆盖。**

   10 月 29 日的一次失败回执记录：总耗时 `14406.2s`，已处理 `2050` 单、`82` 页；Oracle 查询累计 `7244.94s`，连接累计仅 `1.59s`。相减还有约 **7159.67 秒，即近两小时**没有被这两个指标覆盖。

   这些时间可能包含排队、本地入库和质量处理、其他 worker 工作、尚未汇总的页内耗时等，目前不能确定各自占比。因此，不能把全部约 6 小时都归因于拼接键全表扫描。即使索引让查询显著加快，仍需解释剩余耗时，才能承诺整体采集时间。   
   出处：[生产失败回执](/F:/python/blyb/docs/development/drug-trace-timed-report-20261024/evidence/receipt-check-20261029T021210Z.txt)；计时边界见 [drug_trace.py:325](/F:/python/blyb/implementation/plugins/sources/db_view/drug_trace.py:325)。

2. **“6 小时→分钟级”是未经验证的外推，应该从方案结论中删除。**

   报告用拆分谓词查询 F/G 的表现，推导尚未创建的 FBI 会让生产日批进入分钟级。但 F/G 既不是创建 FBI 后的实际 SQL，也没有覆盖第 1 条里的其他耗时。它只能说明“已有高效访问路径”，不能证明整个 worker 的吞吐量。

   更合理的表述是：**预计显著减少门诊逐单查库成本，有望使采集回到 4 小时内，实际收益待完整窗口验证。**   
   出处：[调优报告:58](/F:/python/blyb/docs/development/drug-trace-scope-dev-20261027/view-tuning-20261029/README.md:58)、[生产失败回执](/F:/python/blyb/docs/development/drug-trace-timed-report-20261024/evidence/receipt-check-20261029T021210Z.txt)。

3. **索引目标选得合理：它直接对应应用实际使用的拼接键等值谓词。**

   r10 的两个视图都用同一个日期、处方号、药房拼接表达式生成 `source_bill_id`；应用的单头和码明细查询也确实按该字段等值过滤。为这个表达式建 FBI，技术方向成立，能给当前缺少直接查找路径的谓词提供索引访问。

   因此，我没有发现足以直接判定“这个 FBI 思路不可行”的依据；但目标环境上的创建、表达式识别和实际执行计划仍未验证。   
   出处：[r10 视图:10](/F:/python/blyb/docs/development/drug-trace-content-contract-20261022/sql/draft_normal_outpatient_views_r10.sql:10)、[r10 码视图:48](/F:/python/blyb/docs/development/drug-trace-content-contract-20261022/sql/draft_normal_outpatient_views_r10.sql:48)、[应用模板:55](/F:/python/blyb/implementation/plugins/sources/db_view/drug_trace.py:55)；[Oracle 11g 函数索引说明](https://docs.oracle.com/cd/E11882_01/server.112/e41084/statements_5012.htm)。

4. **现有验收 SQL 缺少最关键的码明细查询。**

   `fbi_dba_option.sql` 只验证基表拼接表达式的 `COUNT(*)`，再执行单头视图的 `COUNT(*)`；**没有验证 `V_DRUG_TRACE_CODE`**。但报告承诺的收益包含码明细从双表扫描变成索引嵌套循环，这恰好是尚未覆盖的部分。

   这个遗漏足以导致“单头验证成功，采集仍慢”的上线结果。   
   出处：[索引验证脚本:12](/F:/python/blyb/docs/development/drug-trace-scope-dev-20261027/view-tuning-20261029/sql/fbi_dba_option.sql:12)、[调优报告:58](/F:/python/blyb/docs/development/drug-trace-scope-dev-20261027/view-tuning-20261029/README.md:58)。

5. **COUNT 查询不能替代生产的全列、排序和分页 SQL。**

   实际码查询包含多列投影、`after_record_id` 游标条件、`ORDER BY source_code_record_id` 和外层 `ROWNUM`。单头查询也读取完整业务字段。COUNT 查询可能省去回表、列计算、排序等工作，执行计划也可能不同。

   后续必须验证原始模板的首屏、后续页和空结果查询，不能只看 COUNT。   
   出处：[实际码查询:55](/F:/python/blyb/implementation/plugins/sources/db_view/drug_trace.py:55)、[实际单头查询:75](/F:/python/blyb/implementation/plugins/sources/db_view/drug_trace.py:75)、[当前验收脚本:14](/F:/python/blyb/docs/development/drug-trace-scope-dev-20261027/view-tuning-20261029/sql/fbi_dba_option.sql:14)。

6. **创建 M 表索引，并不自动保证 T 表采用预期访问路径。**

   `T.TRACE_BILLNO = TO_CHAR(M.PRESC_NO)` 中，`TRACE_BILLNO` 本身没有套函数。若先通过 FBI 找到极少量 M 行，再用其处方号字符串查 T，**T 上现有的 `TRACE_BILLNO` 索引仍然可以使用**。

   所以“连接里出现 TO_CHAR，便导致 T 索引失效”不是准确解释。真正需要确认的是驱动表、连接顺序、基数估计及 T 的实际访问方式。加 FBI 后优化器可能选择更好计划，但不是必然如此。   
   出处：[r10 连接条件:76](/F:/python/blyb/docs/development/drug-trace-content-contract-20261022/sql/draft_normal_outpatient_views_r10.sql:76)、[现有索引与拆分查询记录:20](/F:/python/blyb/docs/development/drug-trace-scope-dev-20261027/view-tuning-20261029/README.md:20)。住院历史报告中“该函数使 T 索引失效”的表述也存在同样问题：[住院中止记录:15](/F:/python/blyb/docs/development/drug-trace-scope-dev-20261027/evidence/deploy/PHASE4-DEFERRED.md:15)。

7. **索引方案漏了函数表达式统计信息的检查与处理。**

   脚本创建索引后直接 EXPLAIN，没有检查基表、索引和隐藏表达式列的统计信息。索引创建可以收集索引统计，但不能据此假定表达式基数估计已经充分。Oracle 11g 文档明确要求重视 FBI 创建后的表和索引统计。

   应先核查统计是否有效、表达式的不同值数量是否合理，再决定受控采集方式；也不宜随意重采整张 HIS 表的所有统计，造成其他 SQL 计划变化。   
   出处：[完整索引脚本](/F:/python/blyb/docs/development/drug-trace-scope-dev-20261027/view-tuning-20261029/sql/fbi_dba_option.sql)；[Oracle CREATE INDEX](https://docs.oracle.com/cd/E11882_01/server.112/e41084/statements_5012.htm)、[11g 索引创建与统计说明](https://docs.oracle.com/cd/E24693_01/server.11203/e17120/indexes003.htm)。

8. **应看实际游标执行计划，不能以 EXPLAIN PLAN 作为最终验收。**

   应用使用绑定参数、生产账号及 Thick 会话。DBA 对简化 SQL 做 EXPLAIN，并不等于应用实际采用该计划。

   后续应针对真实模板及绑定类型，检查实际游标计划；有执行统计时，还应检查实际行数、逻辑读、物理读及各步骤耗时，尤其是 T 的访问方式。Oracle 11g 的 `DBMS_XPLAN.DISPLAY_CURSOR` 支持查看缓存游标及相关运行统计。   
   出处：[应用执行入口:350](/F:/python/blyb/implementation/plugins/sources/db_view/drug_trace.py:350)、[现有 EXPLAIN:14](/F:/python/blyb/docs/development/drug-trace-scope-dev-20261027/view-tuning-20261029/sql/fbi_dba_option.sql:14)；[Oracle 11g DBMS_XPLAN](https://docs.oracle.com/cd/E11882_01/appdev.112/e40758/d_xplan.htm)。

9. **样本太少，无法证明所有日批单据都受益。**

   10 月 31 日证据只有一张 `050121` 单据的重复计时。而一份完整采集回执包含 `3205` 单，其中 `1792 VALID`、`1413 WAITING`，累计 `6539` 次查询，恰好对应 `129` 次发现查询加上每单两次查询。说明采集负载不能只按可发送单据数估算。

   至少还需覆盖其他药房、UNKNOWN 业务、无码单、同号跨日、多码及跨 200 条分页的单据。   
   出处：[单样本计时证据](/F:/python/blyb/docs/development/drug-trace-scope-dev-20261027/view-tuning-20261029/evidence/hint-test-20261031T023339Z.txt)、[完整采集回执](/F:/python/blyb/docs/development/drug-trace-timed-report-20261024/evidence/monitor7-20261029T150347Z.txt)、[整单读取逻辑:88](/F:/python/blyb/implementation/plugins/domains/drug_trace/ingest.py:88)。

10. **NLS 需要分开讨论：整数处方号的风险，不能直接等同于任意 NUMBER 的风险。**

    无格式的 `TO_CHAR(NUMBER)` 合法，不能仅因没有格式掩码就断定 FBI 不可建。对样本中的普通整数 `50235`，切换小数点字符通常不会改变这段整数文本；如果存在小数处方号，情况就不同。

    方案缺少 `PRESC_NO` 的真实精度、scale、约束和历史值范围证据。因此，“NLS 一不同就必然出错”和“处方号都是整数所以完全无风险”，目前都不能成立。   
    出处：[方案 NLS 问题:70](/F:/python/blyb/docs/development/drug-trace-scope-dev-20261027/view-tuning-20261029/review/round-1/方案.md:70)、[现有 NLS 处置说明:28](/F:/python/blyb/docs/development/drug-trace-scope-dev-20261027/view-tuning-20261029/sql/fbi_dba_option.sql:28)；[Oracle 11g TO_CHAR(number)](https://docs.oracle.com/cd/E18283_01/server.112/e17118/functions201.htm)。

11. **日期侧漏查了 `NLS_CALENDAR`；单独强调 `NLS_DATE_LANGUAGE` 不够准确。**

    当前日期掩码全部是数字，没有月份名称或星期名称，因此通常的中英文 `NLS_DATE_LANGUAGE` 差异不是这里的主要问题；显式掩码也避免了默认 `NLS_DATE_FORMAT` 的直接影响。

    但历法设置仍需检查：**有 `YYYY-MM-DD` 掩码，不等于已经固定为公历文本**。这不仅关系索引匹配，还关系 `source_bill_id` 本身是否稳定。   
    出处：[原始日期表达式:10](/F:/python/blyb/docs/development/drug-trace-content-contract-20261022/sql/draft_normal_outpatient_views_r10.sql:10)、[方案:72](/F:/python/blyb/docs/development/drug-trace-scope-dev-20261027/view-tuning-20261029/review/round-1/方案.md:72)；[Oracle 11g 日期格式与语言说明](https://docs.oracle.com/cd/E11882_01/server.112/e41084/sql_elements004.htm)、[Oracle 对历法影响日期格式元素的说明](https://docs.oracle.com/en/database/oracle/oracle-database/26/sqlrf/Format-Models.html)。

12. **“按库默认 NLS 创建，再确认走索引”不足以验证结果正确。**

    Oracle 对涉及字符转换的 FBI 明确提示过：会话 NLS 改变可能导致查询结果错误，不能只把 NLS 当成“索引是否使用”的性能问题。

    本项目使用 Thick 模式，客户端语言和地域设置可能影响会话；本地连接代码中也没有建立明确的 NLS 固定策略。后续应读取实际应用会话的 NLS，核对数据库及 DBA 会话，并在一致数据上比较索引路径与原计算路径的结果；还需覆盖建索引后的新增、更新记录。   
    出处：[连接代码:333](/F:/python/blyb/implementation/plugins/sources/db_view/drug_trace.py:333)、[现有 NLS 建议:62](/F:/python/blyb/docs/development/drug-trace-scope-dev-20261027/view-tuning-20261029/README.md:62)；[Oracle FBI 的 NLS 注意事项](https://docs.oracle.com/cd/E11882_01/server.112/e41084/statements_5012.htm)、[python-oracledb Thick 模式 NLS 说明](https://python-oracledb.readthedocs.io/en/latest/user_guide/globalization.html)。

13. **脚本里的 `FM999999999999999999` 修正示例不能直接采用。**

    固定整数格式可能对小数发生舍入，对超出格式宽度的值产生 `#`，也可能与现有文本表示不同。只修改 FBI 会面临表达式不匹配；同步修改视图则可能改变已经用于本地身份映射、历史关联和游标的 `source_bill_id`。

    所以这属于需要证明兼容性的键格式变更，不能视为顺手补一个 NLS 参数。   
    出处：[修正示例:28](/F:/python/blyb/docs/development/drug-trace-scope-dev-20261027/view-tuning-20261029/sql/fbi_dba_option.sql:28)、[复合身份键:80](/F:/python/blyb/implementation/plugins/domains/drug_trace/ingest.py:80)；[Oracle 数字格式的舍入与溢出规则](https://docs.oracle.com/cd/E11882_01/server.112/e41084/sql_elements004.htm)。

14. **“必须逐字符一致才能匹配”是错误表述。**

    Oracle 比较的是解析后的表达式树，忽略大小写和空白，并非逐字符比较源码。当前方案里是否多一个空格、是否使用表别名，不是问题。

    需要保持的是优化器能够识别的表达式对应关系；增加格式参数、CAST 或其他转换则需要重新验证，不能认为数学或业务意义相同就必然匹配。   
    出处：[报告:62](/F:/python/blyb/docs/development/drug-trace-scope-dev-20261027/view-tuning-20261029/README.md:62)、[脚本:7](/F:/python/blyb/docs/development/drug-trace-scope-dev-20261027/view-tuning-20261029/sql/fbi_dba_option.sql:7)；[Oracle 11g 表达式树匹配说明](https://docs.oracle.com/cd/E11882_01/server.112/e40540/indexiot.htm)。

15. **这是非唯一索引，报告中的“索引唯一范围扫”不准确。**

    DDL 是 `CREATE INDEX`，没有 `UNIQUE`，预期应描述为非唯一索引上的等值 `INDEX RANGE SCAN`。即使实际结果最多一行，也不等于 `INDEX UNIQUE SCAN`。

    为查询提速没有必要擅自改成唯一索引；唯一性会新增写入约束，超出本方案目的。   
    出处：[DDL:8](/F:/python/blyb/docs/development/drug-trace-scope-dev-20261027/view-tuning-20261029/sql/fbi_dba_option.sql:8)、[报告:58](/F:/python/blyb/docs/development/drug-trace-scope-dev-20261027/view-tuning-20261029/README.md:58)；[Oracle 唯一与非唯一索引说明](https://docs.oracle.com/cd/E11882_01/server.112/e40540/indexiot.htm)。

16. **“表结构零改动”需要限定为业务可见结构。**

    FBI 可以保持现有业务列、约束和视图列契约不变，但它会增加数据库对象和维护成本；Oracle 还会用隐藏虚拟列表示函数索引表达式。因此数据字典层面并不是绝对零变化。

    如果“不影响结构”指业务接口和可见列不变，这条路线可以符合目标；如果指不新增任何数据库结构，则不符合。   
    出处：[方案:22](/F:/python/blyb/docs/development/drug-trace-scope-dev-20261027/view-tuning-20261029/review/round-1/方案.md:22)；[Oracle Ask TOM 对 FBI 隐藏列的说明](https://asktom.oracle.com/ords/asktom.search?tag=virtual-columns-in-oracle-11g)。

17. **ONLINE 可以允许并发 DML，但不能写成“对写入没有影响”。**

    建索引需要读取、计算和排序历史数据，并处理并发变化，会消耗 CPU、I/O、临时空间及其他资源；持续时间也受并发 DML 影响。Oracle 11g 对在线建索引还有并行 DML 等限制。

    也应避免套用旧版本知识：Oracle 官方说明，11g 已改进此前在线建索引首尾需要的 DML 阻塞锁。所以准确结论应依据目标小版本和现场负载，不能笼统保证零影响，也不能笼统断言整个过程中禁止写入。   
    出处：[脚本“ONLINE 不锁写”:6](/F:/python/blyb/docs/development/drug-trace-scope-dev-20261027/view-tuning-20261029/sql/fbi_dba_option.sql:6)；[11g 在线建索引说明](https://docs.oracle.com/cd/E24693_01/server.11203/e17120/indexes003.htm)、[Oracle 11g 在线索引改进说明](https://docs.oracle.com/cd/B28359_01/server.111/b28279.pdf)。

18. **仅写“Oracle 11g”，不足以确认 ONLINE 的环境条件。**

    方案没有给出准确版本、Edition 和功能支持核验。11g 官方版本功能表中，在线索引重建属于 EE 功能，不能把标准版和企业版视为相同环境。

    应由 DBA 确认目标实例对拟执行 ONLINE 操作的支持、权限和表空间额度。   
    出处：[方案环境描述:15](/F:/python/blyb/docs/development/drug-trace-scope-dev-20261027/view-tuning-20261029/review/round-1/方案.md:15)；[Oracle 11g 版本功能表](https://docs.oracle.com/cd/E11882_01/license.112/e47877/editions.htm)。

19. **“日增约三千行，因此写入代价可忽略”证据不足。**

    净增行数不能代表实际 INSERT、DELETE、更新索引键以及峰值并发量。这个索引覆盖整张主表，会参与其他业务写入，不能只按上报单据量估算。

    我的判断是：一个短字符串 B-tree 的长期维护代价可能可接受，但应以 HIS 写入延迟、资源占用和峰值表现确认，现稿不能直接写“可忽略”。   
    出处：[代价结论:66](/F:/python/blyb/docs/development/drug-trace-scope-dev-20261027/view-tuning-20261029/README.md:66)；[Oracle 对额外索引增加 DML 成本的说明](https://docs.oracle.com/cd/E11882_01/server.112/e40540/indexiot.htm)。

20. **400–800MB 可以作为粗预算，不能称为已核定空间需求；“视行迁移率”也不是恰当依据。**

    按样本计算，键长为日期 19 字节、两个分隔符、5 位处方号和 6 位药房，共 32 字节；702 万行的键文本本身约 `214.5MiB`，还需加 ROWID、条目和块开销、空闲比例及分配开销。

    因而几百 MB 的量级并不离谱，但最终索引空间与创建峰值空间是两回事，还需预算排序临时空间及在线构建的额外占用。可结合真实键长和近期统计，用 `DBMS_SPACE.CREATE_INDEX_COST` 辅助估算。   
    出处：[空间估算:66](/F:/python/blyb/docs/development/drug-trace-scope-dev-20261027/view-tuning-20261029/README.md:66)；[Oracle 11g 索引容量估算](https://docs.oracle.com/cd/E18283_01/server.112/e17120/schema007.htm)。

21. **README 与执行 SQL 的 NOLOGGING 策略不一致。**

    README 示例含 `ONLINE NOLOGGING`，待审方案和 `fbi_dba_option.sql` 没有 `NOLOGGING`。DBA 从不同文件复制，会得到不同的恢复策略。

    如果使用 NOLOGGING，需明确数据库强制日志、备库和备份恢复条件；Oracle 文档也要求对这类创建操作考虑后续备份。建议收敛为一份明确的最终 DDL。   
    出处：[README:53](/F:/python/blyb/docs/development/drug-trace-scope-dev-20261027/view-tuning-20261029/README.md:53)、[执行 SQL:8](/F:/python/blyb/docs/development/drug-trace-scope-dev-20261027/view-tuning-20261029/sql/fbi_dba_option.sql:8)；[Oracle NOLOGGING 说明](https://docs.oracle.com/cd/E11882_01/server.112/e25494/indexes.htm)。

22. **“外层 hint 无效果”的测量成立，但“已证明 hint 被忽略”的解释尚不成立。**

    日志显示加 hint 前后都约 1.1 秒，可以说明这次尝试没有明显收益；但日志没有展示 SQL 全文、查询块绑定情况或实际执行计划，不能单凭耗时相近确定原因就是“别名未绑定、被静默忽略”。

    同样，也不能用这次外层 hint 的结果排除视图内部或正确定位查询块的 hint。   
    出处：[计时证据](/F:/python/blyb/docs/development/drug-trace-scope-dev-20261027/view-tuning-20261029/evidence/hint-test-20261031T023339Z.txt)、[报告解释:125](/F:/python/blyb/docs/development/drug-trace-scope-dev-20261027/view-tuning-20261029/README.md:125)。

23. **“视图内 hint 无法只读验证”说得过宽；“只改视图绝无解”也没有被现有实验充分证明。**

    应用账号没有基表权限、连接器拦注释，确实限制了当前通道。但有基表读取权限的 DBA 可以直接执行展开后的、带内部 hint 的 SELECT，进行计划和计时验证，**不必先替换生产视图**。

    对单表拼接条件，没有证据表明换一种普通视图写法就能自动变成主键点查；这个判断合理。但“任何视图写法都一样”“1.1 秒是下限”“FBI 是唯一确定方案”，均超出了已测试范围。覆盖索引扫描等其他访问方式是否有收益，也应以计划判断。   
    出处：[报告:132](/F:/python/blyb/docs/development/drug-trace-scope-dev-20261027/view-tuning-20261029/README.md:132)、[hint 备选方案:69](/F:/python/blyb/docs/development/drug-trace-scope-dev-20261027/view-tuning-20261029/README.md:69)。

24. **如果未来允许改取数层，首先应复用现有主键，无须先建新的三列普通索引。**

    仓库记录已有唯一索引 `PK(PRESC_DATE,PRESC_NO)`，而且拆分查询已经很快。在该唯一性确实成立的前提下，这两个条件已经能定位至多一行，`DISPENSARY` 可继续作为药房隔离校验条件。

    因此，“拆分原始键查询”和“新增 `(PRESC_DATE,PRESC_NO,DISPENSARY)` 索引”应分开评估。前者有长期价值，后者未证明必要。不过当前方案限定应用模板不可改，所以它是后续设计选项。   
    出处：[现有主键及实测:20](/F:/python/blyb/docs/development/drug-trace-scope-dev-20261027/view-tuning-20261029/README.md:20)、[应用不可改约束:44](/F:/python/blyb/docs/development/drug-trace-scope-dev-20261027/view-tuning-20261029/review/round-1/方案.md:44)。

25. **不应为了“把函数移到另一侧”而改变连接语义。**

    将连接改成 `TO_NUMBER(T.TRACE_BILLNO)=M.PRESC_NO`，可能把原先不同的文本值，如 `00123` 与 `123`，当成相同值，也可能因非数字记录报错。

    报告对住院日期连接建议直接 `TO_DATE(d.DISP_DATE,...)`，同样需要验证：原字符串比较遇到脏值可能只是无法匹配，转换后则可能导致整条查询失败。此类改写必须证明结果等价，不能只证明能使用索引。   
    出处：[现有字符串连接:78](/F:/python/blyb/docs/development/drug-trace-content-contract-20261022/sql/draft_normal_outpatient_views_r10.sql:78)、[住院日期转换建议:97](/F:/python/blyb/docs/development/drug-trace-scope-dev-20261027/view-tuning-20261029/README.md:97)。

26. **这个 FBI 只覆盖 r10 门诊，不能解决住院分支的独立性能问题。**

    r11 使用另外的申请表、发药主表及不同拼接键，还包含 UNION ALL 和日期字符串连接。建在 `DRUG_PRESC_MASTER` 上的这个索引无法直接优化它们。

    但当前日批驱动主流程确实只筛选 `PATIENT_DISPENSE`，所以住院未覆盖并不否定 FBI 对当前门诊批次的价值；需要收回的是“整个追溯链路已经治本”的泛化结论。   
    出处：[r11 视图:32](/F:/python/blyb/docs/development/drug-trace-scope-dev-20261027/sql/r11_inpatient_views_v3.sql:32)、[住院中止证据:15](/F:/python/blyb/docs/development/drug-trace-scope-dev-20261027/evidence/deploy/PHASE4-DEFERRED.md:15)、[日批候选筛选:477](/F:/python/blyb/docs/development/drug-trace-timed-report-20261024/daily_upload_driver.py:477)。

27. **“计数相同”和“哈希相同”不足以证明结果完全不变。**

    F/G 与原查询的计数相同，不能证明返回的是同一组码。557 行的 ORA_HASH 汇总相同可以作为辅助证据，也不能替代逐列、多重集比较，更没有验证尚未创建的 FBI。

    如果后续修改视图，应在一致数据快照下核对所有输出列及重复次数；如果只建 FBI，则重点验证不同 NLS、空结果、多码及新增更新数据下，索引访问与原计算路径是否一致。   
    出处：[计数等价结论:24](/F:/python/blyb/docs/development/drug-trace-scope-dev-20261027/view-tuning-20261029/README.md:24)、[哈希记录:128](/F:/python/blyb/docs/development/drug-trace-scope-dev-20261027/view-tuning-20261029/README.md:128)。

28. **索引不能修复已有失败运行的复用问题，也不能保证日报 outcome=OK。**

    驱动按日期窗口匹配已有采集运行；如果匹配到 FAILED/CANCELLED，会返回 `collect_not_done`。已有运维文档记载过这个问题。即使查询加速，错误的运行选择仍会失败。

    此外，发送阶段还受总预算、排队、平台响应和业务拒绝影响；回看阶段的失败又不会改变主退出码。因此“建完索引，次日日报应直接 OK”不是完整验收标准。   
    出处：[运行匹配:420](/F:/python/blyb/docs/development/drug-trace-timed-report-20261024/daily_upload_driver.py:420)、[已知复用问题:35](/F:/python/blyb/docs/development/drug-trace-timed-report-20261024/README.md:35)、[最终 outcome 判定:572](/F:/python/blyb/docs/development/drug-trace-timed-report-20261024/daily_upload_driver.py:572)。

29. **查询提速也不等于采集业务完整性已经验收。**

    当前主单的 `updated_at` 是发药时间，码表修改不会直接推进它；`expected_code_count` 仍是 NULL。迟到码、改码和完整码数确认，仍依赖回看机制及其他业务证据。FBI 不会补上这些语义。

    所以后续性能验收应同时核对单据键、码集合和质量状态没有变化，不能仅以“采集 DONE、速度变快”认定结果完整。   
    出处：[r10 主单时间和码数定义:27](/F:/python/blyb/docs/development/drug-trace-content-contract-20261022/sql/draft_normal_outpatient_views_r10.sql:27)、[回看调用:566](/F:/python/blyb/docs/development/drug-trace-timed-report-20261024/daily_upload_driver.py:566)。

30. **上线验证设计可以更稳妥：先评估不可见索引，再决定是否公开给全部 SQL。**

    Oracle 11g 已支持不可见索引，可由验证会话启用，用于比较原始应用 SQL 的实际计划和结果，降低立即影响其他 SQL 计划的范围。它仍会参与 DML 维护，因此不能用它证明“没有写入代价”。

    对这份方案，我认为后续至少需要拿到：**真实模板的两类查询计划与运行统计、NLS 下的结果一致性、完整采集窗口耗时分解、HIS 写入影响**，才能把当前结论升级为“已验证可以解决超时”。这些证据目前均不完整。   
    出处：[当前上线顺序:110](/F:/python/blyb/docs/development/drug-trace-scope-dev-20261027/view-tuning-20261029/README.md:110)；[Oracle 11g 不可见索引说明](https://docs.oracle.com/cd/E11882_01/server.112/e25494/indexes.htm)。
