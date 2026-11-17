# 药房映射、内网校时与看板登录（2026-10-26）

## 已实测结果（2026-10-26 12:11:52）

- 用户本轮确认药房050121、050116均属本院，故沿用本院已有平台身份，新建并批准`hospital-pharmacies-20261026-r1`（ID d13e4d3d-2c52-4988-9aca-67e6d5451d19）。它完整保留050102，并增加上述两药房；其他药房未扩大。三个代码通过应用实际lookup验证命中同一新revision，原字典原样保留。
- 内网<prod-host>已改用`<intranet-host> UDP123`，三次NTP源探测有效，随后chronyd实际选中`^*`、Reach377、Leap Normal，服务active且开机enabled。初次相差约3.664秒，由原有chronyd启动策略校准，没有手动date/makestep命令；后续已连续稳定采样。未重启任何业务容器。DMZ继续用原来正常的<relay-net-host>，本轮没有改DMZ。
- 原上午567 PREPARED恢复已结束，567次全部HTTP200受理；原日报PARTIAL_FAILURE是782个缺字典错误，不是567次发送失败。原回执`/var/log/blyb/daily-upload/20261026-090152.json`保留，不能把它改成当时已全成功。
- 两天已有DONE采集，10-25尚未形成提交的782张（050121=775、050116=7），10-24另815张（810、5），映射缺项均已解除。本轮顺序补发已启动，**22张平台受理、16张CONFIRMED，仍RUNNING**，当前窗口10-25；不能声称1597张都已成功或第二天已开始。状态已确认的新药房目前见050121；050116字典查找通过，其真实首笔尚待运行到相应单据核实。
- 旧41 ACCEPTED、206 UNKNOWN状态版本摘要不变；断点row_version46/since_time2026-10-26T00:00:00不变；配额正常递增而未重置。六容器healthy，每日timer保持enabled。平台受理与CONFIRMED分别统计。

## 登录与HTTP问题

现有地址：`https://<prod-host>/ui/#/drugtrace`。

登录页只有“访问令牌”输入框，**没有独立用户名和密码**；内部管理身份是bootstrap-admin，已通过真实`/api/v1/auth/login`验证。不要填SSH root密码，也不要填开发演示令牌。

用户要求的实际令牌已存本机受限文件：[看板登录信息_仅本机.md](../../../.ai/private/drug-trace-console-20261026/看板登录信息_仅本机.md)。目录只给当前Windows账户和SYSTEM权限；令牌未进此报告、聊天、工具输出或Git。

本机`curl --noproxy '*' --head https://<prod-host>/ui/`退出35（TLS握手失败），`http://<prod-host>/ui/`退出7（TCP80未开放）。现有证书缺IP SAN，正式证书/管理链路仍未验收。凭据可用不代表浏览器通路已经可用。

HTTP技术上可以作为院内看板入口；配置nginx同源HTTP监听并开放管理电脑→<prod-host> TCP80即可，不能把8000/8001/8002重新映射出去。HTTP会明文传输访问令牌和业务数据，本轮只答复可行性，**未改成HTTP、未开80**。若采用HTTP，应限定可信院内管理网访问。另一路选择是补含IP SAN=<prod-host>的院方证书并处理443 TLS策略。看板入口的选择不改变上报服务器→DMZ→平台的HTTPS/mTLS。

## 顺序补发与接手

系统unit：`blyb-mapping-recovery-20261026.service`；脚本：`/opt/blyb/tools/blyb-mapping-recovery-20261026.py`。

执行顺序固定10-25→10-24，每个窗口调用未改动的生产driver，传显式`--date`。开始前核对当前发行、driver SHA、常规driver未运行、无在途发送，两窗口必须已经DONE，禁止落入新HIS采集分支。保持原40/min共享节奏、证据/药房白名单/额度/12轮查询上限，CONFIRMED/ACCEPTED/SENDING/UNKNOWN按原程序跳过，不盲重发。第一窗失败或旧247摘要/断点变化则停止进入第二窗。

