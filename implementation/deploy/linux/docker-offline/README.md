# 离线 Docker 材料（openEuler 22.03 x86_64 · <prod-host>）

目标机无 DNS/公网出口（r6/r8 实测），容器运行时必须离线安装。

## 内容（构建机下载，2026-10-16）

| 文件 | 来源 | 校验 |
|---|---|---|
| docker-29.9.1.tgz | download.docker.com/linux/static/stable/x86_64 | SHA256SUMS（TLS 直连官方源后本地计算） |
| docker-compose-linux-x86_64 | github.com/docker/compose releases v5.6.0 | SHA256SUMS + 官方 .sha256 对拍一致（40343e21…） |
| tar-1.34-4.oe2203sp2.x86_64.rpm | openEuler 22.03-LTS-SP2 everything 仓库 | SHA256SUMS（目标机最小安装无 tar，实测 2026-10-16） |
| install-docker-offline.sh | 本仓库 | 见脚本 |

选择依据：openEuler 22.03-LTS-SP2 官方源实际仅 docker-engine 18.09（仓库
primary.xml 实查；HTML 目录里的"docker-5.3.7"是 pcp-pmda-docker 的子串误匹配），
与本发行镜像及 Compose v2/v5 特性差距过大。静态二进制自包含、无 RPM 依赖链，
与构建机 Docker 29.3 同代。若院方强制只用发行版仓库包，改用 docker-engine 18.09
属未验证分支，须单独评审（healthcheck start_period / 网络语义差异）。

## 目标机安装

```sh
sha256sum -c SHA256SUMS          # 先校验
./install-docker-offline.sh      # root；幂等（已有 dockerd 则退出）
```

安装位置：/usr/local/bin/{docker,dockerd,containerd,runc,…}、
/usr/local/lib/docker/cli-plugins/docker-compose；data-root=<prod-path>/docker；
systemd 单元 docker.service（enable --now）。

## 边界

- 不改动目标机既有 systemd 单元；发现已有 dockerd 一律不覆盖。
- 不安装 iptables 以外的新系统包；静态 dockerd 依赖系统 iptables（openEuler 默认有）。
- SELinux/firewalld 保持开启；容器端口放行走 firewalld 常规流程，不关闭防护。
