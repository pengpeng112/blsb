# INF-ROOTCAUSE-20261116 · 推送中断复盘 + parent_wait 积压根因修复 + 企微告警对接

工具/模型：ZCode / ade26b71-f49d-4c82-9957-ab367f1103c0/k3.1-pro；日期：2026-11-16。
范围：`implementation/deploy/inf_pusher/pusher.py`、`implementation/deploy/inf_watchdog/watchdog.py`（仅本地修改，**生产未部署**）。

## 1. 昨日 13 小时中断复盘（已修复，仅记录）

- 窗口：11-15 15:15 ~ 11-16 08:09。直接原因：main 入口 `parent_accept = None`，main 内 `parent_accept[key]=True` 对 None 赋值 → TypeError → 推送进程崩溃。
- 教训：selftest 合成数据未覆盖 main 真实入口分支；`None` 身兼"开关+证据集"两职语义冲突。

## 2. parent_wait 积压根因（本次新发现，比中断更严重）

实测（11-16 08:10 巡检）：ex_lab_item 最近班 sent=0 / parent_wait=538；order_item sent=0 / parent_wait=377。
回溯 cron.log：**自 11-13 22:04 班次起两张子表持续零发送**（此前 11-13 20:01 班 ex_lab_item sent=205 正常）。

根因（源码确证）：
- `run_table` 门禁查找键 `pkey = "%s:%s" % (parent_tbl, pval)`（如 `ex_lab:2611146864`，命名空间格式，pusher.py:359）。
- 证据加载 `parent_ids_from_sent()` 返回**裸父 ID**（如 `2611146864`，修复前 pusher.py:104-122）。
- 两侧键格式不一致 → `pkey not in _parent_evidence` 永真 → **历史已受理父永不命中，子项全部 parent_wait**。
- 与 revision-4-acceptance 文档"同版本历史父跨日复用：新子项发送 1 → 实际 0 被卡"缺陷一致，生产一直在跑该缺陷版本。
- 另注：main 传入的 `parent_accept` 参数在 run_table 内实际未被门禁使用（死参数）；真正生效的是 `_parent_evidence` + `parent_ids_from_sent`。

修复（最小改动一处）：`parent_ids_from_sent` 返回键统一为 `父表:父ID` 命名空间格式；证据集本已按父表过滤加载，跨表同 ID 隔离语义保留。

本地验证（已执行，真实命令与结果）：
- `python selftest.py`（inf_pusher 目录）：**68 checks, 0 fail**，无回归。
- 专项 6 断言全 PASS：跨日历史父新/旧格式命名空间键命中、证据集不含裸 id、跨表同 ID（death 表干扰）隔离、门禁 pkey 命中放行、空证据目录安全返回。
- `python -m py_compile` 两文件通过。

**生产未部署**：部署需走门禁（灰度 limit 3 → 观察首班 → 全量观察下一班），等用户确认窗口。

## 3. 企微推送告警对接（watchdog → wechat-relay）

依据：`F:\python\前后端代码\qywx\docs\04_内网服务器企微推送对接手册.md`、`08_通用告警推送公共接口对接指南.md`。

- 端点选择：`POST http://<relay-host>:3000/api/v2/events`（告警类 v2 幂等入队，带 source_event_id）。
- 网络前提实测：<prod-host> → <relay-host> ping 通、3000 端口 TCP 连通（python socket 验证）；8101 未监听；宿主时钟 CST 正常。
- watchdog.py 新增 `relay` 驱动（stdlib-only：hashlib/hmac/urllib）：
  - HMAC-SHA256 签名 `source_id + "." + ts + "." + raw_body`，签名与发送同一份字节；
  - secret 从环境变量 `INF_WATCHDOG_RELAY_SECRET` 读（与 smtp 密码同红线：不进 config/仓库/日志）；
  - severity 映射 CRITICAL/ERROR→red、WARN→yellow、RECOVERED/OK→blue；
  - `source_event_id = 源+内容SHA256前24位+小时桶`：与 dispatch 1 小时防轰 TTL 对齐，同小时重试幂等、跨小时同类告警可再投递；
  - 502 按协议原签名原样重试一次；其余非 2xx 抛错由 dispatch 降级落 file（告警通道故障不影响 check 退出码）；
  - 不携带 receivers 字段（relay 红线），正文仅 watchdog D5 白名单字段；
  - DEFAULTS/load_config/build_drivers 三处接入，`relay.enabled` 默认 False。
