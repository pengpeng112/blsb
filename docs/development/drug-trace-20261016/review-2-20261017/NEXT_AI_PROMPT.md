# 下一AI执行提示词：修复真实对接缺口

请在F:\python\blyb顺序接续开发。遵守根AGENTS.md及docs/ai入口，先status再登记实际工具/模型和范围，不自动启多代理、不覆盖其他工具。当前参考Git基线0ca47c6；开工核对新的HEAD，已有修改保留。

先读docs/development/drug-trace-20261016/review-2-20261017/README.md、REVIEW.md、REMEDIATION_PLAN.md与evidence/probe-results.json。上一轮277/6和浏览器Mock闭环不是正式适配器完成证据。本轮审查16个离线观测复现缺陷，详见probe_review.py。不要只把报告“小项”修完就上线，不要重建平台或换语言。

按FIX2-00—08实施全部可离线工作，保留旧源码/证据基线：

1. 请求字段和必填按官方协议修正；两profile不混用。修正response_success与查询嵌套/创建日期/全分页，部分成功不能CONFIRMED。Mock与独立fixture都使用真实协议，删除错误期望需以正确业务断言替代。
2. 修Mock密钥前缀绕过、配置revision和精确证据/目标绑定；加入校时、历史范围、真实发送授权门禁。固定字典/目标/密钥引用版本；重复prepare返回真实绑定版本。SDK向量未齐正式阻断，不靠把常量改True。
3. Oracle适配真实大写列名、日期/Decimal/布尔/空串；固定完整来源主键/SQL游标，修source_system拼接差异和分页不推进；变更跳过单必须持久重取，不只依赖短回看。按connector绑定受限只读连接，r6视图草案保留r5原文。
4. worker执行时检查暂停/撤销/来源与授权，不把PAUSED改RUNNING；统一send状态守卫，UNKNOWN/SENDING查不重发；Submission CAS/lease fencing；dry-run不改业务状态。轮询/手动重查使用新代次，回执保留每次状态变化。
5. 真对账接独立平台快照并完整过滤profile/目标/版本/时间；没有远端证据不能matched，码级能力不足明确未核验。差异处置和补报有理由/批准/查询证明。
6. 应用RealTransport加载客户端证书并与本地DMZ及模拟上游集成测试，禁重定向/隐式重试；DMZ匹配实际Python/Anolis7.9/systemd/低端口权限，修TOP方法白名单和资源超时/响应上限，离线材料有哈希，不用el8包盲装el7。
7. 备份冻结所有实际写入口（storage不能遗漏）、自定义对象路径/产物权限/失败恢复；restore项目名和祖先子孙路径保护；真实隔离PG做完整备份→恢复→对象/任务/令牌校验，不只fake docker。
8. 增加tenant复合外键/owner和连接器隔离A15、父子码阻断、输入/分页/UI/登录修复。迁移从010及后续空闲编号，先查编号，不原地重写009；保留旧版回退兼容。

把探针反例转换为预期正确的测试，16观测只证明当前缺陷，不可“probe退出0”冒充修复通过。按完善计划FIX2-T01—T29留日志/XML，局部通过后必要全量回归和浏览器闭环。测试只用临时库/隔离PG/本地TLS模拟，不连医院生产、不开公网业务。现有Windows PATH可能优先选中坏WSL bash，参考evidence/run_local_tests.py使用已经安装的Git Bash/OpenSSL，不为环境失败删断言。

持续完成本地可逆实现/测试/构建，无需按函数询问。真正缺官方SDK/院方材料的环节精确标EXT阻断，其他代码继续做；不以“等密钥”跳过协议、状态、暂停和恢复修复。

SITE2/LIVE2需对应维护窗口/只读来源资料/首批真实发送范围授权，详见EXT-01—12。此次开发提示词不授权在HIS建视图、不授权全历史重报、不授权向正式平台发送合成单。已有凭据仍只保存在本机受限私有目录，不能放入Git/命令行/日志/发行包。

收尾交R2-01—16处置矩阵、代码/迁移、真实测试命令与结果、SDK/OS/mTLS/PG恢复证据、浏览器截图、离线发行manifest、停发/回退操作、未关闭EXT清单，并更新现有四类实施文档/API矩阵及STATE/BACKLOG/HANDOFF，建立单任务交接后end。明确区分离线实现、现场验收和业务签认，未做的不写通过。
