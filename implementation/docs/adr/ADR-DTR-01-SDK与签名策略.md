# ADR-DTR-01：码上放心平台 SDK 与签名策略

日期：2026-10-16；任务 DTR-IMPLEMENT-20261016（W02/W05）。状态：已实施（离线部分）。

## 背景

码上放心（阿里健康药品追溯）TOP 网关按 `application/x-www-form-urlencoded` 表单 +
签名调用。院方提供的 SDK 证据目前仅有 Java/C# 类名（对应
`alibaba.alihealth.drugtrace.top.yljg.uploadinoutbill`）；Python SDK、签名测试向量、
AppKey/AppSecret 均未取得（EXT-06/EXT-07）。平台主体为 Python，**不因 SDK 语言换栈**
（DEVELOPMENT_PLAN §1）。

## 决策

1. **协议自实现 + 签名证据门禁**：按 TOP 公开规范实现表单编码与 MD5 签名
   （`plugins/destinations/alihealth_drugtrace/protocol.py`）。在官方向量或 SDK
   对照验证通过前，`SIGNATURE_VERIFIED=False`：真实 AppSecret 的 `build_form`
   直接抛 `CAPABILITY_UNSUPPORTED`（引用 EXT-07），**不可能用未验签实现发生产**。
   `__mock__` 前缀密钥仅供离线测试通道（Mock 结果不作平台确认证据，DTR-B03）。
2. **优先官方 Python SDK**：取得官方 Python SDK（含哈希/许可证）后，transport 换用
   SDK 签名路径，与自实现做双跑对拍（同参数同签名）后置 `SIGNATURE_VERIFIED=True`。
3. **SDK 仅限院方指定语言时的最小适配组件**：若官方确认仅提供 Java/C# SDK，则选
   一个**小型、审查过的签名适配组件**（候选：院内已有的 Java 签名服务、或经批准的
   离线 Python TOP 签名实现+官方向量校验），不引入整套运行时、不在 DMZ 落 SDK、
   不重写平台（DEVELOPMENT_PLAN §6）。该选择另立 ADR 记录组件与哈希。
4. **传输与签名解耦**：`transport.py` 只负责 HTTPS（固定 DMZ 转发、无自动重试、
   分层超时）；签名/编码在 protocol 层。SDK 到位后仅替换 protocol 层签名实现。

## 后果

- 未取得向量前：离线全链路（Mock transport）可测；真实发送被显式阻断并在 readiness
  显示 `signature_verified=false`（EXT-07）。
- 取得向量后：新增对照测试（同参数→官方签名==自实现签名）即可放开，无需改动
  service/worker/API 层。
