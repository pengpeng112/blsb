# PLATFORM-LIVE-TEST-20261020

- 工具/模型：Codex / unknown；session `20261020T035007Z-cd0977c5`。
- 用户目标：按真实性测试上传，判断能否正常。
- 范围：implementation、docs、本机受限任务私有目录；顺序接力，无子代理。
- 交接结果：**handed_off，首张真实上传未完成**。已核实两项查询成功及正式链路阻断，未虚报上报成功。
- [实测与后续步骤](../../development/platform-live-test-20261020/README.md)。

## 授权与边界

用户明确要求真实测试；据此执行两项平台只读调用、两台服务器只读现场复测。
未发送业务上传、未编造单据、未重放全历史、未改服务器配置/发行/运行库/批准字典或生产证据。
本轮未再次连接HIS，不沿用上次一次性例外授权；凭据不在输出中。

## 实际改动

1. 添加固定方法的一次性PowerShell只读探针及官方C#参考提取；原始响应DPAPI加密，公共回执按白名单保存。
2. 添加固定命令的现场网络只读探针，复用已有DPAPI凭据、SSH指纹；没有修改SSH信任策略或部署配置。
3. 独立参考对照发现top_sign空参数名/空字符串值不应参与拼接，修复该过滤并新增5项有独立参考依据的回归测试；生产签名门禁保留False。
4. 更新共享STATE/BACKLOG/HANDOFF、文档目录、凭证登记说明和既有四类实施文档/API矩阵及外部协调表。
5. 本次修改前保留protocol.py和待编辑共享文档的当前工作字节于本机受限任务目录。未覆盖其他AI既有未提交修改。

## 真实命令与结果

从根目录，经现有受限凭证包装器执行：

```powershell
& '.ai/private/alihealth-drugtrace-20261020/with-credentials.ps1' `
  -Executable pwsh -ArgumentList @('-NoProfile','-File',
    (Join-Path (Get-Location) 'docs/development/platform-live-test-20261020/query_probe.ps1'))

& '.ai/private/alihealth-drugtrace-20261020/with-credentials.ps1' `
  -Executable pwsh -ArgumentList @('-NoProfile','-File',
    (Join-Path (Get-Location) 'docs/development/platform-live-test-20261020/enterprise_probe.ps1'))

python docs/development/platform-live-test-20261020/network_probe.py
```

前两命令各exit0、各一次真实查询，HTTP200/response_success=true/msg_code=SUCCESS；
状态探针total_num=0，企业目录标识与医院全称匹配。没有患者信息或真实追溯码。
第三命令exit0表示完成诊断，不表示网络通过：<prod-host>→DMZ443/8443拒绝，DMZ无对应监听，
DMZ→官方HTTPS的curl exit28超时。<prod-host>仍20261016T114735Z，6容器healthy；现场零改动。
DMZ时钟约慢32分钟；timedatectl --property选项不兼容exit1，不虚报NTP状态。

脚本一次性保护在首轮之后加强为联网前预留；后续故意重复执行两只读脚本均exit1且回执哈希不变，
拒绝发生在Add-Type/解密环境检查/网络之前。PowerShell两脚本AST语法无错误；network_probe.py py_compile exit0。
未重复发送任何平台请求。

官方C#参考对照四例（纯公开样例，无真实凭证）：修复前2一致/2不一致，修复后4一致。
官方文章文字示例摘要与代码计算不一致证据保留，未更改历史材料或强行通过错误向量。
不把参考代码提取冒称SDK包，未修改SIGNATURE_VERIFIED。

在implementation执行：

```powershell
python -m pytest tests/integration/test_top_official_reference.py tests/integration/test_drug_trace_domain.py tests/integration/test_drug_trace_fix2.py tests/integration/test_drug_trace_fix3.py -q -ra --junitxml=var/platform-live-20261020.xml
```

exit0，**80 passed / 1 skipped，38.19秒**。跳过为已有openssl缺失TLS慢流用例；
有既有弃用告警4495条，未压制。日志var/platform-live-20261020.log、XML同名。
无新增依赖，无本轮全量回归；最近历史全量仍FIX3的334/6/0，不能与本轮局部相加。

## 未完成与恢复条件

- 按维护窗口部署实际DMZ HTTPS转发、证书与<prod-host>药品发行，协调DMZ出口443与兼容NTP。
- 取得应用专属SDK验签证据、上传方法权限/服务有效期/应用与医院绑定等相应资料；不重复索取已有Secret或retail不必填的ent_id。
- 平台确认321/323真实业务口径；院方确认其他厂商既往上报与首张完整未上报单据范围。异步询问已发，未收到答复，不视为没有其他系统上传。
- 生产受限HIS只读绑定/真实视图等继续既有计划；冻结一张候选payload与单号/药房/日期/哈希/revision/配额，上传一次后查终态、逐码核对。
- UNKNOWN/超时不自动重发；不能删除回执再运行探针，不能通过改常量、批准空字典或伪填EXT解除阻断。

文档/保密与证据一致性检查摘要见
[verification.json](../../development/platform-live-test-20261020/evidence/verification.json)。

## 收尾检查

受限profile新增live_verification及平台企业查询返回的ent_id，保存来源，不回显具体值；
保留原始登记字段，本次部分验证不伪装全部批准。未将凭证复制到服务器。

受控包装器执行verify_local.py最终exit0：Secret明文命中0、本轮文档失效链接0、
来源/归档hash异常0、私有目录Git忽略、diff --check exit0；XML确认81 cases=80通过1跳过。
首次检查在写出自身verification.json前报告该自引用缺失，文件生成后复测已消除；不是新增历史失效链接。
project_docs.py inventory exit0（203份）；全仓check仍exit1，233处均为已有历史链接问题，
工具称new仅因未加入旧baseline白名单；未修改历史或增加白名单。
无额外平台请求、全量回归或真实上传。
