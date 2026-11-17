# 真实平台联调与现场链路复测（2026-10-20）

**结论：凭证已通过两项真实只读调用，医院目录信息一致；真实上传尚未执行，正式链路当前不通。**
用户本次明确要求真实测试，已按该请求开展核验；不是仍在等待“是否允许开始”。
但不能把查询成功说成上传成功，也不能猜测单据类型、选取任意历史单据重报或修改门禁造成功。

## 本次实际结果

| 项目 | 实测证据 | 结论与边界 |
|---|---|---|
| Windows → 官方 HTTPS | [状态查询回执](evidence/query-probe-receipt.json)，北京时间11:55；HTTP200、response_success=true、msg_code=SUCCESS、total_num=0 | 用随机从未上传的探针单号进行查询；签名与凭证被该查询接受，无业务单据上传 |
| 医院企业目录 | [企业查询回执](evidence/enterprise-probe-receipt.json)，北京时间11:58；SUCCESS、ref_ent_id与医院全称均一致，status=1、role=3，平台返回ent_id | 医院目录确实存在且与用户材料一致；不是AppKey归属或全部接口权限的证明。不要求用户重新提供已登记资料；本期retail也不以ent_id作为必填前提 |
| <prod-host>运行发行 | [现场只读复测](evidence/network-probe.json)，北京时间约12:00 | 仍为/opt/blyb/releases/20261016T114735Z，6容器healthy；未发现药品新发行已经上线的证据 |
| <prod-host> → DMZ | 同上，实际从<prod-host>测试<relay-net-host>:443和8443 | 两端口均ConnectionRefusedError；不能直接归因防火墙未放通 |
| DMZ服务监听 | 同上，实际SSH登录DMZ并ss -lntH | 无443/8443监听；有既存8001，归属未核，不擅自复用 |
| DMZ → 官方 HTTPS | 同上，curl -4 --noproxy '*' HEAD官方HTTPS，5秒连接超时、exit28 | 出口直连仍失败；本机经其自身网络可达不等于DMZ出口可达。ssl_verify_result=0在连接超时时不构成TLS握手成功证据 |
| DMZ时钟 | 同上，UTC约03:27:55，采集本机UTC约04:00 | 约慢32分钟；timedatectl --property在此旧系统不兼容，NTP状态本轮未取得，不伪称已确认关闭。DMZ若只转发并不产生TOP签名；<prod-host>签名时钟仍需单独核对 |

真实上传次数 **0**，发送患者信息 **0**，远端文件/配置变更 **0**，本轮HIS活库查询 **0**。
没有发送虚假业务单据测试上传权限，没有重放原SQL历史区间。

## 调用方式与秘密保存