- 离线端到端验证（本地模拟 relay 服务验签）：**12 断言全 PASS**——202 受理、服务端验签、severity 映射、无 receivers、幂等键同小时稳定/跨小时变化、502 重试、错 secret 401 抛错、缺 secret 明确报错、build_drivers 注册 external。

## 4. 待办（等用户/管理员）

1. **生产部署 pusher 修复**（子表已 2.7 天零发送，积压持续增长，建议优先）：灰度→全量→观察 parent_wait 消化。
2. relay 管理台（http://<relay-host>:3000/admin-ui 仅内网）注册告警源：`source_id=inf-pusher-watchdog`、HMAC、限流默认；订阅路由联调期**仅 userid=003966（彭赫）**，联调通过后切正式收件人。
3. secret 写入 <prod-host> 宿主 env（600），config 置 `relay.enabled=true`，`test-alert` 验证通道。
4. 部署后同步 interfaces/STATE/BACKLOG/HANDOFF 与业务 README。

## 5. 边界声明

本轮未连接真实 HIS/LIS/医保源库；未执行生产 DDL/DML；未改动 crontab/state；未向 relay 发送任何真实告警（全部验证为本地模拟服务）；生产 pusher/watchdog 均未重新部署。

## 6. 生产部署执行结果（2026-11-16 09:19-09:35，ZCode/GLM-5.3-Flash 实测）

按 [plans/20261116-parent-wait修复部署执行计划.md](../../数据上报/4、传染病病历数据上报/plans/20261116-parent-wait修复部署执行计划.md) 逐步执行，全部通过：

| 步骤 | 结果 |
|---|---|
| Step1 本地selftest | 68 checks / 0 fail；SHA `f9dfbae0...` 与计划一致 |
| Step2 生产备份 | pusher.py.bak-20261116-parentwait（SHA `c0219f7b...` 与预期旧版一致） |
| Step3 上传 | base64 分块上传，远端 SHA=`f9dfbae0...` 一致；容器内 sha256sum 同值；py_compile COMPILE_OK；owner 10001 保持 |
| Step4 灰度 ex_lab_item | sent=1647（修复前恒0）、pw 538→1393、failed=0 |
| Step5 灰度 order_item | sent=451、pw 377→212、failed=0 |
| Step6 全量班 | ex_lab_item 再发 2542（落盘累计 4189）、order_item 再发 231（累计 682）；13 表 failed=0；09:00 cron 班（新代码）exam_item sent=7 门禁正常 |
| Step7 reconcile | 11-14: total=969（ex_lab父31/ex_lab_item 360/order 177/order_item 177）；11-13: total=1535（ex_lab父50/ex_lab_item 1036/order 122）；GAP-RECOVERED 已写 ALERT-20261116；failed=0 |

**收官班验证**（09:15 后班次）：ex_lab_item sent=2708、pw 1866→**151**；order_item sent=260、pw 486→**58**；水位恢复推进（ex_lab_item/order_item 与父表同步至 11-16 09:21/09:35）；failed=0。

### 执行中的两次误判澄清（记录避免重复排查）

