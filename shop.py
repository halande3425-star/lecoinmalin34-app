import json, os, re, secrets, shutil, sqlite3, threading
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import requests
from flask import Flask, jsonify, request, send_from_directory, session

BASE_DIR = Path(__file__).resolve().parent
SHOP_WEB = BASE_DIR / "shop_web"
EXPORT = BASE_DIR / "result(2)(1).json"
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data" if Path("/data").exists() else str(BASE_DIR / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
MEDIA_DIR = DATA_DIR / "telegram_media"
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
DB = Path(os.environ.get("SHOP_DB", str(DATA_DIR / "shop.db")))
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
TELEGRAM_SOURCE_CHAT = os.environ.get("TELEGRAM_SOURCE_CHAT", "@Lecoinmalin34").strip()
MEDIA_SYNC_CHAT_ID = os.environ.get("MEDIA_SYNC_CHAT_ID", "").strip()

app = Flask(__name__, static_folder=str(SHOP_WEB), static_url_path="")
app.secret_key = os.environ.get("SHOP_SECRET_KEY") or secrets.token_hex(32)

NON_PRODUCT = ("avis", "comment passer", "commande", "paiement", "contact", "groupe", "information")

def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def text_of(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(text_of(x.get("text", "") if isinstance(x, dict) else x) for x in value)
    return ""

def media_ref(message):
    for key in ("photo", "file", "thumbnail", "video", "animation", "document"):
        value = message.get(key)
        if isinstance(value, str) and value:
            return value
    return ""

def is_media(message):
    return bool(media_ref(message) or message.get("media_type") or message.get("mime_type"))

def topic_for(reply_id, topics, messages_by_id):
    seen = set()
    current = reply_id
    while isinstance(current, int) and current not in seen:
        if current in topics:
            return current
        seen.add(current)
        current = messages_by_id.get(current, {}).get("reply_to_message_id")
    return None

def init_db():
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, first_name TEXT, last_name TEXT, created_at TEXT);
        CREATE TABLE IF NOT EXISTS categories(id INTEGER PRIMARY KEY, telegram_id INTEGER UNIQUE, name TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS products(
          id INTEGER PRIMARY KEY, telegram_message_id INTEGER UNIQUE, title TEXT NOT NULL,
          price REAL DEFAULT 0, stock INTEGER DEFAULT 0, sizes TEXT DEFAULT '[]', active INTEGER DEFAULT 1,
          category TEXT, category_id INTEGER, image_path TEXT, media_status TEXT DEFAULT 'missing',
          telegram_message_ids TEXT DEFAULT '[]', description TEXT DEFAULT '', created_at TEXT, updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY, tracking TEXT UNIQUE, user_id INTEGER, status TEXT, total REAL, created_at TEXT);
        CREATE TABLE IF NOT EXISTS order_items(id INTEGER PRIMARY KEY, order_id INTEGER, product_id INTEGER, size TEXT, qty INTEGER, price REAL);
        """)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(products)")}
        additions = {
            "category": "TEXT", "category_id": "INTEGER", "image_path": "TEXT",
            "media_status": "TEXT DEFAULT 'missing'", "telegram_message_ids": "TEXT DEFAULT '[]'",
            "description": "TEXT DEFAULT ''", "created_at": "TEXT", "updated_at": "TEXT"
        }
        for name, definition in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE products ADD COLUMN {name} {definition}")

init_db()

def load_export():
    if not EXPORT.exists():
        return [], {}, {}
    try:
        payload = json.loads(EXPORT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], {}, {}
    messages = payload.get("messages") or []
    by_id = {m.get("id"): m for m in messages if isinstance(m.get("id"), int)}
    topics = {
        int(m["id"]): str(m.get("title", "")).strip()
        for m in messages
        if m.get("type") == "service" and m.get("action") in {"topic_created", "topic_edited"} and m.get("title")
    }
    return messages, topics, by_id

def local_media_path(ref):
    if not ref:
        return ""
    candidate = (BASE_DIR / ref).resolve()
    try:
        candidate.relative_to(BASE_DIR.resolve())
    except ValueError:
        return ""
    return str(candidate) if candidate.is_file() else ""

def build_publications():
    messages, topics, by_id = load_export()
    groups = {}
    for message in messages:
        if message.get("type") == "service" or not isinstance(message.get("id"), int):
            continue
        topic_id = topic_for(message.get("reply_to_message_id"), topics, by_id)
        topic_name = topics.get(topic_id, "Sans topic")
        if any(term in topic_name.casefold() for term in NON_PRODUCT):
            continue
        body = text_of(message.get("text")).strip()
        ref = media_ref(message)
        if not body and not ref:
            continue
        # Telegram albums normally share topic and second; preserve their IDs in one product.
        second = str(message.get("date", ""))[:19]
        key = (topic_id, second) if ref else (topic_id, f"text:{message['id']}")
        groups.setdefault(key, {"topic_id": topic_id, "topic_name": topic_name, "messages": []})["messages"].append(message)
    return list(groups.values()), topics

def import_catalog():
    groups, topics = build_publications()
    now = datetime.utcnow().isoformat()
    imported = 0
    local_photos = 0
    with db() as conn:
        for telegram_id, name in topics.items():
            conn.execute("INSERT INTO categories(telegram_id,name) VALUES(?,?) ON CONFLICT(telegram_id) DO UPDATE SET name=excluded.name", (telegram_id, name))
        for group in groups:
            items = group["messages"]
            ids = [int(item["id"]) for item in items]
            primary = ids[0]
            texts = [text_of(item.get("text")).strip() for item in items if text_of(item.get("text")).strip()]
            description = "\n".join(dict.fromkeys(texts))
            title = description.splitlines()[0][:180] if description else f"Publication Telegram #{primary}"
            refs = [media_ref(item) for item in items if media_ref(item)]
            local = next((local_media_path(ref) for ref in refs if local_media_path(ref)), "")
            if local:
                local_photos += 1
            category = group["topic_name"]
            category_row = conn.execute("SELECT id FROM categories WHERE telegram_id=?", (group["topic_id"],)).fetchone()
            category_id = category_row["id"] if category_row else None
            status = "accessible" if local else ("telegram_pending" if refs else "missing")
            existing = conn.execute("SELECT id FROM products WHERE telegram_message_id=?", (primary,)).fetchone()
            values = (title, category, category_id, local or (refs[0] if refs else ""), status, json.dumps(ids, ensure_ascii=False), description, now)
            if existing:
                conn.execute("""UPDATE products SET title=?,category=?,category_id=?,image_path=?,media_status=?,
                    telegram_message_ids=?,description=?,active=1,updated_at=? WHERE id=?""", (*values, existing["id"]))
            else:
                conn.execute("""INSERT INTO products(telegram_message_id,title,category,category_id,image_path,media_status,
                    telegram_message_ids,description,active,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,1,?,?)""", (primary, *values, now))
                imported += 1
    return {"total_messages": len(load_export()[0]), "retained_publications": len(groups), "categories": len(topics), "imported": imported, "local_photos": local_photos}

IMPORT_STATS = import_catalog()

def admin_ok(): return bool(session.get("admin"))
def user_ok(): return bool(session.get("user_id"))

def api_call(method, **payload):
    if not BOT_TOKEN:
        return {"ok": False, "description": "BOT_TOKEN absent"}
    try:
        response = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", json=payload, timeout=45)
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        return {"ok": False, "description": str(exc)}

def sync_one_media(product):
    """Best-effort media sync; Telegram export paths alone are not downloadable URLs.
    MEDIA_SYNC_CHAT_ID must be a private admin chat that the bot can message."""
    if not BOT_TOKEN or not MEDIA_SYNC_CHAT_ID:
        return False
    ids = json.loads(product["telegram_message_ids"] or "[]")
    for message_id in ids:
        forwarded = api_call("forwardMessage", chat_id=MEDIA_SYNC_CHAT_ID, from_chat_id=TELEGRAM_SOURCE_CHAT, message_id=message_id)
        message = forwarded.get("result") or {}
        file_id = None
        if message.get("photo"):
            file_id = message["photo"][-1].get("file_id")
        elif message.get("video", {}).get("thumbnail"):
            file_id = message["video"]["thumbnail"].get("file_id")
        if not file_id:
            continue
        info = api_call("getFile", file_id=file_id)
        file_path = (info.get("result") or {}).get("file_path")
        if not file_path:
            continue
        try:
            raw = requests.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}", timeout=60)
            raw.raise_for_status()
            extension = Path(file_path).suffix or ".jpg"
            target = MEDIA_DIR / f"telegram-{product['telegram_message_id']}{extension}"
            target.write_bytes(raw.content)
            with db() as conn:
                conn.execute("UPDATE products SET image_path=?,media_status=?,updated_at=? WHERE id=?", (str(target.relative_to(BASE_DIR)), "accessible", datetime.utcnow().isoformat(), product["id"]))
            return True
        except (requests.RequestException, OSError):
            continue
    return False

@app.get("/")
def home(): return send_from_directory(str(SHOP_WEB), "index.html")
@app.get("/health")
def health(): return jsonify(ok=True, **IMPORT_STATS)
@app.get("/media/<path:name>")
def media(name):
    return send_from_directory(str(MEDIA_DIR), name)
@app.get("/<path:filename>")
def static_file(filename): return send_from_directory(str(SHOP_WEB), filename)

@app.get("/api/categories")
def categories():
    with db() as conn:
        rows = [dict(row) for row in conn.execute("SELECT c.*,COUNT(p.id) product_count FROM categories c LEFT JOIN products p ON p.category_id=c.id AND p.active=1 GROUP BY c.id ORDER BY c.name")]
    return jsonify(categories=rows)

@app.get("/api/products")
def products():
    with db() as conn:
        rows = [dict(row) for row in conn.execute("SELECT * FROM products WHERE active=1 ORDER BY id DESC")]
    for row in rows:
        row["sizes"] = json.loads(row.get("sizes") or "[]")
        row["telegram_message_ids"] = json.loads(row.get("telegram_message_ids") or "[]")
        row["has_accessible_photo"] = row.get("media_status") == "accessible"
        row["image_url"] = ("/media/" + quote(Path(row["image_path"]).name)) if row["has_accessible_photo"] else None
    return jsonify(products=rows, import_stats=IMPORT_STATS)

@app.post("/api/admin/sync-media")
def sync_media():
    if not admin_ok(): return jsonify(error="Admin requis"), 403
    with db() as conn: rows = [dict(row) for row in conn.execute("SELECT * FROM products WHERE media_status='telegram_pending'")]
    recovered = sum(1 for row in rows if sync_one_media(row))
    return jsonify(ok=True, attempted=len(rows), recovered=recovered)

@app.post("/api/login")
def login():
    data = request.get_json(force=True); first = (data.get("first_name") or "").strip(); last = (data.get("last_name") or "").strip()
    if not first or not last: return jsonify(error="Nom et prénom obligatoires"), 400
    with db() as conn:
        cur = conn.execute("INSERT INTO users(first_name,last_name,created_at) VALUES(?,?,?)", (first, last, datetime.utcnow().isoformat()))
        session["user_id"] = cur.lastrowid; session["name"] = f"{first} {last}"
    return jsonify(ok=True, name=session["name"])

@app.get("/api/me")
def me(): return jsonify(logged=user_ok(), name=session.get("name"))
@app.get("/api/orders")
def orders():
    if not user_ok(): return jsonify(error="Connexion requise"), 401
    with db() as conn: return jsonify(orders=[dict(row) for row in conn.execute("SELECT tracking,status,total,created_at FROM orders WHERE user_id=? ORDER BY id DESC", (session["user_id"],))])

@app.post("/api/admin/login")
def admin_login():
    data = request.get_json(force=True)
    if not ADMIN_PASSWORD or not secrets.compare_digest(str(data.get("password", "")), ADMIN_PASSWORD): return jsonify(error="Accès refusé"), 403
    session["admin"] = True
    return jsonify(ok=True)

@app.get("/api/admin/products")
def admin_products():
    if not admin_ok(): return jsonify(error="Admin requis"), 403
    with db() as conn: return jsonify(products=[dict(row) for row in conn.execute("SELECT * FROM products ORDER BY id DESC")])

@app.post("/api/admin/product")
def admin_product():
    if not admin_ok(): return jsonify(error="Admin requis"), 403
    data = request.get_json(force=True); sizes = json.dumps([str(x).strip() for x in data.get("sizes", []) if str(x).strip()], ensure_ascii=False)
    with db() as conn:
        if data.get("id"):
            conn.execute("UPDATE products SET title=?,price=?,stock=?,sizes=?,active=?,category=?,image_path=?,description=?,updated_at=? WHERE id=?", (data.get("title", ""), float(data.get("price", 0)), int(data.get("stock", 0)), sizes, int(bool(data.get("active", True))), data.get("category", ""), data.get("image_path", ""), data.get("description", ""), datetime.utcnow().isoformat(), int(data["id"])))
        else:
            conn.execute("INSERT INTO products(title,price,stock,sizes,active,category,image_path,description,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (data.get("title", ""), float(data.get("price", 0)), int(data.get("stock", 0)), sizes, 1, data.get("category", ""), data.get("image_path", ""), data.get("description", ""), datetime.utcnow().isoformat(), datetime.utcnow().isoformat()))
    return jsonify(ok=True)

@app.get("/api/admin/orders")
def admin_orders():
    if not admin_ok(): return jsonify(error="Admin requis"), 403
    with db() as conn: return jsonify(orders=[dict(row) for row in conn.execute("SELECT o.*,u.first_name,u.last_name FROM orders o JOIN users u ON u.id=o.user_id ORDER BY o.id DESC")])

@app.post("/api/admin/order-status")
def admin_status():
    if not admin_ok(): return jsonify(error="Admin requis"), 403
    data = request.get_json(force=True); allowed = ["Commande reçue", "Paiement à confirmer", "Payée", "Préparation", "Expédiée", "Livrée", "Annulée"]
    if data.get("status") not in allowed: return jsonify(error="Statut invalide"), 400
    with db() as conn: conn.execute("UPDATE orders SET status=? WHERE id=?", (data["status"], int(data["id"])))
    return jsonify(ok=True)

@app.post("/api/order")
def order():
    if not user_ok(): return jsonify(error="Connexion requise"), 401
    items = request.get_json(force=True).get("items") or []
    if not items: return jsonify(error="Panier vide"), 400
    total = 0; normalized = []
    with db() as conn:
        for item in items:
            product = conn.execute("SELECT * FROM products WHERE id=? AND active=1", (int(item["product_id"]),)).fetchone()
            if not product: continue
            quantity = max(1, int(item.get("qty", 1))); total += float(product["price"]) * quantity; normalized.append((product, quantity, str(item.get("size", ""))))
        if not normalized: return jsonify(error="Panier vide"), 400
        tracking = "LCM34-" + datetime.now().strftime("%y%m%d") + "-" + secrets.token_hex(3).upper()
        cur = conn.execute("INSERT INTO orders(tracking,user_id,status,total,created_at) VALUES(?,?,?,?,?)", (tracking, session["user_id"], "Commande reçue", total, datetime.utcnow().isoformat()))
        for product, quantity, size in normalized: conn.execute("INSERT INTO order_items(order_id,product_id,size,qty,price) VALUES(?,?,?,?,?)", (cur.lastrowid, product["id"], size, quantity, product["price"]))
    return jsonify(ok=True, tracking=tracking, total=total, telegram="https://t.me/lecoinmalin34w")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
