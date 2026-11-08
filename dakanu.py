# dakanu.com 数据访问层（移植自 dsh-klbq/lib/dakanu.js）。
#
# 两个必须保留的细节（都是原作实测结论，改动前先读）：
#
# 1) **SNI 阻断**：本机直连 https://dakanu.com 会在 TLS 握手阶段被 RST
#    （ClientHello 里带这个域名就被重置；不带 SNI 直连 IP 能正常拿到响应）。
#    因此统一「连 IP + 不发 SNI + Host 头指明站点」，并关闭证书校验 ——
#    证书是签给域名的，省略 SNI 后必然对不上。
#    这条通道只取公开战绩、不传任何凭据；不要用它传输密钥或隐私数据。
#    注意：wrap_socket 的 server_hostname 必须是 None，写成域名必被重置。
#
# 2) **两套段位体系**：排位是「至纯物质/升格分子/虚空原子/聚变质子/自由中子/
#    束缚电子/禁闭夸克/无限超弦/奇点」（按 segment 号段），挑战赛是
#    「青铜…传奇」（按 gradeScore 算）。卡片右上角用排位，别混用。

import asyncio
import http.client
import json
import socket
import ssl
import time
import urllib.parse

SITE_HOST = "www.dakanu.com"
# 2026-11 实测的 A 记录；解析失败时兜底
FALLBACK_IPS = ["43.169.14.133", "43.169.13.133"]
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_ip_cache = {"at": 0.0, "ips": []}
_IP_CACHE_TTL = 10 * 60  # 10 分钟


def _resolve_ips():
    """解析站点 IP：系统 DNS 没被拦（只有 SNI 被拦），失败退回内置列表。"""
    now = time.time()
    if _ip_cache["ips"] and now - _ip_cache["at"] < _IP_CACHE_TTL:
        return _ip_cache["ips"]
    try:
        infos = socket.getaddrinfo(SITE_HOST, 443, socket.AF_INET, socket.SOCK_STREAM)
        ips = sorted({info[4][0] for info in infos})
        if ips:
            _ip_cache.update(at=now, ips=ips)
            return ips
    except OSError:
        pass
    return list(FALLBACK_IPS)


def _https_get_once(ip, pathname, timeout=20):
    """直连 IP 发 GET，TLS 不带 SNI，Host 头指明站点。返回 (status, body_text)。"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # 证书签给域名；这条通道只取公开数据

    raw = socket.create_connection((ip, 443), timeout=timeout)
    try:
        tls = ctx.wrap_socket(raw, server_hostname=None)  # 关键：不发 SNI
        conn = http.client.HTTPConnection(ip, 443, timeout=timeout)
        conn.sock = tls  # 预置已建好的 TLS 连接，跳过 connect()（避免它重发 SNI）
        conn.request(
            "GET",
            pathname,
            headers={
                "Host": SITE_HOST,
                "User-Agent": UA,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Accept-Encoding": "identity",
            },
        )
        resp = conn.getresponse()
        body = resp.read()
        status = resp.status
        conn.close()
        return status, body.decode("utf-8", "replace")
    finally:
        try:
            raw.close()
        except OSError:
            pass


async def dakanu_get(pathname, timeout=20):
    """依次尝试各 IP，返回第一个非 5xx 响应。"""
    ips = await asyncio.to_thread(_resolve_ips)
    last_error = None
    for ip in ips:
        try:
            status, text = await asyncio.to_thread(_https_get_once, ip, pathname, timeout)
            if status >= 500:
                last_error = RuntimeError(f"HTTP {status}")
                continue
            return status, text
        except Exception as e:  # noqa: BLE001 —— 逐个 IP 尝试，最后统一抛
            last_error = e
    raise last_error or RuntimeError("dakanu 请求失败")


async def _dakanu_json(pathname):
    status, text = await dakanu_get(pathname)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise RuntimeError(f"返回非 JSON（HTTP {status}）") from None


async def search_players(query):
    """按昵称搜索玩家，返回候选列表 [{playerId, nickname, exact, level}]。"""
    q = (query or "").strip()
    if not q:
        raise ValueError("昵称不能为空")
    data = await _dakanu_json(f"/api/search/players?q={urllib.parse.quote(q)}")
    suggestions = data.get("suggestions") if isinstance(data, dict) else None
    result = []
    for s in suggestions or []:
        player_id = str(s.get("playerId") or "")
        nickname = str(s.get("nickname") or "")
        if player_id and nickname:
            result.append(
                {
                    "playerId": player_id,
                    "nickname": nickname,
                    "exact": s.get("exact") is True,
                    "level": s.get("level"),
                }
            )
    return result


async def fetch_summoner(player_id, nickname=None):
    """拉取玩家详情（段位/总览/模式/角色/最近对局）。"""
    qs = f"?nickname={urllib.parse.quote(nickname)}" if nickname else ""
    data = await _dakanu_json(f"/api/summoner/{urllib.parse.quote(str(player_id))}{qs}")
    if not data.get("ok") or not data.get("profile"):
        raise RuntimeError(data.get("error") or "查询失败：站点未返回战绩数据")
    return data["profile"]


# 排位段位表（站点前端 es[]）：按 segment 号段映射，段内分 IV~I
RANKED_TIERS = [
    {"id": "substance", "name": "至纯物质", "from": 1, "to": 3, "divisions": ["III", "II", "I"], "color": "#6c7178"},
    {"id": "molecule", "name": "升格分子", "from": 4, "to": 6, "divisions": ["III", "II", "I"], "color": "#c07a52"},
    {"id": "atom", "name": "虚空原子", "from": 7, "to": 10, "divisions": ["IV", "III", "II", "I"], "color": "#9aa7b5"},
    {"id": "proton", "name": "聚变质子", "from": 11, "to": 14, "divisions": ["IV", "III", "II", "I"], "color": "#d0a63c"},
    {"id": "neutron", "name": "自由中子", "from": 15, "to": 18, "divisions": ["IV", "III", "II", "I"], "color": "#28b3a4"},
    {"id": "electron", "name": "束缚电子", "from": 19, "to": 23, "divisions": ["V", "IV", "III", "II", "I"], "color": "#3f8fd6"},
    {"id": "quark", "name": "禁闭夸克", "from": 24, "to": 28, "divisions": ["V", "IV", "III", "II", "I"], "color": "#9b5cd6"},
    {"id": "superstring", "name": "无限超弦", "from": 29, "to": 29, "divisions": [], "color": "#d94a44"},
    {"id": "singularity", "name": "奇点", "from": 30, "to": 30, "divisions": [], "color": "#d84a4a"},
]


def ranked_tier_of(segment):
    try:
        seg = int(segment)
    except (TypeError, ValueError):
        return None
    if seg < 1:
        return None
    for tier in RANKED_TIERS:
        if tier["from"] <= seg <= tier["to"]:
            idx = seg - tier["from"]
            division = tier["divisions"][idx] if idx < len(tier["divisions"]) else ""
            return {
                "name": tier["name"],
                "division": division,
                "color": tier["color"],
                "segment": seg,
            }
    return None


def ranked_label(segment):
    tier = ranked_tier_of(segment)
    if not tier:
        return "未定级"
    return f"{tier['name']} {tier['division']}".strip()
