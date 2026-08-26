# 通用 HTTP 请求节点

通用 HTTP 请求节点用于在视频工作流中调用普通 JSON API，并把响应字段提取为命名变量，交给下游“API 值条件”判断。它与“外部 API”算法节点相互独立，不要求返回检测框。

## 请求配置

- 支持 `GET`、`POST`、`PUT`、`PATCH`、`DELETE`。
- 所有方法均支持 Query 参数和请求头；除 GET 外可配置 JSON 请求体。
- URL、Query、请求头和 JSON 请求体都可以使用 `{{ JSONPath }}` 模板。
- 请求失败、超时、非 2xx 或必填变量提取失败时，节点返回 `success=false`，不会中断整条工作流。
- 出站请求不会跟随 HTTP 重定向；3xx 会作为 `http_status` 失败返回。

可用模板上下文：

```json
{
  "workflow": {"id": 1, "name": "巡检"},
  "source": {"id": 2, "name": "东门", "code": "gate-east"},
  "frame": {"timestamp": 123.45},
  "nodes": {
    "upstream-node-id": {
      "result": {},
      "outputs": {}
    }
  }
}
```

例如：

```text
{{ $.source.code }}
{{ $.nodes['http-1'].outputs.risk_score }}
{{ $.nodes['algorithm-1'].result.metadata.score }}
```

字段内容只有一个模板时会保留原始 JSON 类型；模板嵌入普通文本时会转换为字符串。

## 响应提取

每条提取规则包含变量名、JSONPath 和“是否必填”：

```json
[
  {"name": "risk_score", "jsonpath": "$.data.score", "required": true},
  {"name": "tags", "jsonpath": "$.data.tags[*]", "required": false}
]
```

- 单个匹配保留原始类型。
- 多个匹配返回数组。
- 可选变量无匹配时为 `null`。
- 必填变量无匹配时节点标记失败。

## API 值条件

将 HTTP 请求节点连接到条件节点，选择“API 值条件”，即可组合嵌套的 AND/OR 规则。支持等于、不等于、数值比较、包含、属于、存在和真值判断。

比较采用 JSON 强类型，因此数字 `1` 不等于字符串 `"1"`。除命名变量外，还可以使用：

- `$success`：请求及必填提取是否成功。
- `$status_code`：HTTP 状态码。
- `$error_type`：`timeout`、`network_error`、`http_status`、`template_error` 等错误类型。

## 敏感信息

Authorization、Proxy-Authorization、X-API-Key、API-Key 等请求头会自动作为敏感字段，也可以手动将请求头或 Query 参数标为敏感。工作流读取、测试结果和模板导出均会隐藏这些值；编辑时保留空值不会覆盖已保存的凭据，删除对应配置行则会移除凭据。

## 出站访问安全策略

HTTP 请求节点默认允许访问公网及 RFC1918 内网目标，同时永久拒绝回环与云 metadata 等特殊目标。管理员也可以启用更严格的目标范围：

- `HTTP_REQUEST_ALLOWED_HOSTS`：可选的逗号分隔精确主机白名单；留空表示不限制普通目标，非默认端口使用 `host:port`。
- `HTTP_REQUEST_TEST_MAX_CONCURRENCY`：编辑器测试请求的并发上限，默认 `2`。
- `HTTP_REQUEST_TEST_TOTAL_TIMEOUT_SECONDS`：编辑器测试请求的总等待上限，默认 `10` 秒，最大 `30` 秒。

每次请求都会重新解析目标主机并检查全部解析地址。`localhost`、回环地址、链路本地地址、常见云 metadata 主机/IP，以及未指定、多播和保留地址始终拒绝，不能通过白名单放行；RFC1918 私网地址允许访问。实际连接固定到已检查的 IP，并保留原主机名用于 Host、TLS SNI 与证书校验，以防 DNS 重绑定。由于自动重定向已关闭，响应中的 `Location` 不会触发下一跳请求，也不会转发 Authorization、X-API-Key 等凭据。