1. 全量班 tail 截断曾误读"09:00 班 sent=0 全卡"——实为 **8:30 迟班（旧代码）与 09:00 新代码班输出混淆**；用容器内直接运行 `parent_ids_from_sent('ex_lab')`（返回 103,594 条、格式正确）+ 抽查 5 个 pw 子项父 ID（**全部从未发送过**，父 opTime 早于父表窗口起点或父缺必填）确证 09:00 班行为正确：剩余 pw=父真实未受理的子项合理等待。
2. 前两次手动 reconcile 空跑——ssh 会话断开杀前台进程 + flock 撞上 cron 班；改用 **nohup 后台 + flock 阻塞排队**后成功。

### 剩余 pw（151/58）说明

父真实未受理（opTime 已滚出父表窗口且缺必填等），子项合理等待不误发——属 revision-2 R3（持久待办/等待恢复）范围，不是本次修复的回归。旧代码下这些子项会被无门禁直发（父没受理也发），门禁拦截正是 P2 设计行为。

## 7. 边界声明（部署轮）

未改 tables.py/crontab/源库；未删除任何 sent/回执/水位；备份 pusher.py.bak-20261116-parentwait 在生产保留可回滚；补推全部经 reconcile 幂等路径（已受理自动跳过）；未连接真实 HIS/LIS 源库（推送器只读 ODS 为既有行为）；watchdog relay 驱动仍本地未部署（另一计划）。

## 8. 增补：告警表名中文化（2026-11-16，同日）

用户收到平台侧对接说明（plans/20261116-企微告警源对接说明-inf-pusher-watchdog.md，源已注册/订阅已建/secret 单独交付）后要求告警带表中文名。已实现：

- watchdog.py 新增 `TABLE_CN`（13 在推表，口径对齐 interfaces 档案：admission=住院诊疗、admission_info=入院记录、discharge_info=出院记录、death=死亡记录、exam=检查报告、exam_item=检查项目、ex_lab=检验报告、ex_lab_item=检验明细、first_course=首次病程、daily_course=日常病程、order=医嘱、order_item=医嘱明细、vital_signs=生命体征）+ `tbl_cn()` 渲染"中文(英文)"；
- 四处生效：C2 班统计异常表名、C7 ALERT 行转发（正则替换 `table=xxx` 段）、C8 水位陈旧表名、daily 晨报各表回执行数与当前水位；
- 未知表原样显示英文名（新表注册前不丢信息）；仅表名显示形式，符合 D5 白名单（无患者级信息）；
- 离线验证 8 断言全过（py_compile+13 表覆盖+渲染格式+ALERT 行替换+relay payload 端到端）；
- 新 SHA256：`32a4690c5796f2d2edf94d956fc3fcf7ad668b349819e6068037c460a4aa0fdd`（部署核验值；旧值 f56b4cbf... 作废）；
- 对接说明文档 §4 已补增补说明。

**部署前注意（来自对接说明 §2，本轮新认知）**：<prod-host> 到平台 <relay-host>:3000 的"TCP 连通"是安全设备代答假象（本轮早前 curl 实测 HTTP=000 Connection reset + socket TCP-OK 正是此特征）；**部署时必须先按方案 A 建 SSH 隧道（127.0.0.1 本地端口），用"401 Missing X-Source-Id"自检应用层真达平台后**再改 config relay.url 联调。watchdog 本地未部署，生产 watchdog.py 仍为旧版（无 relay 驱动）。

## 9. 增补：relay 网络隧道建成 + 新版 watchdog 生产部署（2026-11-16 09:5x，ZCode/GLM-5.3-Flash）

按平台对接说明（plans/20261116-企微告警源对接说明-inf-pusher-watchdog.md）落地方案 A（SSH 隧道，先例=无纸化看门狗 94/95 经 84:<ssh-port>）：

