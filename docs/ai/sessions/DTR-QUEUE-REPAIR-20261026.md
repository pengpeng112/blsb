# DTR-QUEUE-REPAIR-20261026 交接

Codex / model unknown；session20261026T004340Z-4d9bbc61。用户要求解释41/206/815并一起查看超时，按持续修复/部署/发送授权落实。

[完整实录](../../operations/drug-trace-queue-repair-20261026/README.md)，脚本和脱敏证据同目录。实际运行queue-20261026T010000Z-r2，012和依赖不变。

已实现/部署：send50/query60优先级；每5槽1后台，原lease_epoch不改；driver排队1800s/执行180s分开等原job、未知/超限仍停新发送；12轮耗尽和已有query在途过滤；列表/详情分开展示query错误与耗尽。局部47，全量482pass7skip0fail；构建/离线freeze/源码/安装SHA匹配。原测试fixture错误和过时中止回归保留，最终单次全量为绿。

09:01:52通过原daily-upload.service恢复10-25，只复用原DONE采集和567 PREPARED，前三张已确认跳过；09:09:14新增71次已结束HTTP200受理、65确认，余496PREPARED，全库477确认/47受理/206未知、quota730。旧41+206状态版本hash不变，r10断点46不变，未新采集/HIS访问、未清配额或重发未知。仍RUNNING，不称567全部完成。

观察器blyb-queue-observe-20261026.service，4h只读，每分钟写/var/log/blyb/queue-recovery-20261026/{latest.json,progress.jsonl}，业务退出写final.json；超时写observer-timeout.json。不对外查询/发数据、不排队、不写业务库。当前回执/var/log/blyb/daily-upload/20261026-090152.json可能仍0字节，结束才写；原061029失败文件保留。

部署备份<prod-path>/backups/20261026T005833Z；driver备份/root/blyb-maintenance-backups/20261026T010142Z-driver；升级日志/root/blyb-maintenance-backups/upgrade-queue-20261026T010000Z-r2-r1。

用户仍未回复050121/050116是否均属本院且共用已登记ref_ent_id。批准字典未动；旧10-24缺映射815（810/5），10-25另782（775/7），不混同。旧41/206自动查询预算12已满，过滤无效再查不等于核清；需平台结果或有审计的受控人工复查，不删job清次数。不要重索AppKey/Secret。

新发现未修P1：submission-outcomes先取最近200再筛only=problem，新日大量PREPARED会遮住旧UNKNOWN，列表0不等于206清零；应SQL先筛后limit、保留租户/LEFT JOIN语义并增加旧异常反例。另NTP/CA/TLS、长collect心跳/租约、日报SLA、跨日补偿仍为后续项。

下一棒先check.py app observe并读final/回执/日报终态；当前恢复时不再起driver/worker，不重复deploy/install_driver/recover等一次性脚本。若用户确认药房，使用原API新建/批准新revision再校验，不原地改冻结或批准字典。
