#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
时光杂货店 - 代挂云服务器

流程：
  1. 用户扫码 → GET /login → 实时获取 token → 302 到支付宝
  2. 支付宝确认后回调到我们的 /callback → 捕获 authCode
  3. 本地工具轮询 GET /api/authcodes → 获取 authCode 列表
  4. 本地工具用 authCode 登录游戏

改动：二维码中的回调 URL 被修改为指向本服务器
"""

import os
import json
import time
import threading
from datetime import datetime

import requests
from flask import Flask, request, redirect, jsonify, Response
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import logging

app = Flask(__name__)

# ==================== 配置 ====================
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "shiguang2024")

# 支付宝 API 地址
API_URL = "https://webgwmobiler.alipay.com/gameauth/com.alipay.gameauth.common.facade.service.GameCenterPcAuthFacade/getLoginToken?ctoken=bigfish_ctoken_1a76c5jk1b"

API_HEADERS = {
    'accept': 'application/json, text/plain, */*',
    'content-type': 'application/json',
    'origin': 'https://www.wanyiwan.top',
    'referer': 'https://www.wanyiwan.top/',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36',
    'x-webgw-appid': '180020010001270314',
    'x-webgw-ldc-uid': '61',
    'x-webgw-version': '2.0'
}

config = {
    "admin_password": ADMIN_PASSWORD,
    "site_title": "时光杂货店",
    "scan_count": 0
}

# 收集的 authCode 列表
authcodes = []  # [{'authCode': '...', 'time': '...', 'ip': '...'}]
lock = threading.Lock()

# ==================== 调试日志 ====================
# 内存中的调试日志（最多保留200条）
debug_logs = []
DEBUG_LOG_MAX = 200
debug_lock = threading.Lock()


def debug_log(msg: str, level: str = "INFO"):
    """写入调试日志（同时打印到 stdout）"""
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    entry = f"[{ts}] [{level}] {msg}"
    print(entry)  # Railway 会收集 stdout
    with debug_lock:
        debug_logs.append(entry)
        if len(debug_logs) > DEBUG_LOG_MAX:
            del debug_logs[:50]


def get_base_url():
    return request.host_url.rstrip('/')


def fetch_fresh_token_and_url():
    """
    实时获取新的登录 token 和支付宝 URL
    注意：URL修改现在在 /login 路由中处理
    """
    headers = dict(API_HEADERS)
    debug_log(f"[API] 请求 getLoginToken...")
    try:
        resp = requests.post(API_URL, headers=headers, json={}, timeout=10)
        debug_log(f"[API] 响应状态码: {resp.status_code}")
        debug_log(f"[API] 响应长度: {len(resp.text)}")

        data = resp.json()
        debug_log(f"[API] success={data.get('success')}")
        debug_log(f"[API] data keys: {list(data.get('data', {}).keys()) if data.get('data') else 'N/A'}")

        if data.get('success'):
            qr_code = data['data']['qrCode']
            token = qr_code['token']
            original_url = qr_code['url']  # alipays://... 的链接

            debug_log(f"[API] token={token[:20]}... (len={len(token)})")
            debug_log(f"[API] original_url (前150字符): {original_url[:150]}")
            debug_log(f"[API] original_url 完整长度: {len(original_url)}")

            # 不再这里修改URL，返回原始URL，在/login中处理
            return token, original_url
        else:
            debug_log(f"[API] 请求失败: {data.get('msg', 'unknown')}", "ERROR")
    except Exception as e:
        debug_log(f"[API] 异常: {type(e).__name__}: {e}", "ERROR")
    return None, None


def _modify_callback_in_url(original_url: str, new_callback: str) -> str:
    """
    修改支付宝登录 URL 中的回调地址

    实际 URL 结构（多层编码）:
    第1层: https://render.alipay.com/p/s/ulink?scheme=alipays%3A%2F%2F...
    第2层: alipays://platformapi/startapp?appId=...&url=https%3A%2F%2Frender.alipay.com%2Fp%2Fyuyan%2F...%2FpcLogin.html%3F...%26token%3D...

    策略：逐层 URL decode，找到包含 pcLogin.html 的那层，将整个 url 参数
    替换为我们的 callback URL
    """
    from urllib.parse import parse_qs, urlencode, quote, unquote, urlparse, parse_qsl

    debug_log(f"[MODIFY] 原始URL: {original_url}")
    debug_log(f"[MODIFY] 新callback: {new_callback}")

    # ---- 情况1: 直接 alipays:// 开头 ----
    if original_url.startswith('alipays://'):
        debug_log(f"[MODIFY] 情况1: alipays:// 协议")
        if '?' in original_url:
            path_part, query_part = original_url.split('?', 1)
            params = parse_qs(query_part)
            for k, v in params.items():
                debug_log(f"[MODIFY]   {k} = {str(v)[:120]}")
            params['url'] = [new_callback]
            new_query = urlencode(params, doseq=True)
            result = f"{path_part}?{new_query}"
            debug_log(f"[MODIFY] 修改成功")
            return result

    # ---- 情况2: https://render.alipay.com/p/s/ulink?scheme=... ----
    parsed = urlparse(original_url)
    debug_log(f"[MODIFY] URL scheme={parsed.scheme}, host={parsed.netloc}, path={parsed.path}")
    debug_log(f"[MODIFY] query (前300): {parsed.query[:300]}")

    query_params = dict(parse_qsl(parsed.query))
    debug_log(f"[MODIFY] 顶层参数: {list(query_params.keys())}")

    if 'scheme' in query_params:
        # 解码 scheme 参数得到 alipays:// URL
        scheme_val = query_params['scheme']
        debug_log(f"[MODIFY] scheme 值 (前200): {scheme_val[:200]}")

        # 逐层 decode 直到稳定
        decoded = scheme_val
        for level in range(5):
            try:
                new_decoded = unquote(decoded)
                if new_decoded == decoded:
                    break
                decoded = new_decoded
                debug_log(f"[MODIFY] decode level {level+1}: {decoded[:150]}")
            except Exception:
                break

        debug_log(f"[MODIFY] 最终解码: {decoded[:300]}")

        if decoded.startswith('alipays://'):
            # 解析 alipays:// URL 的参数
            if '?' in decoded:
                alipays_path, alipays_query = decoded.split('?', 1)
                alipays_params = dict(parse_qsl(alipays_query))
                debug_log(f"[MODIFY] alipays 参数: {list(alipays_params.keys())}")

                inner_url = alipays_params.get('url', '')
                if inner_url:
                    debug_log(f"[MODIFY] alipays 内嵌 url (前300): {inner_url[:300]}")

                    # 再逐层 decode 内嵌 url
                    inner_decoded = inner_url
                    for level in range(5):
                        try:
                            new_d = unquote(inner_decoded)
                            if new_d == inner_decoded:
                                break
                            inner_decoded = new_d
                        except Exception:
                            break

                    debug_log(f"[MODIFY] 内嵌 url 解码: {inner_decoded[:300]}")

                    # 策略A: 直接把 alipays 的 url 参数替换为我们的 callback
                    debug_log(f"[MODIFY] >>> 采用策略A: 替换 alipays url 参数为 callback <<<")
                    alipays_params['url'] = new_callback
                    new_alipays_query = urlencode(alipays_params)
                    new_alipays = f"{alipays_path}?{new_alipays_query}"

                    # 重新编码并放回外层 scheme 参数
                    # 需要 encode 一次（从 alipays:// 到 scheme 参数需要一次编码）
                    re_encoded = quote(new_alipays, safe='')
                    query_params['scheme'] = re_encoded

                    # 重建完整 URL
                    new_query = urlencode(query_params)
                    result = f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{new_query}"

                    debug_log(f"[MODIFY] 修改成功!")
                    debug_log(f"[MODIFY] 结果 (前300): {result[:300]}")
                    return result

        debug_log(f"[MODIFY] 无法解析 scheme 参数结构", "WARN")

    # ---- 情况3: url 参数直接存在 ----
    if 'url' in query_params:
        debug_log(f"[MODIFY] 情况3: 直接 url 参数")
        query_params['url'] = new_callback
        new_query = urlencode(query_params)
        result = f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{new_query}"
        debug_log(f"[MODIFY] 修改成功")
        return result

    debug_log(f"[MODIFY] !!! 所有策略均失败，原样返回 !!!", "ERROR")
    return original_url


# ==================== 全局请求日志 ====================
@app.before_request
def log_all_requests():
    """记录所有请求，方便排查"""
    if request.path.startswith('/static'):
        return  # 跳过静态文件
    debug_log(f"[REQ] {request.method} {request.path} from {request.remote_addr}")
    if request.query_string:
        debug_log(f"[REQ]   query: {request.query_string.decode('utf-8', errors='replace')[:300]}")


# ==================== 页面路由 ====================

@app.route('/')
def index():
    """首页 - 展示二维码"""
    base = get_base_url()
    login_url = f"{base}/login"
    qr_img_url = f"https://api.qrserver.com/v1/create-qr-code/?size=250x250&data={login_url}"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{config['site_title']} - 扫码登录</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex; flex-direction: column;
            align-items: center; justify-content: center;
            color: #333;
        }}
        .container {{
            background: white; border-radius: 20px;
            padding: 40px; box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            text-align: center; max-width: 400px; width: 90%;
        }}
        h1 {{ color: #667eea; margin-bottom: 10px; font-size: 24px; }}
        .subtitle {{ color: #999; margin-bottom: 30px; font-size: 14px; }}
        .qr-box {{
            background: #f8f9fa; border-radius: 12px;
            padding: 20px; display: inline-block;
        }}
        .qr-box img {{ width: 250px; height: 250px; display: block; }}
        .tip {{
            background: #fff3cd; border: 1px solid #ffc107;
            border-radius: 8px; padding: 12px; margin: 20px 0;
            font-size: 13px; color: #856404;
        }}
        .badge {{
            background: #28a745; color: white;
            padding: 4px 12px; border-radius: 20px;
            font-size: 12px; display: inline-block; margin-bottom: 20px;
        }}
        .stats {{ color: #999; font-size: 12px; margin-top: 15px; }}
        .url-link {{
            display: inline-block; margin-top: 10px;
            padding: 8px 16px; background: #667eea;
            color: white; border-radius: 8px;
            text-decoration: none; font-size: 13px;
        }}
        .url-link:hover {{ background: #764ba2; }}
        .authcodes-info {{
            background: #e8f5e9; border-radius: 8px;
            padding: 10px; margin-top: 10px; font-size: 12px; color: #2e7d32;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>{config['site_title']}</h1>
        <div class="subtitle">代挂工具 - 支付宝扫码</div>
        <span class="badge">实时二维码</span>
        <div class="qr-box">
            <img src="{qr_img_url}" alt="登录二维码">
        </div>
        <div class="tip">
            打开支付宝 App 扫描二维码<br>
            确认后自动登录游戏
        </div>
        <a href="{login_url}" class="url-link">点击直接跳转登录</a>
        <div class="stats">已扫码 {config['scan_count']} 次</div>
        <div class="authcodes-info">
            待处理 authCode: {len(authcodes)} 个
        </div>
    </div>
</body>
</html>"""


