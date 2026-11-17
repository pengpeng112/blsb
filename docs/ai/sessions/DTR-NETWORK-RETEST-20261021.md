# DTR-NETWORK-RETEST-20261021

Codex / unknown；session `20261021T045902Z-254a4e09`。
用户要求：网络调整后复测，检查是否能推送追溯信息。
范围：docs、本机受限`.ai/private/drug-trace-retest-20261021`；未改implementation或现场运行配置。

## 实际结果

[完整报告与命令](../../development/drug-trace-network-20261021/README.md)。
本轮确认DMZ→官方HTTPS443从此前超时变为HTTP200/TLS通过；并完成一次真实签名query.billstatus，SUCCESS/total_num0。
签名在Windows官方C#参考产生，经SSH stdin给DMZ curl，Secret不落DMZ；不是<prod-host>应用转发链路验收。
<prod-host>→DMZ443 TCP成功，TLS health仍exit35；8443拒绝；<prod-host>→HIS1521 TCP成功。
DMZ转发unit/socket均not-found，安装路径不存在；<prod-host>仍20261016T114735Z，core/worker无AppSecret环境注入。
DMZ时钟慢约32分钟、NTP未同步；本次没校时。

真实上传0、HIS业务查询0、患者信息发送0、远端文件/配置变更0。
待禁发部署与生产来源/平台字典/签名资格/具体未报scope齐备后，才能首张真实推送。
本任务不能宣称已上报，亦不能沿用旧结论说公网443仍不通。

## 取证过程

- ai_session.py status/start exit0，无活动冲突；读取下一AI已完成FIN软件交付及尚未部署交接。
- network_probe.py（新日期revision）exit0：DMZ完成取证；首次<prod-host>二跳exit255、未取到内容。
- 无认证路由/TCP/banner诊断exit0，确认<jump-host>→<prod-host>22可达；本机直连<prod-host>22拒绝不阻断既有二跳。
- 最小二跳命令exit0、指纹匹配、认证成功；未猜测密码或降低主机校验。初次255原因未定位，保留原证据。
- target_inventory.py exit0，实际从<prod-host>绑定源地址测三端口，读取发行/容器与指定环境的存在性，不打印env全量或Secret。
- target_tls_probe.py exit0表示取证结束，内部curl exit35如实保留，无-k、无客户端私钥复制、无业务POST。
- 经已有with-credentials.ps1临时环境运行dmz_query_probe.py exit0：真实官方只读请求一次、无重试/重定向、TLS验证开启。
- 原始响应Windows DPAPI加密，公共回执只输出白名单结果；没有执行上传方法或取得真实HIS码。

## 交接

现场状态尚未达到FIN-10安装完成；沿现有两包和runbook处理实际部署委托/窗口及正式证书，不重装基础框架。
HIS/业务/平台资料缺项保持OPEN；诊断查询成功不自动批准发送或解除SIGNATURE_VERIFIED。
网络两段已有TCP/HTTPS改善，但<prod-host>→DMZ的可信TLS/mTLS应用服务仍需部署和复测。
具体证据、公共文档/秘密扫描及链接/编号检查见
[verification.json](../../development/drug-trace-network-20261021/evidence/verification.json)。

## 收尾检查与状态

4个Python探针py_compile exit0；本机受限verify_task.py由原凭证包装器启动，exit0。
确认唯一真实签名请求为query.billstatus、上传0；DMZ出口与<prod-host> TCP成功、院内TLS失败与现场未安装证据一致。
本轮公共文件Secret明文命中0、失效链接0、来源/归档hash异常0、私有目录Git忽略、git diff --check exit0。
project_docs.py inventory exit0（213份）；全仓check仍exit1，233处既有历史链接不改、不加白名单造绿。
未运行pytest或声称新的业务回归通过。任务以handed_off交接：网络诊断已完成，首张真实推送仍待FIN-10部署和相应业务输入。
