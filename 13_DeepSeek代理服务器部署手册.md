# DeepSeek API 代理服务器部署手册

**版本**：v1.0  
**适用文件**：`server_ds_proxy.php` / `server_ds_admin.php`  
**目标服务器**：deep.xmbs.top（独立 DS 代理服务器）  
**最后更新**：2026-05-25

---

## 1. 环境要求

| 项目 | 最低要求 |
|------|----------|
| PHP | 8.0+ |
| PHP 扩展 | `pdo_sqlite`、`curl`、`mbstring`、`json`、`session` |
| Web 服务器 | Nginx 1.18+ 或 Apache 2.4+ |
| SQLite | 3.x（PHP PDO 内置，无需单独安装） |
| HTTPS | 必须（客户端强制使用 HTTPS 发起请求） |

验证 PHP 扩展是否可用：

```bash
php -m | grep -E "pdo_sqlite|curl|mbstring"
```

---

## 2. 文件部署

### 2.1 上传文件

将以下两个文件上传至服务器的目标目录（如 `/var/www/autocut/`）：

```
/var/www/autocut/
├── server_ds_proxy.php    ← 代理接口（对外）
└── server_ds_admin.php    ← 管理后台（对内）
```

### 2.2 修改配置区

**`server_ds_proxy.php`** 顶部配置区（L23-L25）：

```php
define('DS_API_KEY',   'sk-你的真实DeepSeek密钥');
define('DS_API_URL',   'https://api.deepseek.com/chat/completions');
define('LOG_DB_PATH',  __DIR__ . '/ds_proxy_logs.sqlite3');
```

**`server_ds_admin.php`** 顶部配置区（L7-L8）：

```php
define('ADMIN_PASSWORD', '你的管理员密码');    // 建议16位以上随机字符串
define('LOG_DB_PATH',    __DIR__ . '/ds_proxy_logs.sqlite3');
```

> [!IMPORTANT]
> 两个文件的 `LOG_DB_PATH` 必须指向**同一个** SQLite 文件，否则管理后台无法读取代理日志。

---

## 3. 目录权限配置

SQLite 数据库文件需要 PHP 进程具有**读写权限**：

```bash
# 设置目录可写（PHP-FPM 通常以 www-data 运行）
chown www-data:www-data /var/www/autocut/
chmod 755 /var/www/autocut/

# 数据库文件首次运行自动创建，无需预先创建
# 若目录权限不足会报错: PDOException: unable to open database file
```

---

## 4. Web 服务器配置

### 4.1 Nginx（推荐）

新建虚拟主机配置 `/etc/nginx/sites-available/autocut-ds`：

```nginx
server {
    listen 443 ssl;
    server_name deep.xmbs.top;

    ssl_certificate     /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    root /var/www/autocut;
    index index.php;

    # 代理接口路由：/autocut/v1/ds_proxy → server_ds_proxy.php
    location = /autocut/v1/ds_proxy {
        try_files /server_ds_proxy.php =404;
        fastcgi_pass unix:/run/php/php8.2-fpm.sock;
        include fastcgi_params;
        fastcgi_param SCRIPT_FILENAME $document_root/server_ds_proxy.php;

        # IP 级速率限制（防恶意并发）
        limit_req zone=ds_proxy burst=5 nodelay;
    }

    # 管理后台路由
    location = /autocut/ds_admin {
        try_files /server_ds_admin.php =404;
        fastcgi_pass unix:/run/php/php8.2-fpm.sock;
        include fastcgi_params;
        fastcgi_param SCRIPT_FILENAME $document_root/server_ds_admin.php;

        # 限制管理后台仅允许特定 IP 访问（强烈建议）
        # allow 你的IP地址;
        # deny all;
    }

    # 禁止直接访问 PHP 文件和数据库
    location ~* \.(php|sqlite3)$ {
        deny all;
    }
}

# HTTP → HTTPS 强制跳转
server {
    listen 80;
    server_name deep.xmbs.top;
    return 301 https://$host$request_uri;
}
```

在 `nginx.conf` 的 `http {}` 块中添加速率限制区：

```nginx
limit_req_zone $binary_remote_addr zone=ds_proxy:10m rate=6r/m;
```

激活配置：

```bash
ln -s /etc/nginx/sites-available/autocut-ds /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

### 4.2 Apache（备选）

在目标目录创建 `.htaccess`：

```apache
# 代理接口路由
RewriteEngine On
RewriteRule ^autocut/v1/ds_proxy$ server_ds_proxy.php [L]
RewriteRule ^autocut/ds_admin$    server_ds_admin.php  [L]

# 禁止直接访问数据库
<FilesMatch "\.sqlite3$">
    Require all denied
</FilesMatch>
```

---

## 5. 首次运行验证

### 5.1 测试代理接口

```bash
curl -X POST https://deep.xmbs.top/autocut/v1/ds_proxy \
  -H "Content-Type: application/json" \
  -d '{
    "hwid": "test_hwid_001",
    "messages": [
      {"role": "system", "content": "你是助手"},
      {"role": "user", "content": "1+1=?"}
    ],
    "model": "deepseek-v4-flash",
    "temperature": 0.1
  }'
