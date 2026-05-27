<?php
/**
 * AutoCut DeepSeek 代理管理后台 v2
 * 新增：HWID 封禁 / 配额管控面板
 */

define('ADMIN_PASSWORD', '13903711373');
define('LOG_DB_PATH',    __DIR__ . '/ds_proxy_logs.sqlite3');

session_start();

// ---- 登录/登出 ----
if (isset($_POST['password'])) {
    $_SESSION['ds_admin_auth'] = ($_POST['password'] === ADMIN_PASSWORD);
    if (!$_SESSION['ds_admin_auth']) $login_error = '密码错误';
}
if (isset($_GET['logout'])) { session_destroy(); header('Location: ' . $_SERVER['PHP_SELF']); exit; }
$authed = !empty($_SESSION['ds_admin_auth']);

// ---- 登录页 ----
if (!$authed): ?>
<!DOCTYPE html><html lang="zh"><head><meta charset="UTF-8"><title>DS 管理后台</title>
<style>*{box-sizing:border-box;margin:0;padding:0}body{background:#0a0a0b;display:flex;align-items:center;justify-content:center;height:100vh;font-family:system-ui,sans-serif}.card{background:#121214;border:1px solid rgba(255,255,255,.08);border-radius:16px;padding:48px 40px;width:360px}h1{color:#e1251b;font-size:22px;margin-bottom:8px}p{color:#666;font-size:13px;margin-bottom:32px}input{width:100%;background:#1c1c1e;border:1px solid rgba(255,255,255,.1);border-radius:8px;color:#fff;padding:12px 16px;font-size:14px;outline:none;margin-bottom:16px}input:focus{border-color:#e1251b}button{width:100%;background:#e1251b;color:#fff;border:none;border-radius:8px;padding:13px;font-size:14px;font-weight:600;cursor:pointer}.err{color:#ff453a;font-size:13px;margin-bottom:12px}</style>
</head><body><div class="card"><h1>DeepSeek 代理后台</h1><p>AutoCut 调用日志与 HWID 管控</p>
<?php if(!empty($login_error)):?><div class="err"><?=htmlspecialchars($login_error)?></div><?php endif;?>
<form method="POST"><input type="password" name="password" placeholder="管理员密码" autofocus><button type="submit">进入后台</button></form>
</div></body></html>
<?php exit; endif;

// ---- 数据库 ----
function get_db(): PDO {
    $db = new PDO('sqlite:' . LOG_DB_PATH);
    $db->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
    // 确保两张表存在（兼容旧库或首次访问）
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
        hwid TEXT PRIMARY KEY, blocked INTEGER DEFAULT 0,
        daily_quota INTEGER DEFAULT 10, note TEXT,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )");
    return $db;
}

$db = get_db();

// ---- POST 操作处理 ----
if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['action'])) {
    $action = $_POST['action'];
    $hwid   = trim($_POST['hwid'] ?? '');

    if ($hwid) {
        // 确保记录存在
        $db->prepare("INSERT OR IGNORE INTO hwid_settings (hwid) VALUES (?)")->execute([$hwid]);

        switch ($action) {
            case 'block':
                $db->prepare("UPDATE hwid_settings SET blocked=1, updated_at=datetime('now') WHERE hwid=?")->execute([$hwid]);
                break;
            case 'unblock':
                $db->prepare("UPDATE hwid_settings SET blocked=0, updated_at=datetime('now') WHERE hwid=?")->execute([$hwid]);
                break;
            case 'set_quota':
                $quota = max(0, intval($_POST['quota'] ?? 0));
                $db->prepare("UPDATE hwid_settings SET daily_quota=?, updated_at=datetime('now') WHERE hwid=?")->execute([$quota, $hwid]);
                break;
            case 'set_note':
                $note = trim($_POST['note'] ?? '');
                $db->prepare("UPDATE hwid_settings SET note=?, updated_at=datetime('now') WHERE hwid=?")->execute([$note, $hwid]);
                break;
        }
    }
    header('Location: ' . $_SERVER['PHP_SELF'] . (isset($_GET['page']) ? '?page='.$_GET['page'] : ''));
    exit;
}

