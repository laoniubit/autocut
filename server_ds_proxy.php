<?php
/**
 * AutoCut DeepSeek API 代理服务
 * 接口地址: POST /autocut/v1/ds_proxy
 * 部署路径: deep.xmbs.top/autocut/v1/ds_proxy.php (或通过路由指向)
 */

header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Methods: POST, OPTIONS');
header('Access-Control-Allow-Headers: Content-Type');

if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') { exit; }
if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    http_response_code(405);
    echo json_encode(['error' => 'METHOD_NOT_ALLOWED']);
    exit;
}

// ============================================================
// 配置区 — 部署前修改
// ============================================================
define('DS_API_KEY',   'sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'); // DeepSeek 真实密钥
define('DS_API_URL',   'https://api.deepseek.com/chat/completions');
define('LOG_DB_PATH',  __DIR__ . '/ds_proxy_logs.sqlite3');    // SQLite 数据库路径
// ============================================================

// ---- 解析请求体 ----
$body = file_get_contents('php://input');
$data = json_decode($body, true);

if (!$data || !is_array($data)) {
    http_response_code(400);
    echo json_encode(['error' => 'INVALID_JSON', 'message' => '请求体非法']);
    exit;
}

$hwid        = trim($data['hwid'] ?? '');
$messages    = $data['messages'] ?? [];
$model       = $data['model'] ?? 'deepseek-v4-flash';
$temperature = floatval($data['temperature'] ?? 0.1);
$ip          = $_SERVER['HTTP_X_FORWARDED_FOR'] ?? $_SERVER['HTTP_CF_CONNECTING_IP'] ?? $_SERVER['REMOTE_ADDR'] ?? 'unknown';
$ip          = explode(',', $ip)[0]; // 取第一个 IP（代理链情况）

if (!$hwid || empty($messages)) {
    http_response_code(400);
    echo json_encode(['error' => 'MISSING_PARAMS', 'message' => '缺少 hwid 或 messages']);
    exit;
}

// ---- 数据库初始化 ----
function get_db(): PDO {
    $db = new PDO('sqlite:' . LOG_DB_PATH);
    $db->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
    $db->exec("CREATE TABLE IF NOT EXISTS proxy_logs (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
        hwid             TEXT NOT NULL,
        ip               TEXT,
        model            TEXT,
        prompt_tokens    INTEGER DEFAULT 0,
        completion_tokens INTEGER DEFAULT 0,
        cost_usd         REAL DEFAULT 0.0,
        request_content  TEXT,
        response_content TEXT,
        status           TEXT DEFAULT 'ok',
        error_msg        TEXT
    )");
    $db->exec("CREATE TABLE IF NOT EXISTS hwid_settings (
        hwid        TEXT PRIMARY KEY,
        blocked     INTEGER DEFAULT 0,
        daily_quota INTEGER DEFAULT 10,
        note        TEXT,
        updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
    )");
    return $db;
}

// ---- 费用估算（按 DeepSeek 官方定价，单位 USD）----
function estimate_cost(string $model, int $prompt_tokens, int $completion_tokens): float {
    // DeepSeek 官方定价策略 (2026)
    // deepseek-chat / reasoner 将于 2026/07/24 弃用，迁移至 v4 系列
    $pricing = [
        'deepseek-v4-flash' => ['in' => 0.27, 'out' => 1.10],
        'deepseek-v4-pro'   => ['in' => 0.55, 'out' => 2.19], // 占位预估（建议按官方账单核准）
        'deepseek-chat'     => ['in' => 0.27, 'out' => 1.10],
        'deepseek-reasoner' => ['in' => 0.55, 'out' => 2.19],
    ];
    $p = $pricing[$model] ?? $pricing['deepseek-v4-flash'];
    return ($prompt_tokens / 1_000_000 * $p['in']) + ($completion_tokens / 1_000_000 * $p['out']);
}

// ---- 提取日志用的用户内容（取 user role 的前500字）----
$user_content_log = '';
foreach ($messages as $msg) {
    if (($msg['role'] ?? '') === 'user') {
        $user_content_log = mb_substr($msg['content'] ?? '', 0, 500);
        break;
    }
}

$db = get_db();

// ---- HWID 管控检查（自动注册与风控检查）----
$db->prepare("INSERT OR IGNORE INTO hwid_settings (hwid, daily_quota) VALUES (?, 10)")->execute([$hwid]);

