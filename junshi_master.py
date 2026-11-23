import os
import re
import json
import asyncio
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone, timedelta
import requests
# Safe default: no automatic posts are sent to any group.
ENABLE_GROUP_POSTS = os.environ.get("ENABLE_GROUP_POSTS", "false").lower() == "true"
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ==========================================
# 1. 核心配置（已锁定你的总后台主机器人与管理号）
# ==========================================
MASTER_BOT_TOKEN = os.environ.get("MASTER_BOT_TOKEN", "").strip()
SUPER_ADMIN_USERNAME = "am8818888"  # 已锁定你的总管理号 @AM8818888

# 加拿大28双源开奖接口
PRIMARY_API_URL = "https://www.lspcpc28.com/api/pc28/list"
BACKUP_SITE_URL = "https://kkaakj.com/"
CONFIG_FILE = "junshi_saas_data.json"

# 北京时间 (UTC+8)，用于每日报表统计
BJ_TZ = timezone(timedelta(hours=8))

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)


def today_str() -> str:
    return datetime.now(BJ_TZ).strftime("%Y-%m-%d")


class SaaSManager:
    """多租户总控数据中心（持久化保存总管理、子机器人、子管理及每日报表）"""

    def __init__(self, filepath=CONFIG_FILE):
        self.filepath = filepath
        self.data = self._load()
        self.running_apps: dict[str, object] = {}
        self.running_tasks: dict[str, asyncio.Task] = {}

    def _load(self) -> dict:
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "super_admin_id": None,
            "super_admin_name": f"@{SUPER_ADMIN_USERNAME}",
            "sub_bots": {},
        }

    def save(self):
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def is_super_admin(self, user) -> bool:
        """严格校验总管理员身份（锁定 @AM8818888）"""
        if not user:
            return False
        uname = (user.username or "").lower()
        if uname == SUPER_ADMIN_USERNAME.lower():
            if self.data["super_admin_id"] != user.id:
                self.data["super_admin_id"] = user.id
                self.data["super_admin_name"] = f"{user.full_name} (@{user.username})"
                self.save()
            return True
        return self.data["super_admin_id"] is not None and user.id == self.data["super_admin_id"]

    def ensure_daily_stats(self, token: str):
        bot_cfg = self.data["sub_bots"].get(token)
        if not bot_cfg:
            return
        d = today_str()
        if "daily_stats" not in bot_cfg:
            bot_cfg["daily_stats"] = {}
        if d not in bot_cfg["daily_stats"]:
            bot_cfg["daily_stats"][d] = {"bet_count": 0, "win_count": 0, "turnover": 0, "profit": 0}


manager = SaaSManager()
BET_STAGES = [100, 200, 400, 800, 1600, 3200]


def fetch_canada28() -> list:
    """拉取加拿大28最新开奖记录（主备双源自动切换）"""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        resp = requests.get(PRIMARY_API_URL, headers=headers, timeout=6)
        if resp.status_code == 200:
            raw = resp.json()
            records = raw.get("data") or raw.get("list") or (raw if isinstance(raw, list) else [])
            if isinstance(records, dict):
                records = records.get("list", [])
            parsed = []
            for item in records:
                issue = str(item.get("expect") or item.get("issue") or item.get("qihao") or "")
                sum_val = item.get("sum") or item.get("hezhi") or item.get("result")
                code = str(item.get("opencode") or item.get("openCode") or item.get("code") or "")
                if sum_val is not None and str(sum_val).isdigit():
                    total = int(sum_val)
                elif code:
                    nums = [int(x) for x in code.replace("+", ",").split(",") if x.strip().isdigit()]
                    total = nums[-1] if len(nums) == 4 else sum(nums[:3])
                else:
                    continue
                is_big = total >= 14
                parsed.append((issue, "大" if is_big else "小", is_big, total))
            if len(parsed) >= 2 and parsed[0][0] > parsed[-1][0]:
                parsed.reverse()
            if parsed:
                return parsed
    except Exception:
        pass

    try:
        resp = requests.get(BACKUP_SITE_URL, headers=headers, timeout=8)
        resp.encoding = "utf-8"
        matches = re.findall(r"(\d{7})[^\d]{1,30}(\d)\s*\+\s*(\d)\s*\+\s*(\d)", resp.text)
        parsed = []
        for issue, n1, n2, n3 in matches:
            total = int(n1) + int(n2) + int(n3)
            is_big = total >= 14
            parsed.append((issue, "大" if is_big else "小", is_big, total))
        if len(parsed) >= 2 and parsed[0][0] > parsed[-1][0]:
            parsed.reverse()
        return parsed
    except Exception:
        return []


