# 医院数据归集与上报平台 · 项目总入口

病理为首个业务插件，包含 Windows Agent、Storage、业务 API/worker、交换发布和 Vue 管理台。
**当前设计基线是 v0.4；当前执行状态以 [STATE](docs/ai/STATE.md) 为准。** 本地测试通过与真实医院验收分别记录。

## 我现在应该打开哪个文件

| 使用目的 | 入口 |
|---|---|
| 换工具/换模型继续开发 | [AI 接手入口](docs/ai/START_HERE.md)、[当前交接](docs/ai/HANDOFF.md) |
| 看完成进度与下一项 | [当前状态](docs/ai/STATE.md)、[剩余任务](docs/ai/BACKLOG.md) |
| 按上报项目找接口文档、源码、视图与部署证据 | [各上报目录总索引](数据上报/README.md)、[码上放心接手说明](数据上报/1、码上放心平台/README.md)；每个上报项目必须维护自己目录内的README |
| 启动程序、测试或构建 | [统一启动说明](docs/startup/README.md) |
| 找设计/实现/测试/部署/历史材料 | [文档分类目录](docs/README.md) |
| 查源码职责与运行文件 | [代码与文件地图](docs/CODE_MAP.md) |
| 给任何 AI 的共同规则 | [AGENTS.md](AGENTS.md)、[可直接复制的提示词](AI_START.md) |
| 查看第4轮详细任务 | [任务书](一次性执行任务-本地剩余与内网部署-2026-10-01.md) |

## 从根目录执行

依赖使用当前已安装环境，入口不会自动安装或下载。

```powershell
.\project.ps1 -Action Dev       # 本地合成栈；8000/8001/8002/8090
.\project.ps1 -Action Demo      # 加3片+1重扫演示
.\project.ps1 -Action Test      # 正式回归
.\project.ps1 -Action Probes    # 43项复核检查
.\project.ps1 -Action Build     # 前端离线构建
.\project.ps1 -Action Docs      # 文档链接、原始来源/归档hash检查
.\project.ps1 -Action Status    # 其他工具登记的任务与修改范围
.\project.ps1 -Action Context   # 为不能读本地文件的聊天模型生成接手包
```

使用 `-DryRun` 查看工作目录和命令，不执行任务。开发栈前台启动，用 Ctrl+C 结束该进程。
入口从自身路径定位项目，不依赖调用者当前目录；已有 `implementation/run_dev.py` 等入口保持有效。

## 文件组织

```text
README.md / project.ps1          使用和启动入口
AGENTS.md / CLAUDE.md            工具可发现的共同规则入口
AI_START.md                     任意工具/聊天模型的接手提示词
docs/                           分类索引、共享状态、启动说明、维护记录
implementation/                 唯一业务源码根；原模块路径保留
数据上报/                       各业务原始接口材料＋每个上报项目的README导航
设计交付v0.4/                    当前设计、契约和来源提取
设计交付v0.3/                    在位历史材料；不作为当前基线
归档/                           历史提示词、旧方案和旧执行记录原件
scripts/                        离线文档检查、协作登记、接手包工具
snapshots/                      恢复快照；不是当前源码或完成状态
.ai/                            当前工作区本地协作登记与生成接手包
```

正式规范、报告原件、草案和来源哈希的位置见 [来源分类](docs/sources/README.md)。
源码、数据卷、数据库和厂商原件分别管理；本次文档整理不搬迁运行数据。