@app.route('/login')
def login():
    """
    扫码入口 - 直接跳转到修改后的支付宝登录页
    核心：修改pcLogin.html的回调URL为我们的服务器
    """
    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    debug_log(f"[LOGIN] ========== 新扫码请求 ==========")
    debug_log(f"[LOGIN] 客户端IP: {client_ip}")
    debug_log(f"[LOGIN] User-Agent: {request.headers.get('User-Agent', 'N/A')[:100]}")
    debug_log(f"[LOGIN] Referer: {request.headers.get('Referer', 'N/A')}")

    with lock:
        config["scan_count"] += 1

    token, alipay_url = fetch_fresh_token_and_url()

    if token and alipay_url:
        debug_log(f"[LOGIN] token 获取成功 (len={len(token)})")
        debug_log(f"[LOGIN] alipay_url: {alipay_url[:200]}")

        # 方法1：使用最原始的URL修改方案（可能最稳定）
        # 直接修改alipays:// URL中的url参数
        from urllib.parse import parse_qs, unquote, quote
        
        # 解析原始URL
        parsed = urlparse(alipay_url)
        query = parse_qs(parsed.query)
        
        if 'scheme' in query:
            scheme_val = query['scheme'][0]
            decoded_scheme = unquote(scheme_val)
            debug_log(f"[LOGIN] decoded_scheme (前200): {decoded_scheme[:200]}")
            
            if decoded_scheme.startswith('alipays://platformapi/startapp?'):
                # 解析alipays:// URL
                if '?' in decoded_scheme:
                    alipays_path, alipays_query = decoded_scheme.split('?', 1)
                    alipays_params = parse_qs(alipays_query)
                    
                    if 'url' in alipays_params:
                        # 构建我们的回调URL
                        base = get_base_url()
                        our_callback = f"{base}/callback?token={quote(token)}"
                        
                        # 修改alipays的url参数
                        alipays_params['url'] = [our_callback]
                        
                        # 重建alipays:// URL
                        new_alipays_query = urlencode(alipays_params, doseq=True)
                        new_scheme = f"{alipays_path}?{new_alipays_query}"
                        
                        # 重新编码并放回外层URL
                        query['scheme'] = [quote(new_scheme, safe='')]
                        
                        # 重建完整URL
                        new_query = urlencode(query, doseq=True)
                        final_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{new_query}"
                        
                        debug_log(f"[LOGIN] 最终跳转URL (前200): {final_url[:200]}")
                        debug_log(f"[LOGIN] 执行302跳转...")
                        
                        return redirect(final_url, code=302)
        
        # 方法2：如果方法1失败，使用原始URL
        debug_log(f"[LOGIN] URL修改失败，使用原始支付宝URL")
        debug_log(f"[LOGIN] 执行302跳转...")
        
        return redirect(alipay_url, code=302)
    else:
        debug_log(f"[LOGIN] FAILED: 无法获取 token", "ERROR")
        return Response("Server error, please try later", status=503)