# ==========================================
# 一、总后台逻辑（仅限 @AM8818888 访问）
# ==========================================
def build_super_admin_panel() -> tuple[str, InlineKeyboardMarkup]:
    total_bots = len(manager.data["sub_bots"])
    active_bots = sum(1 for b in manager.data["sub_bots"].values() if b.get("enabled"))
    d = today_str()

    today_turnover = 0
    today_profit = 0
    for token, cfg in manager.data["sub_bots"].items():
        manager.ensure_daily_stats(token)
        st = cfg["daily_stats"][d]
        today_turnover += st["turnover"]
        today_profit += st["profit"]

    text = (
        f"👑 **【总后台控制中心】**\n"
        f"• 总管理员：{manager.data['super_admin_name']}\n"
        f"• 已绑定子机器人：**{total_bots}** 个（运行中: **{active_bots}** 个）\n"
        f"• 今日全盘总流水 (`{d}`)：`{today_turnover}`\n"
        f"• 今日全盘总盈亏：`{today_profit:+d}`\n\n"
        f"📌 **快捷绑定指令（直接发送即可）**：\n"
        f"`绑定 机器人TOKEN @对方管理用户名`\n"
        f"*(例：`绑定 7123456:AAHxxx @zhangsan`)*"
    )
    keyboard = [
        [
            InlineKeyboardButton("📊 查看今日各机器人报表", callback_data="sa_report"),
            InlineKeyboardButton("🤖 管理/解绑子机器人", callback_data="sa_list"),
        ],
        [InlineKeyboardButton("🔄 刷新总后台数据", callback_data="sa_home")],
    ]
    return text, InlineKeyboardMarkup(keyboard)


async def master_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not manager.is_super_admin(user):
        await update.message.reply_text("⛔ 这是总控后台机器人，仅限总管理员访问。")
        return
    text, markup = build_super_admin_panel()
    await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")


async def master_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not manager.is_super_admin(user):
        return
    text = update.message.text.strip()
    m = re.match(r"^绑定\s+(\d+:[A-Za-z0-9_-]+)\s+(@?\S+)$", text)
    if m:
        new_token, owner_username = m.groups()
        owner_clean = owner_username.lstrip("@").strip()
        if new_token == MASTER_BOT_TOKEN:
            await update.message.reply_text("⚠️ 不能把总后台 Token 当作子机器人绑定。")
            return
        if new_token in manager.data["sub_bots"]:
            manager.data["sub_bots"][new_token]["owner_username"] = owner_clean
            manager.save()
            await update.message.reply_text(
                f"✅ 该机器人已更新绑定管理号为 `@{owner_clean}`！", parse_mode="Markdown"
            )
            return

        manager.data["sub_bots"][new_token] = {
            "bot_username": "启动中...",
            "owner_username": owner_clean,
            "owner_id": None,
            "enabled": True,
            "auto_bet": False,
            "target_chat_id": None,
            "target_chat_title": "未绑定群聊",
            "stage_index": 0,
            "daily_stats": {},
        }
        manager.save()

        ok, info = await start_sub_bot(new_token)
        if ok:
            await update.message.reply_text(
                f"🎉 **新机器人绑定并启动成功！**\n"
                f"• 子机器人：`@{info}`\n"
                f"• 归属管理号：`@{owner_clean}`\n"
                f"• 使用方式：让 `@{owner_clean}` 打开 `@{info}` 发送 `/start` 即可进入他自己的后台面板。",
                parse_mode="Markdown",
            )
        else:
            del manager.data["sub_bots"][new_token]
            manager.save()
            await update.message.reply_text(f"❌ 启动失败，请检查 Token 是否正确：{info}")


