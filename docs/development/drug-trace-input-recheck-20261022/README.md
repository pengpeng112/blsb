# 既有资料核对与真实只读实测（2026-10-22）

**已实际执行：正式worker链路只读查询成功；另查得医院药行业基础版服务截止2028-12-31。
已有账号/密钥无需重新提供。真实上传仍为0，当前没有已采集的完整单据。**

执行：Codex / unknown，北京时间2026-10-22 13:58—14:04。现场不改配置、不发上传、
不读取服务器托管凭据内容、不修改HIS、不替院方编造审批。

## 1. 用户以前给过什么，哪些确实不必再索要

| 资料 | 核对结论 |
|---|---|
| <prod-host>、<jump-host>、DMZ的地址与SSH账号凭据 | 已保存；本轮使用既有严格主机指纹连接成功，不重复索取 |
| HIS地址/账号凭据及查询入口 | 已提供；平台当前登记<his-host>:1521，服务名his；用户名不等于业务schema |
| HIS原始SQL | 已有MED_TRACE_RECORD的OUT查询；它不是应用要求的r7两视图实施回执，也没有指定首张药房/完整单据 |
| AppKey/AppSecret/ref_ent_id/医院名称 | 已登记，凭据已被平台只读查询接受；不再索要或在报告打印Secret |
| ent_id | 之前企业目录查询已补齐；本期retail不因此新增库存供应商字段要求 |
| 用户测试意图 | 已有，不重复泛问是否同意开始测试；实际单据、药房、未报核对仍需确定 |

不能把“还缺生产只读来源”写成“用户没给HIS密码”，也不能把“有AppKey”当成全部方法权限证明。

## 2. 本轮真正跑了什么

| 检查 | 结果 | 证据 |
|---|---|---|
| 当前应用发行/容器 | fb-20261021T164800Z；6容器healthy | [application-r3.json](evidence/application-r3.json) |
| 实际签名/真实目的地 | worker UID10001、SIGNATURE_VERIFIED=true、is_mock=false、配置PUBLISHED rev1 | 同上 |
| 正式链路只读查询 | worker实际已发布配置→严格mTLS→DMZ→官方网关；调用query.billstatus 1次，随机探针单号，found=true/total_num=0/unknown=false | 同上；返回0条不是上传或对账成功 |
| 服务有效期 | service.getenddate，business=1、service=11；HTTP200/TLS0、success=true、msg_code=SUCCESS、end_date=2028-12-31 | [service-expiry-r2.json](evidence/service-expiry-r2.json) |
| HIS登记 | 唯一同端点登记仍medical_dict_push，enabled=true；平台未登记两视图 | [metadata.json](evidence/metadata.json)；仅平台元数据，非活库字典 |
| HIS来源配置 | connector DRAFT/无active config、worker DSN为空 | application-r3.json |
| 本地单据/发送 | DrugTraceBill=0、CollectionRun=0、Submission=0、SendQuota=0；collect/send证据MISSING，query/reconcile证据APPROVED | 同上 |
| 医院/药房映射 | drugtrace.ref_ent_id仍draft-1、未批准、items=0；bill_type 321及query A字典已批准 | 同上 |
| DMZ网络与限制 | 443运行；源白名单含<prod-host>和NAT出口<relay-net-host>；只允许query.billstatus，库存关闭 | [dmz.json](evidence/dmz.json) |
| 证书/时间 | DMZ证书发行者含BLYB Test Root CA (NOT FOR PRODUCTION)；NTP未同步、Reach0；DMZ UTC05:26:41而同轮官方HTTPS Date05:59:08，约慢32分27秒 | 同上 |

服务有效期诊断走 **Windows签名→SSH stdin→DMZ curl→官方HTTPS**，不改DMZ转发器白名单，
AppSecret不复制到DMZ，不把它说成生产适配器已实现service.getenddate。
它证明该医院药行业基础版的服务截止日；**不证明uploadretail方法已获权限，也不代表所有服务包都在有效期内。**

