import asyncio
import csv
import io
import re

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.filters import BaseFilter, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, Message

import db
import keyboards as K
from config import MAIN_ADMIN_ID, MESSAGE_TTL_DAYS
from handlers.user import show
from texts import esc, fmt_ts, user_link


class IsAdmin(BaseFilter):
    async def __call__(self, ev) -> bool:
        return ev.from_user is not None and db.is_admin(ev.from_user.id)


router = Router()
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


class A(StatesGroup):
    ban = State()
    unban = State()
    info = State()
    broadcast = State()
    add_word = State()
    del_word = State()
    add_admin = State()
    del_admin = State()
    btn_text = State()
    btn_url = State()
    btn_edit_text = State()
    btn_edit_url = State()


PROMPTS = {
    "ban": (A.ban, "🚫 Пришлите ID пользователя для бана.\nМожно с причиной: <code>123456789 спам</code>"),
    "unban": (A.unban, "✅ Пришлите ID пользователя для разбана."),
    "info": (A.info, "🔍 Пришлите ID пользователя."),
    "bc": (A.broadcast, "📢 Пришлите сообщение для рассылки (текст, фото, видео и т.д.)."),
    "addword": (A.add_word, "➕ Пришлите слова/фразы-триггеры, каждую с новой строки."),
    "delword": (A.del_word, "➖ Пришлите слова/фразы для удаления, каждую с новой строки."),
    "addadmin": (A.add_admin, "👮 Пришлите ID нового админа."),
    "deladmin": (A.del_admin, "👮 Пришлите ID админа, которого нужно снять."),
}

ADMIN_TEXT = "👑 <b>Админ-панель SaveMod</b>\n\nВыберите действие:"


def parse_id(text: str | None) -> int | None:
    m = re.match(r"\s*(-?\d{3,20})", text or "")
    return int(m.group(1)) if m else None


def user_line(r) -> str:
    uname = f" @{r['username']}" if r["username"] else ""
    return f"{user_link(r['user_id'], r['first_name'])}{esc(uname)} <code>{r['user_id']}</code>"


