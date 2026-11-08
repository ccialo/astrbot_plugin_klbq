# 长图渲染：手写 HTML → Edge/Chrome/Chromium 无头截图（移植自 dsh-klbq/lib/render.js）。
#
# 为什么不用 canvas/puppeteer/playwright：
#   AstrBot 插件引入重型截图依赖容易和环境打架，而 Edge 是 Windows 预装的，
#   `--headless=new --screenshot` 已经够用，零第三方依赖。
#   Linux 上可用环境变量 KLBQ_BROWSER 指定 chrome/chromium 路径。
#
# 两趟渲染：先 --dump-dom 量出内容高度，再按该高度截图，
# 否则固定 window-size 会在长图底部留一大块空白。

import asyncio
import datetime
import html as _html
import os
import re
import tempfile
import time
from pathlib import Path

from .dakanu import ranked_tier_of

BROWSERS = [
    # Windows
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    # Linux（服务器迁移场景；也可用 KLBQ_BROWSER 指定）
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/microsoft-edge",
]


def find_browser(override=None):
    if override and os.path.exists(override):
        return override
    from_env = os.environ.get("KLBQ_BROWSER")
    if from_env and os.path.exists(from_env):
        return from_env
    for p in BROWSERS:
        if os.path.exists(p):
            return p
    return None


def _esc(v):
    return _html.escape("" if v is None else str(v), quote=True)


def _pct(v):
    try:
        return f"{float(v) * 100:.1f}%"
    except (TypeError, ValueError):
        return "0.0%"


def _num(v, digits=0):
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "-"
    return f"{n:.{digits}f}" if digits else str(round(n))


def _fmt_duration(s):
    sec = int(s or 0)
    return f"{sec // 60}:{sec % 60:02d}"


def _fmt_time(unix):
    try:
        t = float(unix)
    except (TypeError, ValueError):
        return "-"
    if t <= 0:
        return "-"
    return datetime.datetime.fromtimestamp(t).strftime("%m-%d %H:%M")


_CSS = """
* { box-sizing: border-box; }
body { margin: 0; background: #0d1220; color: #e8eefc;
       font-family: "Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif; }
.wrap { padding: 28px 32px 34px; }
.head { display: flex; align-items: flex-start; justify-content: space-between; }
.nick { font-size: 40px; font-weight: 700; letter-spacing: .5px; line-height: 1.1; }
.sub { margin-top: 10px; font-size: 16px; color: #8fa0bf; }
.dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 6px; }
.badge { padding: 12px 18px; border-radius: 12px; text-align: right; min-width: 240px; }
.badge-row { display: flex; align-items: baseline; justify-content: space-between; gap: 14px; }
.badge-row + .badge-row { margin-top: 6px; }
.badge-label { font-size: 13px; color: #8fa0bf; letter-spacing: .5px; }
.badge-top { font-size: 20px; font-weight: 600; }
.badge-cur { font-size: 28px; font-weight: 700; }
.badge .sc { margin-top: 8px; font-size: 13px; color: #8fa0bf; text-align: right; }
.card { margin-top: 22px; background: #141c2f; border: 1px solid #1f2a44; border-radius: 16px; padding: 18px 20px; }
.card h3 { margin: 0 0 14px; font-size: 17px; font-weight: 600; color: #9fb0cc; letter-spacing: 1px; }
.grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }
.cell { background: #101728; border-radius: 12px; padding: 14px 16px; }
.cell-label { font-size: 13px; color: #7f8fad; }
.cell-value { margin-top: 6px; font-size: 28px; font-weight: 700; color: #ffffff; }
.cell-sub { margin-top: 2px; font-size: 12px; color: #6f80a0; }
.mode-row, .role-row { display: grid; grid-template-columns: 96px 1fr 72px 96px 108px 64px;
                        align-items: center; gap: 10px; padding: 9px 0; border-top: 1px solid #1b2437; font-size: 14px; }
.mode-row:first-child, .role-row:first-child { border-top: none; }
.mode-name, .role-name { color: #dfe8ff; font-weight: 600; }
.mode-bar, .role-bar { height: 8px; background: #1d2740; border-radius: 6px; overflow: hidden; }
.mode-bar i, .role-bar i { display: block; height: 100%; border-radius: 6px;
    background: linear-gradient(90deg, #3a8dff, #43e0c8); }
.mode-win, .role-win { color: #43e0c8; font-weight: 600; text-align: right; }
.mode-kda, .mode-dmg, .mode-rounds, .role-kd, .role-dmg, .role-count { color: #93a4c4; text-align: right; }
.match-row { display: grid; grid-template-columns: 44px 92px 76px 92px 62px 54px 52px 1fr;
              align-items: center; gap: 8px; padding: 8px 0; border-top: 1px solid #1b2437; font-size: 14px; }
.match-row:first-child { border-top: none; }
.m-result { width: 32px; text-align: center; border-radius: 6px; font-weight: 700; font-size: 13px; padding: 3px 0; }
.match-row.win .m-result { background: #1d4f3a; color: #43e0c8; }
.match-row.lose .m-result { background: #4a2130; color: #ff7a94; }
.m-mode { color: #cfdaf2; }
.m-role { color: #93a4c4; }
.m-kda { color: #dfe8ff; font-weight: 600; }
.m-score, .m-dur { color: #8b9cbb; }
.m-delta { color: #7fd6a8; text-align: right; }
.match-row.lose .m-delta { color: #ff92a6; }
.m-time { color: #6f80a0; text-align: right; }
.foot { margin-top: 20px; font-size: 12px; color: #5d6a83; display: flex; justify-content: space-between; }
"""