async def master_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not manager.is_super_admin(query.from_user):
        return
    data = query.data
    if data == "sa_home":
        text, markup = build_super_admin_panel()
        await query.edit_message_text(text, reply_markup=markup, parse_mode="Markdown")
    elif data == "sa_report":
        d = today_str()
        lines = [f"📊 **【每日分机器人数据报表 · {d}】**\n"]
        if not manager.data["sub_bots"]:
            lines.append("暂无已绑定的子机器人。")
        else:
            for idx, (token, cfg) in enumerate(manager.data["sub_bots"].items(), 1):
                manager.ensure_daily_stats(token)
                st = cfg["daily_stats"][d]
                status = "🟢运行" if cfg.get("enabled") else "🔴停用"
                lines.append(
                    f"**{idx}. @{cfg.get('bot_username', '未知')}** ({status})\n"
                    f"   • 管理号：`@{cfg.get('owner_username')}` | 目标群：{cfg.get('target_chat_title')}\n"
                    f"   • 今日注单：`{st['bet_count']}` 笔 (中 `{st['win_count']}`) | 流水：`{st['turnover']}` | 盈亏：**`{st['profit']:+d}`**"
                )
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回总后台", callback_data="sa_home")]])
        await query.edit_message_text("\n".join(lines), reply_markup=markup, parse_mode="Markdown")
    elif data == "sa_list":
        lines = ["🤖 **【子机器人绑定与解绑管理】**\n点击下方按钮可随时停用或彻底解绑对方的管理号与机器人：\n"]
        keyboard = []
        if not manager.data["sub_bots"]:
            lines.append("当前没有任何子机器人。")
        else:
            for idx, (token, cfg) in enumerate(manager.data["sub_bots"].items(), 1):
                b_name = cfg.get("bot_username", "未知")
                owner = cfg.get("owner_username", "未知")
                lines.append(f"{idx}. `@{b_name}` ➔ 绑定管理：`@{owner}`")
                short_id = token.split(":")[0]
                keyboard.append(
                    [InlineKeyboardButton(f"🗑️ 解绑 @{b_name} (@{owner})", callback_data=f"sa_unbind_{short_id}")]
                )
        keyboard.append([InlineKeyboardButton("⬅️ 返回总后台", callback_data="sa_home")])
        await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    elif data.startswith("sa_unbind_"):
        short_id = data.replace("sa_unbind_", "")
        target_token = None
        for t in list(manager.data["sub_bots"].keys()):
            if t.startswith(short_id + ":"):
                target_token = t
                break
        if target_token:
            cfg = manager.data["sub_bots"].pop(target_token)
            manager.save()
            await stop_sub_bot(target_token)
            await query.edit_message_text(
                f"✅ **已彻底解绑并下线！**\n• 机器人：`@{cfg.get('bot_username')}`\n• 原管理号：`@{cfg.get('owner_username')}` 已失去所有后台权限。",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回列表", callback_data="sa_list")]]),
                parse_mode="Markdown",
            )