```

**期望响应：**
```json
{"choices":[{"message":{"content":"2"}}], "usage":{...}}
```

**常见错误：**

| 错误现象 | 原因 | 解决方案 |
|----------|------|----------|
| `PDOException: unable to open database` | 目录无写权限 | `chown www-data /var/www/autocut/` |
| `curl: (60) SSL certificate` | 证书无效 | 检查 SSL 配置或使用 `--insecure` 测试 |
| `{"error":"UPSTREAM_UNREACHABLE"}` | 服务器无法访问 DeepSeek | 检查服务器出口防火墙 |
| `{"error":"DS_ERROR","message":"Incorrect API key"}` | API Key 错误 | 检查 `DS_API_KEY` 配置 |
| `HTTP 502 Bad Gateway` (客户端报 Code 1) | 请求耗时过长，被 Nginx/PHP 物理截断 | 调高网关与 PHP 的 `timeout` 值（见本文档第8节） |

### 5.2 访问管理后台

浏览器打开 `https://deep.xmbs.top/autocut/ds_admin`，输入 `ADMIN_PASSWORD` 登录。

首次成功调用后，后台应显示：
- 总调用数：1
- 独立用户数：1
- 日志表中有一条 `status=ok` 记录

---

## 6. 日常运维

### 6.1 管控操作（管理后台 UI）

> **自动注册机制**：任何新设备在首次请求大模型代理时，将自动被收录至“HWID 管控面板”，并**默认获得每日 10 次的调用配额**。

*注意：管理后台的“HWID 管控面板”和“TOP 15 用户”表已实现完全对齐的 10 列面板视图（包含调用总数、费用、配额进度条、快速备注和封禁等操作），可在任意表格中一键进行管理干预。*

| 场景 | 操作 |
|------|------|
| 封禁用户或解除封禁 | 直接在右侧点击「封禁」/「解封」操作按钮 |
| 修改默认的配额上限 | 在「设置配额」输入框调整数值后点击「设置」 |
| 记录管控原因 | 直接在「备注」输入框打字，点击「保存」 |

### 6.2 数据库备份

```bash
# 每日备份 SQLite（建议加入 crontab）
cp /var/www/autocut/ds_proxy_logs.sqlite3 \
   /backup/ds_proxy_logs_$(date +%Y%m%d).sqlite3

# crontab 示例（每天凌晨3点备份）
0 3 * * * cp /var/www/autocut/ds_proxy_logs.sqlite3 /backup/ds_logs_$(date +\%Y\%m\%d).sqlite3
```

### 6.3 费用监控

管理后台首页**「累计费用估算」**卡片实时显示 USD 和 CNY 估算值。  
费用基于 DeepSeek 官方定价估算（`deepseek-v4-flash`：$0.27/1M 输入 + $1.10/1M 输出），**仅供参考，以 DeepSeek 账单为准**。

---

## 7. 安全加固清单

- `[ ]` 修改 `ADMIN_PASSWORD` 为 16 位以上随机字符串
- `[ ]` 填入真实 `DS_API_KEY`（部署后删除或保护源文件）
- `[ ]` 管理后台 URL 限制特定 IP 访问（Nginx `allow`/`deny`）
- `[ ]` 服务器启用 HTTPS（SSL 证书）
- `[ ]` 配置 Nginx IP 速率限制（`limit_req`）
- `[ ]` 设置数据库文件目录不可 Web 直接访问
- `[ ]` 配置 SQLite 文件定期备份

## 8. 宝塔面板 (BT Panel) 环境调优（防 502/Code 1 崩溃）

如果在运行过程中 Python 客户端频繁报出 `DeepSeek 筛选异常退出 (Code 1)` 且服务器日志显示 150 字节的 **HTTP 502 Bad Gateway**，这并非代码逻辑错误，而是宝塔面板默认的网关超时时间过短（通常为 60 秒），无法承受 DeepSeek 在拥堵时期的长尾响应。

**必须在宝塔面板中执行以下操作将超时阈值提升至 300 秒：**

### 步骤一：调整 PHP 超时限制
1. 登录宝塔面板，进入左侧 **【软件商店】** -> **【已安装】**。
2. 找到当前站点正在使用的 PHP 版本（如 PHP-8.2），点击 **【设置】**。
3. 在左侧菜单选择 **【配置修改】**，找到 `max_execution_time`，将数值从默认的 `100` 或 `30` 改为 `300`。
4. 在左侧菜单选择 **【FPM配置文件】**，在文本中查找 `request_terminate_timeout`，如果有则将其改为 `300`。
5. 点击左侧 **【服务】**，选择 **【重启】** 或 **【重载配置】**。

### 步骤二：调整 Nginx 网关超时
1. 再次回到 **【软件商店】** -> **【已安装】**，找到 Nginx，点击 **【设置】**。
2. 在左侧菜单选择 **【配置修改】**，按 `Ctrl+F` 搜索以下三个参数，如果存在则修改，如果不存在则在 `http { ... }` 块中添加：
   ```nginx
   fastcgi_connect_timeout 300;
   fastcgi_send_timeout 300;
   fastcgi_read_timeout 300;
   ```
3. 保存后，点击左侧 **【服务】** -> **【重载配置】**。

*注意：修改完成后，务必确认修改已生效。这不仅能解决 DeepSeek 代理的长时悬挂断连，也能极大增强工业化部署的鲁棒性。*

---

*部署手册制定：AutoCut 技术团队 / 2026-05-25*

