"""码上放心（阿里健康药品追溯）TOP 目标适配器。

模块：
- protocol.py  TOP 表单编码/签名/响应解析（签名一致性依赖 EXT-07 官方向量验证）
- transport.py HTTPS 传输（固定 DMZ 转发地址；无自动重试；分层超时）
- client.py    两上传方法 + 单据状态查询的规范化客户端
- adapter.py   与 core Submission/DeliveryAttempt 的对接（发送前持久化、UNKNOWN 先查）

不修改 Mock 接口去冒充正式 TOP；未知能力显式报错（DTR-B03）。
"""