# ==========================================
# 二、子机器人独立后台与群聊自动下注逻辑
# ==========================================
def build_sub_panel(token: str) -> tuple[str, InlineKeyboardMarkup]:
    cfg = manager.data["sub_bots"][token]
    manager.ensure_daily_stats(token)
    st = cfg["daily_stats"][today_str()]
    stage = cfg.get("stage_index", 0) + 1
    auto_status = "🟢 自动下注中" if cfg.get("auto_bet") else "🔴 已暂停"
    text = (
        f"⚙️ **【子机器人专属后台面板】**\n\n"
        f"• 机器人：`@{cfg.get('bot_username')}`\n"
        f"• 绑定管理：`@{cfg.get('owner_username')}`\n"
        f"• 投注目标群：**{cfg.get('target_chat_title')}**\n"
        f"• 自动挂机状态：{auto_status}\n"
        f"• 当前倍投关卡：第 **{stage}/6** 关 (`{BET_STAGES[stage - 1]}`)\n"
        f"• 今日战绩：`{st['bet_count']}` 笔 | 盈亏：`{st['profit']:+d}`\n\n"
        f"💡 **绑定下注群方法**：把这个机器人拉进目标群后，在群里发送 **`/bindgroup`** 即可锁定该群自动下注（格式：`大 100`）。"
    )
    toggle_btn = InlineKeyboardButton(
        "⏸️ 停止自动下注" if cfg.get("auto_bet") else "▶️ 开启自动下注", callback_data="sub_toggle"
    )
    keyboard = [
        [toggle_btn, InlineKeyboardButton("🔄 重置倍投(100)", callback_data="sub_reset_stage")],
        [InlineKeyboardButton("🔃 刷新我的后台", callback_data="sub_refresh")],
    ]
    return text, InlineKeyboardMarkup(keyboard)


def make_sub_handlers(token: str):
    def check_owner(user) -> bool:
        cfg = manager.data["sub_bots"].get(token)
        if not cfg:
            return False
        if manager.is_super_admin(user):
            return True
        uname = (user.username or "").lower()
        if uname and uname == cfg.get("owner_username", "").lower():
            if cfg.get("owner_id") != user.id:
                cfg["owner_id"] = user.id
                manager.save()
            return True
        return cfg.get("owner_id") == user.id

    async def sub_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not check_owner(user):
            await update.message.reply_text("⛔ 您未被绑定为此机器人的管理号，请联系总后台管理员授权。")
            return
        text, markup = build_sub_panel(token)
        await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")

    async def sub_bindgroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        chat = update.effective_chat
        if not check_owner(user):
            return
        if chat.type == "private":
            await update.message.reply_text("⚠️ 请把机器人拉进目标投注群后，在**群里**发送 `/bindgroup`。")
            return
        cfg = manager.data["sub_bots"][token]
        cfg["target_chat_id"] = chat.id
        cfg["target_chat_title"] = chat.title or str(chat.id)
        manager.save()
        await update.message.reply_text(
            f"✅ 已锁定本群（**{cfg['target_chat_title']}**）为自动下注目标群！请回私聊后台点击【开启自动下注】。",
            parse_mode="Markdown",
        )

    async def sub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        if not check_owner(query.from_user):
            return
        cfg = manager.data["sub_bots"].get(token)
        if not cfg:
            return
        if query.data == "sub_toggle":
            if not cfg.get("auto_bet") and not cfg.get("target_chat_id"):
                await query.message.reply_text("⚠️ 尚未绑定目标投注群！请先把机器人拉入群聊并发送 `/bindgroup`。")
                return
            cfg["auto_bet"] = not cfg.get("auto_bet", False)
            manager.save()
        elif query.data == "sub_reset_stage":
            cfg["stage_index"] = 0
            manager.save()

        text, markup = build_sub_panel(token)
        await query.edit_message_text(text, reply_markup=markup, parse_mode="Markdown")

    return sub_start, sub_bindgroup, sub_callback


