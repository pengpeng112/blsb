# REPORTING-DIRECTORY-GUIDES-20261023

- 工具/模型：Codex / unknown；会话20261023T005300Z-2e6d9ca8。
- 用户要求：在码上放心原始交付目录建立清晰目录说明，便于后续AI定位接口文档和开发路径；启动入口明确每个上报项目都维护本项目README。
- 修改范围：数据上报（新增导航、不改原件）、根AGENTS/AI_START/README、docs/ai、docs/startup/README、docs/README、docs/catalog及本轮维护记录。

[交付与验证记录](../../maintenance/reporting-directory-guides-20261023/README.md)。

接手先读共享入口/状态/交接及AGENTS，`python scripts/ai_session.py status`实际运行任务为空；登记任务后写入，未启动子代理。
新增总索引/模板及码上放心、医保、病理、历史资料README，根规则与各启动入口同步，后续各业务代码/SQL/接口/部署/证据路径变化须更新该业务README。
码上放心链接最新成功回执、官方复核及NTP配置结果，旧部署计划只标历史背景。医保仅核实材料存在；病理仅补导航，不宣称重新验收或套用药品机器配置。
凭据只给已存在受限登记文档路径，不读取/抄录明文；不打开历史患者/追溯码明细，不改变原始规范或样例。

命令/验证：

- 修改前调用`python scripts/project_docs.py check`：退出1，既有新断链234、来源哈希差异0，保存基线回执。
- 对本轮12份入口/README逐链接检查：退出0，断链0。
- `python scripts/project_docs.py inventory`：退出0，机器目录现270份文档。
- 修改后同一全仓check：退出1，既有新断链仍234，增量0，来源/归档哈希差异0；本轮17份入口/说明检查无新增断链。不把既有失败标成全仓通过，也不修改旧基线掩盖。
- 8份现有目录顶层原件前后SHA一致；本轮涉及源码/API变更0、远程连接0、HIS访问0、上传0；纯文档维护未运行应用回归。

收尾同步共享STATE/HANDOFF/BACKLOG并结束登记。后续上报项目用数据上报内模板补本项目README；具体业务未完成项仍从共享BACKLOG及该业务最新实测证据接续。
