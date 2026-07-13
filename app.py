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
import hashlib
import base64
import sqlite3
import threading
from datetime import datetime, timedelta

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

GAME_HEADERS = {
    'content-type': 'application/x-www-form-urlencoded',
    'user-agent': 'Mozilla/5.0 AlipayMiniApp',
    'origin': 'https://www.wanyiwan.top',
    'referer': 'https://www.wanyiwan.top/',
}

# ==================== ptoken 兑换（服务端直接完成，避免 authCode 过期）====================
PTOKEN_URL   = "https://pvt-api.8rn4u.com/h5verify/ptoken"
PTOKEN_SIGN_KEY = "Jp*4Y8vQOYck2*&Z"
PTOKEN_GID   = "1021669"
PTOKEN_PID   = "783"
PTOKEN_OS    = "android"
PTOKEN_VER   = "4.5.22"
SERVER_LIST_URL = "https://login-sg-35.akbing.com/Web/newPackageServerList"
GAME_CHANNEL_ID = 40
GAME_PACKAGE_MARK = "40005001"
GAME_PACKAGE_VERSION = "3.2.0.1433"
GAME_LANGUAGE = "zh_cn"


def _ptoken_sign(params: dict) -> str:
    """ptoken 接口签名：key 排序后 key=val 直接拼接（无分隔符）+ 密钥，MD5"""
    sorted_str = "".join(f"{k}={v}" for k, v in sorted(params.items()) if v != "")
    return hashlib.md5((sorted_str + PTOKEN_SIGN_KEY).encode()).hexdigest()