@router.message(Command("admin"))
async def cmd_admin(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(ADMIN_TEXT, reply_markup=K.admin_menu())


@router.callback_query(F.data == "adm:main")
async def cb_admin(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(c, ADMIN_TEXT, K.admin_menu())


@router.callback_query(F.data.startswith("adm:ask:") | F.data.in_({"adm:ban", "adm:unban", "adm:info", "adm:bc"}))
async def cb_ask(c: CallbackQuery, state: FSMContext):
    key = c.data.split(":")[-1]
    if key in ("addadmin", "deladmin") and c.from_user.id != MAIN_ADMIN_ID:
        await c.answer("Только главный админ может управлять админами", show_alert=True)
        return
    st, prompt = PROMPTS[key]
    await state.set_state(st)
    await c.message.answer(prompt, reply_markup=K.CANCEL)
    await c.answer()


# ---------- статистика ----------

@router.callback_query(F.data == "adm:stats")
async def cb_stats(c: CallbackQuery):
    s = db.stats()
    text = (
        "📊 <b>Статистика</b>\n\n"
        f"👥 Пользователей: <b>{s['users']}</b>\n"
        f"🆕 Новых за 24ч / 7д: {s['new_24h']} / {s['new_7d']}\n"
        f"🟢 Активных за 24ч: {s['active_24h']}\n"
        f"▶️ Всего запусков /start: {s['starts']}\n"
        f"🚫 Забанено: {s['banned']}\n\n"
        f"💼 Бизнес-подключений активно / всего: {s['conn_active']} / {s['conn_total']}\n"
        f"💾 Сообщений в кэше: {s['cached']}\n\n"
        f"🗑 Поймано удалённых: {s['deleted']} (24ч: {s['deleted_24h']})\n"
        f"✏️ Поймано изменений: {s['edited']}\n"
        f"🛡 Заблокировано скамеров: {s['scam']} (24ч: {s['scam_24h']})\n"
        f"🔇 Активных мутов: {s['mutes']}\n"
        f"🧹 Удалено сообщений замьюченных: {s['muted_deleted']}\n"
        f"🤖 Автоответов: {s['autoreply']}"
    )
    await show(c, text, K.kb([[("🔄 Обновить", "adm:stats")], K.ADMIN_BACK]))


@router.callback_query(F.data == "adm:top")
async def cb_top(c: CallbackQuery):
    rows = db.top_starts(25)
    lines = [f"{i}. {user_line(r)}: <b>{r['starts']}</b> раз(а)" for i, r in enumerate(rows, 1)]
    await show(c, "🏆 <b>Топ по запускам бота</b>\n\n" + ("\n".join(lines) or "Пусто"), K.kb([K.ADMIN_BACK]))


@router.callback_query(F.data == "adm:recent")
async def cb_recent(c: CallbackQuery):
    rows = db.recent_users(25)
    lines = [f"• {user_line(r)}\n   с {fmt_ts(r['first_seen'])}, запусков: {r['starts']}" for r in rows]
    await show(c, "👥 <b>Последние пользователи</b>\n\n" + ("\n".join(lines) or "Пусто"), K.kb([K.ADMIN_BACK]))


@router.callback_query(F.data.in_({"adm:conns", "adm:conns:active"}))
async def cb_conns(c: CallbackQuery):
    active_only = c.data.endswith(":active")
    rows = db.all_connections(active_only)
    lines = []
    for r in rows:
        uname = f" @{r['username']}" if r["username"] else ""
        rights = ("💬" if r["can_reply"] else "🚫💬") + " " + ("🗑" if r["can_delete"] else "🚫🗑")
        lines.append(f"{'🟢' if r['enabled'] else '🔴'} {user_link(r['owner_id'], r['first_name'])}{esc(uname)} "
                     f"<code>{r['owner_id']}</code>\n   {fmt_ts(r['date'])} | права: {rights}")
    s = db.stats()
    text = (f"💼 <b>Кто подключил бота</b>\n"
            f"Активных: {s['conn_active']} | Всего: {s['conn_total']}\n\n"
            + ("\n".join(lines) or "Пока никто")
            + "\n\n<i>🟢 подключён, 🔴 отключил. 💬 может отвечать, 🗑 может удалять. "
              "Время: последнее изменение подключения.</i>")
    toggle = ("Показать всех", "adm:conns") if active_only else ("Только активные", "adm:conns:active")
    await show(c, text[:4096], K.kb([[toggle, ("🔄 Обновить", c.data)], K.ADMIN_BACK]))


# ---------- инфо ----------

def user_info(uid: int):
    r = db.get_user(uid)
    if not r:
        return f"❌ Пользователь <code>{uid}</code> не найден в базе.", None
    conns = db.owner_connections(uid)
    s = db.get_settings(uid)
    ev = lambda t: db.count_events(t, owner_id=uid)  # noqa: E731
    text = (
        f"👤 <b>Пользователь</b>\n{user_line(r)}\n\n"
        f"Первый вход: {fmt_ts(r['first_seen'])}\n"
        f"Последняя активность: {fmt_ts(r['last_seen'])}\n"
        f"Запусков /start: {r['starts']}\n"
        f"Статус: {'🚫 забанен (' + esc(r['ban_reason'] or 'без причины') + ')' if r['banned'] else '✅ активен'}\n"
        f"Админ: {'да' if db.is_admin(uid) else 'нет'}\n\n"
        f"💼 Business: {'🟢 подключён' if any(c['enabled'] for c in conns) else '🔴 нет'}"
        f" (подключений: {len(conns)})\n"
        f"Автоответ: {s['ar_mode']} | Антискам: {'вкл' if s['antiscam'] else 'выкл'}\n"
        f"🗑 удалённых: {ev('deleted')} | 🛡 скам: {ev('scam')} | 🔇 мутов: {len(db.list_mutes(uid))}"
    )
    btn = ("✅ Разбанить", f"adm:do_unban:{uid}") if r["banned"] else ("🚫 Забанить", f"adm:do_ban:{uid}")
    return text, K.kb([[btn], K.ADMIN_BACK])


@router.message(A.info)
async def got_info(m: Message, state: FSMContext):
    uid = parse_id(m.text)
    if uid is None:
        await m.answer("Нужен числовой ID.", reply_markup=K.CANCEL)
        return
    await state.clear()
    text, markup = user_info(uid)
    await m.answer(text, reply_markup=markup or K.kb([K.ADMIN_BACK]))


# ---------- бан / разбан ----------

async def do_ban(bot: Bot, uid: int, reason: str | None) -> str:
    if uid == MAIN_ADMIN_ID:
        return "❌ Нельзя забанить главного админа."
    db.set_ban(uid, True, reason)
    db.del_admin(uid)
    try:
        await bot.send_message(uid, "🚫 Вы заблокированы в боте." + (f"\nПричина: {esc(reason)}" if reason else ""))
    except TelegramAPIError:
        pass
    return f"🚫 Пользователь <code>{uid}</code> забанен." + (f"\nПричина: {esc(reason)}" if reason else "")


async def do_unban(bot: Bot, uid: int) -> str:
    db.set_ban(uid, False)
    try:
        await bot.send_message(uid, "✅ Вы разблокированы в боте. Нажмите /start")
    except TelegramAPIError:
        pass
    return f"✅ Пользователь <code>{uid}</code> разбанен."


@router.message(A.ban)
async def got_ban(m: Message, state: FSMContext, bot: Bot):
    uid = parse_id(m.text)
    if uid is None:
        await m.answer("Нужен числовой ID.", reply_markup=K.CANCEL)
        return
    await state.clear()
    reason = (m.text.strip().split(maxsplit=1)[1:] or [None])[0]
    await m.answer(await do_ban(bot, uid, reason), reply_markup=K.kb([K.ADMIN_BACK]))


@router.message(A.unban)
async def got_unban(m: Message, state: FSMContext, bot: Bot):
    uid = parse_id(m.text)
    if uid is None:
        await m.answer("Нужен числовой ID.", reply_markup=K.CANCEL)
        return
    await state.clear()
    await m.answer(await do_unban(bot, uid), reply_markup=K.kb([K.ADMIN_BACK]))


@router.callback_query(F.data.regexp(r"^adm:do_(ban|unban):-?\d+$"))
async def cb_do_ban(c: CallbackQuery, bot: Bot):
    _, action, uid = c.data.split(":")
    uid = int(uid)
    await c.answer(re.sub("<[^>]+>", "", await do_ban(bot, uid, None) if action == "do_ban" else await do_unban(bot, uid)),
                   show_alert=True)
    text, markup = user_info(uid)
    await show(c, text, markup)


@router.message(Command("ban"))
async def cmd_ban(m: Message, bot: Bot):
    parts = (m.text or "").split(maxsplit=2)
    uid = parse_id(parts[1] if len(parts) > 1 else None)
    if uid is None:
        await m.answer("Использование: <code>/ban ID [причина]</code>")
        return
    await m.answer(await do_ban(bot, uid, parts[2] if len(parts) > 2 else None))


@router.message(Command("unban"))
async def cmd_unban(m: Message, bot: Bot):
    parts = (m.text or "").split()
    uid = parse_id(parts[1] if len(parts) > 1 else None)
    if uid is None:
        await m.answer("Использование: <code>/unban ID</code>")
        return
    await m.answer(await do_unban(bot, uid))


@router.callback_query(F.data == "adm:banlist")
async def cb_banlist(c: CallbackQuery):
    rows = db.banned_list()
    lines = [f"• {user_line(r)}: {esc(r['ban_reason'] or 'без причины')}" for r in rows[:50]]
    text = f"📋 <b>Забаненные ({len(rows)})</b>\n\n" + ("\n".join(lines) or "Никого")
    await show(c, text, K.kb([[("✅ Разбанить по ID", "adm:unban")], K.ADMIN_BACK]))


# ---------- рассылка ----------

@router.message(A.broadcast)
async def got_broadcast(m: Message, state: FSMContext):
    await state.update_data(bc_chat=m.chat.id, bc_msg=m.message_id)
    n = len(db.broadcast_ids())
    await m.answer(f"📢 Отправить это сообщение <b>{n}</b> пользователям?",
                   reply_markup=K.kb([[("✅ Отправить", "adm:bc_go"), ("❌ Отмена", "cancel")]]))


@router.callback_query(F.data == "adm:bc_go", A.broadcast)
async def cb_bc_go(c: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()
    await c.answer()
    await c.message.edit_text("⏳ Рассылка идёт...")
    ok = fail = 0
    for uid in db.broadcast_ids():
        try:
            await bot.copy_message(uid, data["bc_chat"], data["bc_msg"])
            ok += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                await bot.copy_message(uid, data["bc_chat"], data["bc_msg"])
                ok += 1
            except TelegramAPIError:
                fail += 1
        except TelegramAPIError:
            fail += 1
        await asyncio.sleep(0.05)
    await c.message.answer(f"📢 Рассылка завершена.\n✅ Доставлено: {ok}\n❌ Не доставлено: {fail}",
                           reply_markup=K.kb([K.ADMIN_BACK]))


# ---------- антискам-слова ----------

@router.callback_query(F.data == "adm:words")
async def cb_words(c: CallbackQuery):
    words = db.scam_words()
    body = ", ".join(f"<code>{esc(w)}</code>" for w in words)
    if len(body) > 3500:
        body = body[:3500] + "…"
    await show(c, f"🛡 <b>Слова-триггеры антискама ({len(words)})</b>\n\n{body}\n\n"
                  "<i>Срабатывает, если фраза встречается в сообщении (регистр не важен).</i>",
               K.kb([[("➕ Добавить", "adm:ask:addword"), ("➖ Удалить", "adm:ask:delword")], K.ADMIN_BACK]))


def split_words(text: str) -> list[str]:
    return [w.strip() for w in re.split(r"[\n,;]+", text or "") if w.strip()]


@router.message(A.add_word)
async def got_add_word(m: Message, state: FSMContext):
    await state.clear()
    added = [w for w in split_words(m.text) if db.add_scam_word(w)]
    await m.answer(f"✅ Добавлено: {len(added)}", reply_markup=K.kb([[("🛡 К словам", "adm:words")], K.ADMIN_BACK]))


@router.message(A.del_word)
async def got_del_word(m: Message, state: FSMContext):
    await state.clear()
    removed = [w for w in split_words(m.text) if db.del_scam_word(w)]
    await m.answer(f"🗑 Удалено: {len(removed)}", reply_markup=K.kb([[("🛡 К словам", "adm:words")], K.ADMIN_BACK]))


# ---------- экспорт / очистка ----------

@router.callback_query(F.data == "adm:export")
async def cb_export(c: CallbackQuery):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["user_id", "username", "name", "first_seen", "last_seen", "starts", "banned", "ban_reason"])
    for r in db.all_users():
        w.writerow([r["user_id"], r["username"] or "", r["first_name"] or "", fmt_ts(r["first_seen"]),
                    fmt_ts(r["last_seen"]), r["starts"], r["banned"], r["ban_reason"] or ""])
    data = buf.getvalue().encode("utf-8-sig")  # BOM, чтобы Excel открыл кириллицу
    await c.message.answer_document(BufferedInputFile(data, "users.csv"), caption="📁 Экспорт пользователей")
    await c.answer()


@router.callback_query(F.data == "adm:cleanup")
async def cb_cleanup(c: CallbackQuery):
    n = db.cleanup_messages(MESSAGE_TTL_DAYS)
    await c.answer(f"🧹 Удалено из кэша: {n} сообщений старше {MESSAGE_TTL_DAYS} дн.", show_alert=True)


# ---------- админы ----------

@router.callback_query(F.data == "adm:admins")
async def cb_admins(c: CallbackQuery):
    lines = []
    for uid in db.list_admins():
        r = db.get_user(uid)
        name = user_line(r) if r else f"<code>{uid}</code>"
        lines.append(f"• {name}" + (" 👑 главный" if uid == MAIN_ADMIN_ID else ""))
    rows = [K.ADMIN_BACK]
    if c.from_user.id == MAIN_ADMIN_ID:
        rows.insert(0, [("➕ Добавить", "adm:ask:addadmin"), ("➖ Снять", "adm:ask:deladmin")])
    await show(c, "👮 <b>Админы</b>\n\n" + "\n".join(lines), K.kb(rows))


@router.message(A.add_admin)
async def got_add_admin(m: Message, state: FSMContext):
    uid = parse_id(m.text)
    if uid is None:
        await m.answer("Нужен числовой ID.", reply_markup=K.CANCEL)
        return
    await state.clear()
    db.add_admin(uid)
    await m.answer(f"✅ <code>{uid}</code> теперь админ.", reply_markup=K.kb([K.ADMIN_BACK]))


@router.message(A.del_admin)
async def got_del_admin(m: Message, state: FSMContext):
    uid = parse_id(m.text)
    if uid is None:
        await m.answer("Нужен числовой ID.", reply_markup=K.CANCEL)
        return
    await state.clear()
    if uid == MAIN_ADMIN_ID:
        await m.answer("❌ Главного админа снять нельзя.")
        return
    db.del_admin(uid)
    await m.answer(f"✅ <code>{uid}</code> больше не админ.", reply_markup=K.kb([K.ADMIN_BACK]))


# ---------- кнопки-ссылки в главном меню ----------

URL_HELP = ("Подходит: <code>https://...</code>, <code>t.me/канал</code>, <code>@username</code> "
            "или <code>tg://...</code>")


def btns_view():
    buttons = db.list_buttons()
    lines = [f"{i}. {esc(b['text'])} → {esc(b['url'])}" for i, b in enumerate(buttons, 1)]
    text = ("🔗 <b>Кнопки-ссылки</b>\n"
            "Показываются в главном меню у всех пользователей.\n\n"
            + ("\n".join(lines) or "Пока нет ни одной кнопки.")
            + "\n\nНажмите на кнопку, чтобы изменить ссылку, текст, порядок или удалить её.")
    rows = [[(f"✏️ {b['text']}"[:60], f"adm:btn:{b['id']}")] for b in buttons]
    rows.append([("➕ Добавить кнопку", "adm:btn_add")])
    rows.append(K.ADMIN_BACK)
    return text, K.kb(rows)


def btn_view(b):
    text = (f"🔗 <b>Кнопка</b>\n\n"
            f"Текст: {esc(b['text'])}\n"
            f"Ссылка: {esc(b['url'])}")
    bid = b["id"]
    return text, K.kb([
        [InlineKeyboardButton(text="👁 Открыть ссылку", url=b["url"])],
        [("🔗 Изменить ссылку", f"adm:btn_url:{bid}"), ("✏️ Изменить текст", f"adm:btn_text:{bid}")],
        [("⬆️ Выше", f"adm:btn_up:{bid}"), ("⬇️ Ниже", f"adm:btn_down:{bid}")],
        [("🗑 Удалить", f"adm:btn_del:{bid}")],
        [("⬅️ К кнопкам", "adm:btns")],
    ])


async def check_url(m: Message, text: str, url: str) -> bool:
    """Telegram сам проверяет ссылку: пробуем показать превью кнопки."""
    try:
        await m.answer("👁 Превью кнопки:", reply_markup=K.kb([[InlineKeyboardButton(text=text, url=url)]]))
        return True
    except TelegramAPIError:
        await m.answer("❌ Telegram не принял эту ссылку. Пришлите другую.\n" + URL_HELP, reply_markup=K.CANCEL)
        return False


@router.callback_query(F.data == "adm:btns")
async def cb_btns(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(c, *btns_view())


@router.callback_query(F.data.regexp(r"^adm:btn:\d+$"))
async def cb_btn(c: CallbackQuery):
    b = db.get_button(int(c.data.split(":")[2]))
    if not b:
        await c.answer("Кнопка уже удалена", show_alert=True)
        await show(c, *btns_view())
        return
    await show(c, *btn_view(b))


@router.callback_query(F.data.regexp(r"^adm:btn_(up|down):\d+$"))
async def cb_btn_move(c: CallbackQuery):
    _, action, bid = c.data.split(":")
    db.move_button(int(bid), -1 if action == "btn_up" else 1)
    await show(c, *btns_view())


@router.callback_query(F.data.regexp(r"^adm:btn_del:\d+$"))
async def cb_btn_del(c: CallbackQuery):
    db.delete_button(int(c.data.split(":")[2]))
    await c.answer("🗑 Кнопка удалена")
    await show(c, *btns_view())


@router.callback_query(F.data == "adm:btn_add")
async def cb_btn_add(c: CallbackQuery, state: FSMContext):
    await state.set_state(A.btn_text)
    await c.message.answer("➕ Пришлите <b>текст</b> новой кнопки (например: <code>📢 Наш канал</code>).",
                           reply_markup=K.CANCEL)
    await c.answer()


@router.message(A.btn_text, F.text)
async def got_btn_text(m: Message, state: FSMContext):
    await state.update_data(btn_text=m.text.strip()[:64])
    await state.set_state(A.btn_url)
    await m.answer("🔗 Теперь пришлите <b>ссылку</b>, куда будет вести кнопка.\n" + URL_HELP, reply_markup=K.CANCEL)


@router.message(A.btn_url, F.text)
async def got_btn_url(m: Message, state: FSMContext):
    url = db.normalize_url(m.text)
    if not url:
        await m.answer("❌ Это не похоже на ссылку.\n" + URL_HELP, reply_markup=K.CANCEL)
        return
    text = (await state.get_data())["btn_text"]
    if not await check_url(m, text, url):
        return
    await state.clear()
    db.add_button(text, url)
    await m.answer("✅ Кнопка добавлена в главное меню.", reply_markup=K.kb([[("🔗 К кнопкам", "adm:btns")]]))


@router.callback_query(F.data.regexp(r"^adm:btn_(url|text):\d+$"))
async def cb_btn_edit(c: CallbackQuery, state: FSMContext):
    _, action, bid = c.data.split(":")
    await state.update_data(btn_id=int(bid))
    if action == "btn_url":
        await state.set_state(A.btn_edit_url)
        await c.message.answer("🔗 Пришлите новую ссылку для кнопки.\n" + URL_HELP, reply_markup=K.CANCEL)
    else:
        await state.set_state(A.btn_edit_text)
        await c.message.answer("✏️ Пришлите новый текст кнопки.", reply_markup=K.CANCEL)
    await c.answer()


@router.message(A.btn_edit_url, F.text)
async def got_btn_edit_url(m: Message, state: FSMContext):
    b = db.get_button((await state.get_data())["btn_id"])
    if not b:
        await state.clear()
        await m.answer("Кнопка уже удалена.", reply_markup=K.kb([[("🔗 К кнопкам", "adm:btns")]]))
        return
    url = db.normalize_url(m.text)
    if not url:
        await m.answer("❌ Это не похоже на ссылку.\n" + URL_HELP, reply_markup=K.CANCEL)
        return
    if not await check_url(m, b["text"], url):
        return
    await state.clear()
    db.update_button(b["id"], "url", url)
    text, markup = btn_view(db.get_button(b["id"]))
    await m.answer("✅ Ссылка изменена.\n\n" + text, reply_markup=markup)


@router.message(A.btn_edit_text, F.text)
async def got_btn_edit_text(m: Message, state: FSMContext):
    b = db.get_button((await state.get_data())["btn_id"])
    await state.clear()
    if not b:
        await m.answer("Кнопка уже удалена.", reply_markup=K.kb([[("🔗 К кнопкам", "adm:btns")]]))
        return
    db.update_button(b["id"], "text", m.text.strip()[:64])
    text, markup = btn_view(db.get_button(b["id"]))
    await m.answer("✅ Текст изменён.\n\n" + text, reply_markup=markup)