本次两次真实请求均为固定方法的只读探针，使用[官方C#签名参考](https://developer.alibaba.com/docs/doc.htm?articleId=107727&docType=1&treeId=445)
中算法不变的提取代码[OfficialTopReference.cs](OfficialTopReference.cs)，不是官方SDK二进制。
[query_probe.ps1](query_probe.ps1)与[enterprise_probe.ps1](enterprise_probe.ps1)仅实现相应查询，无上传方法选项。
TLS校验开启、禁重定向、零自动重试；原始响应用DPAPI保存在本机受限`.ai/private/alihealth-live-test-20261020/`，
共享回执只保留白名单结果。Secret沿用[已登记的凭证包装器](../platform-credentials-20261020/README.md)，不写入脚本、命令参数或共享报告。
脚本有一次性回执预留，重复运行会在联网前拒绝；已有回执不删除、不覆盖来“再试一次”。

[network_probe.py](network_probe.py)只复用了已知SSH主机指纹及本机受控凭据，在两台机器执行固定只读命令，
未运行安装、重启、配置发布、curl业务POST或读取runtime.env。源端HIS凭据未读取。
本次诊断从Windows直达官方网关，不能作为<prod-host>→DMZ→平台的正式全链路验收。

## 签名对照发现与局部修复

[修复前对照](evidence/offline-signing-comparison.json)：现有Python在通常参数与中文/特殊字符/前导零用例上
与官方C#一致；**空参数名或空字符串值**会参与Python拼接，却被官方参考跳过，导致不一致。
已按官方参考修复top_sign的空项过滤，保留既有SIGNATURE_VERIFIED=False及所有真实发送门禁。
修改前工作文件副本留在本机受限私有目录，未覆盖其他AI的未提交修改。

[修复后对照](evidence/offline-signing-comparison-r2.json)四例一致；
[公开离线向量](evidence/public-reference-vectors.json)只使用公共示例Secret，与真实凭证无关。
新增[独立参考回归用例](../../../implementation/tests/integration/test_top_official_reference.py)覆盖参考摘要和表单空值、中文及前导零。
官方文章的文字示例摘要与其自身C#代码实际计算不符，已保留证据；未把文字示例强改为程序预期来造绿。
以上不等于已取得应用专属SDK，也不整体关闭EXT-07。

## 首张真实上传的接续计划

按顺序处理，每项独立留证；不重装现有基础栈，不一键启用全历史。

1. **先修复实际服务链路。** 根据现有DMZ部署包安装/启用专用HTTPS转发服务，落实正式域名/SAN证书及信任、mTLS（若方案要求），主用443。8443仅在明确启用备用服务后验收。实际部署/重启应沿已有计划的维护窗口执行；本次未修改现场。
2. **协调DMZ出口。** 对<relay-net-host>→eco.taobao.com:443检查域名策略、DNS/NAT/回包与必要的医院出口代理；若要求代理，明确地址、认证与应用配置，不把本机代理出口当DMZ出口。HTTPS HEAD/TLS成功后再验证转发POST，不改回HTTP80或关闭TLS校验。
3. **核对两机时间。** 旧Anolis使用兼容的timedatectl/chronyc/ntpq命令，配置医院NTP；单独检查签名所在<prod-host>的时区与偏差。不能把DMZ时间不准直接解释为所有签名失败。
4. **完成应用签名与发行验收。** 从应用控制台取得官方SDK包/版本/哈希，离线对照真实使用的UTF-8表单参数、空值、session（如需）。修复已落本地；在证据齐全的批准修订中处理正式签名资格，不直接翻常量。构建新发行，按既有备份/回退与维护窗口升级<prod-host>，确认药品插件/迁移/工作者确实载入。
5. **确认本期单据类型。** [零售官方API](https://developer.alibaba.com/docs/api.htm?apiId=52553)当前参数表描述写323，示例写321；用户提供的2026指南摘录又写321。请平台技术方明确医院患者发药应使用哪个值，并给文字或当期文档依据。创建新的批准字典revision；不能原地改冻结字典，也不能试两个类型碰运气。原SDK uploadinoutbill不是本期患者发药uploadretail的替代。
6. **确定一张完整、未上报的真实候选。** 先核实其他厂商是否已经上传（已向用户询问，尚未得到答复）；从当前受限只读登记核对HIS，再通过发药时间索引和完整处方/明细键取一张已完成、非拆零、非退药的完整单据，保留全部真实码，最小必填信息即可。若仍是medical_dict_push登记，落实生产readonly绑定或任务范围内明确例外，不沿用10-19一次性授权。此前只验证了8条码关联，未保存本轮可直接发送的完整候选payload。
7. **冻结候选与防重检查。** 固定单号、真实业务日期、药房、逐码列表、payload哈希、来源/字典/配置revision及首次测试max_bills=1范围，展示脱敏预览；结合院方既往上报口径与平台可用接口检查重复。随机新单号查询为空不证明任何历史码未被其他系统上报，码详情也不能替代完整业务流转对账。
8. **发送一次并核对终态。** 使用批准uploadretail契约，首张可采用ignore_part_success_flag=1（整单拒绝而不静默跳过坏码，须确认契约），不发患者姓名/证件等非必需字段。真实POST只一次；HTTP200或response_success=true仅代表受理。保存受理标识后查询该单，按已批准枚举确认最终处理结果与码数。超时/未知先查询，不换单号或自动重发；部分失败与对账不符立即停止扩大批量。

本次用户请求已授权开展真实测试；上述待项是实际信息与链路缺口，不应再次笼统询问“是否允许测试”。
权限/服务有效期/医院与AppKey绑定仍按接口逐项核对，不能从两项查询推出上传方法权限。
仍需要院方/平台提供的业务信息只有：**是否已有其他系统上报、首张可核对的未上报单据范围、321/323的正式口径**；
AppKey、AppSecret、ref_ent_id、医院全称均不需要重复提供。

## 验证与交接

实际命令、局部pytest结果、一次性脚本验证及文档检查见
[本轮session](../../ai/sessions/PLATFORM-LIVE-TEST-20261020.md)。
正式门禁、批准字典、运行配置、服务器发行均未被本次诊断修改；外部项仍有缺口，不能宣称具备生产上报能力。
