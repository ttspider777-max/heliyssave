from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import db
import keyboards as K
from texts import AR_MODES, HOW_TO, esc, on_off, user_link

router = Router()


class UserStates(StatesGroup):
    ar_text = State()


def main_text(uid: int) -> str:
    conns = db.owner_connections(uid)
    active = any(c["enabled"] for c in conns)
    status = "🟢 подключён" if active else "🔴 не подключён (см. «Как подключить»)"
    return (f"👋 <b>SaveMod</b>: сохраняю удалённые сообщения, мьючу, автоотвечаю и защищаю от скама.\n\n"
            f"Статус Telegram Business: {status}")


def ar_text(s: dict) -> str:
    return (f"🤖 <b>Автоответ</b>\n\n"
            f"Режим: {AR_MODES.get(s['ar_mode'], s['ar_mode'])}\n"
            f"«Не в сети» после: {s['ar_offline_min']} мин без ваших сообщений\n"
            f"Повтор в одном чате: не чаще раза в {s['ar_cooldown_min']} мин\n\n"
            f"Текст:\n<blockquote>{esc(s['ar_text'] or '—')}</blockquote>\n\n"
            f"<i>«Занят»: отвечаю всегда. «Не в сети»: отвечаю, только если вы давно ничего не писали.</i>")


def scam_text(s: dict) -> str:
    return (f"🛡 <b>Антискам</b>: {on_off(s['antiscam'])}\n\n"
            "Если собеседник пишет что-то вроде «скинь код», «продай NFT», «крипта», "
            "«бесплатный премиум», я удаляю сообщение, блокирую (мьючу) его и присылаю вам уведомление "
            "с кнопкой разблокировки.\n\n"
            f"Слов-триггеров в базе: {len(db.scam_words())}")


@router.message(CommandStart())
async def cmd_start(m: Message, state: FSMContext):
    await state.clear()
    db.inc_starts(m.from_user.id)
    db.log_event("start", m.from_user.id)
    await m.answer(main_text(m.from_user.id), reply_markup=K.main_menu(db.is_admin(m.from_user.id)))


@router.message(Command("help"))
async def cmd_help(m: Message):
    await m.answer(HOW_TO, reply_markup=K.kb([K.BACK]))


@router.message(Command("cancel"))
@router.callback_query(F.data == "cancel")
async def cancel(ev, state: FSMContext):
    await state.clear()
    msg = ev.message if isinstance(ev, CallbackQuery) else ev
    if isinstance(ev, CallbackQuery):
        await ev.answer("Отменено")
    await msg.answer(main_text(ev.from_user.id), reply_markup=K.main_menu(db.is_admin(ev.from_user.id)))


async def show(c: CallbackQuery, text: str, markup):
    try:
        await c.message.edit_text(text, reply_markup=markup)
    except Exception as e:
        if "not modified" not in str(e):
            await c.message.answer(text, reply_markup=markup)
    try:
        await c.answer()
    except Exception:
        pass  # на колбэк уже ответили