官方接口依据：[服务截止日API66087](https://developer.alibaba.com/docs/api.htm?apiId=66087)，
参数及响应字段按官方当期说明核对，不猜business/service数值。
本轮共3次平台只读请求：状态查询1次、有效期2次；有效期首轮HTTP200后本地加密归档失败，
修复本地工具后人工新建r2复测，非后台自动重试。真实上传0、HIS活库查询0、远端配置变更0。

## 3. 逐项解释用户转来的剩余清单

### 3.1 r7两视图、生产只读绑定：已有密码，但尚无生效证据

当前应用配置没有绑定生产DSN，来源仍DRAFT且未采集单据。平台元数据也无两视图记录；
**元数据缺记录不能证明HIS活库没有视图**。需要DBA告知是否已执行、actual schema及核验回执；
如已建立，核对授权/登记后接入即可，不应让用户重复交密码或无谓重建同名视图。

HIS技能明确停止条件：

> 连接账号不是已登记只读源或平台 `write_policy` 不是 `readonly`。

依据：[HIS只读SKILL.md](F:/python/数据资产/.agents/skills/hisuser-readonly-sql/SKILL.md)
“阻塞范围与停止条件”；入口为[sjzc技能](C:/Users/Administrator/.zcode/skills/sjzc/SKILL.md)。
本轮刚从<relay-jump>平台重新查询到medical_dict_push，故未解析其服务器凭据、未登录HIS活库；
不是认证密码错误。当前白天的源端查询还需按技能核对具体对象/时段/通道的有效授权。

恢复方式：管理员确认或提供受限只读源登记；DBA确认两视图实际存在且仅授予必要SELECT；
再通过sjzc限量核验、创建已验新SourceProfile、绑定/启用连接器。已有账号若经核实确为受限只读且
可按规定登记使用，不要求为了名称而另建新账号。原库CREATE/GRANT仍由DBA按逐项批准实施。
现有[DBA交付包](../drug-trace-firstbill-exec-20261021/DBA-PACKAGE.md)可沿用。

### 3.2 NTP：以前给的是业务服务器地址，没有指定可用时间源

目前<prod-host>签名可被平台接受；DMZ慢约32分钟，现有chrony源203.107.6.88 Reach0。
这表示尚未收到有效时间样本，不能仅凭此断言一定是防火墙阻断。

请运维确认一个两区可达的院内NTP源，或批准既有时间源可用的访问方式；核对源端UDP123与回包，
维护窗口内校时并检查chronyc tracking/sources、同步状态及偏差。不能把SSH端口22/HTTPS443
已通当成NTP也通，也不能因<prod-host>此刻准就假设长期无漂移。

### 3.3 正式证书：现有证书能加密并严格校验，但它明确标为TEST-ONLY

本轮没有发现院方正式链交付记录；工作区本机证书文件名只见pki/pki-r2/pki-r3测试材料，
不据此推断其他电脑或院方CA未持有正式链。若之前已交，请给正式材料的受控位置/证书签发者即可，
**不要把私钥贴入聊天**。

院方CA/运维应交服务端证书/CA链及独立客户端身份，包含实际访问IP SAN（或确定的域名SAN）、
serverAuth/clientAuth用途。轮换时区分宿主机证书目录与worker容器内 `/etc/blyb/certs`，
从当前挂载配置定位，不能把容器路径误当宿主机路径。两机协调轮换、保留恢复路径后复测。
现有[OPERATIONS §四](../drug-trace-firstbill-exec-20261021/OPERATIONS.md)作为参考，
不得用 `-k` 或把测试链改名来宣称正式链已交付。

### 3.4 平台资料：有效期已查清，剩下上传权限及321/323冲突

**服务期“待提供”可以更新为“接口实测截止2028-12-31”。** 不再要求用户补这项资料。
AppKey/Secret的真实性、医院目录标识和查询能力已有证据；uploadretail权限仍不能从查询能力推出。

官方API52553的bill_type同页仍有“示例321、说明323”的冲突，指南2026.01列321使用出库；
当前配置采用已批准的321。这不是用户忘记给密码，也不是建议轮流向正式平台尝试两个值。
剩余可向平台一次确认：“本院当前AppKey是否开通uploadretail；患者发药/使用出库应传321还是323？”
依据：[阿里官方API52553](https://jaq-doc.alibaba.com/docs/api.htm?apiId=52553)，
本轮重新核对，尚不能把冲突声称已消除。

### 3.5 首张未报scope：原来的多月SQL不是一张已确认未报的完整单据

本次“试一个”已经授权尝试，不再泛问是否同意测试。但必须能确定要上传哪张：药房、日期或完整单据引用，
以及是否由原厂商/其他系统报过。现系统单据为0，即使开放上传方法也没有真实payload可发。

最小输入可写：

```text
药房代码/名称：
单据日期或受控单据引用：
该单已完成且全部追溯码完整：是/待核
其他系统是否已报：已确认未报/待核
本次范围：仅此1张，max_bills=1
```

不必在聊天贴追溯码或患者信息，给受限清单位置即可。取出后由程序进行全码/数量/整单一致性检查，
不任意挑一条扫码行、重用原SQL多月历史区间或假码测试上传权限。

## 4. 除外部输入，还要完成的系统配置操作

此次直接查询到目的地 **is_mock=false、配置PUBLISHED，但Destination.status仍DRAFT**。
两个状态不能混为一谈。`domains/drug_trace/plugin.py` 的BILL_INGESTED事件仅为ENABLED目的地
排PREPARE任务；因此真实采集验收后，还需按现有受控配置生命周期启用正确目的地，必要时人工重驱。
来源本身也仍DRAFT，医院/药房ref_ent_id映射为空。不能把“配置已发布”说成所有对象/字典已启用。

这不妨碍本轮显式诊断客户端查询成功，也不自动证明真实采集→prepare→发送编排已现场验收。
本轮未擅自启用对象或填药房字典，没有做全量代码评审，不以此报告替全部实现担保。

下一次依赖顺序：

1. 先核实视图是否已建及受限只读绑定，补真实药房/业务规则；其他正式证书/校时/平台口径可分别落实。
2. 按批准版本绑定并启用来源/目的地及医院字典；限量采集1张完整单据、冻结预览与dry-run。
3. 首单未报核对和具体scope明确后，按真实证据逐项登记send；DMZ仅增开uploadretail。
4. 正式应用发送1次→查询最终处理状态→声明单据/逐码/外部多报对账覆盖范围→停止扩大范围。

## 5. 保留的诊断失败及更正

- application.json：worker退出1，首轮没有暴露异常内容，无平台请求。
- application-r2.json：探针人为对所有能力都加当前配置版本绑定，query/reconcile返回MISMATCH_CONFIG。
  随后核对service.perform_query、run_reconciliation和readiness，确认当前查询是租户/目的地级批准，
  发送才额外精确绑定配置。**此结果不是部署缺陷，不修改生产证据来迎合错误探针。**
- application-r3.json：按实际查询规则复测成功。没有删除门禁，真实purpose=query仍检查已发布/历史
  配置的tenant/owner及SecretRef/mTLS；采集/发送门禁不变。此前聊天将r2称为登记问题已更正。
- service-expiry.json：curl HTTP200/TLS0，但Windows PowerShell5.1安全模块在继承环境中加载失败，
  导致本地DPAPI归档报CalledProcessError；该轮没有据此声称服务过期或请求失败。
  用公开样例对比验证pwsh可正常加密，再建立r2复测；原回执保留，真实原始响应受限DPAPI存储。

## 6. 文件与边界

- [recheck.py](recheck.py)：metadata/application/dmz只读探针；各回执排他创建，不覆盖旧证据。
- [service_expiry_probe.py](service_expiry_probe.py)：固定方法有效期诊断，禁止上传、零自动重试。
- 所有原始平台响应只存受限 `.ai/private/drug-trace-input-recheck-20261022/` DPAPI文件，
  仅当前Windows用户和SYSTEM访问；共享回执无签名/密钥值。
- 本轮只读探针和文档检查，不修改业务源码、不重跑业务全量测试、不新增服务/证书/权限/配置。
- 已通过：平台元数据、严格SSH、配置状态核对、真实应用链路查询、有效期诊断。
  未做：HIS活库、真实采集、上传权限实证、真实上传、终态/逐码对账。
- 原交接的全量405/7/0保留为上棒结果，不冒称本轮重跑。

用户已被集中询问视图schema/回执及首张范围；未收到答复前不替用户填值。