def _query_game_openid_from_token(game_token: str) -> str:
    """用游戏 token 查询真正的游戏 openId。"""
    token = str(game_token or "").strip()
    if not token:
        return ""
    body_param = json.dumps({
        "openId": "",
        "channelId": GAME_CHANNEL_ID,
        "language": GAME_LANGUAGE,
        "token": token,
        "gid": PTOKEN_GID,
        "pid": PTOKEN_PID,
        "platformUserId": "",
        "packageMark": GAME_PACKAGE_MARK,
        "packageVersion": GAME_PACKAGE_VERSION,
        "hotPackVersion": GAME_PACKAGE_VERSION,
        "appId": "0",
        "childGameId": "0",
    }, separators=(',', ':'), ensure_ascii=False)
    try:
        resp = requests.post(
            SERVER_LIST_URL,
            data=f"param={requests.utils.quote(body_param)}",
            headers=GAME_HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if str(data.get('msg', '') or '').lower() != 'success':
            debug_log(f"[SERVERLIST] 失败: {data}", "WARN")
            return ""
        return str(data.get('openId') or '').strip()
    except Exception as e:
        debug_log(f"[SERVERLIST] 异常: {e}", "WARN")
        return ""


def exchange_authcode_to_token(auth_code: str) -> dict | None:
    """
    用 authCode 立刻换取游戏 token（在服务端完成，避免本地轮询延迟导致 authCode 过期）。
    返回: {'game_token': 'BASE64...', 'openid': '3125875535'}
    失败返回 None。
    """
    ts = int(time.time())
    pdata = json.dumps({
        "code": auth_code,
        "path": {"cid": "0", "num": "0"},
        "scene": "",
        "platform": "Android"
    }, separators=(',', ':'))
    trans_info = base64.b64encode(b'{"cid":"0","num":"0"}').decode()
    device_id = "railway-server-automate"

    params = {
        "dev":        device_id,
        "gid":        PTOKEN_GID,
        "os":         PTOKEN_OS,
        "pdata":      pdata,
        "pid":        PTOKEN_PID,
        "ptoken":     str(ts),
        "refer":      f"{PTOKEN_PID}_{PTOKEN_GID}_0_0",
        "sversion":   PTOKEN_VER,
        "time":       str(ts),
        "trans_info": trans_info,
        "version":    PTOKEN_VER,
    }
    params["sign"] = _ptoken_sign(params)

    try:
        resp = requests.get(
            PTOKEN_URL, params=params,
            headers={'user-agent': 'Mozilla/5.0 AlipayMiniApp'},
            timeout=10
        )
        data = resp.json()
        debug_log(f"[PTOKEN] state={data.get('state')} msg={data.get('msg','')}")
        if data.get('state') == 1:
            d = data.get('data', {})
            token  = d.get('token', '')
            ptoken_openid = str(d.get('openid') or d.get('puid', ''))
            if token:
                game_openid = _query_game_openid_from_token(token) or ptoken_openid
                if game_openid != ptoken_openid:
                    debug_log(f"[PTOKEN] openid 已校正: ptoken_openid={ptoken_openid} game_openid={game_openid}")
                return {
                    'game_token': token,
                    'openid': game_openid,
                    'ptoken_openid': ptoken_openid,
                }
        debug_log(f"[PTOKEN] 失败: {data}", "ERROR")
        return None
    except Exception as e:
        debug_log(f"[PTOKEN] 异常: {e}", "ERROR")
        return None

config = {
    "admin_password": ADMIN_PASSWORD,
    "site_title": "时光杂货店",
    "scan_count": 0
}

# ==================== SQLite 持久化 ====================
DB_PATH = os.environ.get("AUTH_DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "authcodes.db"))

_db_local = threading.local()
_db_lock = threading.Lock()  # 仅用于 scan_count 等非 DB 计数器
authcode_changed = threading.Condition()


def _get_db() -> sqlite3.Connection:
    """获取当前线程的 SQLite 连接"""
    if not hasattr(_db_local, 'conn') or _db_local.conn is None:
        _db_local.conn = sqlite3.connect(DB_PATH, timeout=10)
        _db_local.conn.row_factory = sqlite3.Row
        _db_local.conn.execute("PRAGMA journal_mode=WAL")
    return _db_local.conn


def _init_db() -> int:
    """初始化数据库表，并清理同 openid 的历史重复记录。"""
    db = _get_db()
    db.execute("""
        CREATE TABLE IF NOT EXISTS authcodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time TEXT NOT NULL,
            ip TEXT DEFAULT '',
            authCode TEXT DEFAULT '',
            game_token TEXT DEFAULT '',
            openid TEXT DEFAULT '',
            aliUserId TEXT DEFAULT '',
            serverId TEXT DEFAULT '',
            params TEXT DEFAULT '{}',
            url TEXT DEFAULT '',
            form TEXT DEFAULT '{}',
            report_type TEXT DEFAULT '',
            raw_data TEXT DEFAULT '{}',
            jwt_token TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        )
    """)
    # 兼容旧表：尝试新增字段（表已存在时忽略）
    for col in ("jwt_token", "spanner"):
        try:
            db.execute(f"ALTER TABLE authcodes ADD COLUMN {col} TEXT DEFAULT ''")
        except Exception:
            pass
    db.execute("CREATE INDEX IF NOT EXISTS idx_created_at ON authcodes(created_at)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_authcodes_openid ON authcodes(openid)")
    repaired = _db_repair_openids_from_tokens(db)
    deduped = _db_dedupe_keep_latest(db)
    db.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_authcodes_openid_unique
        ON authcodes(openid)
        WHERE openid IS NOT NULL AND openid <> ''
    """)
    db.commit()
    return repaired + deduped


def _db_dedupe_keep_latest(db: sqlite3.Connection | None = None) -> int:
    """同 openid 仅保留最新一条；返回删除条数。"""
    own_db = db is None
    if db is None:
        db = _get_db()
    result = db.execute("""
        DELETE FROM authcodes
        WHERE openid IS NOT NULL
          AND openid <> ''
          AND id NOT IN (
              SELECT MAX(id)
              FROM authcodes
              WHERE openid IS NOT NULL AND openid <> ''
              GROUP BY openid
          )
    """)
    if own_db:
        db.commit()
    return int(result.rowcount or 0)


def _db_repair_openids_from_tokens(db: sqlite3.Connection | None = None) -> int:
    """修正历史记录中被存成支付宝标识的 openid。"""
    own_db = db is None
    if db is None:
        db = _get_db()
    rows = db.execute("""
        SELECT id, game_token, openid
        FROM authcodes
        WHERE game_token IS NOT NULL AND game_token <> ''
        ORDER BY id DESC
        LIMIT 50
    """).fetchall()
    repaired = 0
    for row in rows:
        real_openid = _query_game_openid_from_token(row['game_token'])
        if not real_openid:
            continue
        stored_openid = str(row['openid'] or '').strip()
        if stored_openid == real_openid:
            continue
        db.execute("UPDATE authcodes SET openid = ? WHERE id = ?", (real_openid, int(row['id'])))
        repaired += 1
    if own_db and repaired:
        db.commit()
    return repaired


def _db_insert(entry: dict):
    """插入一条 authcode 记录"""
    with authcode_changed:
        db = _get_db()
        openid = str(entry.get('openid', '') or '').strip()
        if openid:
            db.execute("DELETE FROM authcodes WHERE openid = ?", (openid,))
        cur = db.execute("""
            INSERT INTO authcodes (time, ip, authCode, game_token, openid, aliUserId, serverId, params, url, form, report_type, raw_data, jwt_token, spanner)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            entry.get('time', ''),
            entry.get('ip', ''),
            entry.get('authCode', ''),
            entry.get('game_token', ''),
            openid,
            entry.get('aliUserId', ''),
            entry.get('serverId', ''),
            json.dumps(entry.get('params', {}), ensure_ascii=False),
            entry.get('url', ''),
            json.dumps(entry.get('form', {}), ensure_ascii=False),
            entry.get('report_type', ''),
            json.dumps(entry.get('raw_data', {}), ensure_ascii=False, default=str),
            entry.get('jwt_token', ''),
            entry.get('spanner', ''),
        ))
        db.commit()
        authcode_changed.notify_all()
        return cur.lastrowid


