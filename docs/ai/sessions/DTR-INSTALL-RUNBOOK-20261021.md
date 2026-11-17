# DTR-INSTALL-RUNBOOK-20261021

Codex / unknown；session `20261021T060306Z-363dc338`。
用户要求：核对如何安装/开启，供其他AI一次执行；本轮仅编制可执行部署单，不代为上机。
范围：docs、本机受限`.ai/private/drug-trace-install-runbook-20261021/`。

## 交付与真实核查

- [安装与启用执行单](../../deployment/drug-trace-install-20261021/README.md)。
- [下一AI直接执行提示词](../../deployment/drug-trace-install-20261021/NEXT_AI_DEPLOY.md)。
- [实际材料审计](../../deployment/drug-trace-install-20261021/evidence/material-audit.json)。

核实发行真正位于implementation/var，不在根var；应用发行根就是rel-fin，无fin-ID额外子目录。
应用147条、DMZ14条SHA校验全部匹配，DMZ tar整体SHA与sidecar一致；读取manifest时没有显示私钥内容。
旧dmz-release-pack.json是120644旧记录，不能覆盖130019实际最终包。

发现并纳入安装前置：

1. Linux镜像pip_freeze/依赖锁缺oracledb；药品Oracle仓储默认Thin、未见显式Thick初始化。
2. linux/lib.sh统一compose/compose_project没有药品override，临时双-f不保证后续运维保持配置。
3. 包内forwarder.crt为TEST-ONLY，SAN只有127.0.0.1/dmz.local、不含DMZ真实IP，到期11-19；启动前须正确证书。
4. worker UID10001与宿主root700/600挂载私钥存在可读性要求，计划明确受限group/只读挂载校验。
5. DMZ安装器ss路径解析有静态疑点，交下一AI用实际输出/固定样例核实修正。本机Git Bash不在预期路径，未执行该shell反例，不冒称已复现。
6. init工具默认首tenant、目的地Mock、配置schema仅展示；部署单要求明确tenant及真实配置API，不以--apply冒称已启用。

联网只查python-oracledb官方初始化/Thin/Thick说明，确认旧Oracle需Thick及首次连接前初始化；未安装包或访问HIS。
业务代码未改、未构建新发行；上述改动由下一DTR-DEPLOY任务实际实现并重打包，旧发行不原地修改。

## 执行范围与验收安排

区分L1服务启动、L2真实采集、L3真实推送；缺L2/L3资料不阻断独立L1工作。
当前用户交其他AI明确执行后，按其实际部署委托推进，不沿用旧无授权记录停止；院方实际窗口限制仍遵从。
备份升级短时影响现有病理/API，先告知并备份恢复基线；不自动HIS DDL/DML、生产restore或大幅跳时。
列DPL-P01—P05安装前置与DPL-A01—A08现场验收，实测/未测/外部阻断分开。
仅现有真正存在的安装/verify/init入口写为可用命令；建议新增持久开关明确为待实现，不冒称已有。
已有凭证位置沿既有受限目录与sjzc，不重复索取、不回显，不把AppSecret放DMZ。

## 本轮验证与交接

本轮是纯计划/材料审计，无新pytest、无SSH/HIS/平台业务请求、无部署或真实上传。
共享文档修改前保存工作字节；结果见
[verification.json](../../deployment/drug-trace-install-20261021/evidence/verification.json)。
下一任务DTR-DEPLOY-20261021待执行；本轮编制完成不等于L1—L3已完成。

## 实际收尾结果

project_docs.py inventory exit0（216份）；受限verify_runbook.py经原凭证包装器执行exit0：
P01—P05/A01—A08编号完整唯一、本轮文档链接缺失0、Secret明文命中0、来源/归档hash异常0、
git diff --check exit0，私有目录Git忽略。
全仓project_docs.py check仍exit1，233处已有历史失效链接未改、未加白名单造绿。
材料审计147/14完整性通过不等于生产启用通过，新修复/重打包/安装都未执行。
本轮任务编制交付达成，标completed；DTR-DEPLOY-20261021仍待下一AI按当前用户委托执行。
