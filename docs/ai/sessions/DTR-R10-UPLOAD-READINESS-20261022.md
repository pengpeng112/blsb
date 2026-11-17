# DTR-R10-UPLOAD-READINESS-20261022

- 工具/模型：Codex / unknown。
- 会话：20261022T092613Z-a3fc391c。
- 用户要求：r10 SQL已执行，核查能否上传；此前已授权符合条件后测试一张。
- 范围：本轮探针/证据、共享状态和对应私有基线，无业务源码或远端运行配置修改。

## 已实测

1. 新只读账号登录成功，PHARMACY两视图VALID、17/18列。16条限量SELECT均成功。
   20头中15张正常已完成、5张住院仍UNKNOWN；100正常码样本20位数字、删除原值0。
2. 一张1头4码，主/码revision一致、无重复；将本地quality.py实际纯函数AST送入远端进程，
   以现数据评估VALID、0阻断，仅包装层级警告；数据不离远端、不导出患者/实际码/单号。
   它是纯质量评估，不是生产采集或已批准未报单，也未验证该单全部未拆零。
3. <prod-host>首轮SSH255/Connection refused，保留application-r1失败；r2 SSH0/worker0成功。
   仍fb-20261021T164800Z/6healthy、首屏修复未部署、DSN空、来源DRAFT无配置、医院映射空、目的地DRAFT。
   真实平台配置PUBLISHED、签名true，正式链路状态查询成功1次，业务单据/采集/submission/额度0。
4. DMZ仍仅query.billstatus；官方HTTPS HEAD200、时间接近官方，长期NTP未同步/测试链仍在。
   上传白名单没开是应用配置，不报告为TCP443仍不通。

## 命令/结果

- check_reader.py r1：退出0，16 SELECT成功，质量VALID。
- check_runtime.py application r1：退出1，SSH拒绝；r2：退出0，SSH/worker/只读平台查询成功。
- check_runtime.py dmz r1：退出0。
- 本地视图token/源码编译/事实回执/链接检查见evidence/local-validation.json。
- 未修改HIS/平台/远端配置，上传0；未构建/部署或重跑业务测试。

## 接续

**HIS视图已通过，不再要求用户重改SQL或重建账号。** r10执行回执和真实结构/质量事实已齐，
后续工作是部署前轮修复、安全绑定来源、正常发布映射/启用目的地、准备真实发送证据/必要方法/单张额度。
院方尚未指定确认未报首张，不能把本次随机4码候选当未报确认。
详见[当前核查/接续步骤](../../development/drug-trace-r10-readiness-20261022/README.md)。