def _db_list(openid: str = "", since_id: int = 0) -> list[dict]:
    """获取记录（按 id 升序，兼容旧 API 的列表下标顺序）"""
    db = _get_db()
    where = []
    args = []
    if openid:
        where.append("openid = ?")
        args.append(openid)
    if since_id:
        where.append("id > ?")
        args.append(int(since_id))
    sql = "SELECT * FROM authcodes"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id"
    rows = db.execute(sql, args).fetchall()
    return [_db_row_to_dict(r) for r in rows]


def _db_latest_id() -> int:
    """获取当前最大记录 id。"""
    db = _get_db()
    row = db.execute("SELECT COALESCE(MAX(id), 0) FROM authcodes").fetchone()
    return int(row[0] or 0)


def _compact_authcode(row: dict) -> dict:
    """轻量记录，用于快照/心跳，避免出站 token/authCode。"""
    return {
        "id": row.get("id"),
        "time": row.get("time", ""),
        "openid": row.get("openid", ""),
        "aliUserId": row.get("aliUserId", ""),
        "serverId": row.get("serverId", ""),
        "has_game_token": bool(row.get("game_token") or row.get("token")),
        "has_authCode": bool(row.get("authCode") or row.get("auth_code")),
        "has_jwt_token": bool(row.get("jwt_token")),
        "jwt_token": row.get("jwt_token", "") or "",
        "spanner": row.get("spanner", "") or "",
    }


def _db_row_to_dict(row) -> dict:
    """将数据库行转为 API 响应格式的 dict"""
    d = dict(row)
    # 解析 JSON 字段
    for k in ('params', 'form', 'raw_data'):
        if k in d and isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k])
            except (json.JSONDecodeError, TypeError):
                pass
    return d


def _db_delete_by_id(entry_id: int) -> dict | None:
    """按数据库 id 删除一条记录"""
    with authcode_changed:
        db = _get_db()
        row = db.execute("SELECT * FROM authcodes WHERE id = ?", (entry_id,)).fetchone()
        if row:
            db.execute("DELETE FROM authcodes WHERE id = ?", (entry_id,))
            db.commit()
            authcode_changed.notify_all()
            return _db_row_to_dict(row)
        return None


def _db_delete_by_index(index: int) -> dict | None:
    """按列表下标删除（兼容旧客户端，按 id 升序第 N 条）"""
    with authcode_changed:
        db = _get_db()
        row = db.execute("SELECT * FROM authcodes ORDER BY id LIMIT 1 OFFSET ?", (index,)).fetchone()
        if row:
            db.execute("DELETE FROM authcodes WHERE id = ?", (row['id'],))
            db.commit()
            authcode_changed.notify_all()
            return _db_row_to_dict(row)
        return None


