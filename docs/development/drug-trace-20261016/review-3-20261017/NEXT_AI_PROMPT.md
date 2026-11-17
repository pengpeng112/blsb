# 下一AI：FIX3剩余链路修复

在F:\python\blyb继续，不重建Python/Vue/PG平台。先读AGENTS.md、docs/ai状态/交接、review-3-20261017/README及REVIEW。登记真实工具/模型与实际范围，顺序接力。当前HEAD仍0ca47c6且FIX2大量修改未提交；务必保存当前工作区基线，不能重置/覆盖。旧报告、原始视图和冻结payload保留，新迁移从011及后续空闲编号（先查编号）。

本轮复核：最新原局部33/0/0通过，独立14反例仍复现。不要把“测试通过”当7组剩余问题已消失。按以下FIX3顺序修代码、补正确行为测试，并交逐项处置证据。

## FIX3-01 企业身份与查询请求

医院必须有分离的ref_ent_id和ent_id批准字典：上传ref_user_id来自ref_ent_id，库存from/to医院一侧来自ent_id，供应商一侧也是ent_id。采购与退供应商方向分别测试，故意让两种医院ID完全不同防止测试同值掩盖错误。query.billstatus使用其独立bill_type选择器及创建日期/分页契约，不直接把323/102当查询类型；若平台明确支持其他值留版本证据。

验收G3-01：双ID+双方向正确；G3-02：上传与query字段枚举独立、日期时区明确，mock使用真实形状。

## FIX3-02 来源分页与运行事务

禁止SQL拼接键与独立列ORDER BY混用。使用等值前缀逐字段keyset，含updated_at/source_system/pharmacy/business_kind/source_bill_id，Python和SQL共享同一排序契约。加入YF1/YF10、源系统前缀、含分隔符、同刻多页及重复/不推进页测试。

安全点不要在无checkpoint时隐式commit整页数据。明确选择整窗事务，或“每完整单/页快照+outbox+checkpoint同一事务”的可恢复策略，记录ADR并保持不漏不重语义。暂停/撤销检查用独立只读会话或显式refresh/expire等保证看到其他事务，避免expire_on_commit=False缓存；逐页/重取/真实连接前都查run、证据、connector和范围。不能把取消改成暂停或把PAUSED覆盖DONE。

按批准connector配置重建dsn_env/schema/SecretRef/对象白名单，API不丢连接绑定；采集profile/租户/owner范围有效，历史补报与增量独立checkpoint。更正/迟到/失败重取保持持久恢复。

验收G3-03：不同键长度同刻多页无漏；G3-04：独立session暂停/撤销后安全点实际停；G3-05：第二页异常，已保存快照与checkpoint原子一致，重启不漏；G3-06：source连接/范围不能跨connector或tenant。

## FIX3-03 受理后轮询与查询错误

ACCEPTED后自动排第一代query，与发送结果/outbox在可恢复事务内；UNKNOWN按策略查询；PROCESSING、临时查询失败、传播延迟NOT_FOUND均有受限退避，达到上限明确告警/人工而不是静默结束。查询只读身份/原配置固定。

区分found=false（完整成功查询确实无匹配）、权限/参数错误、网络未知、不完整分页；error_response不能变NOT_FOUND，任何一页失败不能用已收集半份数据判成功。缺total_num的满页也递增page；重复页/不推进/超页/总时间预算明确受控，不无限循环。

验收G3-07：正常受理不点击UI也能到终态；G3-08：权限失败不是无记录，晚页错误不可CONFIRMED；G3-09：无total满页翻到page2，重复/空缺页等不挂死。

## FIX3-04 真实批准与配置闭包

EvidenceBody/API/UI支持config_release_id、connector/机构、药房、profile/业务、窗口、单据/批量上限、批准与失效信息。正式证据不能空配置当通配、不能空scope当全开放。药房/配额须实际逐单复核，配额要事务并发预留/计数，不能只存字段；时间按一致时区比较，不能replace(tzinfo=None)丢偏移。

prepare及build_client均校验ConfigRevision tenant/owner/state=PUBLISHED及profile绑定，SecretRef也受权限；不能读取其他租户或草稿配置。字典取值和其revision必须来自同一条实际命中记录，保存值/批准版本/digest。旧提交query/reconcile也用原机构身份，不能换目标当前配置查询。

配置漂移或旧未发送提交需要重prepare时，提供明确安全换代机制：PREPARED可在取消旧未发送尝试后创建新的不可变提交/配置代次；受理/UNKNOWN不按新配置盲重发。禁止“提示重新prepare却永远返回旧包”死路，且不原地改旧payload。

验收G3-10：API/UI实际传递审批范围与精确配置；G3-11：空范围/非批准药房/超配额/时区边界/并发批量拒绝；G3-12：跨tenant/owner/DRAFT配置拒绝；G3-13：旧批准字典+新草稿审计仍指向真正旧版本；G3-14：未发送配置变更可安全换代，已受理或UNKNOWN不可重发。

## FIX3-05 对账必须完整

query与reconcile复用一个经过G3-08/09验证的只读全分页客户端；两条路径不能一条修好、另一条仍page1/无日期。分页不完整或权限失败→UNVERIFIED，不能missing_remote/matched/可补报。独立平台快照范围与实际tenant/connector/profile/机构/药房/窗口一致。

要检测远端额外单据需有经确认的范围枚举能力；没有则明确“仅本地已知单据逐单核对，未检查平台额外单据”。没有本地Submission的源单也不能未经查明就按漏报补发；使用稳定bill_code映射查询。码级能力缺失继续显式未核验。

验收G3-15：total=2只取得1项不得matched；G3-16：错误/多页/额外单/多profile、多目标与配置identity范围测试；G3-17：补报必须基于完整独立证据和批准。

## FIX3-06 运维、迁移与交付

本机隔离PG完成真正backup→restore→对象/交换/配置/令牌/任务校验；无需等待生产维护窗口才做本机演练。旧009/010库真实升级与复合tenant FK/坏数据约束测试；复合唯一索引不代替外键。修transport/DMZ硬deadline，按剩余预算读并上限响应，测试持续慢流；Anolis运行材料/服务权限仍须同OS验证，缺材料如实标阻断。

把本包14反例转换为正确业务测试，覆盖新增G3-01—19：G3-18真实PG恢复/旧库迁移，G3-19慢流deadline与禁发浏览器闭环。按改动局部通过再必要全量回归；保留33/0/0和历史305/6证据，不修改错误行为来维持旧断言。

提交逐项R3处置、真实命令/XML/log、离线发行manifest、浏览器/恢复证据和外部清单。更新现有四类实施文档/API矩阵与共享STATE/BACKLOG/HANDOFF，单任务交接后end。

## 真实接入边界

SIGNATURE_VERIFIED仍False，须官方SDK/向量一致证据后随受审适配器版本启用，不能单独改常量。HIS readonly/准确服务名/实际视图、平台AppKey/权限/企业映射、DMZ443/DNS/证书/NTP/运行材料、历史口径/首批授权等EXT-01—12仍逐项关闭。

本地可逆实现/测试/构建持续做；本提示词不授权活库DDL、全历史补报、把合成单发正式平台、无窗口升级现网。材料齐后现场先禁发升级和只读预览，再按明确机构/药房/时间/配额小批真实单据验证、终态查询、对账签认。分别报告代码、现场、业务三种完成状态。
