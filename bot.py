import os
import re
import logging
from urllib.parse import urljoin

import requests
import psycopg
from psycopg.rows import dict_row
from flask import Flask, request, abort, jsonify

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("saviron-faq")

BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_ID = int(os.environ["OWNER_ID"])
DATABASE_URL = os.environ["DATABASE_URL"]
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]
BASE_URL = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")

if not re.fullmatch(r"[A-Za-z0-9_-]{20,}", WEBHOOK_SECRET):
    raise RuntimeError("WEBHOOK_SECRET должен содержать минимум 20 символов: латиница, цифры, _ или -")

API = f"https://api.telegram.org/bot{BOT_TOKEN}/"
app = Flask(__name__)


def db():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def init_db():
    with db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS faq (
                id BIGSERIAL PRIMARY KEY,
                question TEXT NOT NULL,
                answer TEXT NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        con.execute("""
            INSERT INTO settings(key, value) VALUES ('enabled', '1')
            ON CONFLICT (key) DO NOTHING
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS user_states (
                user_id BIGINT PRIMARY KEY,
                state TEXT NOT NULL,
                question TEXT
            )
        """)


def tg(method, payload=None):
    response = requests.post(API + method, json=payload or {}, timeout=20)
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data}")
    return data.get("result")


def send(chat_id, text, reply_markup=None, reply_to=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if reply_to:
        payload["reply_to_message_id"] = reply_to
    return tg("sendMessage", payload)


def norm(value):
    value = (value or "").lower().replace("ё", "е")
    value = re.sub(r"[^\w\s]", " ", value)
    return " ".join(value.split())


def enabled():
    with db() as con:
        row = con.execute("SELECT value FROM settings WHERE key='enabled'").fetchone()
        return bool(row and row["value"] == "1")


def set_enabled(value):
    with db() as con:
        con.execute(
            "INSERT INTO settings(key,value) VALUES('enabled',%s) "
            "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value",
            ("1" if value else "0",)
        )


def panel_markup():
    status = "🟢 Ответы включены" if enabled() else "🔴 Ответы выключены"
    return {"inline_keyboard": [
        [{"text": "➕ Добавить вопрос и ответ", "callback_data": "add"}],
        [{"text": "📋 Мои вопросы", "callback_data": "list"}],
        [{"text": status, "callback_data": "toggle"}],
        [{"text": "ℹ️ Как добавить в группу", "callback_data": "how"}],
    ]}


def show_panel(chat_id, text="⚙️ Панель администратора\n\nЗдесь можно добавлять вопросы и ответы, просматривать их и включать или выключать ответы в группах."):
    send(chat_id, text, panel_markup())


def set_state(user_id, state, question=None):
    with db() as con:
        con.execute(
            """INSERT INTO user_states(user_id,state,question) VALUES(%s,%s,%s)
               ON CONFLICT(user_id) DO UPDATE
               SET state=EXCLUDED.state, question=EXCLUDED.question""",
            (user_id, state, question)
        )


def get_state(user_id):
    with db() as con:
        return con.execute(
            "SELECT state, question FROM user_states WHERE user_id=%s",
            (user_id,)
        ).fetchone()


def clear_state(user_id):
    with db() as con:
        con.execute("DELETE FROM user_states WHERE user_id=%s", (user_id,))


def handle_private_message(message):
    user = message.get("from", {})
    user_id = user.get("id")
    chat_id = message["chat"]["id"]
    text = (message.get("text") or "").strip()

    if text == "/start":
        if user_id == OWNER_ID:
            clear_state(user_id)
            show_panel(chat_id)
        else:
            send(chat_id, "Я FAQ-бот. Добавьте меня в группу, чтобы я отвечал на настроенные вопросы.")
        return

    if user_id != OWNER_ID:
        return

    if text == "/cancel":
        clear_state(user_id)
        show_panel(chat_id, "Действие отменено. Панель администратора:")
        return

    state = get_state(user_id)
    if state and state["state"] == "question":
        if not text:
            send(chat_id, "Отправь вопрос обычным текстом.")
            return
        set_state(user_id, "answer", text)
        send(chat_id, "Теперь отправь ответ, который бот будет писать в группе. Для отмены — /cancel.")
        return

    if state and state["state"] == "answer":
        if not text:
            send(chat_id, "Ответ не должен быть пустым. Отправь текст ответа.")
            return
        with db() as con:
            con.execute(
                "INSERT INTO faq(question,answer) VALUES(%s,%s)",
                (state["question"], text)
            )
        clear_state(user_id)
        send(chat_id, "✅ Вопрос и ответ сохранены!", panel_markup())
        return


def handle_callback(callback):
    user_id = callback.get("from", {}).get("id")
    message = callback.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    data = callback.get("data", "")

    if user_id != OWNER_ID:
        tg("answerCallbackQuery", {"callback_query_id": callback["id"], "text": "Нет доступа", "show_alert": True})
        return

    tg("answerCallbackQuery", {"callback_query_id": callback["id"]})

    if data == "add":
        set_state(user_id, "question")
        send(chat_id, "Напиши вопрос, на который бот должен отвечать. Для отмены — /cancel.")
    elif data == "list":
        with db() as con:
            rows = con.execute("SELECT id, question FROM faq ORDER BY id DESC LIMIT 40").fetchall()
        if not rows:
            send(chat_id, "Пока нет сохранённых вопросов.", panel_markup())
            return
        buttons = [[{"text": f"🗑 Удалить #{r['id']}", "callback_data": f"del:{r['id']}"}] for r in rows]
        buttons.append([{"text": "↩️ В панель", "callback_data": "panel"}])
        lines = [f"#{r['id']} — {r['question']}" for r in rows]
        send(chat_id, "📋 Сохранённые вопросы:\n\n" + "\n".join(lines), {"inline_keyboard": buttons})
    elif data.startswith("del:"):
        try:
            faq_id = int(data.split(":", 1)[1])
        except ValueError:
            return
        with db() as con:
            con.execute("DELETE FROM faq WHERE id=%s", (faq_id,))
        send(chat_id, f"🗑 Вопрос #{faq_id} удалён.", panel_markup())
    elif data == "toggle":
        new_value = not enabled()
        set_enabled(new_value)
        show_panel(chat_id, "Настройка изменена.")
    elif data == "how":
        send(
            chat_id,
            "1. Добавь бота в группу.\n"
            "2. В @BotFather отправь /setprivacy → выбери бота → Disable.\n"
            "3. Разреши боту отправлять сообщения.\n"
            "4. После этого он сможет отвечать на сохранённые вопросы."
        )
    elif data == "panel":
        show_panel(chat_id)


def handle_group_message(message):
    text = message.get("text") or ""
    if not text or text.startswith("/") or not enabled():
        return
    incoming = norm(text)
    if not incoming:
        return

    with db() as con:
        rows = con.execute("SELECT question, answer FROM faq ORDER BY id").fetchall()

    # Совпадение по полному вопросу или по фразе вопроса внутри сообщения.
    for row in rows:
        if incoming == norm(row["question"]):
            send(message["chat"]["id"], row["answer"], reply_to=message.get("message_id"))
            return
    for row in rows:
        q = norm(row["question"])
        if len(q) >= 3 and q in incoming:
            send(message["chat"]["id"], row["answer"], reply_to=message.get("message_id"))
            return


def process_update(update):
    if "callback_query" in update:
        handle_callback(update["callback_query"])
        return
    message = update.get("message")
    if not message:
        return
    chat_type = message.get("chat", {}).get("type")
    if chat_type == "private":
        handle_private_message(message)
    elif chat_type in ("group", "supergroup"):
        handle_group_message(message)


@app.get("/")
def health():
    return jsonify({"status": "ok", "service": "Saviron FAQ Bot"})


@app.post("/" + WEBHOOK_SECRET)
def webhook():
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if secret != WEBHOOK_SECRET:
        abort(403)
    update = request.get_json(silent=True)
    if not isinstance(update, dict):
        return jsonify({"ok": True})
    try:
        process_update(update)
    except Exception:
        log.exception("Ошибка обработки обновления Telegram")
    return jsonify({"ok": True})


def configure_webhook():
    if not BASE_URL:
        log.warning("RENDER_EXTERNAL_URL не задан; webhook не установлен автоматически.")
        return
    webhook_url = f"{BASE_URL}/{WEBHOOK_SECRET}"
    tg("setWebhook", {
        "url": webhook_url,
        "secret_token": WEBHOOK_SECRET,
        "allowed_updates": ["message", "callback_query"],
        "drop_pending_updates": True,
    })
    log.info("Webhook установлен: %s", BASE_URL)


init_db()
configure_webhook()