日志在`/var/log/blyb/mapping-recovery-20261026/`：baseline.json；progress.json；2026-10-25.json/2026-10-24.json；对应.stderr；最终final.json；异常error.json。单窗回执在该窗结束前可以是0字节。wrapper最终DRIVER_WINDOWS_COMPLETED仅指driver成功结束，不等于旧247人工核对完成或全部新受理单已经终态。

只读检查命令：

```powershell
$env:PYTHONUTF8='1'
python docs/operations/drug-trace-mapping-ntp-20261026/check.py app status
```

**不要重跑launch_recovery.py/ntp_apply/mapping_apply来做状态检查，也不要在本任务运行期间另开每日driver或第二个发送消费者。** 此处是一次性修复，不能复制去当新定时任务。原每日timer仍只处理昨日；wrapper完成前勿重启core容器。平台查询经真实worker完成，不在core直接调用发送/查询handler。

未来每日任务会读取新批准字典。若运行终态为失败，先看分日回执和旧单保护证据，再通过既有幂等driver受控恢复，不改库强制成功、不清配额/查询预算。

## 命令、证据与恢复

本轮均由Codex（模型unknown）执行；所有应用源码/镜像/表结构未改，无HIS写入或重新采集，无依赖安装。脚本AST语法校验通过；真实API登录、字典创建/查找、只读分日核验、NTP选源及补发均有下列现场JSON。没有为纯配置操作重跑全量回归；之前482通过/7跳过属于上一轮源码版本验证，不能冒充本轮新测。

| 操作 | 实际命令/结果 |
|---|---|
| 基线 | `python check.py app inspect`，exit0 |
| 只测时钟源 | `python check.py app ntp_probe`，exit0，3次有效UDP123响应 |
| NTP修改 | `python check.py app ntp_apply`，exit0，选中/Normal/开机自启 |
| 字典 | `python check.py app mapping_apply`，exit0，新增API批准revision，旧字典未改 |
| 分日核验及登录 | `python check.py app preflight`，exit0，782+815无提交、映射可解，两采集DONE |
| 私有登录文件 | `python save_login.py`，exit0，ACL设置退出0，无令牌输出 |
| 顺序补发 | `python launch_recovery.py`，exit0，systemd unit已启动 |
| 持续结果 | `python check.py app status`，exit0，见最新[app-status-20261026T041151333882Z.json](evidence/app-status-20261026T041151333882Z.json) |

上述脚本工作目录在本报告同目录；从根目录执行需补完整相对路径。源探测代码不改钟，真正时钟校准由ntp_apply启动的chronyd完成。

时钟旧配置备份：`/root/blyb-ntp-backups/20261026-u_mqpwf2/chrony.conf`；新SHA202464c634be609c97e1cd2447552de736b1f3e7e8f228d7a2bef90a4b0261e2。恢复旧时钟源会重现原不可达问题，不建议无故回退。

字典前后证据：`/root/blyb-maintenance-backups/mapping-20261026/20261026T040614Z.json`。字典是追加版本，不能删/改旧批准版本或回滚业务库覆盖新增发送记录；如身份需更正，应先停新发送，再新建更正revision并评估已发送单。私有登录文件没有修改服务器账号/密码/权限。

## 剩余人工及程序事项

1. 旧41受理+206未知仍需平台单据核查，不能据本轮字典修复重发。
2. 看板正式证书/443实际管理访问，或明确采用HTTP并部署相应入口；当前HTTP尚不可打开。
3. 补发完整回执、各药房首单与最终状态待持续观察；待确认不等于失败。
4. 原outcomes先limit200后筛问题的漏显、长采集心跳与07:30 SLA、其他历史区间仍为既有后续事项，未在本轮改动。

本轮交接：[DTR-MAPPING-NTP-20261026](../../ai/sessions/DTR-MAPPING-NTP-20261026.md)。