def build_html(profile, width=900, max_matches=10):
    """生成整张长图的 HTML。"""
    p = profile or {}
    player = p.get("player") or {}
    presence = p.get("presence") or {}
    summary = p.get("summary") or {}
    modes = p.get("modes") if isinstance(p.get("modes"), list) else []
    roles = sorted(p.get("roles") or [], key=lambda r: r.get("count") or 0, reverse=True)
    matches = (p.get("matches") or [])[:max_matches]
    role_name = {str(r.get("roleId")): r.get("name") for r in roles}

    # 排位：当前 / 历史最高
    rank = p.get("rank") or {}
    cur_tier = ranked_tier_of(rank.get("segment"))
    top_tier = ranked_tier_of(rank.get("historicalSegment"))
    cur_name = f"{cur_tier['name']} {cur_tier['division']}".strip() if cur_tier else "未定级"
    top_name = f"{top_tier['name']} {top_tier['division']}".strip() if top_tier else "未定级"
    cur_color = (cur_tier or {}).get("color", "#9aa7b5")
    top_color = (top_tier or {}).get("color", "#cdd8ee")
    online = presence.get("state") == "online" or presence.get("statusText") == "online"

    def stat_cell(label, value, sub=""):
        sub_html = f'<div class="cell-sub">{_esc(sub)}</div>' if sub else ""
        return (
            f'<div class="cell"><div class="cell-label">{_esc(label)}</div>'
            f'<div class="cell-value">{_esc(value)}</div>{sub_html}</div>'
        )

    mode_rows = "".join(
        f'<div class="mode-row">'
        f'<div class="mode-name">{_esc(m.get("label") or m.get("mode") or "-")}</div>'
        f'<div class="mode-bar"><i style="width:{min(100, float(m.get("winRate") or 0) * 100):.1f}%"></i></div>'
        f'<div class="mode-win">{_pct(m.get("winRate"))}</div>'
        f'<div class="mode-kda">KDA {_num(m.get("kda"), 2)}</div>'
        f'<div class="mode-dmg">均伤 {_num(m.get("avgDamage"))}</div>'
        f'<div class="mode-rounds">{_num(m.get("rounds"))} 场</div></div>'
        for m in modes
    )

    role_rows = "".join(
        f'<div class="role-row">'
        f'<div class="role-name">{_esc(r.get("name") or r.get("roleId"))}</div>'
        f'<div class="role-bar"><i style="width:{min(100, float(r.get("winRate") or 0) * 100):.1f}%"></i></div>'
        f'<div class="role-win">{_pct(r.get("winRate"))}</div>'
        f'<div class="role-kd">KD {_num(r.get("kd"), 2)}</div>'
        f'<div class="role-dmg">均伤 {_num(r.get("avgDamage"))}</div>'
        f'<div class="role-count">{_num(r.get("count"))} 场</div></div>'
        for r in roles[:8]
    )

    match_parts = []
    for m in matches:
        win = m.get("result") == "win"
        try:
            delta = float(m.get("divisionScoreDelta"))
        except (TypeError, ValueError):
            delta = 0.0
        delta_txt = f"+{round(delta)}" if delta > 0 else (str(round(delta)) if delta < 0 else "")
        match_parts.append(
            f'<div class="match-row {"win" if win else "lose"}">'
            f'<div class="m-result">{"胜" if win else "负"}</div>'
            f'<div class="m-mode">{_esc(m.get("modeLabel") or m.get("mode") or "-")}</div>'
            f'<div class="m-role">{_esc(role_name.get(str(m.get("roleId"))) or "-")}</div>'
            f'<div class="m-kda">{_num(m.get("kills"))}/{_num(m.get("deaths"))}/{_num(m.get("assists"))}</div>'
            f'<div class="m-score">{_num(m.get("myScore"))}:{_num(m.get("opponentScore"))}</div>'
            f'<div class="m-dur">{_fmt_duration(m.get("durationSeconds"))}</div>'
            f'<div class="m-delta">{_esc(delta_txt)}</div>'
            f'<div class="m-time">{_fmt_time(m.get("playedAt"))}</div></div>'
        )
    match_rows = "".join(match_parts)

    dot_color = "#3ddc84" if online else "#6b7280"
    region = f' · {_esc(presence["regionLabel"])}' if presence.get("regionLabel") else ""
    division_score = (
        f'<div class="sc">段位分 {_num(rank["divisionScore"])}</div>'
        if rank.get("divisionScore")
        else ""
    )
    modes_card = f'<div class="card"><h3>模式表现</h3>{mode_rows}</div>' if mode_rows else ""
    roles_card = f'<div class="card"><h3>角色表现（按场次）</h3>{role_rows}</div>' if role_rows else ""
    matches_card = f'<div class="card"><h3>最近对局</h3>{match_rows}</div>' if match_rows else ""
    queried = player.get("queriedAt") or time.time() * 1000
    foot_time = _fmt_time(float(queried) / 1000)

    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<style>{_CSS}
