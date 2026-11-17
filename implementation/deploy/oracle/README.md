# Oracle Instant Client 19c 构建材料（DPL-P01）

后端镜像 Thick 模式所需 Instant Client。zip 不入 Git（体积），构建机按下表
下载/核对后放置于本目录；缺失时 `docker build` 的 `COPY ${IC_ZIP}` 会直接
失败（不静默跳过 Oracle 能力）。

| 项 | 值 |
|---|---|
| 文件名 | `instantclient-basiclite-linuxx64-19.32.0.0.0dbru.zip` |
| 来源 URL | https://download.oracle.com/otn_software/linux/instantclient/1932000/instantclient-basiclite-linux.x64-19.32.0.0.0dbru.zip |
| SHA256 | `16947fc0fa55b861903aad6f9d2ddee7b4fedaf789a175ee36b46b82ec64fcbd` |
| 大小 | 51104368 字节 |
| 下载日期 | 2026-10-21 |

选择依据：HIS 为旧 Oracle 11g。python-oracledb Thin 仅支持 12.1+；
Thick + Instant Client 19c 是官方 client/server 互操作矩阵中支持
11.2.0.4 服务端的最新客户端线（21c 起最低支持 12.1.0.2）。若 DBA 复核
HIS 精确版本低于 11.2.0.4，需换 18c/12.2 客户端并重做本层——
SOURCE_READY 在核验前保持 BLOCKED，与本材料不冲突。

镜像内安装位置：解包至 `/opt/oracle/instantclient_19_32`，稳定软链
`/opt/oracle/instantclient`；`LD_LIBRARY_PATH` 与
`BLYB_DRUGTRACE_ORACLE_CLIENT_LIB_DIR` 均指向该稳定路径。
许可：Oracle Free Use Terms and Conditions（FUTC）允许随应用分发使用；
来源哈希如上可独立交付核验。
