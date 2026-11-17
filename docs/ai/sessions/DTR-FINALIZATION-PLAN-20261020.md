# DTR-FINALIZATION-PLAN-20261020

工具/模型：Codex / unknown；session `20261020T061842Z-3813446d`。
范围：docs、本机受限计划基线目录；用户要求详细计划，交另一AI尽量一轮完成剩余工作。
本轮交付是**计划完成**，下一任务DTR-FINALIZE-20261020仍**待执行**。

## 交付

- [执行任务书](../../development/drug-trace-finalization-20261020/README.md)：FIN-00—11，共12工作包。
- [验收矩阵](../../development/drug-trace-finalization-20261020/ACCEPTANCE.md)：FIN-A01—38，全部为待验证要求。
- [网络与资料协调](../../development/drug-trace-finalization-20261020/EXTERNAL_INPUTS_AND_NETWORK.md)：真实方向/端口、过去结果、非网络输入、已有资料无回显入口。
- [可直接复制的下一AI提示词](../../development/drug-trace-finalization-20261020/NEXT_AI_PROMPT.md)：完整执行边界、局部/全量命令、交付要求。
- 更新现有STATE/BACKLOG/HANDOFF、docs目录和catalog；未创建平行业务状态总表。

## 编制依据与重点

只读阅读最新10-20联调、10-19HIS事实、FIX3与当前源码，包括DMZ forwarder/install/service/config，
来源profiles/r6、平台transport/client/build_client/query_dates/readiness，以及实际Compose/发行/证书工具。
本轮未连接服务器、HIS或官方业务API，未读密钥/数据库托管凭据。

源码静态发现的具体收尾项已进入任务书：旧systemd可能不支持现有unit选项；
install --verify服务inactive/无443仍可能成功；旧python存在时运行时准备不足；
材料unit实际来源与哈希、仅代码prev回退、TLS/请求读阶段预算/并发和日志查询串边界需要补强；
session未由build_client端到端传入；历史业务日期与平台查询日期语义需核对。
这些为代码/文档依据，未声称现场复现或全部都是新的已验证漏洞。

联网只阅读systemd219与Python官方文档，核对旧systemd的socket activation、journal与Directories配置语义。
推荐.socket托管443＋非root应用接收FD，要求下一AI在精确旧版本环境实测，未假定现场一定systemd219。
不建议改编程语言/重建框架或对系统Python setcap来解决部署。

非网络依赖单列：正式证书与mTLS、兼容运行时、HIS readonly/视图/规则/变更源、SDK或明确替代验签依据、
321/323及查询状态/日期、既往上报与首张具体范围、现场维护窗口。用户现有凭证不重复索取。
可独立离线工作持续推进，不能为了“一次完成”伪批字典、改门禁、源库DDL或发任意历史单据。

## 验证范围

本轮纯计划/文档维护，不修改implementation业务代码、不运行pytest、不构建、不部署、不发真实数据。
局部80通过1跳过与历史334/6为此前结果，不能登记为本轮通过。
执行文档catalog、链接、来源档案hash、工作包/验收编号唯一性及路径存在性、Secret明文检查、git diff --check。
计划共享文件修改前保存当前字节于本机受限`.ai/private/dtr-finalization-plan-20261020/baseline/`。
检查结果见[verification.json](../../development/drug-trace-finalization-20261020/evidence/verification.json)。

## 交接

下一AI按本任务包登记DTR-FINALIZE-20261020后执行，先完成开发/发行，再按实际授权与窗口禁发部署。
网络开后先真实只读正式链路，再一张完整未报单据与终态/核码；当前真实上传次数仍0。
不得把本计划编制完成或38项验收要求存在，写成代码/现场/真实上报全部完成。

## 实际收尾结果

project_docs.py inventory exit0，目录由203增至208份（本包4文档＋session）。
受限包装器启动本机私有verify_plan.py，exit0：12工作包/38验收编号完整唯一、8个既有局部测试路径和13个源码引用路径存在、
本轮链接缺失0、Secret明文命中0、来源/归档hash异常0、git diff --check exit0。
全仓project_docs.py check仍exit1，233处为既有历史链接（未进旧baseline所以工具标new）；本轮新增/编辑文件无失效链接，未改历史造绿。
13个只读检查源码的SHA256记入verification.json，供下一AI核对接手字节。
未运行任何业务测试或构建，未实际执行FIN-00—11；本任务仅计划编制，达成交付后标completed。
