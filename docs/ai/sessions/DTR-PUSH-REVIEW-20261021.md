# DTR-PUSH-REVIEW-20261021

工具/模型：Codex / unknown。登记：20261021T152826Z-50526c58。
范围：docs/ai、docs/development/drug-trace-push-review-20261021；未修改业务源码及远端配置。

结论：L1 部署成果经本轮现场核验成立；可以继续只读联调，真实上传仍阻断。
完整[复核与接续步骤](../../development/drug-trace-push-review-20261021/README.md)。

实测命令：该目录 probe_readiness.py target / dmz / followup，均退出0；证据 CAPTURED
不代表所有子项成功。第一次 ledger 查询误用 version 列、NTP show 不兼容，各退出1；
保留原证据，改 step/name、timedatectl status 后均退出0。Python 编译/文档链接检查见
任务目录 evidence/local-validation.json。

现场：app dpl-20261021T111000Z、6 容器 healthy、迁移001—011；worker Thick19.32、
密钥可读不可写；<prod-host>→DMZ443真实mTLS HTTP200 UP、→HIS1521 TCP通；DMZ
20261021T113750Z、unit active、上游 HTTPS HEAD200/TLS0。DMZ约慢32分钟且未同步。

直接读取应用库（显式只读事务、指定 first-hospital）：连接器DRAFT/无active config，
目的地Mock DRAFT/无revision，destination配置0，三字典draft-1，业务单据/采集/证据/配额/
submission全0。worker DSN空；真实签名断言抛CapabilityError；DMZ白名单仅query.billstatus。

未执行：HIS登录、DDL、生产配置发布、SDK验收、真实业务查询、上传、校时/证书/服务变更。
未重跑业务测试；本轮没有业务代码修改。沿用上棒全量380/7/2及fake修补后局部56通过记录。

共享STATE清理被部署证据覆盖的“未部署/端口不通”当前描述；历史报告不改。
BACKLOG保留DTR-CERT-ROTATE/L2-SOURCE/L3-FIRSTBILL，新增动态readiness维护项。
下一步按复核文档六步完成来源、资格、真实配置及首张具体scope；不重复索要已有凭据，
不手改签名开关、不填假证据，不把既有测试意图扩大为任意历史批量发送。