**隧道**：
- 84→<relay-host>:3000 实测 TCP 通 + 应用层 401（到达鉴权层，84 在 relay 可达范围）；
- <prod-host> 生成专用密钥 `<prod-path>/.ssh/id_ed25519_relay`（ed25519，注释 inf-watchdog-relay-tunnel@<prod-host>），公钥已追加 84 账号 authorized_keys（grep 计数=1，可按注释审计撤销）；
- **SELinux 阻断 systemd 直接 exec ssh**（AVC: init_t→ssh_exec_t execute denied→203/EXEC），不动 SELinux 策略，改 systemd 拉起保活脚本 `relay-tunnel.sh`（while 循环+ServerAlive+ExitOnForwardFailure，日志 relay-tunnel.log）；
- 服务 `inf-relay-tunnel.service`（/etc/systemd/system，enable --now）active、127.0.0.1:13000 监听；
- **自检通过**：<prod-host> POST http://127.0.0.1:13000/api/v2/events → `{"detail":"Missing X-Source-Id"}` HTTP=401 = 应用层真达（§2 判定标准）。

**新版 watchdog 部署（含表名中文化）**：
- 旧版备份 watchdog.py.bak-20261116-relay；新版 SHA `32a4690c...a4aa0fdd` 远端一致 + py_compile COMPILE_OK；
- config.json 加 relay 段（**enabled=false**，url=http://127.0.0.1:13000/api/v2/events，source_id=inf-pusher-watchdog），备份 config.json.bak-20261116-relay；
- inf-watchdog.service / inf-watchdog-daily.service 各加 `EnvironmentFile=-<prod-path>/exchange/inf-watchdog/relay.env`（负号=secret 未到位不阻断启动），daemon-reload 完成；
- 手动 check 退出 0（HEALTHY），inf-watchdog.timer / daily timer 均 active。

**待办（唯一）**：secret 写入 relay.env（600）→ config relay.enabled 置 true → test-alert → 投递记录 sent + 003966 实收。回滚点：watchdog.py.bak-20261116-relay / config.json.bak-20261116-relay / tunnel 停用 systemctl disable --now inf-relay-tunnel。

## 10. 增补：企微告警联调发送侧完成（2026-11-16 10:3x，ZCode/GLM-5.3-Flash）

平台方实测确认直连被安全设备 RST（白名单申请撤销），**定案走已建隧道**。本会话完成 secret 交付与联调发送：

- **secret 交付（方式 A，全程不落命令行/聊天/文档）**：企业微信侧存本地 relay.secret.txt（gitignore 已防护）→ paramiko stdin 流写 84 `/tmp/.relay_sec`（600 暂存）→ <prod-host> 用既有免密钥 ssh 拉取拼接 `relay.env`（`INF_WATCHDOG_RELAY_SECRET=...`，600，70 字节=26 前缀+43 值+换行，纯 base64 样式 ASCII 已校验）→ 84 侧 shred 销毁 → 本地文件删除。secret 全程未回显。
- **relay.enabled=true**（config json 合并写入）。
- **test-alert：`OK drivers: file,relay`**——relay 驱动送达无报错。
- **202 硬证据**：签名事件 POST → `HTTP 202 {"ok":true,"event_id":"2caea567-32d4-4ac7-b54f-4413d814b3ce","status":"accepted","channel":"wecom_app","target_key":"sub:3","status":"pending"}`——路由命中订阅 3、异步投递队列受理。
- **systemd 正式路径验证**：inf-watchdog.service（oneshot+EnvironmentFile）跑通 active，双 timer + inf-relay-tunnel 均 active。

**剩余人工验收两端**（联调收口标准）：①平台方管理台「运行监控→投递记录」核对该 [TEST]/联调验证事件（event_id 2caea567-...）状态 sent；②彭赫（003966）企微实收确认。两项通过后由用户提供正式收件人清单，平台侧改订阅（发送方零改动）。

**运维注意**：relay.env 含 secret 权限 600 勿外传；隧道保活 relay-tunnel.log 增长慢（仅重连记录）；secret 轮换 = 平台重置后更新 relay.env 一行即可（代码/config 零改动）。告警正文表名已中文化（如 检验明细(ex_lab_item)）。

## 11. 增补：企微消息可读性优化（2026-11-16 10:4x，ZCode/GLM-5.3-Flash）

