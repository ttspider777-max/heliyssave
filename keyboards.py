from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import db
from texts import on_off


def kb(rows) -> InlineKeyboardMarkup:
    """Строки из пар (текст, callback_data) или готовых InlineKeyboardButton."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [b if isinstance(b, InlineKeyboardButton) else InlineKeyboardButton(text=b[0], callback_data=b[1])
         for b in row] for row in rows
    ])


def link_rows():
    return [[InlineKeyboardButton(text=b["text"], url=b["url"])] for b in db.list_buttons()]


BACK = [("⬅️ Назад", "m:main")]
CANCEL = kb([[("❌ Отмена", "cancel")]])


def main_menu(is_admin=False):
    rows = [
        [("🤖 Автоответ", "m:ar"), ("🛡 Антискам", "m:scam")],
        [("🔔 Уведомления", "m:notify"), ("🔇 Замьюченные", "m:mutes")],
        [("📊 Моя статистика", "m:stats"), ("📖 Как подключить", "m:help")],
        *link_rows(),
    ]
    if is_admin:
        rows.append([("👑 Админ-панель", "adm:main")])
    return kb(rows)


def ar_menu(s):
    mark = lambda m: "• " if s["ar_mode"] == m else ""  # noqa: E731
    return kb([
        [(mark("off") + "Выкл", "ar:mode:off"), (mark("always") + "Занят", "ar:mode:always"),
         (mark("offline") + "Не в сети", "ar:mode:offline")],
        [("✏️ Изменить текст", "ar:text")],
        [(f"⏱ Не в сети после: {m} мин" if m == s["ar_offline_min"] else f"{m}", f"ar:off:{m}")
         for m in (5, 10, 30, 60)],
        [(f"🔁 Повтор: {m} мин" if m == s["ar_cooldown_min"] else f"{m}", f"ar:cd:{m}")
         for m in (15, 60, 180, 1440)],
        BACK,
    ])


def scam_menu(s):
    return kb([
        [(f"Антискам: {on_off(s['antiscam'])}", "set:antiscam")],
        BACK,
    ])


def notify_menu(s):
    return kb([
        [(f"🗑 Удалённые: {on_off(s['notify_deleted'])}", "set:notify_deleted")],
        [(f"✏️ Изменённые: {on_off(s['notify_edited'])}", "set:notify_edited")],
        BACK,
    ])


def mutes_menu(mutes):
    rows = [[(f"🔊 Размьютить {m['chat_id']}", f"unmute:{m['chat_id']}")] for m in mutes[:30]]
    rows.append(BACK)
    return kb(rows)


def admin_menu():
    return kb([
        [("📊 Статистика", "adm:stats"), ("🏆 Топ запусков", "adm:top")],
        [("💼 Кто подключил бота", "adm:conns")],
        [("👥 Новые юзеры", "adm:recent"), ("🔍 Инфо по ID", "adm:info")],
        [("🚫 Бан", "adm:ban"), ("✅ Разбан", "adm:unban")],
        [("📋 Список банов", "adm:banlist"), ("📢 Рассылка", "adm:bc")],
        [("🛡 Антискам-слова", "adm:words"), ("📁 Экспорт CSV", "adm:export")],
        [("🧹 Очистить кэш", "adm:cleanup"), ("👮 Админы", "adm:admins")],
        [("🔗 Кнопки-ссылки", "adm:btns")],
    ])


ADMIN_BACK = [("⬅️ В админку", "adm:main")]
