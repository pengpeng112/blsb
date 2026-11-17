# 追溯平台是否收到：人工只读核查

**最新结论（2026-10-26 18:18:41）：247笔全部查清，41笔平台确认成功，206笔按原身份/单据号查询未找到。** 原下午143/63部分报告保留在下方；18:12用户要求继续后实测额度已恢复，有限核查完成。仅查询，本地状态未回写、未重发。

用户2026-10-26要求解释“结果待核查”并查询追溯平台是否已收到。本轮查询限已存在的206 UNKNOWN、41 ACCEPTED，另加已确认单据作正对照；不连接HIS、不上传、不重发、不更改业务库或自动查询预算。

## 为什么会显示“结果待核查”

UNKNOWN表示原发送没有取得可确认的业务结果。不是“平台确认未收到”，也不是“上传成功”。只有平台返回匹配同一企业、单据号和类型的业务状态，才能进一步判断。

15:12读取生产账本：206 UNKNOWN中205次发送HTTP429、1次send-failure:FileNotFoundError；41 ACCEPTED均有HTTP200受理记录。最后一轮历史查询：UNKNOWN中126因HTTP429未核实、80完整查询OK但无匹配单据；41 ACCEPTED最后查询也均HTTP429。247笔均已达到原自动查询12轮上限。

这些都是10-24业务单据，不是今日界面升级产生的新错误。自动轮询停止后，平台后来处理完成，本地可能仍保留ACCEPTED。因此要复核平台，不能把旧状态直接当作平台当前状态。

## 本次真实查询方式

链路为<prod-host> worker→DMZ HTTPS/mTLS专用转发→淘宝平台；使用现有批准身份、冻结payload的ref_ent_id/bill_code/query_bill_type和冻结配置。仅允许TOP方法alibaba.alihealth.drugtrace.top.yljg.query.billstatus；在调用transport之前检查表单method，任何上传方法立即拒绝。

应用PostgreSQL事务设置READ ONLY，只读取冻结单据和已批准query证据；不调用会推进状态或排新任务的perform_query，也不清除/重置12轮查询预算。这是用户本次单独授权的有限人工核查，结果独立留档，**看板状态暂不回写**。

首次sample4笔：已确认正对照1笔仍3/51；UNKNOWN两笔均NOT_FOUND；ACCEPTED一笔3/51。账本摘要/attempt/job/receipt/quota均不变，实际6次平台查询。

首次批核查7笔（含对照）：5笔ACCEPTED查到3/51，第6笔ACCEPTED查询返回HTTP200内TOP错误code7，程序停止并保留证据；该错误不是NOT_FOUND。冷却后的单笔诊断查到该笔3/51。此时6笔受理已确认成功、2笔UNKNOWN未找到，其余239笔未核完。

第二版只查其余239笔，另加同一已确认正对照，最低间隔3.2秒。合并相交的业务日期窗和上传日期窗为其并集，覆盖范围相同，仍逐笔固定企业/单据号/类型；没有使用未经确认的全企业枚举接口。任一明确查询错误/网络未知/未知状态再次停止，不盲判未收到。

r2查完35笔ACCEPTED及141笔UNKNOWN后又遇一笔TOP7，原结果保留为失败中断；冷却后另建r3，准备仅核剩余63笔，间隔增至4.5秒，并保存TOP错误code/msg/sub_code字段。r3正对照仍成功，但第一笔待核查再次返回code7，子码accesscontrol.limited-by-api-access-count，明确App Call Limited。程序停止，**未完成剩余63笔，不宣称247笔全量核查通过**。

此错误是平台返回的API调用次数限制；不能用HTTP200认定查询成功，也不能猜测是每秒/每分钟/每天额度。需平台核实该AppKey、该query.billstatus接口的实际额度与恢复时间。进一步降速已实测仍被拒，未再循环请求、换AppKey或绕过限制。

原文件/服务器复核目录不原地覆盖：首轮/root/blyb-maintenance-backups/unknown-review-20261026/，后续-r2/、-r3/；probe.py、results.jsonl、stderr.log、final.json均私有权限。公开证据只含聚合、内部UUID、单据号SHA256、平台枚举，不含患者信息、原始追溯码或凭据。

## 实际命令与证据

工作区根目录，现有Python依赖和固定SSH指纹，无安装下载：

```powershell
$env:PYTHONUTF8='1'
python docs/operations/drug-trace-unknown-check-20261026/check.py app inspect
python docs/operations/drug-trace-unknown-check-20261026/prepare_probe.py
python docs/operations/drug-trace-unknown-check-20261026/check.py app sample
python docs/operations/drug-trace-unknown-check-20261026/check.py app all
python docs/operations/drug-trace-unknown-check-20261026/prepare_diagnosis.py
python docs/operations/drug-trace-unknown-check-20261026/check.py app diagnose
python docs/operations/drug-trace-unknown-check-20261026/prepare_resume.py
python docs/operations/drug-trace-unknown-check-20261026/check.py app resume
python docs/operations/drug-trace-unknown-check-20261026/check.py app progress2
python docs/operations/drug-trace-unknown-check-20261026/prepare_final_pass.py
python docs/operations/drug-trace-unknown-check-20261026/check.py app finalpass
python docs/operations/drug-trace-unknown-check-20261026/check.py app progress3
python docs/operations/drug-trace-unknown-check-20261026/check.py app recheck_limit
python docs/operations/drug-trace-unknown-check-20261026/prepare_evening_resume.py
python docs/operations/drug-trace-unknown-check-20261026/check.py app evening
python docs/operations/drug-trace-unknown-check-20261026/check.py app progress4
python docs/operations/drug-trace-unknown-check-20261026/finish_review.py
python docs/operations/drug-trace-unknown-check-20261026/check_review.py
```

