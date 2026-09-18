
import os, sqlite3, secrets, hashlib, json
from flask import Flask, request, jsonify, session, send_from_directory
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent
SHOP_WEB = BASE_DIR / "shop_web"
app = Flask(__name__, static_folder=str(SHOP_WEB), static_url_path="")
app.secret_key = os.environ.get("SHOP_SECRET_KEY", secrets.token_hex(32))
DB = Path(os.environ.get("SHOP_DB", "/data/shop.db" if Path("/data").exists() else str(BASE_DIR / "shop.db")))
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
TELEGRAM_DM = "lecoinmalin34w"

def db():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

def init():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, first_name TEXT, last_name TEXT, created_at TEXT);
        CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY, telegram_message_id INTEGER UNIQUE, title TEXT, price REAL DEFAULT 0, stock INTEGER DEFAULT 0, sizes TEXT DEFAULT '[]', active INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY, tracking TEXT UNIQUE, user_id INTEGER, status TEXT, total REAL, created_at TEXT);
        CREATE TABLE IF NOT EXISTS order_items(id INTEGER PRIMARY KEY, order_id INTEGER, product_id INTEGER, size TEXT, qty INTEGER, price REAL);
        """)
init()

def admin_ok(): return bool(session.get("admin"))
def user_ok(): return bool(session.get("user_id"))

@app.get("/")
def home(): return send_from_directory(str(SHOP_WEB),"index.html")

@app.get("/health")
def health(): return jsonify(ok=True, app="lecoinmalin34-app")

@app.post("/api/login")
def login():
    d=request.get_json(force=True); first=(d.get("first_name") or "").strip(); last=(d.get("last_name") or "").strip()
    if not first or not last: return jsonify(error="Nom et prénom obligatoires"),400
    with db() as c:
        cur=c.execute("INSERT INTO users(first_name,last_name,created_at) VALUES(?,?,?)",(first,last,datetime.utcnow().isoformat()))
        session["user_id"]=cur.lastrowid; session["name"]=f"{first} {last}"
    return jsonify(ok=True,name=session["name"])

@app.get("/api/me")
def me(): return jsonify(logged=user_ok(),name=session.get("name"))

@app.get("/api/products")
def products():
    with db() as c:
        rows=[dict(x) for x in c.execute("SELECT * FROM products WHERE active=1 ORDER BY id DESC")]
    for x in rows: x["sizes"]=json.loads(x["sizes"] or "[]")
    return jsonify(products=rows)

@app.post("/api/order")
def order():
    if not user_ok(): return jsonify(error="Connexion requise"),401
    d=request.get_json(force=True); items=d.get("items") or []
    if not items: return jsonify(error="Panier vide"),400
    tracking="LCM34-"+datetime.now().strftime("%y%m%d")+"-"+secrets.token_hex(3).upper()
    total=0; normalized=[]
    with db() as c:
        for i in items:
            p=c.execute("SELECT * FROM products WHERE id=? AND active=1",(int(i["product_id"]),)).fetchone()
            if not p: continue
            qty=max(1,int(i.get("qty",1))); size=str(i.get("size",""))
            sizes=json.loads(p["sizes"] or "[]")
            if sizes and size not in sizes: return jsonify(error=f"Taille indisponible: {size}"),400
            total += float(p["price"])*qty; normalized.append((p,qty,size))
        cur=c.execute("INSERT INTO orders(tracking,user_id,status,total,created_at) VALUES(?,?,?,?,?)",(tracking,session["user_id"],"Commande reçue",total,datetime.utcnow().isoformat()))
        oid=cur.lastrowid
        for p,qty,size in normalized:
            c.execute("INSERT INTO order_items(order_id,product_id,size,qty,price) VALUES(?,?,?,?,?)",(oid,p["id"],size,qty,p["price"]))
    return jsonify(ok=True,tracking=tracking,total=total,telegram=f"https://t.me/{TELEGRAM_DM}")

@app.get("/api/orders")
def orders():
    if not user_ok(): return jsonify(error="Connexion requise"),401
    with db() as c: rows=[dict(x) for x in c.execute("SELECT tracking,status,total,created_at FROM orders WHERE user_id=? ORDER BY id DESC",(session["user_id"],))]
    return jsonify(orders=rows)

@app.post("/api/admin/login")
def admin_login():
    d=request.get_json(force=True)
    if not ADMIN_PASSWORD or not secrets.compare_digest(str(d.get("password","")),ADMIN_PASSWORD): return jsonify(error="Accès refusé"),403
    session["admin"]=True; return jsonify(ok=True)

@app.get("/api/admin/products")
def admin_products():
    if not admin_ok(): return jsonify(error="Admin requis"),403
    with db() as c: rows=[dict(x) for x in c.execute("SELECT * FROM products ORDER BY id DESC")]
    for x in rows: x["sizes"]=json.loads(x["sizes"] or "[]")
    return jsonify(products=rows)

@app.post("/api/admin/product")
def admin_product():
    if not admin_ok(): return jsonify(error="Admin requis"),403
    d=request.get_json(force=True); pid=d.get("id")
    sizes=json.dumps([str(x).strip() for x in d.get("sizes",[]) if str(x).strip()],ensure_ascii=False)
    with db() as c:
        if pid:
            c.execute("UPDATE products SET title=?,price=?,stock=?,sizes=?,active=? WHERE id=?",
                      (d.get("title",""),float(d.get("price",0)),int(d.get("stock",0)),sizes,int(bool(d.get("active",True))),int(pid)))
        else:
            c.execute("INSERT INTO products(telegram_message_id,title,price,stock,sizes,active) VALUES(?,?,?,?,?,1)",
                      (d.get("telegram_message_id"),d.get("title",""),float(d.get("price",0)),int(d.get("stock",0)),sizes))
    return jsonify(ok=True)

@app.get("/api/admin/orders")
def admin_orders():
    if not admin_ok(): return jsonify(error="Admin requis"),403
    with db() as c:
        rows=[dict(x) for x in c.execute("""SELECT o.id,o.tracking,o.status,o.total,o.created_at,u.first_name,u.last_name
        FROM orders o JOIN users u ON u.id=o.user_id ORDER BY o.id DESC""")]
    return jsonify(orders=rows)

@app.post("/api/admin/order-status")
def admin_status():
    if not admin_ok(): return jsonify(error="Admin requis"),403
    d=request.get_json(force=True)
    allowed=["Commande reçue","Paiement à confirmer","Payée","Préparation","Expédiée","Livrée","Annulée"]
    if d.get("status") not in allowed: return jsonify(error="Statut invalide"),400
    with db() as c: c.execute("UPDATE orders SET status=? WHERE id=?",(d["status"],int(d["id"])))
    return jsonify(ok=True)

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",8080)))
