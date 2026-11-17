# 运维手册（DTR-FIRSTBILL-EXEC-20261021 现场实态）

记录本轮实际执行的配置发布/链路联调操作、后续手动批次/停发/回退命令与副作用。
**不含任何密码/Secret/私钥/原始患者与码明细**（令牌/Secret 均经受限通道，
见各命令说明）。

## 一、当前现场状态（2026-10-21 17:0x UTC 实测）

| 对象 | 状态 |
|---|---|
| <prod-host> 发行 | `/opt/blyb/releases/fb-20261021T164800Z`（current）；迁移 001—011 |
| 签名资格 | worker `SIGNATURE_VERIFIED=True`（发行内置，随代码回退） |
| 目的地 | `alihealth-drugtrace-retail`（345df8f4-…）is_mock=**False**，active config=**PUBLISHED rev1**（2e138e50-…） |
| 字典 | bill_type=official-202601-r1（PATIENT_DISPENSE→321）、query_bill_type=official-202601-r1（→A）已批准；ref_ent_id 仍 draft-1（缺真实药房代码） |
| 证据 | query/reconcile 键 EXT-09 **APPROVED**（有效至 2027-10-21）；collect/send 未登记 |
| 就绪 | LOCAL_SERVICE=READY、PLATFORM=READY、SOURCE/SEND_SCOPE=NOT_READY、NETWORK=UNKNOWN（如实） |
| DMZ | 发行 20261021T113750Z 不变；allow_ips=[<prod-host>, **<relay-net-host>**（跨区 NAT 出口，实测）]；方法白名单仍仅 query.billstatus；enable_inout_upload=false |
| 时钟 | <prod-host> 与平台差≤10s（未NTP同步）；DMZ 慢约32.4分 |
| 备份 | 升级前 `<prod-path>/backups/20261021T165233Z`（哈希校验过）；DMZ 配置 `/etc/blyb-dmz/forwarder.json.pre-fb05` |

## 二、本轮实际执行过的操作（含副作用）

1. **升级 <prod-host>**：备份（短停 4 写入口约 21s）→ install.sh 新发行 → 6 容器
   重建（restart 计数清零属升级预期）。旧发行目录/环境备份保留。
2. **发布真实配置/字典/证据**：platform_admin（bootstrap 令牌，经
   runtime.env→root-only 临时文件→容器 stdin，全程不入 argv/日志）调用
   `/api/v1/drug-trace/destinations/{id}/config-revisions`、`/dictionaries`、
   `/drug-trace/evidence`。审计记录在应用 audit 表。
3. **DMZ allow_ips 追加 <relay-net-host>**：医院跨区 NAT 使 <prod-host> 出站到达 DMZ
   时源地址改写为 <relay-net-host>（转发器日志 reject ip=<relay-net-host> 实证）。
   追加后 systemctl restart（服务中断数秒）。**主门禁不变**：mTLS 客户端
   证书 + TOP 方法白名单 + 限流；allow_ips 是纵深防御层。
4. **只读联调**：worker 内一次性诊断脚本（真实配置+真实签名）查询探针单号
   3 次；平台只读调用合计 3 次，无上传。

## 三、常用运维命令（已按实参数核验或为既有脚本路径）

```bash
# 就绪报告（动态；多目的地时加 --destination）
docker exec blyb-core-1 python -m apps.init_drug_trace --readiness \
    --tenant first-hospital
# 现状+缺口
docker exec blyb-core-1 python -m apps.init_drug_trace --check --tenant first-hospital

# 目的地配置预览/发布（默认 check 只读；dry-run 零写入）
docker exec blyb-core-1 python -m apps.publish_drug_trace_config \
    --tenant first-hospital --check
docker exec blyb-core-1 python -m apps.publish_drug_trace_config \
    --tenant first-hospital --destination <dest-id> --config /root/<cfg.json> --dry-run
# 发布（先 --dry-run 后 --apply --publish --set-real；publish 需 platform_admin 走 API 时同理）

# 停发（任一即生效；发送 worker 会复核拒绝）
# a) 撤销 send 证据：POST /api/v1/drug-trace/evidence/{id}/revoke（platform_admin）
# b) DMZ 收口：恢复 /etc/blyb-dmz/forwarder.json.pre-fb05 后
#    systemctl restart blyb-dmz-forwarder.service
# c) 配置换代：发布新 ConfigRevision（旧 PUBLISHED 自动 RETIRED）

# 查询能力独立使用（不受停发影响）：EXT-09 证据在即查询可用
#   UI：https://<prod-host>/ui/ 药品追溯 → 查询；或 submissions/{sid}/query API

# 手动批次采集（解锁 EXT-01/02 后；显式小窗口，绝不全期扫描）
#   UI/API：POST /api/v1/drug-trace/collection-runs
#   {connector_id, window_begin, window_end, mode:"oracle", profile:"alihealth_yljg_retail_v1"}

# 回退 <prod-host> 应用：恢复 runtime.env 备份 → 切回旧 release 目录 → install.sh
#   （数据库只进不退；先取新备份再回退）
# DMZ 回退：cp forwarder.json.pre-fb05 forwarder.json && systemctl restart …

# 备份校验（只读）：cd <prod-path>/backups/<dir> && sha256sum -c SHA256SUMS
# 恢复演练（只读）：restore.sh <backup> --validate-only
```

## 四、正式证书轮换流程（院方链到位后）

1. 受控接收：服务端 SAN 含 <prod-host>/<relay-net-host> + serverAuth；worker
   clientAuth 新客户端身份；运维探针独立 clientAuth；CA 链文件。
2. <prod-host>：/etc/blyb/certs 平行放置新链 → 更新 runtime.env 证书路径/挂载 →
   compose 渲染校验 → 维护窗口滚动重启 → 复测 mTLS health/无证书拒绝/UID 权限。
3. DMZ：/etc/blyb-dmz/certs 替换 forwarder/clients-ca → install.sh --verify。
4. 两机保留旧链配置直至新链全验通过后移除；全程不出现 -k/CERT_NONE。
5. 证书内容只输出元数据（日期/SAN/EKU），不打印私钥。

## 五、NTP 校时（院内源到位后）

1. 确认源可达（DMZ/<prod-host> 分别 chronyc sources 出 Reach）。
2. 维护窗口改 /etc/chrony.conf server 行 → systemctl restart chronyd。
3. 记录偏差收敛（chronyc tracking）与 timedatectl 'NTP synchronized: yes'。
4. 不用证书回溯签发替代校时；DMZ 慢 32 分在 TLS 有效窗内不阻断转发。

## 六、风险与注意事项

- **NAT 出口 IP**：院方若调整跨区 NAT，<relay-net-host> 可能变化——转发器 403
  reason=source_ip_not_allowed 日志可定位新地址（加白前先核实来源）。
- **时钟漂移**：<prod-host> 未 NTP 同步，当前与平台差≤10s；长期漂移会先影响 TOP
  timestamp 校验——发送前建议复测（diagnose 脚本模式）。
- **query 限流**：官方明示 query.billstatus 易限流；查询代次上限 12 次已内置
  退避，勿手动循环调用。
- **Secret 边界**：AppSecret 仅 worker 环境；引导令牌仅 runtime.env(600)；
  任何诊断不得打印（本轮全部经布尔/白名单输出）。