async def sub_bot_worker(token: str, app):
    last_issue = None
    pending_bet = None
    while token in manager.data["sub_bots"]:
        try:
            cfg = manager.data["sub_bots"][token]
            records = fetch_canada28()
            if records:
                latest_issue, latest_size, latest_is_big, latest_sum = records[-1]
                if last_issue != latest_issue:
                    last_issue = latest_issue
                    manager.ensure_daily_stats(token)
                    st = cfg["daily_stats"][today_str()]

                    if pending_bet:
                        amt = pending_bet["amount"]
                        pick = pending_bet["pick"]
                        st["bet_count"] += 1
                        st["turnover"] += amt
                        if pick == latest_size:
                            st["win_count"] += 1
                            st["profit"] += amt
                            cfg["stage_index"] = 0
                            res_txt = f"🎉 第 {latest_issue} 期开【{latest_size}】，`{pick} {amt}` **命中**！今日盈亏: `{st['profit']:+d}`"
                        else:
                            st["profit"] -= amt
                            cfg["stage_index"] = (cfg["stage_index"] + 1) % len(BET_STAGES)
                            res_txt = f"❌ 第 {latest_issue} 期开【{latest_size}】，`{pick} {amt}` **未中**！下关注码: `{BET_STAGES[cfg['stage_index']]}`"
                        pending_bet = None
                        manager.save()
                        if cfg.get("owner_id"):
                            await app.bot.send_message(cfg["owner_id"], res_txt, parse_mode="Markdown")

                    if cfg.get("auto_bet") and cfg.get("target_chat_id") and len(records) >= 10:
                        last_sizes = [h[2] for h in records[-20:]]
                        streak = 1
                        for i in range(len(last_sizes) - 1, 0, -1):
                            if last_sizes[i] == last_sizes[i - 1]:
                                streak += 1
                            else:
                                break
                        last_str = "大" if last_sizes[-1] else "小"
                        rev_str = "小" if last_sizes[-1] else "大"
                        pick = rev_str if streak >= 4 else (last_str if streak >= 2 else None)
                        if pick:
                            amt = BET_STAGES[cfg.get("stage_index", 0)]
                            pending_bet = {"pick": pick, "amount": amt, "issue": latest_issue}
                            if ENABLE_GROUP_POSTS:
                                bet_cmd = f"{pick} {amt}"
                                await app.bot.send_message(cfg["target_chat_id"], bet_cmd)
        except Exception as e:
            logging.error(f"子机器人轮询异常: {e}")
        await asyncio.sleep(15)


async def start_sub_bot(token: str) -> tuple[bool, str]:
    try:
        app = ApplicationBuilder().token(token).build()
        s_start, s_bind, s_cb = make_sub_handlers(token)
        app.add_handler(CommandHandler("start", s_start))
        app.add_handler(CommandHandler("bindgroup", s_bind))
        app.add_handler(CallbackQueryHandler(s_cb))
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)

        bot_username = app.bot.username
        manager.data["sub_bots"][token]["bot_username"] = bot_username
        manager.save()

        manager.running_apps[token] = app
        manager.running_tasks[token] = asyncio.create_task(sub_bot_worker(token, app))
        return True, bot_username
    except Exception as e:
        return False, str(e)


async def stop_sub_bot(token: str):
    task = manager.running_tasks.pop(token, None)
    if task:
        task.cancel()
    app = manager.running_apps.pop(token, None)
    if app:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


# ==========================================
# 三、假端口服务（用于 Render 免费版 Web Service 保活）
# ==========================================
def start_dummy_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Junshi Master Bot is running")

        def log_message(self, format, *args):
            pass

    port = int(os.environ.get("PORT", 10000))
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()


async def main():
    if not MASTER_BOT_TOKEN:
        raise RuntimeError("Missing required environment variable: MASTER_BOT_TOKEN")
    threading.Thread(target=start_dummy_server, daemon=True).start()

    master_app = ApplicationBuilder().token(MASTER_BOT_TOKEN).build()
    master_app.add_handler(CommandHandler("start", master_start))
    master_app.add_handler(CallbackQueryHandler(master_callback))
    master_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, master_text_handler))
    await master_app.initialize()
    await master_app.start()
    await master_app.updater.start_polling(drop_pending_updates=True)
    print(f"👑 总后台主机器人 @{master_app.bot.username} 已上线（锁定总管理 @{SUPER_ADMIN_USERNAME}）！")
    for token in list(manager.data["sub_bots"].keys()):
        ok, info = await start_sub_bot(token)
        if ok:
            print(f"🤖 子机器人 @{info} 已自动恢复运行！")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