all/resume/finalpass/evening为一次性启动，目录存在即拒绝，**不能重跑**。progress2/progress3/progress4只读，不产生新的平台查询。最终结果以本目录最终核查汇总及evidence/app-progress4最终final为准；进行中数字不能称全量结论。18:12只读配置元数据另确认247笔均使用AppKey35085469、零售profile和冻结query_bill_type=A；未读取Secret。r4记录位于服务器unknown-review-20261026-r4/，之前r1—r3原件均保留。

初始只读账本SQL有未限定id列错误，修正后又发现schema_ref没有domain字段导致目标为空，已按既有DataPackage.domain正确关联复验206+41。失败/空证据保留；未由空结果认定没有异常。诊断请求没有再次出现TOP7，所以仅能确定曾返回code7及随后恢复，不能凭此猜测平台精确额度。

## 结果怎么理解及后续

- CONFIRMED且result_type=3/sub_process_flag=51：本次查到平台处理成功；单据级确认不替代逐码对账。
- NOT_FOUND：在上述身份、类型和覆盖日期内，平台完整查询没有该单据；不是网络失败。仍不能凭此证明历史从未收到或人工从其他身份/单号上传过。
- QUERY_ERROR/UNKNOWN：本次查询没有完成有效核实，不能当作未收到。

确认成功的受理单据后续应走可审计的人工状态同步；未找到的UNKNOWN应结合既往人工上报名单、原回执和单据身份核对，形成明确恢复清单后再走受控补报。此次没有自动发送或回写，也没有改变scope/配额/冻结配置/视图。

改进待办：平台TOP code7的更细粒度退避和保留sub_code/sub_msg；自动轮询耗尽后的人工复核入口及审计回写；问题列表先筛后分页；UNKNOWN按“最后查询未找到/查询失败”展示原因。均须独立实现与验证，不能把本轮诊断材料当作生产功能已修复。


## 最终交付：部分核查，外部限制未解决（2026-10-26 15:49:14）

| 原状态 | 平台实测 | 数量 |
|---|---|---:|
| ACCEPTED | 已确认成功3/51 | 41 |
| UNKNOWN | 完整查询未找到NOT_FOUND | 143 |
| UNKNOWN | 平台调用次数限制，未核实 | 1 |
| UNKNOWN | 本轮尚未查到 | 62 |

**已核实184笔，剩余63笔。不是全量成功。** r3已退出1，最后一次平台错误code7/msg App Call Limited/sub_code accesscontrol.limited-by-api-access-count，进一步降速仍被拒，未开第四轮、未换身份或绕过。任务交接待平台方核实/恢复该AppKey查询接口调用次数额度，额度周期未确认。

[核查汇总](evidence/review-summary.json)、[逐笔内部UUID/结果清单（无患者与原始追溯码）](核查明细_无患者信息.csv)。清单包含全部247笔，NOT_CHECKED明确表示未查，不能当未收到；QUERY_ERROR也不能当NOT_FOUND。平台查询成功的41笔尚未回写，所以看板仍保留41受理，属本次只读边界。

原业务状态2576/41/206、配额2823、源断点46、查询job12轮均不变。原有定时任务、部署和发送范围未修改。后续需要一个可审计人工状态同步入口，保留平台证据并防止误写；不直接UPDATE，不使用perform_query绕开入队预算。未找到单据按历史人工上报核对后再审批恢复方案。

交平台支持的现成说明：当前AppKey的alibaba.alihealth.drugtrace.top.yljg.query.billstatus在2026-10-26 15:37:22及15:39:09（北京时间）返回HTTP200/error_response code7 App Call Limited，sub_code accesscontrol.limited-by-api-access-count；已降至4.5秒间隔仍拒绝。请核实该AppKey/该API的调用次数额度、计数周期、剩余额度及恢复时间，必要时扩容；并确认已受理后的推荐查询节奏。HTTP连通不代表查询成功，不需要据此新增端口。


## 全量实测结论（2026-10-26 18:18:41）

247笔已全部完成一次有效人工核查：**{'UNKNOWN->NOT_FOUND': 206, 'ACCEPTED->CONFIRMED': 41}**。所有结果均来自真实平台查询；不是合成数据或从本地旧状态推算。

当前平台已确认成功的41笔，需后续通过受控人工回写入口更新本地状态。206笔UNKNOWN按冻结企业/单据号/类型及覆盖业务与上传日期窗口查询NOT_FOUND（若结果分类变化以核查汇总为准）；不能把NOT_FOUND扩大为所有企业身份/所有历史单号从未上报的结论。先核对人工上报名单/相同追溯码的既往记录，再形成恢复清单，不能直接重发。

本地业务状态仍2576 CONFIRMED/41 ACCEPTED/206 UNKNOWN；quota2823、源断点46不变。查看页面仍有41/206是本次未回写，不代表这次查询没有执行。没有发任何新药品数据、没有清零查询代次或配额。

证据：[最终核查汇总](evidence/review-summary-final.json)、[最终逐笔内部UUID及平台结果（不含患者/原始追溯码）](核查明细_最终版_无患者信息.csv)。原部分核查汇总及CSV原样保留；不能用最终成功覆盖下午TOP7错误。
