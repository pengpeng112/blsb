# Runbook：TLS 证书与验证（OPS-01）

适用：<prod-host> API 入口（nginx 443）与 DMZ 转发（<relay-net-host>）。核心约束：
**证书必须带 SAN（Subject Alternative Name）**——无 SAN 的证书即使被加入信任，
按 IP 或域名做主机名校验也必然失败；验收不得依赖 `curl -k` / 关闭校验。

## 一、临时测试证书（仅测试；正式链以院方 PKI 为准）

`implementation/deploy/linux/certs.sh` 提供离线生成与校验（不联网、私钥不出目录）：

```bash
# 在目标机（或运维机）生成测试根 CA 与带 SAN 的服务端证书
./certs.sh make-test-ca /etc/blyb/certs-test
./certs.sh issue-server /etc/blyb/certs-test --san IP:<prod-host> --san DNS:blyb.xxx.local --days 30
./certs.sh verify /etc/blyb/certs-test --host <prod-host>   # 链 + SAN 名称匹配，拒绝即失败

# 部署：把 tls.crt/tls.key 放入 $BLYB_TLS_CERT_DIR（默认 /etc/blyb/certs）后
docker compose --project-name blyb --env-file /etc/blyb/runtime.env restart nginx
```

注意：
- 测试 CA 的 CN 带 `BLYB-TEST-ONLY` 标记；`test-root-ca.key` 权限 600，绝不入 Git/发行包。
- 客户端（浏览器/运维机 curl）需信任 `test-root-ca.crt` 才能通过链校验；
  `status.sh` 的 TLS 验收会优先使用同目录 `test-root-ca.crt` 作为 CA。
- 重复签发不会静默覆盖（`--force` 才覆盖）。

## 二、验收方式（status.sh）

`status.sh` 已移除 `-k` 验收：从部署证书解析 SAN，取本机 IP/DNS 或第一个 DNS 名称，
用 `curl --cacert <CA>` 做链+名称校验；无 SAN 的证书直接判失败并提示更换。
人工验收同样使用 `certs.sh verify` 或：

```bash
curl --cacert /etc/blyb/certs/test-root-ca.crt https://<prod-host>/health   # SAN 含该 IP
curl --cacert /etc/blyb/certs/test-root-ca.crt https://blyb.xxx.local/health # SAN 含该 DNS
```

## 三、正式证书轮换（院方 CA 签发后）

1. 取得新链：`tls.crt`（含中间链）与 `tls.key`；SAN 必须覆盖对内发布的服务名/IP
   （域名方案优先；临时用 IP SAN 的测试证书在正式证书就绪后立即下线）。
2. 备份旧证书：`cp -a /etc/blyb/certs /etc/blyb/certs.bak-$(date +%Y%m%d)`（600）。
3. 放置新证书（属主 root:root，key 600）→ `certs.sh verify /etc/blyb/certs --host <名称>`
   → `compose restart nginx` → `status.sh` 确认 TLS 验收通过。
4. 回退：恢复备份目录后 restart；旧证书撤销按院方 CA 流程登记。
5. 到期提醒：`certs.sh verify` 顺带人工核对 `-days`；监控项接入后由
   `openssl x509 -enddate` 到期告警覆盖（DTR-E06）。

## 四、DMZ 转发证书

内网侧（<prod-host>→DMZ）与外网侧（DMZ→eco.taobao.com）证书独立配置，不复用不匹配域名的证书。
DMZ 组件的 mTLS 客户端证书（<prod-host> 身份）由院方 PKI 签发前，使用
`deploy/dmz/` 提供的离线测试 CA 材料生成工具；正式启用等 EXT-04 关闭。