def _db_clear() -> int:
    """清空所有记录，返回删除数量"""
    with authcode_changed:
        db = _get_db()
        count = db.execute("SELECT COUNT(*) FROM authcodes").fetchone()[0]
        db.execute("DELETE FROM authcodes")
        db.commit()
        authcode_changed.notify_all()
        return count


def _db_count() -> int:
    """获取记录总数"""
    db = _get_db()
    return db.execute("SELECT COUNT(*) FROM authcodes").fetchone()[0]


def _db_cleanup_old(days: int = 30) -> int:
    """删除 N 天前的记录，返回删除条数"""
    db = _get_db()
    result = db.execute(
        "DELETE FROM authcodes WHERE created_at < datetime('now', 'localtime', ?)",
        (f'-{days} days',)
    )
    db.commit()
    return result.rowcount


# ==================== 自动清理线程 ====================
_cleanup_stop = threading.Event()


def _daily_cleanup_thread():
    """后台线程：每天清理一次超过30天的记录"""
    while not _cleanup_stop.wait(86400):  # 24小时
        try:
            count = _db_cleanup_old(30)
            if count > 0:
                debug_log(f"[CLEANUP] 自动清理：删除 {count} 条超过30天的记录")
        except Exception as e:
            debug_log(f"[CLEANUP] 异常: {e}", "ERROR")

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
    """首页 - 展示二维码（支持 ?server=4000104 指定区服）"""
    base = get_base_url()
    # 支持 URL 参数传入 serverId（方便不同区服的用户用不同链接）
    server_id = request.args.get('server', '')
    login_path = f"/login?server={server_id}" if server_id else "/login"
    login_url = f"{base}{login_path}"
    qr_img_url = f"https://api.qrserver.com/v1/create-qr-code/?size=250x250&data={requests.utils.quote(login_url)}"

    server_hint = f"（区服 {server_id}）" if server_id else ""

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
        .server-tag {{
            background: #e8f0fe; color: #1a73e8;
            padding: 2px 10px; border-radius: 12px;
            font-size: 12px; display: inline-block; margin-bottom: 15px;
        }}
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
        <div class="subtitle">代挂工具 - 支付宝扫码 {server_hint}</div>
        {'<span class="server-tag">区服 ' + server_id + '</span>' if server_id else ''}
        <span class="badge">实时二维码</span>
        <div class="qr-box">
            <a href="{login_url}" target="_blank">
                <img src="{qr_img_url}" alt="登录二维码">
            </a>
        </div>
        <div class="tip">
            打开支付宝 App 扫描二维码<br>
            确认后自动登录游戏
        </div>
        <a href="{login_url}" class="url-link">点击直接跳转登录</a>
        <div class="stats">已扫码 {config['scan_count']} 次</div>
        <div class="authcodes-info">
            待处理 authCode: {_db_count()} 个
        </div>
    </div>
