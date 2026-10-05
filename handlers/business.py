import logging

from aiogram import Bot, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BusinessConnection, BusinessMessagesDeleted, Message

import db
from config import MAIN_ADMIN_ID
from keyboards import kb
from texts import RIGHTS_WARN, WELCOME, esc, user_link

router = Router()
log = logging.getLogger(__name__)

# сообщения, которые удалил сам бот (мут/антискам/команды) — о них не уведомляем
bot_deleted: set[tuple[str, int, int]] = set()
_conn_cache: dict[str, dict] = {}

MEDIA_TYPES = ("video", "animation", "document", "audio", "voice", "video_note", "sticker")
NO_CAPTION = ("sticker", "video_note")


async def get_conn(bot: Bot, conn_id: str) -> dict | None:
    conn = _conn_cache.get(conn_id) or db.get_connection(conn_id)
    if conn is None:
        try:
            db.save_connection(await bot.get_business_connection(conn_id))
        except TelegramAPIError:
            return None
        conn = db.get_connection(conn_id)
    _conn_cache[conn_id] = conn
    return conn


def extract_media(m: Message):
    if m.photo:
        return "photo", m.photo[-1].file_id
    for t in MEDIA_TYPES:
        obj = getattr(m, t, None)
        if obj:
            return t, obj.file_id
    return None, None


def cache(conn: dict, m: Message):
    media_type, file_id = extract_media(m)
    u = m.from_user
    db.cache_message(conn["id"], m.chat.id, m.message_id, u.id if u else None,
                     u.full_name if u else None, m.text or m.caption, media_type, file_id)


async def send_cached(bot: Bot, chat_id: int, row, header: str):
    """Отправляет владельцу сохранённое сообщение (текст и/или медиа)."""
    text = row["text"]
    body = header + (f"\n\n<blockquote>{esc(text)}</blockquote>" if text else "")
    mt, fid = row["media_type"], row["file_id"]
    try:
        if not mt:
            await bot.send_message(chat_id, body[:4096])
            return
        if mt in NO_CAPTION or len(body) > 1024:
            await bot.send_message(chat_id, body[:4096])
            body = None
        send = getattr(bot, f"send_{mt}")
        await send(chat_id, fid, **({"caption": body} if body else {}))
    except TelegramAPIError as e:
        log.warning("send_cached failed: %s", e)


async def delete_msgs(bot: Bot, conn: dict, chat_id: int, ids: list[int]) -> bool:
    if not conn["can_delete"]:
        return False
    for i in ids:
        bot_deleted.add((conn["id"], chat_id, i))
    try:
        await bot.delete_business_messages(conn["id"], ids)
        return True
    except TelegramAPIError as e:
        log.warning("delete_business_messages failed: %s", e)
        for i in ids:
            bot_deleted.discard((conn["id"], chat_id, i))
        return False


async def notify(bot: Bot, owner_id: int, text: str, **kw):
    try:
        await bot.send_message(owner_id, text, **kw)
    except TelegramAPIError as e:
        log.warning("notify %s failed: %s", owner_id, e)


def missing_rights(conn: dict) -> str:
    missing = []
    if not conn["can_reply"]:
        missing.append("• <b>Ответ на сообщения</b> (для автоответа)")
    if not conn["can_delete"]:
        missing.append("• <b>Удаление всех сообщений</b> (для .mute и антискама)")
    return "\n".join(missing)


# ---------- подключение / отключение ----------

@router.business_connection()
async def on_connection(bc: BusinessConnection, bot: Bot):
    db.touch_user(bc.user)
    db.save_connection(bc)
    _conn_cache.pop(bc.id, None)
    owner = bc.user.id
    if db.is_banned(owner):
        await notify(bot, bc.user_chat_id, "🚫 Вы заблокированы в этом боте, функции недоступны.")
        return
    conn = db.get_connection(bc.id)
    who = f"{user_link(owner, bc.user.full_name)}" + (f" @{esc(bc.user.username)}" if bc.user.username else "")
    if owner != MAIN_ADMIN_ID:
        rights = f"ответ: {'✅' if conn['can_reply'] else '❌'}, удаление: {'✅' if conn['can_delete'] else '❌'}"
        status = "🟢 подключил" if bc.is_enabled else "🔴 отключил"
        await notify(bot, MAIN_ADMIN_ID, f"💼 {who} (<code>{owner}</code>) {status} бота\nПрава: {rights}")
    if not bc.is_enabled:
        db.log_event("disconnect", owner)
        await notify(bot, bc.user_chat_id, "🔌 Бот отключён от вашего аккаунта. Подключить снова: /start → «Как подключить».")
        return
    db.log_event("connect", owner)
    db.get_settings(owner)
    text = WELCOME
    missing = missing_rights(conn)
    if missing:
        text += RIGHTS_WARN.format(missing=missing)
    await notify(bot, bc.user_chat_id, text)


