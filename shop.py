import json, logging, os, secrets, sqlite3
from datetime import datetime
from pathlib import Path

import requests
from flask import Flask, jsonify, request, send_from_directory, session

BASE = Path(__file__).resolve().parent
WEB = BASE / "shop_web"
EXPORT = BASE / "result(2)(1).json"
DATA = Path(os.environ.get("DATA_DIR", "/data" if Path("/data").exists() else str(BASE / "data")))
MEDIA = DATA / "telegram_media"
DB = Path(os.environ.get("SHOP_DB", str(DATA / "shop.db")))
DATA.mkdir(parents=True, exist_ok=True)
MEDIA.mkdir(parents=True, exist_ok=True)
DB.parent.mkdir(parents=True, exist_ok=True)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
SOURCE = os.environ.get("TELEGRAM_SOURCE_CHAT", "@Lecoinmalin34").strip()
SYNC_CHAT = os.environ.get("MEDIA_SYNC_CHAT_ID", "").strip()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
app = Flask(__name__, static_folder=str(WEB), static_url_path="")
app.secret_key = os.environ.get("SHOP_SECRET_KEY") or secrets.token_hex(32)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("lecoinmalin34")


def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def text_of(value):
    if isinstance(value, str): return value
    if isinstance(value, list): return "".join(text_of(x.get("text", "") if isinstance(x, dict) else x) for x in value)
    return ""


def media_ref(message):
    for key in ("photo", "file", "video", "animation", "document"):
        if isinstance(message.get(key), str) and message[key]: return message[key]
    return ""


def load_export():
    try: data = json.loads(EXPORT.read_text(encoding="utf-8"))
    except Exception as exc:
        log.error("export_load_failed file=%s error=%s", EXPORT, exc)
        return [], {}, {}, lambda _: None
    messages = data.get("messages", [])
    by_id = {m.get("id"): m for m in messages if isinstance(m.get("id"), int)}
    topics = {int(m["id"]): str(m["title"]).strip() for m in messages if m.get("type") == "service" and m.get("action") in ("topic_created", "topic_edited") and m.get("title")}
    def find_topic(reply):
        seen = set()
        while isinstance(reply, int) and reply not in seen:
            if reply in topics: return reply
            seen.add(reply); reply = (by_id.get(reply) or {}).get("reply_to_message_id")
        return None
    return messages, topics, by_id, find_topic


