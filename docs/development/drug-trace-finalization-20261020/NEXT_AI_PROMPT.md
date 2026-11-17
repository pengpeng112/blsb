# 下一AI可直接使用的执行提示词

将下面内容交给具备本机文件/终端权限的AI。当前文档只是任务委托材料，尚未执行其开发/部署。
配套[完整计划](README.md)、[验收矩阵](ACCEPTANCE.md)、[网络与资料](EXTERNAL_INPUTS_AND_NETWORK.md)必须一起读。

---

工作区F:\python\blyb。请执行 **DTR-FINALIZE-20261020**，尽量一轮完成剩余开发、测试、发行、安装工具及具备授权/窗口条件的现场禁发部署。
不要只输出新的计划，也不要每个阶段问是否继续。遇一个外部缺口，只阻断对应步骤，完成所有可独立推进的工作。
最终如仍缺证书/HIS/业务字典/SDK/维护窗口，不许把结果写成“只剩网络”或“全部完成”。

先读取：

1. 根AGENTS.md、docs/ai/START_HERE.md、STATE.md、HANDOFF.md。
2. docs/development/drug-trace-finalization-20261020/四份文档。
3. docs/development/platform-live-test-20261020/README.md、platform-credentials-20261020/README.md、his-live-20261019/README.md。
4. 本次涉及的代码、FIX3交付及源码目录AGENTS；不以归档报告覆盖新证据。

根目录执行ai_session.py status/start登记实际工具/模型（未知写unknown）与implementation、docs范围。
保持单AI顺序执行，保留未提交工作字节，禁止reset/clean覆盖FIX2/FIX3和10-20签名修复。
阶段checkpoint，收尾更新共享状态与本轮session再end；未完成现场项用handed_off，不伪称全任务completed。

已知事实：

- <prod-host>现网仍20261016T114735Z，6容器healthy；不是药品新发行已安装。
- DMZ=<relay-net-host>，Anolis7.9，无443/8443监听，原有8001不得占用。
- <prod-host>→DMZ443/8443拒绝；DMZ→eco.taobao.com443超时；此前DMZ→gw.api.taobao.com80成功。
- 本机两项真实只读查询SUCCESS、医院目录一致；真实上传0，不能冒称正式DMZ链路验收。
- 本地已有药品模块、真实传输、DMZ forwarder、PG009—011、FIX3；不得重建框架。
- 10-20已修复TOP空名/空字符串签名并有独立参考用例；局部80通过1跳过，SIGNATURE_VERIFIED仍False。
- HIS活库8条关联已验，但生产readonly/两视图/完整业务规则/持续变更发现未完成；r1/r2历史不能改标签。

按完整计划FIN-00—11推进，以下关键问题不得遗漏：

1. **旧系统可运行**：DMZ采用.socket托管443＋非root进程接收FD，交付兼容旧systemd的unit/socket和经el7等效ABI实测的私有运行时；不对系统Python setcap，不升级整机Python/systemd，不使用旧系统不支持的AmbientCapabilities/strict/append等指令冒充兼容。
2. **转发可靠性**：固定HTTPS上游/路径、mTLS/源IP、原字节、无重试、限额；补TLS握手/慢头/慢body/上游头/回写预算和握手前连接限额；严禁开放代理/自动改HTTP/关闭TLS。
3. **安装真验收**：现有--verify服务没起来也可能exit0，必须修；材料哈希覆盖实际unit/运行时、原子切换、真实TLS健康检查、首装/升级失败恢复和完整回退（含socket/unit/相容配置），既有8001不受影响。
4. **HIS真来源**：r7/真实主单与逐码SQL草案、Oracle11g、完整复合键和前导零；正常/拆零/退药/住院规则分开，未知等待；显式批次与可靠增量分开，没有可索引变更源不得承诺捕捉任意旧单变化。
5. **平台契约**：完成有来源的签名资格验证；session可选链路打通；321/323/终态字典待官方依据；重点验证历史bill_time与实际上传日期不同的潜在漏查，按当期官方确认的日期语义处理。不得单改常量、猜字典或自动重发。
6. **实际应用配置**：幂等初始化默认草稿/dry-run，证据审批不自动造；实际worker的SecretRef、mTLS与Oracle运行时挂载可用，不能只在Windows测试。DMZ不存AppSecret。
7. **运维与UI**：serverAuth/clientAuth正确、证书轮换、旧系统DNS/NTP诊断、隔离PG恢复演练；readiness按代码/本机/网络/来源/平台/范围分层，未知不是绿灯；前端显示真实/Mock与阻断原因。
8. **验证与两发行包**：FIN-A01—38逐项记录，先局部再必要全量，真实本地mTLS合成闭环/实际浏览器；源码包和镜像层排除真实库、凭据/私钥、患者文件。输出<prod-host>与DMZ独立离线包、来源/哈希、已实现的安装/诊断/回退命令。