</body>
</html>"""


def _poll_for_authcode(session_token: str, server_id: str):
    """
    后台线程：用 session_token 轮询 loginForPc，等手机扫码授权成功，
    再调 queryPcGameAuthInfo 拿 authCode，存入全局 authcodes 列表。
    
    抓包确认的真实流程：
      1. loginForPc (轮询，带 session_token) → 成功后返回 userId + 新 token
      2. queryPcGameAuthInfo (appId=2021004170660258) → 返回 authCode
    """
    LOGIN_FOR_PC_URL = (
        "https://webgwmobiler.alipay.com/gameauth/com.alipay.gameauth.common.facade"
        ".service.GameCenterPcAuthFacade/loginForPc"
        "?ctoken=bigfish_ctoken_1a76c5jk1b"
    )
    AUTH_INFO_URL = (
        "https://webgwmobiler.alipay.com/gamecenterhome/com.alipay.gamecenterhome"
        ".common.facade.service.GameCenterPcGameFacade/queryPcGameAuthInfo"
        "/uprodhatchstation66500008?ctoken=bigfish_ctoken_1a76c5jk1b"
    )
    APP_ID = "2021004170660258"  # 时光杂货店 appId（从抓包确认）

    max_polls = 60   # 最多等 ~3 分钟（每次3秒）
    poll_interval = 3

    headers = dict(API_HEADERS)

    debug_log(f"[POLL] 开始轮询 loginForPc，session_token={session_token[:20]}...")

    for attempt in range(max_polls):
        time.sleep(poll_interval)
        try:
            headers['x-game-token-pcweb'] = session_token
            resp = requests.post(
                LOGIN_FOR_PC_URL,
                headers=headers,
                json={"token": session_token},
                timeout=10
            )
            data = resp.json()
            if data.get('success') and data.get('data'):
                user_id = data['data'].get('userId', '')
                new_token = data['data'].get('token', session_token)
                # 从 loginForPc 响应中提取 spanner Cookie
                set_cookie = resp.headers.get('Set-Cookie', '')
                spanner_cookie = [c.strip() for c in set_cookie.split(';') if c.strip().startswith('spanner=')]
                poll_cookie = spanner_cookie[0] if spanner_cookie else ''
                debug_log(f"[POLL] ✅ 扫码成功! userId={user_id}，cookie={'有' if poll_cookie else '无'}，获取 authCode...")

                # 拿 authCode（带上 Cookie）
                auth_headers = dict(API_HEADERS)
                auth_headers['x-game-token-pcweb'] = new_token
                if poll_cookie:
                    auth_headers['Cookie'] = poll_cookie
                auth_resp = requests.post(
                    AUTH_INFO_URL,
                    headers=headers,
                    json={"appId": APP_ID},
                    timeout=10
                )
                auth_data = auth_resp.json()
                if auth_data.get('success') and auth_data.get('data'):
                    auth_code = auth_data['data'].get('authCode', '')
                    if auth_code:
                        debug_log(f"[POLL] authCode 获取成功，立刻兑换 ptoken（避免过期）...")
                        # ★ 关键修复：在服务端立刻兑换，不等本地轮询
                        tok_data = exchange_authcode_to_token(auth_code)
                        if tok_data:
                            entry = {
                                'time':       __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                'ip':         'alipay-pc-poll',
                                'authCode':   auth_code,
                                'game_token': tok_data['game_token'],
                                'openid':     tok_data['openid'],
                                'aliUserId':  str(user_id),
                                'serverId':   server_id,
                                'params':     {'authCode': auth_code, 'server': server_id},
                                'url':        '',
                                'form':       {},
                                'jwt_token':  str(new_token),
                                'spanner':    poll_cookie,
                            }
                            _db_insert(entry)
                            debug_log(f"[POLL] ✅ token 兑换成功并存储! userId={user_id} openid={tok_data['openid']} serverId={server_id or '未指定'}")
                        else:
                            debug_log(f"[POLL] ⚠ ptoken 兑换失败，仍存入原始 authCode（本地端可能失败）", "WARN")
                            entry = {
                                'time':       __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                'ip':         'alipay-pc-poll',
                                'authCode':   auth_code,
                                'game_token': '',
                                'openid':     '',
                                'aliUserId':  str(user_id),
                                'serverId':   server_id,
                                'params':     {'authCode': auth_code, 'server': server_id},
                                'url':        '',
                                'form':       {},
                            }
                            _db_insert(entry)
                        return
                    else:
                        debug_log(f"[POLL] ❌ queryPcGameAuthInfo 未返回 authCode: {auth_data}", "ERROR")
                        return
                else:
                    debug_log(f"[POLL] ❌ queryPcGameAuthInfo 失败: {auth_data}", "ERROR")
                    return
            else:
                # 用户未扫码，继续等待
                if attempt % 5 == 0:
                    debug_log(f"[POLL] 等待扫码... attempt={attempt+1}/{max_polls}")
        except Exception as e:
            debug_log(f"[POLL] 异常: {e}", "WARN")

    debug_log(f"[POLL] 超时（{max_polls * poll_interval}秒），停止等待", "WARN")


@app.route('/login')
def login():
    """
    扫码入口：
      1. 调 getLoginToken 拿到 sessionToken + 支付宝二维码 URL
      2. 302 跳转原始支付宝 URL（手机扫码）
      3. 后台线程用 sessionToken 轮询 loginForPc，扫码成功后自动拿 authCode
    注意：不修改支付宝 URL，修改会导致支付宝 App 无法识别！
    """
    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    server_id = request.args.get('server', '')

    debug_log(f"[LOGIN] ========== 新扫码请求 ==========")
    debug_log(f"[LOGIN] 客户端IP: {client_ip}")
    debug_log(f"[LOGIN] server_id: {server_id or '未指定'}")
    debug_log(f"[LOGIN] User-Agent: {request.headers.get('User-Agent', 'N/A')[:100]}")

    with _db_lock:
        config["scan_count"] += 1

    token, alipay_url = fetch_fresh_token_and_url()

    if token and alipay_url:
        debug_log(f"[LOGIN] token 获取成功 (len={len(token)})")
        debug_log(f"[LOGIN] 启动后台轮询线程...")

        # 后台线程轮询等待扫码
        t = threading.Thread(
            target=_poll_for_authcode,
            args=(token, server_id),
            daemon=True
        )
        t.start()

        debug_log(f"[LOGIN] 执行302跳转（原始URL，不修改）...")
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
    捕获 authCode 并保存，支持 server 参数（区服ID）透传
    """
    params = dict(request.args)
    client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)

    debug_log(f"[CALLBACK] ========== 收到回调 ==========")
    debug_log(f"[CALLBACK] IP: {client_ip}")
    debug_log(f"[CALLBACK] 完整URL: {request.url}")

    # 打印所有 query 参数
    debug_log(f"[CALLBACK] Query 参数 ({len(params)} 个):")
    for k, v in params.items():
        debug_log(f"[CALLBACK]   {k} = {str(v)[:200]}")

    # 检查 POST body
    form_data = dict(request.form)
    if form_data:
        debug_log(f"[CALLBACK] Form 数据 ({len(form_data)} 个):")
        for k, v in form_data.items():
            debug_log(f"[CALLBACK]   form[{k}] = {str(v)[:200]}")

    # 提取 authCode
    auth_code = None
    for key_name in ['authCode', 'code', 'auth_code', 'token', 'authToken']:
        val = params.get(key_name)
        if val:
            auth_code = val[0] if isinstance(val, list) else val
            if auth_code:
                debug_log(f"[CALLBACK] 从 query['{key_name}'] 找到 authCode (len={len(auth_code)})")
                break
    if not auth_code:
        for key_name in ['authCode', 'code', 'auth_code', 'token', 'authToken']:
            val = form_data.get(key_name)
            if val:
                auth_code = str(val[0] if isinstance(val, list) else val)
                debug_log(f"[CALLBACK] 从 form['{key_name}'] 找到 authCode (len={len(auth_code)})")
                break

    # 提取 serverId（如果回调 URL 携带了）
    server_id = params.get('server', '') or params.get('serverId', '') or ''
    if isinstance(server_id, list):
        server_id = server_id[0] if server_id else ''

    entry = {
        'time':     datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'ip':       client_ip,
        'authCode': auth_code,
        'serverId': server_id,   # 区服 ID（可能为空）
        'params':   params,
        'url':      request.url,
        'form':     form_data,
    }

    _db_insert(entry)

    if auth_code:
        debug_log(f"[CALLBACK] ✅ 成功获取 authCode! serverId={server_id or '未指定'}")
    else:
        debug_log(f"[CALLBACK] ⚠ 未找到 authCode，所有参数已记录", "WARN")

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
        <h1>{"登录成功 ✓" if auth_code else "已收到回调"}</h1>
        <p>代挂工具正在处理中，请稍候...</p>
        <p style="color:#999;font-size:12px;">你可以关闭此页面</p>
    </div>
