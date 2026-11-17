# 下一AI一次性开发执行提示词

下面正文可整体复制。它要求完成可执行开发，不把外部资料缺失包装成已上线。

---

你在F:\python\blyb接续开发药品追溯上报。基础平台已由上一工具部署，当前已核实发行20261016T114735Z、6服务healthy，但未配置采集源/上报目标/任务，药品追溯与DMZ转发尚未实现。不要重建平台、换语言或重新安装<prod-host>基础栈。

先读根AGENTS.md、docs/ai/START_HERE.md、STATE.md、HANDOFF.md和自己的相关子目录AGENTS；再读docs/development/drug-trace-20261016/下README、DEPLOYMENT_REVIEW、DEVELOPMENT_PLAN、ACCEPTANCE、EXTERNAL_INPUTS、SQL_REVIEW。设计基线v0.4，业务源码implementation。旧部署计划是历史依据，不按它的“部署进行中”旧状态重做。

先运行`python scripts/ai_session.py status`，确认无人占用，按真实工具/模型登记DTR-IMPLEMENT-20261016及实际修改范围。顺序接力，不自动多代理并行；未知模型写unknown。建立可恢复Git基线和待改文件哈希，不覆盖其他人的修改。

按开发计划W00—W09持续实施：

1. 先修TLS证书SAN/验证、备份一致性与恢复路径/compose问题；加入独立项目/目录恢复演练，绝不覆盖生产目录。药品真实能力必须有明确必需证据，空ExternalBlocker表不能放行；保留病理既有规则。
2. 在plugins/domains/drug_trace、sources/db_view、destinations/alihealth_drugtrace增量开发。复用SourceRecord/Checkpoint、配置release、Job/lease/outbox、PackageRevision、Submission/Attempt/Receipt、Reconciliation、鉴权审计。把manifest、worker jobs/events、目标派发和领域payload实际接通，不能只创建插件目录。
3. 两个独立profile都交付离线链路：用户指定uploadinoutbill用于供应商流转；uploadretail用于患者发退药。默认正式停用，只有获批准且材料齐备的分支才激活。不能把OUT一概当供应商出库。
4. 用户新给的MED_TRACE_RECORD历史SQL见SQL_REVIEW。MAX(T_TRACE_CODE)可能丢多码，LISTAGG(PRESC_DETAILID)不是稳定单据号；核对RECORD_ID/TRACE_BILLNO/处方明细关系、F/S/T层级、完成/删除/退药、跨药房键。交revision5视图契约与合成样例，不原地改revision4。不要盲目写JOIN或在HIS建视图。时间用绑定的半开区间，长期增量使用可靠事件序列或统一updated_at复合游标。
5. 009起新增迁移，先查编号，SQLite与真实隔离PG都测；整单快照+checkpoint/job同事务。0/1/N、前导零、tenant/owner、配置revision固定、quality等待、dry-run零外部副作用不能放松。
6. 完成TOP协议adapter、两上传响应与查询解析、签名/SDK验证、发送前持久化、超时UNKNOWN先查询、部分成功/乱序回执、逐单逐码对账和审计。未知能力显式阻断，不能用Mock代替真实adapter。正式SDK/签名材料缺失时仅该项阻断，其他模块照常完成并留下精确缺项。
7. 交付DMZ专用HTTPS转发与离线安装/回退：固定eco.taobao.com/router/rest，<prod-host> mTLS身份、方法白名单、原字节保持、无缓存/无自动POST重试、TLS校验、限流和日志脱敏。Anolis7.9当前无nginx/docker/python3，先选兼容材料，保留既有8001。
8. 交完整API、Vue药品总览/单据/质量/提交/对账/就绪配置，沿用现有角色、同源和密钥引用；用浏览器跑合成闭环，不仅npm build。
9. 按ACCEPTANCE留下OPS、DTR-A/B/C/D/E/S真实命令与断言证据；局部通过后跑必要全量回归，历史229passed/6skipped不是新结果。完成发行白名单/镜像/离线依赖与hash manifest、升级回滚runbook、四类实施文档和API矩阵、STATE/BACKLOG/HANDOFF及一个任务交接。

现场现状：<prod-host>通过<jump-host>:<ssh-port>普通二跳SSH可登录；<prod-host>→HIS1521TCP通但未认证。HIS服务名存在his/hisuser/orcl冲突，<relay-jump>数据资产受控连接目前medical_dict_push不满足readonly，不能绕过或擅自修改登记。现有7表232列仅目录信息，未验证活库。

DMZ<relay-net-host>可登录，443/8443转发尚未监听，强制IPv4到eco:443仍超时，HTTP80可达不是HTTPS验收；两机NTP未同步、DMZ约慢32分钟。<prod-host>现有自签证书缺IP SAN，即使信任证书也不能按IP通过验证。先交代码/配置模板和离线测试，现场网络/证书/资料阻断只影响相关步骤。

凭据只从本机受限`.ai/private/reporting-20261015/deployment-login.private.json`读取；不要回显或放进命令行、Git、日志、构建上下文。API管理令牌保存在目标/etc/blyb/runtime.env。主机指纹必须匹配既有证据；不绕过跳板direct-tcpip限制。用户此前授权只读核查不等于授权无限真实采集/发送。

可逆本地实现、测试、构建无需逐项询问。不得因为缺AppKey/HIS视图就只写文档退出；完成所有不依赖它们的实现和验证。真实HIS连接、DDL、正式平台发送、现网重启/升级按对应资料和明确范围/窗口执行；缺项交外部阻断表，不能发合成单去生产平台试接口。代码可部署为业务禁用模式；实际启用/真实业务验收另列。

最终交付必须分别报告：已实测通过、实现未测、外部阻断；列实际代码/迁移/发行包位置、测试日志、浏览器和恢复证据、未关闭EXT项、现场是否改动、如何停发与回退。不要写“全部完成”掩盖尚未签认的真实上报。