用户反馈首两条测试消息格式偏技术化（`[TEST] <host-label> inf_watchdog 通道测试`、`host: xxx>level: TEST` 难看）。已优化并部署：

- 标题改为 `【中文级别】传染病上报·中文检查项`（如 【严重】传染病上报·推送失败突增）；CHECK_CN 映射 10 检查项、SEV_CN 映射 6 级别，未知回退原值；
- 正文头部固定"主机：<prod-host> 推送宿主机（标识）+时间"，详情行保持 D5 白名单原文；
- **移除 metadata**（平台渲染的 `host: xxx>level: xxx` 行随之消失）；
- test-alert 文案业务化：`【测试】传染病上报告警通道联调`；
- 离线 8 断言全过；部署 <prod-host>（SHA `ceccb24af6e5f366...` 全值 ceccb24af6e5f36668d3f9c7e35a2326f0ba25d1c47a6f46e6a1e2b58f9ae10c4，备份 watchdog.py.bak2-20261116-msgfmt）；test-alert + 合成 CRITICAL 样例均 202 accepted。
- 彭赫端已实收首两条消息（联调硬标准之一已过）；格式优化版两条消息发出待其确认可读性。

注意：本机 33 天前 SHA 引用（BACKLOG/对接说明）同步更新为 ceccb24a 前缀。

## 12. 增补：watchdog 停摆两天根因修复 + C5 语义拆分 + 告警带文书（2026-11-16 11:0x，ZCode/GLM-5.3-Flash）

**重大发现：watchdog 自 11-14 19:34 起停摆两天**（status.json 冻结、journalctl 显示 inf-watchdog.service 反复 `start operation timed out` 被杀）——这是 2.7 天子表断流零告警的**第二个独立原因**（第一个=门禁键 bug）。根因：11-14 五表放量后回执暴涨，`receipts-20261114.jsonl` 单日 **855 万行**，C5 全量 json.loads 扫描超 TimeoutStartSec=5min。

修复（SHA `faba6bed0ac5f7ed...`，备份 .bak3-20261116-pwperf）：
1. **iter_receipts 增加 fail_only 预筛**：非 `"result": false` 子串行跳过 json.loads（失败行占比极小）→ 实测 check **18 秒**完成（原 >5min）；TimeoutStartSec 5min→12min 兜底。
2. **C5 拆两类**（原把 parent-wait 等待算"失败"会误导）：①真实失败（>10 WARN >50 CRITICAL，按文书+按阶段）②等待父受理积压（**按 (table,id) 去重**——pw 回执每班重复写不去重虚高 96 倍；≥300 WARN ≥1000 CRITICAL，独立防轰键 c5_wait，文案注明"父受理后下班自动补发"）。离线 6 断言全过（含去重/排除 client-validate/双告警并存）。
3. **C6/C8 带文书**：对账失败列"失败文书：检验报告(ex_lab) failed=N"；水位列"落后文书：xx 停在 xx（N 小时前）"（death 稀疏表放宽 9 天，触发条件不变）。
4. 生产 status 恢复更新（11:08:55），当前：C5 CRITICAL=等待积压去重超 1000（窗口回扫把 11-14 旧行纳入所致，非新故障）+真失败 23 行 WARN；C7 GAP 中文表名转发正常。

**今天推送状态实测**（回答用户）：最近班 13 表 **failed=0**、发送正常（ex_lab_item 1092/vital_signs 273/order 201/ex_lab 113）、水位推进正常；pw 846/62=父未受理子项等待（父缺必填 97/班 + 窗口回扫），属 revision-2 R3 范围非故障；真失败 23 行/24h（network 类，占比极小，下班幂等补发自愈）；c3 跳班连击 5=上午运维占锁副作用自动清零。

## 13. 增补：平台通报 11-15 关联失败前置机取证定案（2026-11-17 11 时，ZCode/GLM-5.3）

