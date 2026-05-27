# AutoCut × DeepSeek API 代理接口协议

**版本**：v1.1  
**生效版本**：AutoCut v12.81+  
**接口地址**：`https://deep.xmbs.top/autocut/v1/ds_proxy`  
**协议**：HTTPS / JSON  
**最后更新**：2026-05-25

---

## 1. 背景与设计原则

客户端 `acut_deepseek.py` 在本地**未配置 DeepSeek API Key** 时，自动切换至代理模式，向本服务器发起请求。服务端持有真实 API Key，客户端永远不接触密钥。

**分支路由规则（客户端侧）：**

```
本地 api_key 有值  →  直连 api.deepseek.com（服务端无感知）
本地 api_key 为空  →  POST /autocut/v1/ds_proxy（本协议）
```

---

## 2. 请求规格

### 2.1 基本信息

| 项目 | 值 |
|------|----|
| Method | `POST` |
| Content-Type | `application/json` |
| 超时 | 120s（与直连模式一致） |

### 2.2 请求体（JSON）

```json
{
  "hwid": "a3f8c2d1e4b5...",
  "messages": [
    {
      "role": "system",
      "content": "# 叙事导演+价值精算引擎..."
    },
    {
      "role": "user",
      "content": "ID | 内容\n1 | 今天给大家介绍...\n2 | 这款产品的核心..."
    }
  ],
  "model": "deepseek-v4-flash",
  "temperature": 0.1
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `hwid` | string | ✅ | 客户端硬件指纹，SHA-256(MAC地址)[:32]，用于鉴权与配额计数 |
| `messages` | array | ✅ | 透传给 DeepSeek 的对话数组，格式与 OpenAI 兼容 |
| `model` | string | ✅ | 模型名称，如 `deepseek-v4-flash` |
| `temperature` | float | ✅ | 采样温度，客户端默认 `0.1` |

---

## 3. 响应规格

### 3.1 成功响应（HTTP 200）

结构与 DeepSeek 官方 `/chat/completions` 原始返回**完全一致**，客户端直接解析 `choices[0].message.content`：

```json
{
  "choices": [
    {
      "message": {
        "content": "14 | S | 10\n25 | H | 9\n3 | X | 0"
      }
    }
  ]
}
```

> [!IMPORTANT]
> 服务端**必须**原样透传 DeepSeek 的响应结构，不得对 `choices` 字段做任何包装或改名。  
> 客户端解析代码：`response.json()["choices"][0]["message"]["content"]`

### 3.2 业务错误响应（HTTP 200 + error 字段）

当请求合法但触发业务限制时，返回 HTTP 200 但包含 `error` 字段：

```json
{
  "error": "QUOTA_EXCEEDED",
  "message": "今日调用次数已达上限，请明日再试"
}
```

客户端检测到 `error` 字段后会抛出 `ValueError(message)`，显示在 UI 错误弹窗中。

### 3.3 HTTP 错误码

| HTTP 状态码 | 含义 | 客户端行为 |
|-------------|------|-----------|
| `401` | HWID 未注册 / 无授权记录 | 弹出"请先激活授权"提示 |
| `403` | 账号已封禁或被拒绝服务 | 弹出服务端返回的 message |
| `429` | 触发服务端速率限制 | 弹出"请求过于频繁"提示 |
| `500` | 服务端内部错误 / DeepSeek 转发失败 | 弹出"代理服务异常" |

---

## 4. 服务端实现要求

### 4.1 管控检查（封禁 + 配额）

**DS 代理服务器不负责鉴权**，鉴权由独立授权服务器（`abc.nelsoncode.com`）承担。

代理服务器在转发前查询本地 `hwid_settings` 表执行两项检查：

| 检查项 | 条件 | 返回 |
|--------|------|------|
| 封禁检查 | `blocked = 1` | HTTP 403 + `{"error":"BLOCKED"}` |
| 配额检查 | `daily_quota > 0` 且今日成功调用次数 ≥ 配额 | HTTP 200 + `{"error":"QUOTA_EXCEEDED"}` |

> 自动注册机制：任何新设备在首次请求时，将被自动写入管控面板，并赋予默认每天 10 次的调用配额。配额超出统一返回 HTTP 200（非 4xx），客户端通过检测 `"error"` 字段识别并抛出 `ValueError`。

### 4.2 配额管控

**所有封禁和配额配置均通过管理后台 UI 人工操作**，不区分试用版/正式版，按 HWID 单独设置：

| 操作 | 效果 |
|------|------|
| 设置 `blocked=1` | 该 HWID 所有请求返回 403 |
| 设置 `daily_quota=N` | 每自然日成功调用上限为 N 次（0=不限，默认=10） |
| 设置备注 | 记录管控原因 |

超出配额时返回：

```json
{"error": "QUOTA_EXCEEDED", "message": "今日调用次数已达上限（N 次），请明日再试"}
```

### 4.3 DeepSeek 转发

验证通过后，服务端构造并发出标准 OpenAI 兼容请求：

```http
POST https://api.deepseek.com/chat/completions
Authorization: Bearer <服务端私有 API Key>
Content-Type: application/json

{
  "model": "<透传客户端的 model>",
  "messages": <透传客户端的 messages>,
  "temperature": <透传客户端的 temperature>
}
```

将 DeepSeek 响应**原样返回**给客户端，不做任何数据包装。

### 4.4 配额计数时机

**应在 DeepSeek 成功响应后才计数**（而非请求进入时），避免因网络异常或 DeepSeek 超时导致配额被误消耗。

---

## 5. 安全约定

- 服务端 API Key **不得**出现在任何响应体或日志中
- `hwid` 用于**日志记录与管控检查**，代理服务器不跨服务器进行授权鉴权
- 建议在 Web 服务器层（Nginx/Apache）对此接口添加 IP 级速率限制，防止恶意并发

---

## 6. 客户端相关代码位置

| 文件 | 位置 | 说明 |
|------|------|------|
| `acut_deepseek.py` | L55~L82 | 代理分支路由逻辑 |
| `acut_license.py` | L36~L40 | `get_hwid()` 硬件指纹生成 |

---

*协议制定：AutoCut 技术团队 / 2026-05-25*