当前用户要求的是编写给你执行的计划；现场操作以用户交付本提示词时的明确部署委托与维护窗口为准。
有对应授权时，同轮完成两台禁发部署；不重复询问每个可逆操作。没有窗口先完成全部本地实现和交付材料，不假称已上机。
不自行修改医院网络策略、校时大幅跳变、HIS DDL/DML、生产restore或既有8001；这些需要准确对象/影响范围的现有授权。
真实HIS核验用docs/startup/DATABASE_ACCESS.md→sjzc完整流程，不把历史一次性授权当长期许可，不读/打印服务器托管凭据。

现成应用资料：.ai/private/alihealth-drugtrace-20261020/profile.json与app-secret.dpapi，使用with-credentials.ps1临时注入授权子进程，禁止回显/命令行明文/Git/镜像。
SSH资料在.ai/private/reporting-20261015/，必须保持已固定主机密钥；不复制私有目录进发行包。
不要重复索要AppKey/AppSecret/ref_ent_id/医院全称或retail不必填的供应商ent_id；平台返回的医院ent_id也已在本机profile保存。
Linux不能直接解本机DPAPI，按受控部署渠道注入实际签名进程，不能假装已经支持未实现的file: SecretRef。

常用检查在implementation执行：

```powershell
python -m pytest tests/integration/test_top_official_reference.py tests/integration/test_drug_trace_domain.py tests/integration/test_drug_trace_fix2.py tests/integration/test_drug_trace_fix3.py tests/integration/test_drug_trace_api.py tests/integration/test_drug_trace_ops.py tests/integration/test_dmz_forwarder.py tests/integration/test_his_source_profile.py -q -ra
python -m pytest tests -q -ra --junitxml=var/dtr-finalize-full.xml
```

新增测试按实际路径补入局部命令，日志/XML保存证据；全量完成后无新变化不重复跑。
前端在implementation/apps/admin_web使用现有依赖执行同源npm run build；构建后做实际浏览器闭环。
隔离PG/旧systemd/el7运行时缺环境仅阻断对应验收，不以fake shell或静态文本冒称现场成功。

网络开通后走正式链路只读验证，再在具体业务范围齐全时发首张完整未报单据（max_bills=1），保留受理/自动查询终态/码核对。
用户已要求真实测试，不笼统再问“允许测试吗”；但未知单据类型、既往上报和具体范围不能自行猜。
HTTP200/受理/空单号查询/Mock不算首张上传成功，UNKNOWN先查不重发，不全历史自动补报。

最终必须交付：

- 已改文件与可恢复基线；两发行包/manifest及源码对应版本。
- FIN工作包和FIN-A验收逐项真实命令/退出码/证据，分实测通过/实现未测/外部阻断。
- 两台各自安装前后状态、禁发证据/迁移/健康/回退；未部署则明确标注。
- 唯一剩余清单，列提供方、缺项、影响与恢复命令；若主要仍是网络，必须用证据证明其他输入已齐。
- 更新四类实施文档/API矩阵、共享STATE/BACKLOG/HANDOFF及session；真实上传笔数如实记录。

请持续执行到所有可独立完成项结束后再交接，不只回复“可以”或再给一版计划。
