# BLYB DMZ 转发器部署（v2 · DTR-FINALIZE-20261020）

专用 HTTPS 转发器：内网 `POST /alihealth/router/rest` → 上游固定
`https://eco.taobao.com/router/rest`。mTLS + 源 IP 白名单 + 原字节 + 无重试。
**不是开放代理**；不接收任意目标 URL/CONNECT。

目标机（2026-10-20 只读实测）：Anolis OS 7.9 / systemd **219** / glibc 2.17 /
系统 OpenSSL 1.0.2k / **无系统 python3**；既有 8001=java 服务不得占用。

## 架构（FIN-01）

```
systemd(PID1) --socket--> 持有 TCP 443 监听（blyb-dmz-forwarder.socket）
    └── FD 传入 → blyb-dmz-forwarder.service（User=blyb-dmz，非 root，
        CapEff=0——不需要 CAP_NET_BIND_SERVICE，也不需要 AmbientCapabilities）
        └── /opt/blyb-dmz/runtime/bin/python3.11 forwarder.py（私有运行时，
            cpython-3.11.9 自带 OpenSSL 3.0.13；centos:7/glibc2.17 已实测）
```

- unit 只用 systemd 219 支持的指令（静态白名单测试 + 现场终验）。
- 日志进 journal（219 无 per-unit journald 限额；全局保留策略不动）。
- `/health` 与业务 POST 同样要求 mTLS；只报本地进程状态，不冒称上游正常。

## 发行与安装（FIN-03）

构建机打包（产出 manifest.json + SHA256SUMS + tar.gz + .sha256）：

```bash
python deploy/dmz/pack_dmz_release.py \
  --runtime <cpython-3.11.9-el7-install_only.tar.gz> \
  --runtime-sha 78b1c16a9fd032997ba92a60f46a64f795cd18ff335659dfdf6096df277b24d5 \
  --runtime-url https://github.com/astral-sh/python-build-standalone/releases/download/20240415/cpython-3.11.9+20240415-x86_64-unknown-linux-gnu-install_only.tar.gz \
  [--certs <TEST-ONLY证书目录>]
```

安装（材料经批准通道拷入 DMZ，默认 /root/blyb-dmz-material）：

```bash
cd /root/blyb-dmz-material && ./install.sh                 # 首装/升级（原子切换，失败自动恢复）
./install.sh --verify                                      # 严格本地验收（任一项不过→非零）
./install.sh --verify --with-upstream                      # 加上游探测；出口码4=LOCAL_READY/UPSTREAM_BLOCKED
./install.sh --status                                      # 状态摘要
./install.sh --diagnose                                    # 分层诊断 JSON（runtime/config/tls/upstream）
./install.sh --rollback                                    # 回退上一版本（配置/证书不盲回）
./install.sh --uninstall [--purge]                         # 停止并移除（默认保留配置/证书/运行时）
```

`--verify` 检查（全部通过才 exit 0）：release 文件哈希、unit 与模板渲染一致、
socket 监听地址、service active、进程用户=blyb-dmz、解释器在私有运行时、
`--check-config`、**真实 mTLS GET /health 200**、无客户端证书必须握手失败、
8001 归属与安装基线一致。上游连通性是独立状态（不吞进警告）。

## 配置（/etc/blyb-dmz/forwarder.json）

见 `config.template.json`。关键项：
- `listen_host` 必须与 socket ListenStream 地址一致（systemd 模式 FD 地址
  不符会拒绝启动）；绑具体 IP（0.0.0.0 无法做 SAN 校验，被拒绝）。
- `allowed_top_methods` 默认 `{uploadretail, query.billstatus}`；
  `uploadinoutbill` 需 `enable_inout_upload=true` 显式启用。
- 预算：tls_handshake/header/body_read/write/upstream_* 各阶段独立 +
  `total_request_deadline` 总时限；`max_pending_handshakes` 握手前连接限额。
- 证书：`tls_cert`（serverAuth，SAN 覆盖 listen 地址）、`client_ca`（签发
  <prod-host> 客户端与 ops-probe 证书的 CA）、`upstream_ca`（Anolis 系统链
  /etc/pki/tls/certs/ca-bundle.crt 可用）。

## 证书（FIN-07）

- 服务端：院方 PKI SAN 证书（缺正式 PKI 时用 TEST-ONLY 链完成隔离验收，
  绝不把测试根加入生产全局信任）。
- <prod-host> 客户端（clientAuth）：仅存 <prod-host> 受限目录，**不复制到 DMZ**。
- 运维探针（clientAuth，ops-probe）：存 DMZ /etc/blyb-dmz/certs，仅用于
  本机 mTLS /health 与安装器健康检查。
- 签发工具：`deploy/linux/certs.sh issue-client|issue-ops-probe`（测试链）。

## 时间/DNS（FIN-07）

`deploy/linux/diag_time_dns.sh`（兼容旧 systemd/最小安装；未识别命令如实
记录，不伪称 NTP 通过）。2026-10-20 实测 DMZ 时钟慢约 32 分钟、
`NTP synchronized: no`——校时方案与时间源由院方提供，安装器不跳时。

## 安全不变式

- 停止必须**同时停 service 与 socket**（先 service 后 socket，否则 socket
  把 service 再拉起：`systemctl stop blyb-dmz-forwarder.service blyb-dmz-forwarder.socket`。
- 不 setcap 系统 Python、不升级整机 systemd/OpenSSL、不改其他服务全局日志策略。
- 日志脱敏：只记随机关联 rid/方法枚举/大小/状态/耗时/错误分类；不记请求行、
  查询串、header、body、签名、session、追溯码、上游 Location。
- 私钥/真实环境配置只放 /etc/blyb-dmz（700）；不进发行包/镜像/Git。
- 固定上游 Host/SNI=eco.taobao.com；请求 Host 头不转发；不读代理环境变量。