# ---------- команды владельца в чате ----------

async def owner_command(m: Message, bot: Bot, conn: dict) -> bool:
    text = (m.text or "").strip()
    if not text.startswith("."):
        return False
    cmd, _, arg = text.partition(" ")
    cmd = cmd.lower()
    owner, chat_id = conn["owner_id"], m.chat.id
    name = user_link(chat_id, m.chat.full_name)

    if cmd in (".mute", ".мут"):
        if chat_id == owner:
            return True
        if chat_id == MAIN_ADMIN_ID:
            await notify(bot, owner, "🛡 Разработчика бота замьютить нельзя.")
            await delete_msgs(bot, conn, chat_id, [m.message_id])
            return True
        db.mute(owner, chat_id, "manual")
        db.log_event("mute", owner)
        warn = "" if conn["can_delete"] else "\n\n⚠️ Нет права на удаление сообщений, удалять не получится. Включите его в настройках бизнес-бота."
        await notify(bot, owner, f"🔇 {name} замьючен. Все его сообщения будут сразу удаляться.{warn}",
                     reply_markup=kb([[("🔊 Размьютить", f"unmute:{chat_id}")]]))
    elif cmd in (".unmute", ".размут"):
        ok = db.unmute(owner, chat_id)
        await notify(bot, owner, f"🔊 {name} размьючен." if ok else f"ℹ️ {name} не был в муте.")
    elif cmd in (".save", ".сохранить"):
        r = m.reply_to_message
        if not r:
            await notify(bot, owner, "💾 Команду <code>.save</code> нужно отправить <b>ответом</b> на сообщение.")
        else:
            mt, fid = extract_media(r)
            row = {"text": r.text or r.caption, "media_type": mt, "file_id": fid}
            author = r.from_user.full_name if r.from_user else "?"
            await send_cached(bot, owner, row, f"💾 Сохранено из чата с {name}\nАвтор: {esc(author)}")
    elif cmd in (".busy", ".занят"):
        db.set_setting(owner, "ar_mode", "always")
        if arg.strip():
            db.set_setting(owner, "ar_text", arg.strip()[:1000])
        await notify(bot, owner, "💤 Автоответ включён (режим «Занят»).")
    elif cmd in (".free", ".свободен"):
        db.set_setting(owner, "ar_mode", "off")
        await notify(bot, owner, "✅ Автоответ выключен.")
    else:
        return False

    await delete_msgs(bot, conn, chat_id, [m.message_id])
    return True


# ---------- входящие / исходящие сообщения ----------

@router.business_message()
async def on_business_message(m: Message, bot: Bot):
    conn = await get_conn(bot, m.business_connection_id)
    if not conn or not conn["enabled"]:
        return
    owner, chat_id = conn["owner_id"], m.chat.id
    if db.is_banned(owner):
        return

    if m.sender_business_bot:  # отправлено ботом от имени владельца
        cache(conn, m)
        return

    if m.from_user and m.from_user.id == owner:
        db.set_last_active(owner)
        if not await owner_command(m, bot, conn):
            cache(conn, m)
        return

    # сообщение от собеседника
    is_dev = chat_id == MAIN_ADMIN_ID  # разработчика не мьютим и не проверяем антискамом
    if not is_dev and db.is_muted(owner, chat_id):
        if await delete_msgs(bot, conn, chat_id, [m.message_id]):
            db.log_event("muted_deleted", owner)
        return

    s = db.get_settings(owner)
    text = m.text or m.caption or ""
    if s["antiscam"] and text and not is_dev:
        word = db.find_scam(text)
        if word:
            await antiscam(m, bot, conn, word, text)
            return

    cache(conn, m)
    await autoreply(m, bot, conn, s)