</body>
</html>"""


# ==================== API ====================

@app.route('/api/authcodes', methods=['GET'])
def list_authcodes():
    """获取 authCode 列表；支持 openid/since_id/brief/wait 以降低出站流量。"""
    openid = str(request.args.get("openid", "") or "").strip()
    since_id = int(request.args.get("since_id") or request.args.get("since") or 0)
    wait_seconds = min(max(int(request.args.get("wait") or 0), 0), 30)
    brief = str(request.args.get("brief", "0")).lower() in ("1", "true", "yes")
    latest_only = str(request.args.get("latest", "0")).lower() in ("1", "true", "yes")

    if wait_seconds:
        with authcode_changed:
            deadline = time.monotonic() + wait_seconds
            while True:
                items = _db_list(openid=openid, since_id=since_id)
                if items:
                    break
                remain = deadline - time.monotonic()
                if remain <= 0:
                    items = []
                    break
                authcode_changed.wait(timeout=min(remain, 5))
    else:
        items = _db_list(openid=openid, since_id=since_id)

    if latest_only and items:
        items = [items[-1]]

    out_items = [_compact_authcode(row) for row in items] if brief else items
    return jsonify({
        "ok": True,
        "count": len(out_items),
        "latest_id": _db_latest_id(),
        "authcodes": out_items
    })


@app.route('/api/authcodes/listen', methods=['GET'])
def listen_authcodes():
    """短长轮询新记录；无新记录时只返回很小的空 JSON。"""
    since_id = int(request.args.get("since_id") or request.args.get("since") or 0)
    wait_seconds = min(max(int(request.args.get("wait") or 3), 0), 10)

    with authcode_changed:
        latest_id = _db_latest_id()
        if since_id > latest_id:
            since_id = 0
        deadline = time.monotonic() + wait_seconds
        while True:
            items = _db_list(since_id=since_id)
            if items:
                break
            remain = deadline - time.monotonic()
            if remain <= 0:
                items = []
                break
            authcode_changed.wait(timeout=min(remain, 1))

    return jsonify({
        "ok": True,
        "count": len(items),
        "latest_id": _db_latest_id(),
        "authcodes": items
    })


@app.route('/api/authcodes/<int:idx>', methods=['DELETE'])
def consume_authcode(idx):
    """消费（删除）一个已处理的 authCode。支持按数据库 id 或列表下标删除。"""
    # 优先按 id 查找（更可靠），找不到则 fallback 到按列表下标
    removed = _db_delete_by_id(idx)
    if removed:
        return jsonify({"ok": True, "removed": removed.get('authCode', '')})
    # fallback: 按列表下标（兼容旧客户端）
    removed = _db_delete_by_index(idx)
    if removed:
        return jsonify({"ok": True, "removed": removed.get('authCode', '')})
    return jsonify({"ok": False, "msg": "index out of range"})


@app.route('/api/authcodes/refresh', methods=['POST'])
def refresh_authcode():
    """
    用 JWT token 刷新 game_token（无需重新扫码）。

    JWT token 来自 loginForPc 的返回，有效期较长（按月计），
    可在 game_token 过期后用来获取新的 authCode → 新 game_token。

    POST JSON:
      jwt_token: str  (必填)
      openid: str     (可选，用于 DB 去重)

    返回:
      game_token: str
      openid: str
      authCode: str
    """
    data = request.get_json(force=True, silent=True) or {}
    jwt_token = str(data.get('jwt_token', '') or '').strip()
    openid = str(data.get('openid', '') or '').strip()

    if not jwt_token:
        return jsonify({"ok": False, "msg": "jwt_token required"}), 400

    APP_ID = "2021004170660258"
    AUTH_INFO_URL = (
        "https://webgwmobiler.alipay.com/gamecenterhome/com.alipay.gamecenterhome"
        ".common.facade.service.GameCenterPcGameFacade/queryPcGameAuthInfo"
        "/uprodhatchstation66500008?ctoken=bigfish_ctoken_1a76c5jk1b"
    )

    headers = dict(API_HEADERS)
    headers['x-game-token-pcweb'] = jwt_token

    # 从 DB 查找存着的 spanner cookie，带上它访问支付宝（避免 SESSION_EXPIRED）
    spanner = ""
    if openid:
        try:
            db = _get_db()
            row = db.execute("SELECT spanner FROM authcodes WHERE openid = ? ORDER BY id DESC LIMIT 1", (openid,)).fetchone()
            if row and row['spanner']:
                spanner = row['spanner']
                headers['Cookie'] = spanner
        except Exception:
            pass

    debug_log(f"[REFRESH] 用 JWT token 刷新 game_token (openid={openid or '未指定'}, cookie={'有' if spanner else '无'})")

    try:
        auth_resp = requests.post(
            AUTH_INFO_URL,
            headers=headers,
            json={"appId": APP_ID},
            timeout=10
        )
        auth_data = auth_resp.json()
        debug_log(f"[REFRESH] queryPcGameAuthInfo success={auth_data.get('success')}")

        # 保存响应中的新 Cookie
        resp_set_cookie = auth_resp.headers.get('Set-Cookie', '')
        if resp_set_cookie and openid:
            spanner_parts = [c.strip() for c in resp_set_cookie.split(';') if c.strip().startswith('spanner=')]
            if spanner_parts:
                spanner = spanner_parts[0]
                try:
                    db = _get_db()
                    db.execute("UPDATE authcodes SET spanner = ? WHERE openid = ?", (spanner, openid))
                    db.commit()
                except Exception:
                    pass

        if auth_data.get('success') and auth_data.get('data'):
            auth_code = auth_data['data'].get('authCode', '')
            if auth_code:
                debug_log(f"[REFRESH] authCode 获取成功，兑换 game_token...")
                tok_data = exchange_authcode_to_token(auth_code)
                if tok_data:
                    resolved_openid = tok_data['openid'] or openid
                    entry = {
                        'time':       datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        'ip':         'refresh-endpoint',
                        'authCode':   auth_code,
                        'game_token': tok_data['game_token'],
                        'openid':     resolved_openid,
                        'serverId':   '',
                        'params':     {},
                        'url':        '',
                        'form':       {},
                        'jwt_token':  jwt_token,
                        'spanner':    spanner,
                    }
                    _db_insert(entry)
                    debug_log(f"[REFRESH] ✅ 刷新成功! openid={resolved_openid}")
                    return jsonify({
                        "ok": True,
                        "game_token": tok_data['game_token'],
                        "openid": resolved_openid,
                        "authCode": auth_code,
                        "spanner": spanner,
                    })
                else:
                    debug_log(f"[REFRESH] ptoken 兑换失败", "ERROR")
                    return jsonify({"ok": False, "msg": "ptoken exchange failed"}), 502
            else:
                debug_log(f"[REFRESH] authCode 为空: {auth_data}", "WARN")
                return jsonify({"ok": False, "msg": "no authCode in response"}), 502
        else:
            debug_log(f"[REFRESH] queryPcGameAuthInfo 失败: {auth_data}", "WARN")
            return jsonify({"ok": False, "msg": f"queryPcGameAuthInfo failed: {auth_data.get('msg','')}"}), 502
    except Exception as e:
        debug_log(f"[REFRESH] 异常: {e}", "ERROR")
        return jsonify({"ok": False, "msg": str(e)}), 500


@app.route('/api/authcodes/clear', methods=['POST'])
def clear_authcodes():
    """清空所有 authCode"""
    count = _db_clear()
    return jsonify({"ok": True, "msg": f"Cleared {count} authcodes"})


@app.route('/api/accounts', methods=['GET'])
def list_accounts():
    """兼容旧接口"""
    items = _db_list()
    compat = [{'time': a['time'], 'ip': a['ip'], 'params': a.get('params', {})} for a in items]
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
        "pending_authcodes": _db_count(),
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

    _db_insert(entry)

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

# 应用启动时初始化（兼容 gunicorn 和直接运行）
_deduped = _init_db()
debug_log("[INIT] SQLite 数据库初始化完成")
if _deduped > 0:
    debug_log(f"[INIT] 已清理同 openid 历史重复记录 {_deduped} 条")
_cleanup_thread = threading.Thread(target=_daily_cleanup_thread, daemon=True)
_cleanup_thread.start()
debug_log("[INIT] 自动清理线程已启动（每日清理30天前记录）")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8888))
    print(f"[+] Starting server on port {port}...")
    app.run(host='0.0.0.0', port=port, debug=False)