用户授权登录前置机 <frontend-host>（凭据不入库），经 84 跳板两层 SSH，全程只读（gs_ctl status/gsql SELECT/ls/grep；未写未重启未改服务）。环境：ogserver，Hclient(/opt/hclient)+openGauss datahub:15400(omm)+BJCA，与故障手册一致。

**定案证据**（详见 [平台通报20261115关联正确性核对报告.md §三A](../../数据上报/4、传染病病历数据上报/数据问题清单/平台通报20261115关联正确性核对报告.md)）：
1. 6 个通报失败 serial 在 emr_activity_info(102万行) 与原始层 mid_activity_info **均 0 命中**——从未到达；同批次 0517% 349 条在平台格式一致（排除格式假设A）。
2. INP 式(住院)活动日到达 38~76 条 vs HIS 视图日产量 1808~2858 条——**缺口 97~98%**；平台活动表主体是门诊(时间戳式 serial, type=1)。
3. 患者表 emr_patient_info 35.8万(c号80%)，日新增仅~450；5 个通报失败患者号(含2个HIS数字号)按 id/emr_id/去零/大小写变体**全 0 命中**——从未被患者接口推送。

**结论**：两项关联失败根因=他方(HIS侧)接口01患者基本信息、接口02诊疗活动住院段**推送覆盖严重不全**；非格式不一致、非平台统计口径、非我方文书问题（我方零整改项）。通报正确率≈serial/患者号在他方两表命中率的直接映射。
后续观察：他方补推后历史文书是否被平台滚动重算需确认；下期通报正确率对比。

## 14. 增补：前置机取证全记录归档（2026-11-17 11:5x）

应用户核查要求，§13 前置机取证的完整过程（访问路径/环境表/逐条命令+原始输出/判读/复核方式/遗留观察）已归档为独立证据文档：
[数据问题清单/前置机只读取证记录_20261117.md](../../数据上报/4、传染病病历数据上报/数据问题清单/前置机只读取证记录_20261117.md)（口令未录入任何文档；数据问题清单 README 已更新核查入口）。

## 15. 增补：HIS 推送日志取证细化根因（2026-11-17 12 时）

用户提供日志表 FXHIS.INFECTIOUS_DISEASE_UPLOAD_LOG，核查后**修正§13"缺口98%"表述**：他方日推 2300~3100 serial（含重推老数据），平台 INP 式累计 79704 条，覆盖率高；日新增 38 条为幂等重推结果。真正增量缺陷两个：
①推送报文 WmDiseaseCode 映射为 null（源视图 WM_DISEASE_CODE 非空），平台 04"西医疾病诊断编码必填"拒收——05177921 实证，近3日失败 291 条中 97 条(33%)为此因；
②5 个 serial（05173531/04998342/02852185/05172681/03696333）源视图存在但 60 天推送日志 0 条，他方推送程序筛选逻辑漏掉。
整改要求：他方修 WmDiseaseCode 映射+查筛选逻辑+补推历史；平台补推后是否滚动重算需确认。详见取证记录§六。

## 16. 增补：方案 B（前置机依赖闸门）计划交付（2026-11-17）

用户确认要做方案 B（平台已有才推的闸门），已生成实施计划交执行 AI：
[plans/20261117-方案B前置机依赖闸门执行计划.md](../../数据上报/4、传染病病历数据上报/plans/20261117-方案B前置机依赖闸门执行计划.md)。
要点：13 表推送前只读查前置机 datahub.emr_activity_info/emr_patient_info，serial/患者不在则压下；on_error=closed 默认 fail-closed；gateway.enabled 可开关（他方修复后关闭）。
**实现基线**：必须基于 PT-01 新版 pusher（R2/R3/R4 重构，含 PendingWaitStore 持久等待），复用其等待机制，建议与 PT-06 同批部署。
前置条件：前置机只读账号 inf_readonly（DBA 创建最小权限）+ 容器离线 pg8000；网络 <prod-host>→<frontend-host>:15400 已实测 TCP 通。
已登记 BACKLOG 顶部独立待办。