// ---- 统计 ----
$stats = $db->query("SELECT COUNT(*) AS total_calls,
    COALESCE(SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END), 0) AS ok_calls,
    COALESCE(SUM(CASE WHEN status='error' THEN 1 ELSE 0 END), 0) AS err_calls,
    COUNT(DISTINCT hwid) AS unique_users,
    COALESCE(SUM(prompt_tokens), 0) AS total_pt, 
    COALESCE(SUM(completion_tokens), 0) AS total_ct,
    COALESCE(SUM(cost_usd), 0) AS total_cost FROM proxy_logs")->fetch(PDO::FETCH_ASSOC);

// ---- 分页日志 ----
$page   = max(1, intval($_GET['page'] ?? 1));
$per    = 25;
$fhwid  = trim($_GET['hwid'] ?? '');
$fstat  = trim($_GET['status'] ?? '');
$where  = []; $params = [];
if ($fhwid) { $where[] = "hwid LIKE ?"; $params[] = "%$fhwid%"; }
if ($fstat) { $where[] = "status = ?";  $params[] = $fstat; }
$wsql   = $where ? 'WHERE '.implode(' AND ',$where) : '';
$cnt_st = $db->prepare("SELECT COUNT(*) FROM proxy_logs $wsql"); 
$cnt_st->execute($params); 
$total = (int)$cnt_st->fetchColumn();
$pages  = max(1, ceil($total / $per));
$logs_st = $db->prepare("SELECT * FROM proxy_logs $wsql ORDER BY id DESC LIMIT $per OFFSET ".(($page-1)*$per));
$logs_st->execute($params); $logs = $logs_st->fetchAll(PDO::FETCH_ASSOC);

