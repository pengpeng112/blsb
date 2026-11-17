# 网络调整后复测与推送判断（2026-10-21）

**网络调整已生效：DMZ→官方HTTPS及真实签名只读查询成功；<prod-host>→DMZ443和HIS1521的TCP成功。正式推送仍不可用：DMZ转发未安装、院内TLS失败、<prod-host>仍旧发行。**
时间：北京时间约13:00—13:04。用户本次要求复测并检查能否推送。
本次真实业务上传 **0笔**，患者信息发送 **0**，HIS业务查询 **0**，服务器文件/配置改动 **0**。

## 实测矩阵

| 起点 | 目的/项目 | 本次结果 | 证据及边界 |
|---|---|---|---|
| <prod-host> | <relay-net-host>:443 | TCP成功，约1ms | [内网实测](evidence/intranet-inventory.json)；不等于HTTPS/mTLS或转发程序可用 |
| <prod-host> | https://<relay-net-host>/health | curl exit35、SSL_ERROR_SYSCALL、HTTP000 | [严格TLS探测](evidence/intranet-dmz-tls.json)，未关闭证书校验、无客户端证书；尚未取得可用TLS服务响应，不是已通过mTLS |
| <prod-host> | <relay-net-host>:8443 | ConnectionRefusedError | 备用端口默认不启用，不因此判主用443的网络开通失败 |
| <prod-host> | <his-host>:1521 | TCP成功 | 仅端口握手，不构成本轮HIS账号/视图认证 |
| DMZ<relay-net-host> | eco.taobao.com:443 | HTTPS HEAD HTTP200，TLS验证结果0，curl exit0 | [DMZ实测](evidence/network-probe.json)，此前10-20超时结论已被本次成功更新 |
| DMZ<relay-net-host> | 官方query.billstatus | HTTP200、response_success=true、msg_code=SUCCESS、total_num=0 | [真实查询回执](evidence/dmz-signed-query.json)，随机从未上传的探针单号，不发送真实码或患者资料 |
| DMZ<relay-net-host> | gw.api.taobao.com:80 | HTTP HEAD200 | 仅兼顾历史网络比较，真实签名查询使用HTTPS，未降级HTTP上传 |
| DMZ | 转发器service/socket及安装目录 | unit均not-found；/opt/blyb-dmz及/etc/blyb-dmz/forwarder.json不存在 | [DMZ实测](evidence/network-probe.json)，未见443/8443监听；8001原有服务保留 |
| <prod-host> | 发行及容器 | 仍20261016T114735Z，6容器healthy | [内网实测](evidence/intranet-inventory.json)，core/worker BLYB_PLUGINS环境为空、未见AppSecret环境注入；空插件环境不等于代码完全无插件，不能替代药品实际加载证据 |
| DMZ | 时钟 | 慢约32分钟，NTP synchronized:no | [DMZ实测](evidence/network-probe.json)，未校时；本次查询由Windows正确时间签名，未因DMZ时钟偏差失败 |

443 TCP可连但DMZ本机无监听及TLS失败并存，可能涉及中间网络设备应答/映射等，当前未定位。
不能仅用TCP成功认定HTTPS转发已经部署，也不能仅用TLS失败认定网络科没有开端口。
后续先安装约定服务并本机验证TLS，再从<prod-host>复测；若仍异常，带上述源/目的和时间核查网络设备。

## 真实签名查询的准确含义

[build_readonly_form.ps1](build_readonly_form.ps1)在Windows使用10-20留存的官方C#参考签名；
[dmz_query_probe.py](dmz_query_probe.py)经已固定SSH指纹登录DMZ，将签名表单从SSH stdin传给curl，
由DMZ直接访问官方HTTPS。Secret没有复制到DMZ，签名表单不在命令参数、远端环境/文件或公共日志中。
零自动重试、无重定向、严格TLS；原始响应DPAPI保存本机受限`.ai/private/drug-trace-retest-20261021/`。

此结果验证**DMZ出口和该方法的凭证签名可调用**，不是<prod-host>应用→DMZ转发器的正式链路验收，
不是零售上传权限证明。没有改SIGNATURE_VERIFIED，没有用诊断绕过应用门禁上传。

## 目前为什么还不能推送

1. **DMZ转发服务尚未安装。** 上一AI只完成代码、离线包和隔离验收，本次现场确认仍无service/socket/配置。
2. **<prod-host>尚未升级药品新发行和配置。** 仍旧镜像；药品新迁移、来源/目标、实际签名进程密钥与mTLS挂载均不能视为已配置。
3. **业务输入和生产来源尚未完成验收。** 最新交接仍保留HIS生产readonly/r7视图、业务分支/拆零/finalized、321/323、签名资格、具体未报单据与首批scope等缺项。本轮只核查网络，未将其自动批准。

下一步沿[收尾计划FIN-10/11](../drug-trace-finalization-20261020/README.md)：
已有两份离线包→在明确部署委托及维护窗口内先禁发安装DMZ、再备份升级<prod-host>→可信TLS/mTLS健康→实际应用正式路径只读查询→资料和具体首张范围齐后单笔上传/自动查终态/核码。
已有平台和SSH资料无需再次提供；不能再把“公网443仍超时”作为当前主要阻断。
本次没有执行安装、重启、迁移、配置发布、HIS DDL或真实上传。

## 命令与取证完整性

```powershell
python docs/development/drug-trace-network-20261021/network_probe.py
python docs/development/drug-trace-network-20261021/target_inventory.py
python docs/development/drug-trace-network-20261021/target_tls_probe.py
```

上述工具exit0表示完成诊断，矩阵内TLS/备用端口等失败没有被判为通过。
初次组合取证的二跳SSH exit255、未取得<prod-host>内容，见network-probe.json；没有据此判服务器离线。
随后无认证TCP/banner诊断确认22可达，最小SSH命令与独立内网取证成功：
[路由诊断](evidence/ssh-route-diagnosis.json)、[登录诊断](evidence/target-login-diagnosis.json)。初次原因未定位，旧失败证据保留。

真实查询经既有受限包装器启动dmz_query_probe.py，exit0；新的日期/回执，不覆盖10-20一次性证据。
一次性脚本重复执行会在请求前拒绝。仅本地诊断工具/文档变更，未修改业务代码，无本轮pytest或全量回归。
共享状态与具体检查见[session](../../ai/sessions/DTR-NETWORK-RETEST-20261021.md)。
