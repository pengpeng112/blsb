# 可直接转交：追溯码系统NTP与看板访问

## 1. 必须恢复的校时通路

| 源IP | 源端口 | 目的IP | 目的端口 | 用途 |
|---|---|---|---|---|
| <prod-host> | 客户端动态UDP端口 | <relay-net-host> | **UDP 123** | 上报服务器持续NTP同步 |
| <relay-net-host> | UDP 123 | <prod-host> | 原请求源端口，有状态回包 | 返回NTP响应 |

现状：<prod-host>已配置正确地址，chronyd已active+enabled；动态源端口、固定123源端口均无响应，Reach=0、Not synchronised。
同一时钟服务器从DMZ<relay-net-host>访问正常、Normal，服务器并非整体不可用。<prod-host>→DMZ的UDP123中继路径也实测不通，未采用中继。

请检查跨区ACL、回程路由/NAT、时钟服务器访问白名单；有NAT时以实际源地址放行。TCP123不能替代UDP123；也不要仅允许返回到客户端UDP123。

放通后在<prod-host>核验：

```bash
chronyc online
chronyc burst 4/4
chronyc -n sources
chronyc tracking
```

验收须选中`^* <relay-net-host>`且Leap status=Normal。当前服务持续64—256秒轮询，网络恢复后会重试，无需重装软件。

## 2. 看板管理入口

地址：`https://<prod-host>/ui/#/drugtrace`，目的TCP443；从实际管理电脑允许访问。
服务器端页面和JS/CSS均200，API均200；本次Windows电脑能建TCP连接，但随后TLS握手被断开，Python为SSLEOFError，Chromium也断开，需排查管理链路TLS策略/检查设备。

同时请运维交付覆盖正式域名或IP SAN=<prod-host>的证书链。现有证书仅有CN=blyb-<prod-host>、无SAN，严格IP校验失败；生产校验未关闭。
不需要开放8000/8001/8002；继续通过nginx443同源入口。

其他上报HTTPS链路仍可用，本轮修复后DMZ已观察226次200转发、0次限流。NTP/看板管理访问与平台上报HTTPS属于不同通路，不能相互替代验收。