$s_stmt = $db->prepare("SELECT blocked, daily_quota FROM hwid_settings WHERE hwid = ?");
$s_stmt->execute([$hwid]);
$setting = $s_stmt->fetch(PDO::FETCH_ASSOC);

if ($setting && intval($setting['blocked'])) {
    $db->prepare("INSERT INTO proxy_logs (hwid,ip,model,request_content,status,error_msg) VALUES(?,?,?,?,'error','BLOCKED')")
       ->execute([$hwid, $ip, $model, $user_content_log]);
    http_response_code(403);
    echo json_encode(['error' => 'BLOCKED', 'message' => '此设备已被封禁，请联系客服']);
    exit;
}

if ($setting && intval($setting['daily_quota']) > 0) {
    $quota = intval($setting['daily_quota']);
    $c_stmt = $db->prepare("SELECT COUNT(*) FROM proxy_logs WHERE hwid = ? AND status = 'ok' AND date(created_at,'localtime') = date('now','localtime')");
    $c_stmt->execute([$hwid]);
    if (intval($c_stmt->fetchColumn()) >= $quota) {
        echo json_encode(['error' => 'QUOTA_EXCEEDED', 'message' => "今日调用次数已达上限（{$quota} 次），请明日再试"]);
        exit;
    }
}

// ---- 向 DeepSeek 转发 ----
$ds_payload = json_encode([
    'model'       => $model,
    'messages'    => $messages,
    'temperature' => $temperature,
], JSON_UNESCAPED_UNICODE);

$ch = curl_init(DS_API_URL);
curl_setopt_array($ch, [
    CURLOPT_POST           => true,
    CURLOPT_POSTFIELDS     => $ds_payload,
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_TIMEOUT        => 120,
    CURLOPT_HTTPHEADER     => [
        'Authorization: Bearer ' . DS_API_KEY,
        'Content-Type: application/json',
    ],
]);

$ds_response = curl_exec($ch);
$http_code   = curl_getinfo($ch, CURLINFO_HTTP_CODE);
$curl_error  = curl_error($ch);

// ---- 转发失败（网络/cURL错误）----
if ($curl_error || $ds_response === false) {
    $err = 'cURL error: ' . $curl_error;
    $db->prepare("INSERT INTO proxy_logs (hwid,ip,model,request_content,status,error_msg) VALUES(?,?,?,?,'error',?)")
       ->execute([$hwid, $ip, $model, $user_content_log, $err]);

    http_response_code(502);
    echo json_encode(['error' => 'UPSTREAM_UNREACHABLE', 'message' => '代理上游连接失败，请稍后重试']);
    exit;
}

$ds_data = json_decode($ds_response, true);

// ---- DeepSeek 返回非200或结构异常 ----
if ($http_code !== 200 || !isset($ds_data['choices'])) {
    $err = $ds_data['error']['message'] ?? "DeepSeek HTTP $http_code";
    $db->prepare("INSERT INTO proxy_logs (hwid,ip,model,request_content,status,error_msg) VALUES(?,?,?,?,'error',?)")
       ->execute([$hwid, $ip, $model, $user_content_log, $err]);

    http_response_code($http_code >= 400 ? $http_code : 502);
    echo json_encode(['error' => 'DS_ERROR', 'message' => "DeepSeek 返回错误: $err"]);
    exit;
}

// ---- 成功：记录详细日志（含 Token 用量和费用估算）----
$usage             = $ds_data['usage'] ?? [];
$prompt_tokens     = intval($usage['prompt_tokens']     ?? 0);
$completion_tokens = intval($usage['completion_tokens'] ?? 0);
$cost              = estimate_cost($model, $prompt_tokens, $completion_tokens);
$response_content  = mb_substr($ds_data['choices'][0]['message']['content'] ?? '', 0, 500);

$db->prepare("INSERT INTO proxy_logs
    (hwid, ip, model, prompt_tokens, completion_tokens, cost_usd, request_content, response_content, status)
    VALUES (?,?,?,?,?,?,?,?,'ok')")
   ->execute([$hwid, $ip, $model, $prompt_tokens, $completion_tokens, $cost, $user_content_log, $response_content]);

// ---- 原样返回 DeepSeek 响应（结构不作任何包装）----
echo $ds_response;
