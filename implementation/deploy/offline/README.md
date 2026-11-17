# 离线包构建与目标机安装（T13 回填）

## 产物（已生成于 `deploy/offline/dist/`）

| 材料 | 内容 | 用途 |
|---|---|---|
| `requirements.lock.txt` | 147 个包的锁定清单（pip freeze 全量传递依赖） | 目标机按锁安装，不用 `>=` 浮动 |
| `wheelhouse/` | 全量 wheel（24 个顶层入口，覆盖锁内全部传递依赖） | `--no-index` 离线安装源 |
| `sha256-manifest.json` | 各材料 sha256 | 安装前完整性校验 |
| `sbom.json` | 依赖 SBOM（名称/版本） | 发行审计 |
| `licenses.txt` | 依赖许可证 | 合规 |

构建脚本：`deploy/offline/build_offline.py`（在可联网的开发机执行一次；
目标机全程不需要网络）。

## 目标机安装步骤（医院内网，离线）

```powershell
# 0) 前置：目标机安装 CPython 3.14 64 位 Windows。
#    wheelhouse 中的 pydantic_core、sqlalchemy 是 cp314-win_amd64，不能换 3.12/3.13。
#    发行目录含 apps/ packages/ plugins/ profiles/ deploy/ docs/
#    run_dev.py requirements.txt apps/admin_web/dist/。

# 1) 校验材料完整性（可选但建议）
Get-Content deploy\offline\dist\sha256-manifest.json   # 与实际文件 hash 比对

# 2) 离线安装依赖（不访问任何索引；--no-index 只用本地 wheelhouse）
python -m pip install --no-index --find-links deploy\offline\dist\wheelhouse `
    -r deploy\offline\dist\requirements.lock.txt

# 3) 迁移 + 启动（内网形态，含令牌加固）
powershell -ExecutionPolicy Bypass -File deploy\intranet\start.ps1
#    （首次初始化/健康检查/UI/Agent enroll/令牌轮换见 deploy\intranet\README.md）
```

## 边界（如实）

- 本机状态：代码/前端产物/离线材料（lock147包/wheelhouse/哈希清单/SBOM/许可证）
  已产出；**清洁环境安装演练未执行**（无独立清洁机器/容器）。
- 前端产物 `apps/admin_web/dist/` 随包分发；`npm run build` 仅开发机需要
  （目标机无 Node 依赖）。
- Docker/容器清洁安装同样未执行（无 docker 环境；compose 材料见 `deploy/compose/`）。
