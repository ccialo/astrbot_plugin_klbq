# astrbot_plugin_klbq — 卡拉彼丘（Strinova）战绩查询插件。
#
# 移植自 DSH 插件 dsh-klbq（https://github.com/CSAHuan/dsh-klbq，MIT）。
# 按游戏昵称从 dakanu.com 取数，渲染成一张长图（排位段位/总览/模式/角色/最近对局）。
#
# 设计取舍（沿用原作）：
#   - 图片写到系统临时目录（klbq-cards/），消息链直接引用本地文件发送；
#   - 不依赖 canvas/puppeteer/playwright：用系统自带 Edge/Chrome 无头截图（见 render.py）。

import re
import tempfile
import time
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Plain
from astrbot.api.star import Context, Star, register

from .dakanu import fetch_summoner, ranked_label, search_players
from .render import build_html, render_png

DEFAULT_MAX_MATCHES = 10
CARD_DIR = Path(tempfile.gettempdir()) / "klbq-cards"


def _pct(v):
    try:
        return f"{float(v) * 100:.1f}%"
    except (TypeError, ValueError):
        return "-"


def _num(v, digits=0):
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "-"
    return f"{n:.{digits}f}" if digits else str(round(n))


def summary_text(result):
    s = result.get("summary") or {}
    lines = [
        f"{result['nickname']}（Lv.{result.get('level') or '-'}）",
        f"当前段位：{result['current_rank']}　历史最高：{result['top_rank']}",
        f"场次 {_num(s.get('rounds'))}　胜率 {_pct(s.get('winRate'))}"
        f"　KDA {_num(s.get('kda'), 2)}　均伤 {_num(s.get('avgDamage'))}",
    ]
    recent = result.get("recent") or []
    if recent:
        lines.append(
            f"最近 {len(recent)} 场："
            + " / ".join(f"{'胜' if m['result'] == 'win' else '负'} {m['kda']}" for m in recent)
        )
    return "\n".join(lines)


async def query_player(nickname, max_matches=DEFAULT_MAX_MATCHES, width=900):
    """昵称 → 长图。返回结果字典（含 image_path 与数据摘要）。"""
    q = str(nickname or "").strip()
    if not q:
        raise ValueError("nickname 不能为空")

    # 纯数字按 UID 直接查（昵称也可能是纯数字，失败再退回搜索）
    if q.isdigit():
        try:
            profile = await fetch_summoner(q)
            pick = {"playerId": q, "nickname": profile.get("player", {}).get("nickname") or q}
        except Exception:
            pick = None
            profile = None
        if pick:
            return await _build_result(pick, profile, max_matches, width, fuzzy=False)

    candidates = await search_players(q)
    lower = q.lower()

    # 只接受「完全同名」（含接口 exact 标记）；前缀匹配会把「汐汐不汐汐丿」
    # 误认为「汐汐不汐汐」，多个相似昵称时绝不静默选一个，列出让用户挑。
    exact = next(
        (c for c in candidates if c.get("exact") or c["nickname"].lower() == lower),
        None,
    )
    if exact:
        pick, fuzzy = exact, False
    elif len(candidates) == 1:
        pick, fuzzy = candidates[0], True
    elif candidates:
        return {"need_choice": True, "query": q, "candidates": candidates}
    else:
        raise RuntimeError(f"没找到玩家「{q}」，检查游戏昵称是否正确（或改用 UID 查询）")

    profile = await fetch_summoner(pick["playerId"], pick["nickname"])
    return await _build_result(pick, profile, max_matches, width, fuzzy)


async def _build_result(pick, profile, max_matches, width, fuzzy):
    max_matches = max(0, min(20, int(max_matches)))

    html_text = build_html(profile, width=width, max_matches=max_matches)
    buffer = await render_png(html_text, width=width)

    CARD_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r'[\\/:*?"<>|\s]+', "_", str(profile.get("player", {}).get("nickname") or pick["nickname"]))[:40]
    file = CARD_DIR / f"{safe_name}-{int(time.time() * 1000)}.png"
    file.write_bytes(buffer)

    s = profile.get("summary") or {}
    rank = profile.get("rank") or {}
    matches = (profile.get("matches") or [])[:max_matches]
    return {
        "nickname": profile.get("player", {}).get("nickname") or pick["nickname"],
        "player_id": pick["playerId"],
        "level": profile.get("player", {}).get("level"),
        "current_rank": ranked_label(rank.get("segment")),
        "top_rank": ranked_label(rank.get("historicalSegment")),
        "summary": s,
        "recent": [
            {
                "result": m.get("result"),
                "mode": m.get("modeLabel") or m.get("mode"),
                "kda": f"{_num(m.get('kills'))}/{_num(m.get('deaths'))}/{_num(m.get('assists'))}",
                "score": f"{_num(m.get('myScore'))}:{_num(m.get('opponentScore'))}",
            }
            for m in matches
        ],
        "image_path": str(file),
        "image_bytes": len(buffer),
        "fuzzy": fuzzy,
    }


@register(
    "astrbot_plugin_klbq",
    "CSAHuan（原作）· 移植",
    "卡拉彼丘（Strinova）战绩查询：按昵称从 dakanu.com 取数并渲染长图",
    "0.1.1",
    "https://github.com/CSAHuan/dsh-klbq",
)
class KlbqPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    @filter.command("klbq", alias={"卡拉彼丘战绩"})
    async def klbq(self, event: AstrMessageEvent, nickname: str = ""):
        """查询卡拉彼丘战绩。用法：/klbq <游戏昵称>"""
        nickname = (nickname or "").strip()
        if not nickname:
            # 兼容参数注入失效的情况：从原始消息文本里取指令之后的部分
            parts = event.message_str.split(maxsplit=1)
            if len(parts) > 1:
                nickname = parts[1].strip()
        if not nickname:
            yield event.plain_result("用法：/klbq <游戏昵称>\n例如：/klbq ahiahdiawh")
            return

        yield event.plain_result(f"正在查询「{nickname}」的战绩，渲染长图需要几秒…")
        try:
            result = await query_player(nickname)
        except Exception as e:  # noqa: BLE001 —— 统一回复错误，不让异常冒泡
            logger.warning(f"klbq 查询失败：{e}")
            yield event.plain_result(f"查询失败：{e}")
            return

        if result.get("need_choice"):
            # 多个相似昵称：列出让用户选，绝不静默挑一个（可能不是同一个人）
            lines = [f"没有昵称为「{result['query']}」的玩家，找到 {len(result['candidates'])} 个相似昵称："]
            for i, c in enumerate(result["candidates"], 1):
                lv = f"Lv.{c['level']}" if c.get("level") else "等级未知"
                lines.append(f"{i}. {c['nickname']}（{lv}，UID {c['playerId']}）")
            lines.append("请确认完整昵称，或直接用 UID 再查：/klbq <UID>")
            yield event.plain_result("\n".join(lines))
            return

        text = summary_text(result)
        if result.get("fuzzy"):
            text = f"未找到完全同名玩家，为你匹配到「{result['nickname']}」：\n" + text
        yield event.chain_result([Plain(text), Image.fromFileSystem(result["image_path"])])
