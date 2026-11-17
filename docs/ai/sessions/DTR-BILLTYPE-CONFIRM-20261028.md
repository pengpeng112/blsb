# DTR-BILLTYPE-CONFIRM-20261028 交接（bill_type=321 确认记录）

- 执行：ZCode / GLM-5.3，2026-10-28，session `20261028T024058Z-a5fd8c8f`。
- 用户确认（原话）："bill_type 用 321 即可"——住院发药与患者退药统一 321；EXT-08 关闭。
- 本轮纯文档+代码核实（payload.py 退药报文构造），零生产访问。

## 要点

1. 字典载荷已备好（bill_type/query_bill_type 两命名空间，target=321/A，部署时 approve），
   见 RESULT「bill_type 已确认=321」节。
2. 退药首单观察保护（已写入部署清单）：321 下退药报文=正数量+原单冻结码（本地 reversal_of
   非平台字段）——首次真实退药只放 1 张（max_bills=1 模式），核对平台回执与库存冲正语义后
   再批量。住院无此问题。
3. 用户侧自查 SQL（050102 IN 方向日切片）为退药 IN 码正确查法（我们此前对 050121 已查过：
  历史 IN 码 17,238 行经 r11 视图可关联）。

## 剩余唯一待办：部署窗口

顺序：build_release → <prod-host> upgrade.sh（避开 06:00–08:30、用户在场）→ 驱动 v3 替换
（/opt/blyb/tools/）→ r11 连接器配置发布（profile 关闭 unresolved+新配置 objects 指向四视图）
→ 上述字典 approve → 退药首单试传观察 → 后续观察首个含住院/回看的日批。