@router.callback_query(F.data == "m:main")
async def cb_main(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(c, main_text(c.from_user.id), K.main_menu(db.is_admin(c.from_user.id)))


@router.callback_query(F.data == "m:help")
async def cb_help(c: CallbackQuery):
    await show(c, HOW_TO, K.kb([K.BACK]))


@router.callback_query(F.data == "m:ar")
async def cb_ar(c: CallbackQuery):
    s = db.get_settings(c.from_user.id)
    await show(c, ar_text(s), K.ar_menu(s))


@router.callback_query(F.data.startswith("ar:mode:"))
async def cb_ar_mode(c: CallbackQuery):
    mode = c.data.split(":")[2]
    if mode in AR_MODES:
        db.set_setting(c.from_user.id, "ar_mode", mode)
    await cb_ar(c)


@router.callback_query(F.data.regexp(r"^ar:(off|cd):\d+$"))
async def cb_ar_minutes(c: CallbackQuery):
    _, kind, value = c.data.split(":")
    db.set_setting(c.from_user.id, "ar_offline_min" if kind == "off" else "ar_cooldown_min", int(value))
    await cb_ar(c)


@router.callback_query(F.data == "ar:text")
async def cb_ar_text(c: CallbackQuery, state: FSMContext):
    await state.set_state(UserStates.ar_text)
    await c.message.answer("✏️ Пришлите новый текст автоответа (до 1000 символов).", reply_markup=K.CANCEL)
    await c.answer()


@router.message(UserStates.ar_text, F.text)
async def got_ar_text(m: Message, state: FSMContext):
    db.set_setting(m.from_user.id, "ar_text", m.text[:1000])
    await state.clear()
    s = db.get_settings(m.from_user.id)
    await m.answer("✅ Текст сохранён.\n\n" + ar_text(s), reply_markup=K.ar_menu(s))


@router.callback_query(F.data == "m:scam")
async def cb_scam(c: CallbackQuery):
    s = db.get_settings(c.from_user.id)
    await show(c, scam_text(s), K.scam_menu(s))


@router.callback_query(F.data == "m:notify")
async def cb_notify(c: CallbackQuery):
    s = db.get_settings(c.from_user.id)
    await show(c, "🔔 <b>Уведомления</b>\n\nО чём мне вам сообщать:", K.notify_menu(s))


@router.callback_query(F.data.in_({"set:antiscam", "set:notify_deleted", "set:notify_edited"}))
async def cb_toggle(c: CallbackQuery):
    key = c.data.split(":")[1]
    db.toggle_setting(c.from_user.id, key)
    await (cb_scam(c) if key == "antiscam" else cb_notify(c))


@router.callback_query(F.data == "m:mutes")
async def cb_mutes(c: CallbackQuery):
    mutes = db.list_mutes(c.from_user.id)
    if not mutes:
        text = "🔇 Замьюченных нет.\n\nЧтобы замьютить, напишите <code>.mute</code> в чате с человеком."
    else:
        lines = [f"• {user_link(r['chat_id'], str(r['chat_id']))}: "
                 f"{'антискам' if r['reason'].startswith('antiscam') else 'вручную'}" for r in mutes[:30]]
        text = f"🔇 <b>Замьюченные ({len(mutes)})</b>\n\n" + "\n".join(lines)
    await show(c, text, K.mutes_menu(mutes))


@router.callback_query(F.data.startswith("unmute:"))
async def cb_unmute(c: CallbackQuery):
    chat_id = int(c.data.split(":")[1])
    ok = db.unmute(c.from_user.id, chat_id)
    await c.answer("🔊 Размьючен" if ok else "Уже не в муте", show_alert=False)
    if c.message.text and c.message.text.startswith("🔇 Замьюченные"):
        await cb_mutes(c)


@router.callback_query(F.data == "m:stats")
async def cb_stats(c: CallbackQuery):
    uid = c.from_user.id
    ev = lambda t: db.count_events(t, owner_id=uid)  # noqa: E731
    text = (f"📊 <b>Ваша статистика</b>\n\n"
            f"🗑 Пойманных удалённых: {ev('deleted')}\n"
            f"✏️ Пойманных изменений: {ev('edited')}\n"
            f"🛡 Заблокировано скамеров: {ev('scam')}\n"
            f"🔇 Удалено сообщений от замьюченных: {ev('muted_deleted')}\n"
            f"🤖 Отправлено автоответов: {ev('autoreply')}\n"
            f"🔇 Сейчас в муте: {len(db.list_mutes(uid))}")
    await show(c, text, K.kb([K.BACK]))


@router.message(StateFilter(None), F.chat.type == "private")
async def fallback(m: Message):
    await m.answer(main_text(m.from_user.id), reply_markup=K.main_menu(db.is_admin(m.from_user.id)))