def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, first_name TEXT, last_name TEXT, created_at TEXT);
        CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY, tracking TEXT UNIQUE, user_id INTEGER, status TEXT, total REAL, created_at TEXT);
        CREATE TABLE IF NOT EXISTS order_items(id INTEGER PRIMARY KEY, order_id INTEGER, product_id INTEGER, size TEXT, qty INTEGER, price REAL);
        CREATE TABLE IF NOT EXISTS telegram_media(message_id INTEGER, asset_index INTEGER DEFAULT 0, path TEXT NOT NULL, created_at TEXT, PRIMARY KEY(message_id, asset_index));
        """)
init_db()


def mirror():
    messages, topics, by_id, find_topic = load_export()
    result = {str(i): {"id": i, "name": name, "messages": []} for i, name in topics.items()}
    unassigned = []
    for m in messages:
        if m.get("type") == "service" or not isinstance(m.get("id"), int): continue
        tid = find_topic(m.get("reply_to_message_id"))
        item = {"id": m["id"], "date": m.get("date", ""), "text": text_of(m.get("text")), "media_ref": media_ref(m), "telegram_url": f"https://t.me/{SOURCE.lstrip('@').split('/')[0]}/{m['id']}"}
        if tid is None: unassigned.append(item)
        else: result[str(tid)]["messages"].append(item)
    return result, unassigned, len(messages)


def telegram(method, **payload):
    if not BOT_TOKEN:
        log.error("telegram_%s skipped reason=BOT_TOKEN_missing", method)
        return {"ok": False, "description": "BOT_TOKEN absent"}
    try:
        response = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", json=payload, timeout=60)
        data = response.json()
        if not data.get("ok"):
            log.error("telegram_%s_failed chat=%s message_id=%s error=%s", method, payload.get("chat_id"), payload.get("message_id"), data.get("description"))
        else:
            log.info("telegram_%s_ok chat=%s message_id=%s", method, payload.get("chat_id"), payload.get("message_id"))
        return data
    except Exception as exc:
        log.error("telegram_%s_exception error=%s", method, exc)
        return {"ok": False, "description": str(exc)}


def sync_message(message_id):
    if not SYNC_CHAT:
        log.error("media_sync_skipped message_id=%s reason=MEDIA_SYNC_CHAT_ID_missing", message_id)
        return 0
    forwarded = telegram("forwardMessage", chat_id=SYNC_CHAT, from_chat_id=SOURCE, message_id=message_id)
    forwarded_message = forwarded.get("result") or {}
    file_ids = []
    if forwarded_message.get("photo"):
        file_ids.append(forwarded_message["photo"][-1].get("file_id"))
    for key in ("video", "document", "animation"):
        thumb = (forwarded_message.get(key) or {}).get("thumbnail") or (forwarded_message.get(key) or {}).get("thumb") or {}
        if thumb.get("file_id"): file_ids.append(thumb["file_id"])
    recovered = 0
    for index, file_id in enumerate(x for x in file_ids if x):
        info = telegram("getFile", file_id=file_id)
        path = (info.get("result") or {}).get("file_path")
        if not path:
            log.error("media_getfile_failed message_id=%s file_id_present=true", message_id)
            continue
        try:
            response = requests.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{path}", timeout=90)
            response.raise_for_status()
            target = MEDIA / f"{message_id}-{index}{Path(path).suffix or '.jpg'}"
            target.write_bytes(response.content)
            with db() as c: c.execute("INSERT OR REPLACE INTO telegram_media VALUES(?,?,?,?)", (message_id, index, str(target.relative_to(DATA)), datetime.utcnow().isoformat()))
            log.info("media_download_ok message_id=%s path=%s bytes=%s", message_id, target, len(response.content))
            recovered += 1
        except Exception as exc:
            log.error("media_download_failed message_id=%s file_path=%s error=%s", message_id, path, exc)
    return recovered


@app.get("/")
def home(): return send_from_directory(str(WEB), "index.html")

@app.get("/<path:name>")
def serve_shop_file(name): return send_from_directory(str(WEB), name)

@app.get("/media/<path:name>")
def media_file(name): return send_from_directory(str(MEDIA), name)

@app.get("/health")
def health():
    topics, unassigned, total = mirror()
    with db() as c: recovered = c.execute("SELECT COUNT(*) n FROM telegram_media").fetchone()["n"]
    return jsonify(ok=True, total_messages=total, topics=len(topics), unassigned=len(unassigned), recovered_media=recovered, bot_configured=bool(BOT_TOKEN), source_configured=bool(SOURCE), sync_configured=bool(SYNC_CHAT))

@app.get("/api/telegram/topics")
def telegram_topics():
    topics, unassigned, total = mirror()
    return jsonify(topics=[{"id": v["id"], "name": v["name"], "count": len(v["messages"])} for v in topics.values()], unassigned_count=len(unassigned), total_messages=total)

@app.get("/api/telegram/topic/<int:topic_id>")
def telegram_topic(topic_id):
    topics, _, _ = mirror(); topic = topics.get(str(topic_id))
    if not topic: return jsonify(error="Topic introuvable"), 404
    with db() as c:
        for message in topic["messages"]:
            rows = c.execute("SELECT path FROM telegram_media WHERE message_id=? ORDER BY asset_index", (message["id"],)).fetchall()
            message["image_urls"] = ["/media/" + Path(r["path"]).name for r in rows]
            message["image_url"] = message["image_urls"][0] if message["image_urls"] else None
    return jsonify(topic=topic)

@app.post("/api/admin/login")
def admin_login():
    data = request.get_json(force=True)
    if not ADMIN_PASSWORD or not secrets.compare_digest(str(data.get("password", "")), ADMIN_PASSWORD): return jsonify(error="Accès refusé"), 403
    session["admin"] = True; return jsonify(ok=True)

@app.post("/api/admin/sync-media")
def sync_media():
    if not session.get("admin"): return jsonify(error="Admin requis"), 403
    topics, _, _ = mirror(); ids = [m["id"] for topic in topics.values() for m in topic["messages"] if m.get("media_ref")]
    recovered = sum(sync_message(i) for i in ids)
    return jsonify(ok=True, attempted=len(ids), recovered=recovered, sync_chat_configured=bool(SYNC_CHAT))

if __name__ == "__main__":
    log.info("startup bot_token_configured=%s source=%s sync_chat_configured=%s", bool(BOT_TOKEN), SOURCE, bool(SYNC_CHAT))
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