def _modify_pc_login_callback(pc_login_url: str, new_callback: str) -> str:
    """
    修改 pcLogin.html URL 中的回调地址
    pcLogin.html URL 结构（可能有两种情况）:
    
    1. 有url参数的情况（旧版本）:
        https://render.alipay.com/p/yuyan/180020010001270314/0.2.2304261138.43/pcLogin.html
        ?appId=2021003129681023
        &source=pcWeb
        &url=https%3A%2F%2Fwww.wanyiwan.top%2Falipay%2Fcallback%3F...
        
    2. 没有url参数的情况（新版本）:
        https://render.alipay.com/p/yuyan/180020010001206617/pcLogin.html?caprMode=sync
        # 授权流程由pcLogin.html的JS处理，不是HTTP回调
        
    策略：
    - 如果有url参数：修改它指向我们的服务器
    - 如果没有url参数：保持原样，因为PC登录流程不走HTTP回调
    """
    from urllib.parse import urlparse, parse_qs, urlencode

    debug_log(f"[MOD_PC] 原始URL: {pc_login_url[:200]}")
    debug_log(f"[MOD_PC] 新callback: {new_callback}")

    parsed = urlparse(pc_login_url)
    params = parse_qs(parsed.query)

    debug_log(f"[MOD_PC] 参数: {list(params.keys())}")

    # 检查是否有url参数
    if 'url' in params:
        # 情况1：有url参数，需要修改
        old_url = params['url'][0] if isinstance(params['url'], list) else params['url']
        debug_log(f"[MOD_PC] 原url参数: {old_url[:150]}")

        # 解析原url，保留其中的其他参数（如token）
        old_parsed = urlparse(old_url)
        old_params = parse_qs(old_parsed.query)
        debug_log(f"[MOD_PC] 原url参数: {list(old_params.keys())}")

        # 构建新的回调URL，保留原参数
        new_parsed = urlparse(new_callback)
        new_params = parse_qs(new_parsed.query)

        # 合并参数：保留原url中的参数，但替换host和path
        merged_params = dict(old_params)
        merged_params.update(new_params)

        # 构建新的完整回调URL
        new_callback_with_params = f"{new_callback}"
        if merged_params:
            if '?' not in new_callback_with_params:
                new_callback_with_params += '?'
            else:
                new_callback_with_params += '&'
            new_callback_with_params += urlencode(merged_params, doseq=True)

        params['url'] = new_callback_with_params
        debug_log(f"[MOD_PC] 新url参数: {new_callback_with_params[:150]}")
        
        # 重建URL
        new_query = urlencode(params, doseq=True)
        result = f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{new_query}"
    else:
        # 情况2：没有url参数，保持原样（PC登录不走HTTP回调）
        debug_log(f"[MOD_PC] 没有url参数，保持原URL不变（PC登录由JS处理）")
        result = pc_login_url

    debug_log(f"[MOD_PC] 结果: {result[:200]}")
    return result