body {{ width: {width}px; }}
.badge {{ border: 1px solid {cur_color}55; background: {cur_color}14; }}
</style></head>
<body><div class="wrap">
  <div class="head">
    <div>
      <div class="nick">{_esc(player.get("nickname") or "-")}</div>
      <div class="sub">
        <span class="dot" style="background:{dot_color}"></span>{"在线" if online else "离线"}{region}
        · Lv.{_num(player.get("level"))}
      </div>
    </div>
    <div class="badge">
      <div class="badge-row">
        <span class="badge-label">历史最高</span>
        <span class="badge-top" style="color:{top_color}">{_esc(top_name)}</span>
      </div>
      <div class="badge-row">
        <span class="badge-label">当前段位</span>
        <span class="badge-cur" style="color:{cur_color}">{_esc(cur_name)}</span>
      </div>
      {division_score}
    </div>
  </div>

  <div class="card">
    <h3>总览</h3>
    <div class="grid">
      {stat_cell("场次", _num(summary.get("rounds")), f'{_num(summary.get("wins"))} 胜 {_num(summary.get("losses"))} 负')}
      {stat_cell("胜率", _pct(summary.get("winRate")))}
      {stat_cell("KDA", _num(summary.get("kda"), 2), f'KD {_num(summary.get("kd"), 2)}')}
      {stat_cell("场均伤害", _num(summary.get("avgDamage")), f'总伤害 {_num(summary.get("damage"))}')}
      {stat_cell("场均击杀", _num(summary.get("avgKill"), 1), f'助攻 {_num(summary.get("avgAssist"), 1)}')}
      {stat_cell("MVP 率", _pct(summary.get("mvpRate")), f'爆头率 {_pct(summary.get("headshotRate"))}')}
    </div>
  </div>

  {modes_card}
  {roles_card}
  {matches_card}

  <div class="foot">
    <span>数据来源 dakanu.com · 仅供娱乐参考</span>
    <span>{foot_time}</span>
  </div>
  <div id="__h" style="display:none"></div>
  <script>document.getElementById('__h').textContent = String(Math.ceil(document.body.getBoundingClientRect().height));</script>
</div></body></html>"""


async def _run_browser(bin_path, args, timeout=60):
    proc = await asyncio.create_subprocess_exec(
        bin_path,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("浏览器渲染超时") from None
    return stdout.decode("utf-8", "replace"), stderr.decode("utf-8", "replace")


async def render_png(html_text, width=900, timeout=90, browser_path=None):
    """HTML → PNG（两趟：先量高度，再按高度截图）。返回 PNG 字节。"""
    bin_path = find_browser(browser_path)
    if not bin_path:
        raise RuntimeError(
            "没找到 Edge/Chrome/Chromium，无法渲染图片（可用环境变量 KLBQ_BROWSER 指定路径）"
        )

    tmpdir = Path(tempfile.mkdtemp(prefix="astrbot-klbq-"))
    html_file = tmpdir / "card.html"
    png_file = tmpdir / "card.png"
    profile_dir = tmpdir / "profile"
    html_file.write_text(html_text, encoding="utf-8")
    url = html_file.resolve().as_uri()
    base_args = [
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--no-first-run",
        "--disable-extensions",
        "--hide-scrollbars",
        "--force-device-scale-factor=2",
        f"--user-data-dir={profile_dir}",
    ]

    height = 1600
    try:
        stdout, _ = await _run_browser(bin_path, [*base_args, "--dump-dom", url], timeout)
        m = re.search(r'id="__h"[^>]*>\s*(\d+)\s*<', stdout)
        if m:
            height = max(400, min(20000, int(m.group(1))))
    except Exception:  # noqa: BLE001 —— 量高度失败就退回默认值
        pass

    await _run_browser(
        bin_path,
        [*base_args, f"--screenshot={png_file}", f"--window-size={width},{height}", url],
        timeout,
    )
    for _ in range(20):
        if png_file.exists() and png_file.stat().st_size > 1000:
            break
        await asyncio.sleep(0.15)

    if not png_file.exists():
        raise RuntimeError("截图结果为空")
    buf = png_file.read_bytes()
    try:
        for f in tmpdir.iterdir():
            if f.is_file():
                f.unlink()
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)
    except OSError:
        pass  # 清理失败无妨
    if not buf:
        raise RuntimeError("截图结果为空")
    return buf
