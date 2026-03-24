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


def get_base_url():
    return request.host_url.rstrip('/')


def fetch_fresh_token_and_url():
    """
    实时获取新的登录 token 和支付宝 URL
    关键：修改回调 URL 为我们自己的服务器
    """
    headers = dict(API_HEADERS)
    try:
        resp = requests.post(API_URL, headers=headers, json={}, timeout=10)
        data = resp.json()
        if data.get('success'):
            qr_code = data['data']['qrCode']
            token = qr_code['token']
            original_url = qr_code['url']  # alipays://... 的链接

            # 解析 alipays:// URL 中的回调地址
            # 格式通常是: alipays://platformapi/startapp?appId=xxx&url=ENCODED_CALLBACK_URL
            # 我们需要把 url 参数替换为指向我们自己的 /callback
            callback_url = f"{get_base_url()}/callback"

            # 尝试修改回调地址
            modified_url = _modify_callback_in_url(original_url, callback_url)

            return token, modified_url
    except Exception as e:
        app.logger.error(f"fetch_fresh_token error: {e}")
    return None, None


def _modify_callback_in_url(original_url: str, new_callback: str) -> str:
    """
    修改 alipays:// URL 中的回调地址
    alipays URL 格式: alipays://platformapi/startapp?appId=xxx&url=ENCODED_URL
    """
    from urllib.parse import parse_qs, urlparse, urlencode, urlunparse, quote

    # alipays:// 协议需要特殊处理
    if original_url.startswith('alipays://'):
        # 提取 query 部分
        if '?' in original_url:
            path_part, query_part = original_url.split('?', 1)
            params = parse_qs(query_part)

            # 替换 url 参数为我们的回调
            # 原始 url 参数通常是编码过的支付宝回调
            params['url'] = [new_callback]

            # 重新编码
            new_query = urlencode(params, doseq=True)
            return f"{path_part}?{new_query}"

    # 如果不是 alipays:// 格式，尝试作为普通 URL 处理
    return original_url


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
    """核心：扫码入口 - 实时获取新token并重定向到支付宝"""
    with lock:
        config["scan_count"] += 1

    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    app.logger.info(f"[SCAN] {client_ip} at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    token, redirect_url = fetch_fresh_token_and_url()

    if redirect_url:
        app.logger.info(f"[SCAN] OK -> redirecting to Alipay")
        return redirect(redirect_url, code=302)
    else:
        app.logger.error(f"[SCAN] FAILED to get token")
        return Response("Server error, please try again later", status=503)


@app.route('/callback')
def callback():
    """
    支付宝扫码确认后的回调
    捕获 authCode 并保存
    """
    params = dict(request.args)
    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)

    app.logger.info(f"[CALLBACK] from {client_ip}")
    app.logger.info(f"[CALLBACK] params keys: {list(params.keys())}")
    app.logger.info(f"[CALLBACK] full params: {json.dumps(params, ensure_ascii=False)}")

    # 尝试从多个可能的参数名中提取 authCode
    auth_code = (
        params.get('authCode', [None])[0]
        or params.get('code', [None])[0]
        or params.get('auth_code', [None])[0]
        or params.get('token', [None])[0]
    )

    entry = {
        'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'ip': client_ip,
        'authCode': auth_code,
        'params': params,
    }

    with lock:
        authcodes.append(entry)

    # 同时也保留旧格式的 accounts 记录（兼容）
    account_entry = {
        'time': entry['time'],
        'ip': client_ip,
        'params': params
    }

    if auth_code:
        app.logger.info(f"[CALLBACK] Got authCode! length={len(auth_code)}")

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


# ==================== 启动 ====================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8888))
    print(f"[+] Starting server on port {port}...")
    app.run(host='0.0.0.0', port=port, debug=False)