@app.route('/callback')
def callback():
    """
    支付宝扫码确认后的回调
    捕获 authCode 并保存
    """
    params = dict(request.args)
    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)

    debug_log(f"[CALLBACK] ========== 收到回调 ==========")
    debug_log(f"[CALLBACK] IP: {client_ip}")
    debug_log(f"[CALLBACK] 方法: {request.method}")
    debug_log(f"[CALLBACK] 完整URL: {request.url}")
    debug_log(f"[CALLBACK] Content-Type: {request.headers.get('Content-Type', 'N/A')}")
    debug_log(f"[CALLBACK] User-Agent: {request.headers.get('User-Agent', 'N/A')[:100]}")
    debug_log(f"[CALLBACK] Referer: {request.headers.get('Referer', 'N/A')}")

    # 打印所有 query 参数
    debug_log(f"[CALLBACK] Query 参数 ({len(params)} 个):")
    for k, v in params.items():
        debug_log(f"[CALLBACK]   {k} = {str(v)[:200]}")

    # 如果有 POST body 也打印
    if request.method == 'POST':
        debug_log(f"[CALLBACK] POST body: {request.get_data(as_text=True)[:500]}")

    # 检查 request.form
    form_data = dict(request.form)
    if form_data:
        debug_log(f"[CALLBACK] Form 数据 ({len(form_data)} 个):")
        for k, v in form_data.items():
            debug_log(f"[CALLBACK]   form[{k}] = {str(v)[:200]}")

    # 尝试从多个可能的参数名中提取 authCode
    auth_code = None
    for key_name in ['authCode', 'code', 'auth_code', 'token', 'authToken']:
        val = params.get(key_name)
        if val:
            auth_code = val[0] if isinstance(val, list) else val
            if auth_code:
                debug_log(f"[CALLBACK] 从 '{key_name}' 找到 authCode: {auth_code[:20]}... (len={len(auth_code)})")
                break

    if not auth_code:
        # 也检查 form data
        for key_name in ['authCode', 'code', 'auth_code', 'token', 'authToken']:
            val = form_data.get(key_name)
            if val:
                auth_code = str(val)
                debug_log(f"[CALLBACK] 从 form '{key_name}' 找到 authCode: {auth_code[:20]}... (len={len(auth_code)})")
                break

    entry = {
        'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'ip': client_ip,
        'authCode': auth_code,
        'params': params,
        'url': request.url,
        'form': form_data,
    }

    with lock:
        authcodes.append(entry)

    if auth_code:
        debug_log(f"[CALLBACK] 成功获取 authCode!")
    else:
        debug_log(f"[CALLBACK] !!! 未找到 authCode !!! 所有参数已记录", "WARN")
        debug_log(f"[CALLBACK] 提示: 支付宝可能没有回调到此URL，请检查 alipays:// 链接中的回调配置", "WARN")

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>登录成功</title>
    <style>
        body {{
            font-family: -apple-system, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            display: flex; align-items: center; justify-content: center;
            min-height: 100vh; margin: 0;
        }}
        .box {{
            background: white; border-radius: 16px; padding: 40px;
            text-align: center; box-shadow: 0 10px 40px rgba(0,0,0,0.2);
        }}
        h1 {{ color: #28a745; }}
    </style>
</head>
<body>
    <div class="box">
        <h1>{"登录成功" if auth_code else "已收到回调"}</h1>
        <p>代挂工具正在处理中，请稍候...</p>
        <p style="color:#999;font-size:12px;">你可以关闭此页面</p>
    </div>
</body>
</html>"""


# ==================== API ====================

@app.route('/api/authcodes', methods=['GET'])
def list_authcodes():
    """获取待处理的 authCode 列表（本地工具轮询此接口）"""
    with lock:
        return jsonify({
            "ok": True,
            "count": len(authcodes),
            "authcodes": authcodes
        })


@app.route('/api/authcodes/<int:index>', methods=['DELETE'])
def consume_authcode(index):
    """消费（删除）一个已处理的 authCode"""
    with lock:
        if 0 <= index < len(authcodes):
            removed = authcodes.pop(index)
            return jsonify({"ok": True, "removed": removed.get('authCode', '')})
    return jsonify({"ok": False, "msg": "index out of range"})


@app.route('/api/authcodes/clear', methods=['POST'])
def clear_authcodes():
    """清空所有 authCode"""
    with lock:
        count = len(authcodes)
        authcodes.clear()
    return jsonify({"ok": True, "msg": f"Cleared {count} authcodes"})


@app.route('/api/accounts', methods=['GET'])
def list_accounts():
    """兼容旧接口"""
    with lock:
        compat = [{'time': a['time'], 'ip': a['ip'], 'params': a['params']} for a in authcodes]
        return jsonify({"ok": True, "count": len(compat), "accounts": compat})


@app.route('/api/accounts', methods=['DELETE'])
def clear_accounts():
    """兼容旧接口"""
    return clear_authcodes()


@app.route('/api/health')
def health():
    return jsonify({
        "status": "ok",
        "scan_count": config["scan_count"],
        "pending_authcodes": len(authcodes),
        "time": datetime.now().isoformat()
    })


@app.route('/api/debug/logs')
def get_debug_logs():
    """获取调试日志"""
    with debug_lock:
        return jsonify({
            "ok": True,
            "count": len(debug_logs),
            "logs": debug_logs
        })


@app.route('/api/report-token', methods=['POST'])
def report_token():
    """
    接收前端 JS 上报的 token 数据
    来自中间页面的 postMessage/storage 监听
    """
    try:
        data = request.get_json(force=True)
    except Exception:
        data = {}

    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)

    debug_log(f"[REPORT-TOKEN] ========== 前端上报 ==========")
    debug_log(f"[REPORT-TOKEN] IP: {client_ip}")
    debug_log(f"[REPORT-TOKEN] 数据: {json.dumps(data, ensure_ascii=False, default=str)[:1000]}")

    # 提取可能的 authCode / token
    token_value = None
    report_type = data.get('type', 'unknown')

    if report_type == 'postmessage':
        inner = data.get('data', {})
        if isinstance(inner, dict):
            token_value = (
                inner.get('token') or inner.get('authCode') or
                inner.get('gameToken') or inner.get('sessionId') or
                inner.get('x-game-token-pcweb') or
                inner.get('accessToken') or inner.get('code')
            )
        elif isinstance(inner, str) and len(inner) > 10:
            token_value = inner
    elif report_type == 'storage_set':
        key = data.get('key', '')
        value = data.get('value', '')
        token_value = value
        debug_log(f"[REPORT-TOKEN] storage key={key}")

    # 保存
    entry = {
        'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'ip': client_ip,
        'authCode': token_value,
        'report_type': report_type,
        'raw_data': data,
    }

    with lock:
        authcodes.append(entry)

    if token_value:
        debug_log(f"[REPORT-TOKEN] >>> 捕获到 token! (len={len(token_value)}) <<<")
    else:
        debug_log(f"[REPORT-TOKEN] 未提取到有效 token", "WARN")

    return jsonify({"ok": True, "captured": token_value is not None})


@app.route('/api/debug/clear', methods=['POST'])
def clear_debug_logs():
    """清空调试日志"""
    with debug_lock:
        count = len(debug_logs)
        debug_logs.clear()
    return jsonify({"ok": True, "msg": f"Cleared {count} logs"})


@app.route('/debug')
def debug_page():
    """调试日志页面 - 在浏览器直接查看"""
    with debug_lock:
        logs_html = "\n".join(
            f"<tr><td>{line}</td></tr>"
            for line in debug_logs[-100:]  # 最近100条
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>调试日志 - 时光杂货店</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: 'Consolas', 'Monaco', monospace;
            background: #1e1e1e; color: #d4d4d4;
            padding: 20px;
        }}
        h1 {{ color: #4ec9b0; margin-bottom: 10px; font-size: 18px; }}
        .info {{ color: #999; margin-bottom: 15px; font-size: 12px; }}
        table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
        td {{ padding: 2px 8px; border-bottom: 1px solid #333; word-break: break-all; }}
        tr:hover {{ background: #2d2d2d; }}
        .warn {{ color: #dcdcaa; }}
        .error {{ color: #f44747; }}
        .refresh-btn {{
            background: #0e639c; color: white; border: none;
            padding: 8px 16px; border-radius: 4px; cursor: pointer;
            margin-bottom: 15px; font-size: 13px;
        }}
        .refresh-btn:hover {{ background: #1177bb; }}
        a {{ color: #4ec9b0; }}
        .links {{ margin-top: 15px; font-size: 12px; }}
        .links a {{ margin-right: 15px; }}
    </style>
</head>
<body>
    <h1>调试日志</h1>
    <div class="info">
        总计 {len(debug_logs)} 条日志 | 当前时间 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    </div>
    <button class="refresh-btn" onclick="location.reload()">刷新 (F5)</button>
    <div class="links">
        <a href="/">首页</a>
        <a href="/api/authcodes">authcodes API</a>
        <a href="/api/health">健康检查</a>
        <a href="/api/debug/logs" target="_blank">JSON格式日志</a>
    </div>
    <table>
        {logs_html or '<tr><td>暂无日志</td></tr>'}
    </table>
</body>
</html>"""


# ==================== 启动 ====================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8888))
    print(f"[+] Starting server on port {port}...")
    app.run(host='0.0.0.0', port=port, debug=False)