// ---- TOP HWID ----
$top_hwids = $db->query("SELECT l.hwid, COUNT(*) AS calls, SUM(l.cost_usd) AS cost,
    MAX(l.ip) AS last_ip, MAX(l.created_at) AS last_seen,
    s.blocked, s.daily_quota, s.note
    FROM proxy_logs l LEFT JOIN hwid_settings s ON l.hwid=s.hwid
    WHERE l.status='ok' GROUP BY l.hwid ORDER BY calls DESC LIMIT 15")->fetchAll(PDO::FETCH_ASSOC);

// ---- 今日各 HWID 调用次数（用于配额参考）----
$today_counts_raw = $db->query("SELECT hwid, COUNT(*) AS cnt FROM proxy_logs
    WHERE status='ok' AND date(created_at,'localtime')=date('now','localtime')
    GROUP BY hwid")->fetchAll(PDO::FETCH_ASSOC);
$today_counts = [];
foreach ($today_counts_raw as $r) $today_counts[$r['hwid']] = $r['cnt'];

// ---- 所有已配置管控的 HWID ----
$managed = $db->query("SELECT s.*, COUNT(l.id) AS calls, SUM(l.cost_usd) AS cost,
    MAX(l.ip) AS last_ip, MAX(l.created_at) AS last_seen
    FROM hwid_settings s LEFT JOIN proxy_logs l ON s.hwid=l.hwid
    GROUP BY s.hwid ORDER BY s.updated_at DESC")->fetchAll(PDO::FETCH_ASSOC);

?>
<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AutoCut DS 管理后台</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0a0a0b;color:#e5e5e7;font-family:'Inter',system-ui,sans-serif;font-size:13px}
a{color:#e1251b;text-decoration:none}
.header{background:#121214;border-bottom:1px solid rgba(255,255,255,.08);padding:0 28px;display:flex;align-items:center;height:52px}
.header h1{font-size:15px;font-weight:700;color:#fff;flex:1}.header h1 span{color:#e1251b}
.header a{color:#555;font-size:12px}
.wrap{max-width:1500px;margin:0 auto;padding:24px 28px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:14px;margin-bottom:24px}
.sc{background:#121214;border:1px solid rgba(255,255,255,.08);border-radius:10px;padding:18px}
.sc .lb{color:#555;font-size:10px;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px}
.sc .vl{font-size:24px;font-weight:700;color:#fff}
.sc .vl.red{color:#e1251b}.sc .vl.green{color:#2cd758}
.sc .sub{color:#444;font-size:10px;margin-top:3px}
.sec{background:#121214;border:1px solid rgba(255,255,255,.08);border-radius:10px;margin-bottom:20px;overflow:hidden}
.sec-h{padding:14px 18px;border-bottom:1px solid rgba(255,255,255,.06);display:flex;align-items:center;gap:10px}
.sec-h h2{font-size:13px;font-weight:600;color:#ccc;flex:1}
table{width:100%;border-collapse:collapse}
th{color:#444;font-size:10px;text-transform:uppercase;letter-spacing:.05em;padding:9px 14px;text-align:left;border-bottom:1px solid rgba(255,255,255,.06)}
td{padding:10px 14px;border-bottom:1px solid rgba(255,255,255,.04);vertical-align:middle}
tr:last-child td{border-bottom:none}
tr:hover td{background:rgba(255,255,255,.02)}
.badge{display:inline-block;padding:2px 7px;border-radius:4px;font-size:10px;font-weight:700}
.badge.ok{background:rgba(44,215,88,.15);color:#2cd758}
.badge.err{background:rgba(255,69,58,.15);color:#ff453a}
.badge.blocked{background:rgba(255,69,58,.2);color:#ff453a}
.badge.limited{background:rgba(245,166,35,.15);color:#f5a623}
.badge.free{background:rgba(44,215,88,.1);color:#2cd758}
.hwid{font-family:monospace;font-size:11px;background:#1c1c1e;border:1px solid rgba(255,255,255,.08);padding:2px 7px;border-radius:4px;color:#8fb2ff}
.cost{color:#f5a623;font-weight:600;font-family:monospace}
.cc{max-width:240px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;color:#666}
/* filter */
.fb{padding:12px 18px;background:#0f0f11;border-bottom:1px solid rgba(255,255,255,.06);display:flex;gap:8px;flex-wrap:wrap}
.fb input,.fb select{background:#1c1c1e;border:1px solid rgba(255,255,255,.1);color:#ccc;border-radius:6px;padding:6px 10px;font-size:12px;outline:none}
.fb input:focus,.fb select:focus{border-color:#e1251b}
.btn{display:inline-block;padding:5px 12px;border-radius:6px;font-size:11px;font-weight:600;cursor:pointer;border:none;text-align:center}
.btn-red{background:#e1251b;color:#fff}.btn-red:hover{background:#c0190f}
.btn-gray{background:#2a2a2e;color:#ccc}.btn-gray:hover{background:#333}
.btn-orange{background:rgba(245,166,35,.2);color:#f5a623;border:1px solid rgba(245,166,35,.3)}
.btn-sm{padding:3px 9px;font-size:10px}
/* inline form */
.ifrm{display:flex;gap:6px;align-items:center}
.ifrm input[type=number]{width:70px;background:#1c1c1e;border:1px solid rgba(255,255,255,.1);color:#ccc;border-radius:5px;padding:4px 8px;font-size:12px;outline:none}
.ifrm input[type=text]{width:150px;background:#1c1c1e;border:1px solid rgba(255,255,255,.1);color:#ccc;border-radius:5px;padding:4px 8px;font-size:12px;outline:none}
/* pagination */
.pg{padding:14px 18px;display:flex;gap:6px;align-items:center;border-top:1px solid rgba(255,255,255,.06)}
.pg a,.pg span{padding:4px 10px;border-radius:5px;font-size:11px;border:1px solid rgba(255,255,255,.08)}
.pg a{color:#ccc}.pg a:hover{background:rgba(255,255,255,.05)}
.pg span.cur{background:#e1251b;color:#fff;border-color:#e1251b}
.pg .info{color:#444;border:none}
.quota-bar{height:4px;background:#1c1c1e;border-radius:2px;margin-top:4px;overflow:hidden}
.quota-bar .fill{height:100%;background:#f5a623;border-radius:2px}
.quota-bar .fill.danger{background:#ff453a}
</style>
</head>
<body>
<div class="header">
  <h1>AutoCut <span>DeepSeek</span> 代理管理后台</h1>
  <a href="?logout=1">退出</a>
</div>
<div class="wrap">

<!-- 统计卡片 -->
<div class="stats">
  <div class="sc"><div class="lb">总调用</div><div class="vl"><?=number_format($stats['total_calls'])?></div><div class="sub">成功 <?=$stats['ok_calls']?> / 失败 <?=$stats['err_calls']?></div></div>
  <div class="sc"><div class="lb">独立用户</div><div class="vl"><?=number_format($stats['unique_users'])?></div></div>
  <div class="sc"><div class="lb">输入 Token</div><div class="vl"><?=number_format($stats['total_pt'])?></div></div>
  <div class="sc"><div class="lb">输出 Token</div><div class="vl"><?=number_format($stats['total_ct'])?></div></div>
  <div class="sc"><div class="lb">累计费用</div><div class="vl red">$<?=number_format($stats['total_cost'],4)?></div><div class="sub">≈ ¥<?=number_format($stats['total_cost']*7.25,2)?></div></div>
</div>

<!-- HWID 管控面板 -->
<div class="sec">
  <div class="sec-h"><h2>🛡️ HWID 管控面板</h2><span style="color:#555;font-size:11px">封禁 / 配额 / 备注 — 所有变更立即生效</span></div>
  <table>
    <thead><tr>
      <th>#</th><th>HWID</th><th>IP</th><th>总调用</th><th>累计费用</th><th>最后活跃</th><th>配额 / 状态</th><th>设置配额</th><th>备注 (回车保存)</th><th>封禁操作</th>
    </tr></thead>
    <tbody>
    <?php if(empty($managed)):?>
    <tr><td colspan="10" style="text-align:center;color:#555;padding:30px">暂无管控记录。新设备请求将自动加入。</td></tr>
    <?php endif;?>
    <?php $idx=0; foreach($managed as $m):
      $idx++;
      $today = $today_counts[$m['hwid']] ?? 0;
      $quota = intval($m['daily_quota']);
      $pct   = $quota > 0 ? min(100, round($today/$quota*100)) : 0;
    ?>
    <tr>
      <td style="color:#444"><?=$idx?></td>
      <td><span class="hwid"><?=htmlspecialchars($m['hwid'])?></span></td>
      <td style="font-family:monospace;font-size:11px;color:#8fb2ff"><?=htmlspecialchars($m['last_ip']??'-')?></td>
      <td style="font-weight:700;color:#fff"><?=number_format($m['calls']??0)?></td>
      <td><span class="cost">$<?=number_format($m['cost']??0,5)?></span></td>
      <td style="color:#444;font-size:11px"><?=htmlspecialchars($m['last_seen']??'-')?></td>
      <!-- 配额与状态合并 -->
      <td>
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
            <div>
                <span style="color:<?=$pct>=100?'#ff453a':($pct>=80?'#f5a623':'#2cd758')?>;font-weight:700"><?=$today?></span>
                <span style="color:#444"> / </span>
                <span style="color:#888"><?=$quota>0?$quota:'∞'?></span>
            </div>
            <div>
                <?php if($m['blocked']):?>
                  <span class="badge blocked" style="margin-left:8px;">🚫 已封禁</span>
                <?php elseif($quota>0):?>
                  <span class="badge limited" style="margin-left:8px;">⏱ 限 <?=$quota?>/天</span>
                <?php else:?>
                  <span class="badge free" style="margin-left:8px;">✅ 自由</span>
                <?php endif;?>
            </div>
        </div>
        <?php if($quota>0):?>
        <div class="quota-bar"><div class="fill <?=$pct>=90?'danger':''?>" style="width:<?=$pct?>%"></div></div>
        <?php endif;?>
      </td>
      <!-- 设置配额 -->
      <td>
        <form method="POST" class="ifrm">
          <input type="hidden" name="action" value="set_quota">
          <input type="hidden" name="hwid" value="<?=htmlspecialchars($m['hwid'])?>">
          <input type="number" name="quota" min="0" value="<?=$quota?>" placeholder="0=不限">
          <button class="btn btn-orange btn-sm">设置</button>
        </form>
      </td>
      <!-- 备注表单与文本合并 -->
      <td>
        <form method="POST" class="ifrm">
          <input type="hidden" name="action" value="set_note">
          <input type="hidden" name="hwid" value="<?=htmlspecialchars($m['hwid'])?>">
          <input type="text" name="note" value="<?=htmlspecialchars($m['note']??'')?>" placeholder="记录备注...">
          <button class="btn btn-gray btn-sm">保存</button>
        </form>
      </td>
      <!-- 封禁/解封 移至最右侧 -->
      <td>
        <form method="POST" style="display:inline">
          <input type="hidden" name="hwid" value="<?=htmlspecialchars($m['hwid'])?>">
          <?php if($m['blocked']):?>
            <input type="hidden" name="action" value="unblock">
            <button class="btn btn-gray btn-sm">解封</button>
          <?php else:?>
            <input type="hidden" name="action" value="block">
            <button class="btn btn-red btn-sm" onclick="return confirm('确认封禁此 HWID？')">封禁</button>
          <?php endif;?>
        </form>
      </td>
    </tr>
    <?php endforeach;?>
    </tbody>
  </table>
</div>

<!-- TOP 用户 -->
<div class="sec">
  <div class="sec-h"><h2>🔥 TOP 15 用户</h2></div>
  <table>
    <thead><tr>
      <th>#</th><th>HWID</th><th>IP</th><th>总调用</th><th>累计费用</th><th>最后活跃</th><th>配额 / 状态</th><th>设置配额</th><th>备注 (回车保存)</th><th>封禁操作</th>
    </tr></thead>
    <tbody>
    <?php if(empty($top_hwids)):?>
    <tr><td colspan="10" style="text-align:center;color:#555;padding:30px">暂无活跃记录。</td></tr>
    <?php endif;?>
    <?php $idx=0; foreach($top_hwids as $m):
      $idx++;
      $today = $today_counts[$m['hwid']] ?? 0;
      $quota = intval($m['daily_quota']);
      $pct   = $quota > 0 ? min(100, round($today/$quota*100)) : 0;
    ?>
    <tr>
      <td style="color:#444"><?=$idx?></td>
      <td><span class="hwid"><?=htmlspecialchars($m['hwid'])?></span></td>
      <td style="font-family:monospace;font-size:11px;color:#8fb2ff"><?=htmlspecialchars($m['last_ip']??'-')?></td>
      <td style="font-weight:700;color:#fff"><?=number_format($m['calls']??0)?></td>
      <td><span class="cost">$<?=number_format($m['cost']??0,5)?></span></td>
      <td style="color:#444;font-size:11px"><?=htmlspecialchars($m['last_seen']??'-')?></td>
      <!-- 配额与状态合并 -->
      <td>
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
            <div>
                <span style="color:<?=$pct>=100?'#ff453a':($pct>=80?'#f5a623':'#2cd758')?>;font-weight:700"><?=$today?></span>
                <span style="color:#444"> / </span>
                <span style="color:#888"><?=$quota>0?$quota:'∞'?></span>
            </div>
            <div>
                <?php if($m['blocked']):?>
                  <span class="badge blocked" style="margin-left:8px;">🚫 已封禁</span>
                <?php elseif($quota>0):?>
                  <span class="badge limited" style="margin-left:8px;">⏱ 限 <?=$quota?>/天</span>
                <?php else:?>
                  <span class="badge free" style="margin-left:8px;">✅ 自由</span>
                <?php endif;?>
            </div>
        </div>
        <?php if($quota>0):?>
        <div class="quota-bar"><div class="fill <?=$pct>=90?'danger':''?>" style="width:<?=$pct?>%"></div></div>
        <?php endif;?>
      </td>
      <!-- 设置配额 -->
      <td>
        <form method="POST" class="ifrm">
          <input type="hidden" name="action" value="set_quota">
          <input type="hidden" name="hwid" value="<?=htmlspecialchars($m['hwid'])?>">
          <input type="number" name="quota" min="0" value="<?=$quota?>" placeholder="0=不限">
          <button class="btn btn-orange btn-sm">设置</button>
        </form>
      </td>
      <!-- 备注表单与文本合并 -->
      <td>
        <form method="POST" class="ifrm">
          <input type="hidden" name="action" value="set_note">
          <input type="hidden" name="hwid" value="<?=htmlspecialchars($m['hwid'])?>">
          <input type="text" name="note" value="<?=htmlspecialchars($m['note']??'')?>" placeholder="记录备注...">
          <button class="btn btn-gray btn-sm">保存</button>
        </form>
      </td>
      <!-- 封禁/解封 移至最右侧 -->
      <td>
        <form method="POST" style="display:inline">
          <input type="hidden" name="hwid" value="<?=htmlspecialchars($m['hwid'])?>">
          <?php if($m['blocked']):?>
            <input type="hidden" name="action" value="unblock">
            <button class="btn btn-gray btn-sm">解封</button>
          <?php else:?>
            <input type="hidden" name="action" value="block">
            <button class="btn btn-red btn-sm" onclick="return confirm('确认封禁此 HWID？')">封禁</button>
          <?php endif;?>
        </form>
      </td>
    </tr>
    <?php endforeach;?>
    </tbody>
  </table>
</div>

<!-- 详细日志 -->
<div class="sec">
  <div class="sec-h"><h2>📋 调用日志</h2><span style="color:#555;font-size:11px">共 <?=number_format($total)?> 条</span></div>
  <form method="GET" class="fb">
    <input type="text" name="hwid" placeholder="HWID 筛选..." value="<?=htmlspecialchars($fhwid)?>">
    <select name="status">
      <option value="">全部</option>
      <option value="ok" <?=$fstat==='ok'?'selected':''?>>✅ 成功</option>
      <option value="error" <?=$fstat==='error'?'selected':''?>>❌ 错误</option>
    </select>
    <button type="submit" class="btn btn-red">筛选</button>
    <a href="?" class="btn btn-gray">重置</a>
  </form>
  <table>
    <thead><tr><th>ID</th><th>时间</th><th>HWID</th><th>IP</th><th>模型</th><th>Tokens In/Out</th><th>费用</th><th>状态</th><th>请求内容</th><th>响应结果</th></tr></thead>
    <tbody>
    <?php if(empty($logs)):?><tr><td colspan="10" style="text-align:center;color:#555;padding:30px">无数据</td></tr><?php endif;?>
    <?php foreach($logs as $lg):?>
    <tr>
      <td style="color:#444"><?=$lg['id']?></td>
      <td style="color:#555;font-size:11px;white-space:nowrap"><?=$lg['created_at']?></td>
      <td><span class="hwid"><?=htmlspecialchars(substr($lg['hwid'],0,14))?>...</span></td>
      <td style="font-family:monospace;font-size:11px;color:#8fb2ff"><?=htmlspecialchars($lg['ip'])?></td>
      <td style="color:#888;white-space:nowrap"><?=htmlspecialchars($lg['model'])?></td>
      <td style="font-family:monospace;font-size:11px;color:#666;white-space:nowrap"><?=number_format($lg['prompt_tokens'])?> / <?=number_format($lg['completion_tokens'])?></td>
      <td><?php if($lg['cost_usd']>0):?><span class="cost">$<?=number_format($lg['cost_usd'],5)?></span><?php else:?><span style="color:#333">-</span><?php endif;?></td>
      <td>
        <span class="badge <?=$lg['status']==='ok'?'ok':'err'?>"><?=$lg['status']==='ok'?'✅':'❌'?></span>
        <?php if($lg['error_msg']):?><div style="color:#ff453a;font-size:10px;margin-top:3px"><?=htmlspecialchars(substr($lg['error_msg'],0,50))?></div><?php endif;?>
      </td>
      <td class="cc" title="<?=htmlspecialchars($lg['request_content']??'')?>"><?=htmlspecialchars(mb_substr($lg['request_content']??'-',0,60))?></td>
      <td class="cc" title="<?=htmlspecialchars($lg['response_content']??'')?>"><span style="color:#2cd758;font-family:monospace;font-size:11px"><?=htmlspecialchars(mb_substr($lg['response_content']??'-',0,60))?></span></td>
    </tr>
    <?php endforeach;?>
    </tbody>
  </table>
  <?php if($pages>1):?>
  <div class="pg">
    <?php if($page>1):?><a href="?page=<?=$page-1?>&hwid=<?=urlencode($fhwid)?>&status=<?=urlencode($fstat)?>">‹</a><?php endif;?>
    <?php for($i=max(1,$page-2);$i<=min($pages,$page+2);$i++):?>
      <?php if($i===$page):?><span class="cur"><?=$i?></span><?php else:?><a href="?page=<?=$i?>&hwid=<?=urlencode($fhwid)?>&status=<?=urlencode($fstat)?>"><?=$i?></a><?php endif;?>
    <?php endfor;?>
    <?php if($page<$pages):?><a href="?page=<?=$page+1?>&hwid=<?=urlencode($fhwid)?>&status=<?=urlencode($fstat)?>">›</a><?php endif;?>
    <span class="info">第 <?=$page?>/<?=$pages?> 页 · <?=number_format($total)?> 条</span>
  </div>
  <?php endif;?>
</div>

</div><!-- .wrap -->
</body>
</html>
