# DTR-HIS-DDL-DMZ-CLOCK-20261022

工具/模型：Codex / unknown；session：20261022T072539Z-2519dffd。
用户明确要求：登录数据库核查，给创建视图/用户命令由用户执行，并先纠正DMZ时间。

交付：[HIS执行包与校时说明](../../deployment/his-ddl-clock-20261022/README.md)。
范围：本轮docs/SQL/探针、共享docs/ai、受限基线；未改业务源码、未在HIS执行DDL/DML或上传。

HIS：用户在知晓medical_dict_push后追加本次明确连接请求，按即时有限字典/零行解析只读范围执行。
复用sjzc标准连接器，凭据不出<relay-jump>；原source登记策略不修改，不作为生产采集readonly验收。
5条字典SELECT全成功（连接his、3源表VALID、20所需字段、目标用户未见、USERS/TEMP、19索引列）。
两目标视图当前账号未见；新定义完整SELECT投影通过Oracle零行解析（码视图首轮成功、主视图r2成功）。
主视图首轮内联SELECT的字符串分号被连接器规则拒绝，改等价CHR(59)通过；未降低只读校验。
另DUAL常量SELECT证明首次空游标比较匹配0，兼容修复已定位并交NEXT_AI_SOURCE_FIX，源码未改。

SQL交付：新r8结构/质量等待版，不修改r7原件；修t_trace_code/NULL键/类型/原始VARCHAR2记录ID；
锁定owner+应用reader、hidden密码、最小角色权限、跨schema owner基表SELECT WITH GRANT OPTION、
两个私有同义词、DBA/reader验证及注释回退。UNKNOWN/finalized/数量等未批准项不伪造；
仅字段定义解析实测，账号/视图创建授权仍由用户执行。

DMZ：precheck首轮私有Python无系统CA链失败（未改时钟），指定Anolis系统CA后r2成功。
apply按两次严格TLS HTTPS Date与本机UTC交叉核对，向前校正1946.3秒，校后差0.57秒。
service verify退出0，mTLS200/无证书拒绝；转发PID23519及Java8001 PID14438保持，
chrony/forwarder配置哈希不变、零服务重启。长期NTP仍无可达源，不冒称已恢复自动同步。
命令：fix_dmz_clock.py check / check-r2 / apply，退出1/0/0，失败证据保留。

HIS命令：数据资产venv运行his_inspect.py（默认字典）/compile/compile-r2/null-semantics，
退出0/1/0/0；compile首轮一个失败一个成功，分号原因与修订说明在README。
本地静态检查/脚本契约/文件链接/哈希证据见任务evidence/local-validation.json。
未重跑业务测试，不沿用上棒405/7/0声称本轮全量通过。

共享STATE/HANDOFF/BACKLOG更新：新增r8执行入口、有限活库证据及一次校时；
NTP自动同步/正式证书/业务规则/首次游标兼容和真实源绑定/首单仍未完成。
