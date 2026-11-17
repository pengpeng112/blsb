"""药品追溯领域插件（DTR-IMPLEMENT-20261016）。

模块布局（W02—W07 增量交付）：
- contracts.py  两 profile 契约、业务分类、视图字段契约、状态机常量
- models.py     领域表 + 编号迁移 009
- source.py     只读视图抽取入口（复用 plugins/sources/db_view 的 Warehouse 协议）
- ingest.py     整单抽取/事务 checkpoint/幂等导入
- quality.py    质量规则（0/1/N 码、重复、原值、阈值）
- payload.py    冻结包构建、bill_code 稳定映射、字典校验
- service.py    采集/准备/发送/查询/对账编排与生产证据门禁
- routes.py     /api/v1/drug-trace API
- plugin.py     manifest 注册

发布默认生产能力关闭；真实采集/发送须 DrugTraceProductionEvidence 显式批准（空证据拒绝）。
"""
