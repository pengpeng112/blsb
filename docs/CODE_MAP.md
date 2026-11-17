# 代码、启动、数据和生成文件地图

| 路径 | 职责/性质 | 管理方式 |
|---|---|---|
| `implementation/apps/` | core/storage/exchange API、worker、Agent、前端、seed/migrate/metrics入口 | 业务源码，不随文档分类搬迁 |
| `implementation/packages/` | contracts/core_db/workflow_core/connector_sdk/mapping/storage基础库 | 业务源码；数据库默认位置另见下表 |
| `implementation/plugins/` | 注册装配、病理/metrics领域、来源/目的地/格式适配 | 领域边界不按模型划分；兼容plugin_registry路径保留 |
| `implementation/tests/` | unit/contract/integration/fault/e2e/performance/functional | 自动验证；functional为真实栈门控，不能混淆skip与通过 |
| `implementation/docs/` | 实施详细状态、ADR、runbooks、审查证据 | 由docs总索引分类，当前交接摘要在根docs/ai |
| `implementation/deploy/` | compose、Windows脚本、offline构建与产物 | 脚本存在与已现场验证分开登记 |
| `implementation/migrations/` | 目录骨架；实际编号迁移主要在core_db/migrate与插件注册 | 修改前查实际注册，不能按空目录猜迁移已做 |
| `implementation/profiles/` / `hospital_packs/` | 预留目录/配置形态；实际版本另在数据库和seed | 不把目录存在当Profile正式批准 |
| `implementation/runbooks/` | 当前空预留目录 | 有效运维文档在implementation/docs/runbooks，统一从索引读取 |
| `scripts/` | 文档校验、协作登记、上下文包 | 维护工具；标准库，不依赖新的AI服务 |
| `project.ps1` | 根目录稳定启动包装 | 固定工作目录，委托既有入口，透传退出码 |

## 数据与生成物

| 路径 | 实际性质 | 注意事项 |
|---|---|---|
| `implementation/packages/var/blyb.sqlite3*` | 未设置数据库URL时的核心开发库默认位置 | 不因为目录位于packages就当源码移动 |
| `implementation/var/` | 交换库、卷、日志、队列、临时测试、审查输出 | 不批量清理；不同任务用各自隔离子目录 |
| `implementation/scanner-output/` | 可能由Agent生成/读取的扫描来源 | 源文件保留，不当临时文档删 |
| `implementation/apps/admin_web/dist/` | 前端构建产物 | 可按需重新构建，保留构建日志；不是手工编辑源码 |
| `implementation/apps/admin_web/node_modules/` | 已安装前端依赖 | 离线构建使用现有目录，不归入源码/共享包 |
| `implementation/build/`、`deploy/*/dist/` | 打包中间物/EXE/离线发行包 | 生成物；发行hash/SBOM是证据，不能以存在代替安装验收 |
| `snapshots/` | 开工前恢复快照 | 保留，非最新代码，不默认供AI搜索 |
| `.ai/` | 参与工具在本工作区的任务登记、上下文包 | 无密钥；本地协作元数据，交接正文放docs/ai/sessions |
| `.v2c/`、`.video_agent/` | 工具私有目录 | 不修改、不扫描会话秘密；共享事实写公共文档 |

当前没有Git仓库；已增加gitignore分类，尚未执行git init、提交或发布。
以后采用Git时，各工具交接记录可以随源码同步；并行代码开发宜使用独立分支/工作区，并在合并前核对共享台账。
