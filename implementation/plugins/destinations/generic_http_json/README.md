# generic_http_json 通用 HTTP JSON 推送适配器（LT-03；LT-03-R1 修订）

配置驱动的通用推送目的地：大多数国内上报平台（传染病前置机、各类 HTTP JSON 接口）
只需配置 URL、回执规则、可重试码即可接入。标准库 urllib 实现，零新依赖。

## 配置字段（存于 Destination 的 PUBLISHED ConfigRevision，config_type="destination"、owner_id=destination.id）

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| base_url | string | 必填 | 仅 scheme://host:port（路径/查询放 path_template）；**is_mock=True 的目的地只允许本机回环**（127.0.0.1/localhost/::1） |
| allowed_schemes | string[] | ["http","https"] | scheme 白名单 |
| allowed_hosts | string[] | 必填 | 主机白名单 |
| allowed_ports | int[] | 必填 | 端口白名单 |
| method | "POST"/"PUT" | POST | |
| path_template | string | "/" | 可含 {payload顶层字段} 占位符；值只允许 [A-Za-z0-9_-] |
| headers | object | Content-Type: application/json | 追加请求头 |
| credential_ref | string/null | null | 只允许 "env:VAR_NAME"；运行时注入 Authorization；缺失→UNSUPPORTED 不回显 |
| timeout_seconds | 1—120 | 15 | |
| max_attempts | 1—5 | 3 | **仅 idempotency_contract=CONFIRMED 时生效**（见下） |
| backoff_seconds | number[] | [2,4,6] | 同上 |
| rate_limit_per_minute | 1—6000 | 60 | 进程内令牌桶（每 destination 一桶） |
| receipt_rules | Rule[] | [] | 声明式回执规则，按序命中即返回 outcome |
| default_outcome | outcome | UNKNOWN | 规则全未命中时 |
| error_code_path | string | "errorCode" | |
| error_message_path | string | "desc" | |
| remote_ref_path | string/null | null | 对方流水号路径 |
| idempotency_contract | ""/"CONFIRMED" | ""（未证实） | LT-03-R1：只有 "CONFIRMED"（有书面/平台确认的对端幂等证据）才允许 POST 不确定结果后的自动重试；`Idempotency-Key` 请求头本身不构成对端保证 |
| report_target_code | string | — | 真实发送（is_mock=False）必填：LT-01 生产闸门入参 |
| real_send_evidence_ref | string | — | LT-03-R1：真实发送的独立启用证据引用（工单/批复编号），与 Target 字段分开登记 |

Rule = `{json_path: "result"/"data.code"/"items.0.ok", op: eq|in|exists|not_exists, value, outcome}`；
outcome ∈ ACCEPTED/CONFIRMED/PARTIAL/REJECTED/RETRYABLE/UNKNOWN/UNSUPPORTED。

## 语义（照传染病 inf_pusher 实测口径；LT-03-R1/R02 修订）

- `result=true` → ACCEPTED；其它 errorCode → REJECTED（不重试）；
- **POST 不确定结果不盲重发**（LT-03-R1）：超时/断连/HTTP 408/429/5xx、
  errorCode 01/99（服务端异常）都可能已被对端接收——未证实对端幂等
  （idempotency_contract 缺省）时第一次不确定结果即 **UNKNOWN**（交
  查询/人工核查，不自动再次 POST）；仅 idempotency_contract="CONFIRMED"
  时按同一冻结请求退避重试，耗尽→UNKNOWN（429 的 Retry-After 保留传递）；
- **重定向一律禁止**：任何 HTTP 3xx → REJECTED（不跟随 Location；原始 URL
  白名单校验覆盖不了 urllib 自动跳转，重定向可能把凭据/报文转发到未核准主机）；
- 其余 4xx → REJECTED；响应非 JSON → UNKNOWN("响应不是 JSON")；
- 每次 send 前**对最终 URL 再过一次白名单**（防 path_template 注入）；
- error_message 经 redact_text 脱敏（≥6 位数字→***、18 位身份证→***、截断 200 字）。

## 装配与审计（LT-03-R1）

- `perform_push` 派发：真实（is_mock=False）generic 目的地过
  `report_target_code` + `assert_target_can_send_real`（要求 sole_pusher=
  **CONFIRMED**）+ `real_send_evidence_ref` 三重闸门；Mock（is_mock=True）
  generic 目的地**显式走同一 generic 适配器与行为档**（服务层强制 base_url
  为本机回环，改 is_mock/Target 字段不能绕过）；其他 Mock 目的地保留旧
  MockHttpClient 行为。
- 每次尝试（含重试与触网前拒绝）各落一行 `DeliveryAttempt`
  （response_snapshot 含 `mock` 布尔，Mock 证据与真实上报证据分离）。

## 传染病前置机示例（Mock；真实地址由 LT-05/后续生产切换时填写，不写入仓库）

```json
{
  "base_url": "http://127.0.0.1:8090",
  "allowed_hosts": ["127.0.0.1"],
  "allowed_ports": [8090],
  "allowed_schemes": ["http"],
  "method": "POST",
  "path_template": "/api/{business_type}",
  "timeout_seconds": 15,
  "max_attempts": 3,
  "backoff_seconds": [2, 4, 6],
  "rate_limit_per_minute": 60,
  "receipt_rules": [
    {"json_path": "result", "op": "eq", "value": true, "outcome": "ACCEPTED"},
    {"json_path": "errorCode", "op": "in", "value": ["01", "99"], "outcome": "RETRYABLE"},
    {"json_path": "errorCode", "op": "exists", "outcome": "REJECTED"}
  ],
  "default_outcome": "UNKNOWN",
  "error_code_path": "errorCode",
  "error_message_path": "desc",
  "credential_ref": null,
  "report_target_code": "inf_cdc_frontend"
}
```

真实传染病前置机为 `http://<frontend-host>`（仅此注释提示；生产切换时按 EXT 证据流程
填写 base_url 与白名单，并给 ReportTarget 过 `assert_target_can_send_real` 闸门
（须 sole_pusher=CONFIRMED + real_send_evidence_ref）。本批新业务始终无真实发送能力。

## 限速边界

进程内令牌桶（`rate.py`）：**跨进程限速未实现**，单 worker 部署下有效；
多 worker 需 LT-04 后续增强（可复用 alihealth rate_control 的共享文件预算方案）。
等待网络/重试期间不持有业务事务（发送在服务层 commit 边界之外的重试循环内）。