async def antiscam(m: Message, bot: Bot, conn: dict, word: str, text: str):
    owner, chat_id = conn["owner_id"], m.chat.id
    db.mute(owner, chat_id, f"antiscam: {word}")
    db.log_event("scam", owner)
    deleted = await delete_msgs(bot, conn, chat_id, [m.message_id])
    if conn["can_reply"]:
        try:
            await bot.send_message(chat_id, "🛡 Сообщение заблокировано антискам-системой. Ваши сообщения больше не доставляются.",
                                   business_connection_id=conn["id"])
        except TelegramAPIError as e:
            log.warning("antiscam reply failed: %s", e)
    note = "" if deleted else "\n\n⚠️ Удалить сообщение не удалось: включите право «Удаление сообщений»."
    await notify(
        bot, owner,
        f"🛡 <b>Антискам!</b>\n{user_link(chat_id, m.chat.full_name)} (<code>{chat_id}</code>) "
        f"написал подозрительное сообщение (триггер: «{esc(word)}»).\n"
        f"Он заблокирован: все его сообщения будут удаляться.\n\n<blockquote>{esc(text[:1500])}</blockquote>{note}",
        reply_markup=kb([[("🔓 Разблокировать", f"unmute:{chat_id}")]]),
    )


async def autoreply(m: Message, bot: Bot, conn: dict, s: dict):
    if s["ar_mode"] == "off" or not s["ar_text"] or not conn["can_reply"]:
        return
    if s["ar_mode"] == "offline" and db.now() - (s["last_active"] or 0) < s["ar_offline_min"] * 60:
        return
    if not db.ar_try(conn["owner_id"], m.chat.id, s["ar_cooldown_min"] * 60):
        return
    try:
        await bot.send_message(m.chat.id, s["ar_text"], business_connection_id=conn["id"], parse_mode=None)
        db.log_event("autoreply", conn["owner_id"])
    except TelegramAPIError as e:
        log.warning("autoreply failed: %s", e)


# ---------- изменённые ----------

@router.edited_business_message()
async def on_edited(m: Message, bot: Bot):
    conn = await get_conn(bot, m.business_connection_id)
    if not conn or not conn["enabled"] or db.is_banned(conn["owner_id"]):
        return
    owner = conn["owner_id"]
    old = db.get_cached(conn["id"], m.chat.id, m.message_id)
    cache(conn, m)
    if not old or (m.from_user and m.from_user.id == owner):
        return
    new_text = m.text or m.caption or ""
    old_text = old["text"] or ""
    if old_text == new_text or not db.get_settings(owner)["notify_edited"]:
        return
    db.log_event("edited", owner)
    await notify(
        bot, owner,
        f"✏️ {user_link(m.chat.id, m.chat.full_name)} изменил сообщение\n\n"
        f"<b>Было:</b>\n<blockquote>{esc(old_text[:1800]) or '—'}</blockquote>\n"
        f"<b>Стало:</b>\n<blockquote>{esc(new_text[:1800]) or '—'}</blockquote>",
    )


# ---------- удалённые ----------

@router.deleted_business_messages()
async def on_deleted(event: BusinessMessagesDeleted, bot: Bot):
    conn = await get_conn(bot, event.business_connection_id)
    if not conn or not conn["enabled"] or db.is_banned(conn["owner_id"]):
        return
    owner, chat_id = conn["owner_id"], event.chat.id
    notify_on = db.get_settings(owner)["notify_deleted"]
    for mid in event.message_ids:
        key = (conn["id"], chat_id, mid)
        if key in bot_deleted:
            bot_deleted.discard(key)
            continue
        row = db.get_cached(conn["id"], chat_id, mid)
        if not row or row["from_id"] == owner or not notify_on:
            continue
        db.log_event("deleted", owner)
        header = f"🗑 {user_link(chat_id, row['from_name'] or event.chat.full_name)} удалил сообщение"
        await send_cached(bot, owner, row, header)
