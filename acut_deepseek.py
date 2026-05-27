import json
import requests
import re
import os
import typing
import threading
import time

def deepseek_elite_selector(segments: list, target_duration: int = 90, high_score_theme: str = "", low_score_theme: str = "", config: typing.Optional[dict] = None, project_id: typing.Optional[str] = None, workdir: typing.Optional[str] = None, log_fn: typing.Optional[typing.Callable] = None) -> list:
    """
    [DeepSeek AI Selector v9.2 - Industrial Clean Mode]
    Refactor: Removed circular dependencies. Added type hints.
    """
    if not segments:
        return []

    if log_fn: log_fn("DeepSeek 核心引擎已唤醒，正在初始化...", is_progress=True, pct=5)
    if log_fn: log_fn("正在构建语义矩阵...", is_progress=True, pct=10)

    # --- Stage 1: Build Dynamic Prompt ---
    high_score_rule = f"- **加分项**: 优先保留与【{high_score_theme}】高度相关的片段。" if high_score_theme else ""
    low_score_rule = f"- **减分项**: 若字幕包含【{low_score_theme}】，标签必须判定为 **X**，分数强制为 **0**。" if low_score_theme else ""

    DYNAMIC_PROMPT = f"""# 叙事导演+价值精算引擎 (v9.2 Dual-Axis)

## 任务
你是资深短视频导演。请对直播素材进行【叙事功能】分类并评定【种草价值】。

## 标签与打分体系
1. **标签 (Category)**:
   - **H (Hook)**: 开头勾子/抓人瞬间。
   - **S (Selling Point)**: 核心卖点/产品优势。
   - **D (Detail)**: 穿搭建议/细节补充。
   - **E (Ending)**: 总结/金句收尾。
   - **X (Trash)**: 废话/垃圾。

2. **分数 (Score: 0-10)**:
   - **10**: 绝佳，不可错过。
   - **7-9**: 优质，建议保留。
   - **4-6**: 普通，有额度再留。
   - **0-3**: 劣质。

## 筛选偏好规则
{high_score_rule}
{low_score_rule}

## 输出格式 (严格简洁)
`ID | 标签 | 分数`

例如：
14 | S | 10
25 | S | 7
245 | E | 10
"""

    # --- Stage 2: Dual-Axis Tagging & Scoring ---
    cfg = config or {}
    api_key = cfg.get("deepseek_api_key", "").strip()
    base_url = cfg.get("deepseek_base_url", "https://api.deepseek.com")
    model = cfg.get("deepseek_model", "deepseek-v4-flash")
    temperature = float(cfg.get("deepseek_temperature", 0.1))
    timeout = int(cfg.get("deepseek_timeout", 120))
    target_limit = target_duration if target_duration is not None else cfg.get("auto_select_limit_s", 90)

    input_lines = ["ID | 内容"]
    for s in segments:
        input_lines.append(f"{s.get('id')} | {s.get('text', '').replace('\n', ' ')}")

    user_content = "\n".join(input_lines)
    messages = [{"role": "system", "content": DYNAMIC_PROMPT}, {"role": "user", "content": user_content}]

    try:
        # [工业级防假死进度条]：
        # 为什么是从 30% 开始？因为前置的资源初始化(5%)和构建请求语义矩阵(10-30%)等本地同步操作已完成，
        # 在等待网络 IO 返回前，进度条自然停留在 30%。后续的响应解析与物理重组占 25% (75%~100%)。
        # 此处的异步多线程进度条，专门用于填补这 30% ~ 74% 的长尾网络通讯真空期。
        if log_fn: log_fn("正在建立 DeepSeek AI 语义通讯...", is_progress=True, pct=30)
        
        thread_result = {}
        
        def _api_worker():
            try:
                if api_key:
                    # [直连模式] 本地已配置 API Key，直接调用 DeepSeek
                    payload = {"model": model, "messages": messages, "temperature": temperature}
                    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
                    resp = requests.post(f"{base_url}/chat/completions", json=payload, headers=headers, timeout=timeout)
                    resp.raise_for_status()
                    thread_result["response"] = resp
                else:
                    # [代理模式] 本地未配置 API Key，走服务端代理
                    import acut_license
                    proxy_payload = {"hwid": acut_license.get_hwid(), "messages": messages, "model": model, "temperature": temperature}
                    resp = requests.post("https://deep.xmbs.top/server_ds_proxy.php", json=proxy_payload, timeout=timeout)
                    resp.raise_for_status()
                    resp_json = resp.json()
                    if "error" in resp_json:
                        raise ValueError(resp_json.get("message", "代理服务异常"))
                    thread_result["response"] = resp
            except Exception as e:
                thread_result["error"] = e

        api_thread = threading.Thread(target=_api_worker)
        api_thread.start()
        
        # 默认预期最大耗时 100 秒，每 10 秒更新一次。
        # 起点 pct=30, 目标 pct=74，共需递增 44%。每 10 秒递增 4%。
        current_pct = 30
        while api_thread.is_alive():
            api_thread.join(timeout=10.0)
            if api_thread.is_alive():
                current_pct = min(74, current_pct + 4)
                if log_fn: log_fn(f"正在等待 AI 分析返回... (耗时较长请耐心等待)", is_progress=True, pct=current_pct)
        
        if "error" in thread_result:
            raise thread_result["error"]
            
        response = thread_result.get("response")
        if not response:
            raise RuntimeError("API通讯线程异常终止，未获得有效响应")
        if log_fn: log_fn("正在解析叙事逻辑并构建语义矩阵...", is_progress=True, pct=75)
        content = response.json()["choices"][0]["message"]["content"]
        
        # --- Stage 3: Matrix Aggregation ---
        if log_fn: log_fn("正在将 AI 建议同步至本地缓存...", is_progress=True, pct=85)
        temp_dir = None
        if project_id and workdir:
            temp_dir = os.path.join(workdir, project_id, "temp")
            os.makedirs(temp_dir, exist_ok=True)
            with open(os.path.join(temp_dir, "deepseek_dual_axis.txt"), "w", encoding="utf-8") as f:
                f.write(content)

        groups = {"H": [], "S": [], "D": [], "E": []}
        for line in content.split("\n"):
            parts = line.split("|")
            if len(parts) >= 3:
                try:
                    sid = int(re.sub(r"\D", "", parts[0]))
                    tag = parts[1].strip().upper()
                    score = float(re.sub(r"[^\d.]", "", parts[2]))
                    if tag in groups:
                        seg = next((s for s in segments if s["id"] == sid), None)
                        if seg:
                            seg["_ai_score"] = score
                            seg["_ai_tag"] = tag
                            groups[tag].append(seg)
                except: continue
        
        if log_fn: log_fn("正在应用筛选策略与配额优化...", is_progress=True, pct=92)
        for k in groups:
            groups[k] = sorted(groups[k], key=lambda x: x["_ai_score"], reverse=True)

        selected_pool = []
        accum_dur = 0
        for tag in ["H", "E", "S", "D"]:
            for s in groups[tag]:
                if tag in ["H", "E"] or accum_dur < target_limit:
                    if accum_dur + s["duration"] <= target_limit + 10:
                        selected_pool.append(s)
                        accum_dur += s["duration"]
                    if tag in ["S", "D"] and accum_dur >= target_limit: break
        
        def final_sort_key(s):
            order = {"H": 0, "S": 1, "D": 2, "E": 3}
            return (order.get(s["_ai_tag"], 1), s["id"])

        final_segs = sorted(selected_pool, key=final_sort_key)
        selected_ids = [s["id"] for s in final_segs]

        if temp_dir:
            with open(os.path.join(temp_dir, "deepseek_response.json"), "w", encoding="utf-8") as f:
                json.dump({"selected_ids": selected_ids, "actual_total_duration": round(accum_dur, 2), "mode": "Dual-Axis v9.2"}, f, ensure_ascii=False, indent=2)

        if log_fn: log_fn("DeepSeek 筛选完成", is_progress=True, pct=100)
        return selected_ids
    except requests.exceptions.HTTPError as he:
        if he.response is not None and he.response.status_code in (401, 403):
            raise ValueError(f"DeepSeek 鉴权失败 ({he.response.status_code}): API Key 无效或额度不足，请检查密钥配置")
        raise RuntimeError(f"DeepSeek 网络请求异常 ({he.response.status_code if he.response is not None else '未知状态'}): {str(he)}")
    except Exception as e:
        raise RuntimeError(f"DeepSeek 筛选失败: {str(e)}")

