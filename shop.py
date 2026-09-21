import os, sqlite3, secrets, json, re
from pathlib import Path
from datetime import datetime
from flask import Flask, request, jsonify, session, send_from_directory

BASE_DIR=Path(__file__).resolve().parent
SHOP_WEB=BASE_DIR/'shop_web'
EXPORT=BASE_DIR/'result(2)(1).json'
DB=Path(os.environ.get('SHOP_DB','/data/shop.db' if Path('/data').exists() else str(BASE_DIR/'shop.db')))
DB.parent.mkdir(parents=True,exist_ok=True)
app=Flask(__name__,static_folder=str(SHOP_WEB),static_url_path='')
app.secret_key=os.environ.get('SHOP_SECRET_KEY') or secrets.token_hex(32)
ADMIN_PASSWORD=os.environ.get('ADMIN_PASSWORD','')
TELEGRAM_DM='lecoinmalin34w'
NON_PRODUCT=('avis','commande','comment passer','paiement','contact','information','groupe')

def db():
 c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

def text_value(v):
 if isinstance(v,str): return v
 if isinstance(v,list): return ''.join(text_value(x.get('text','') if isinstance(x,dict) else x) for x in v)
 return ''

def has_media(m):
 return any(m.get(k) not in (None,'',[],{}) for k in ('photo','file','thumbnail','video','animation','audio','voice','document','media_type','mime_type'))

def slug(s): return re.sub(r'[^a-z0-9]+','-',s.casefold()).strip('-') or 'topic'

def init():
 with db() as c:
  c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,first_name TEXT,last_name TEXT,created_at TEXT);
  CREATE TABLE IF NOT EXISTS categories(id INTEGER PRIMARY KEY,telegram_id INTEGER UNIQUE,name TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY,telegram_message_id INTEGER UNIQUE,title TEXT,price REAL DEFAULT 0,stock INTEGER DEFAULT 0,sizes TEXT DEFAULT '[]',active INTEGER DEFAULT 1,category TEXT,category_id INTEGER,image_path TEXT,media_status TEXT DEFAULT 'non_accessible',telegram_message_ids TEXT DEFAULT '[]',description TEXT DEFAULT '');
  CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY,tracking TEXT UNIQUE,user_id INTEGER,status TEXT,total REAL,created_at TEXT);
  CREATE TABLE IF NOT EXISTS order_items(id INTEGER PRIMARY KEY,order_id INTEGER,product_id INTEGER,size TEXT,qty INTEGER,price REAL);''')
  cols={r['name'] for r in c.execute('PRAGMA table_info(products)')}
  for name,definition in [('category','TEXT'),('category_id','INTEGER'),('image_path','TEXT'),('media_status',"TEXT DEFAULT 'non_accessible'"),('telegram_message_ids',"TEXT DEFAULT '[]'"),('description',"TEXT DEFAULT ''")]:
   if name not in cols: c.execute(f'ALTER TABLE products ADD COLUMN {name} {definition}')
init()

def import_export():
 if not EXPORT.exists(): return {'imported':0,'categories':0,'accessible_photos':0}
 try: data=json.loads(EXPORT.read_text(encoding='utf-8'))
 except Exception: return {'imported':0,'categories':0,'accessible_photos':0}
 messages=data.get('messages',[]); topics={}
 for m in messages:
  if m.get('type')=='service' and m.get('action') in ('topic_created','topic_edited') and m.get('title'):
   topics[int(m['id'])]=str(m['title']).strip()
 # Group media posted in the same topic and same timestamp second; this avoids
 # turning a Telegram album into many duplicate cards without guessing products.
 groups={}
 for m in messages:
  if m.get('type')=='service' or not isinstance(m.get('id'),int): continue
  rid=m.get('reply_to_message_id'); tid=rid if isinstance(rid,int) and rid in topics else None
  name=topics.get(tid,'Sans topic')
  if any(x in name.casefold() for x in NON_PRODUCT): continue
  if not (has_media(m) or text_value(m.get('text')).strip()): continue
  stamp=m.get('date',''); key=(tid,stamp[:19] if isinstance(stamp,str) else '', bool(text_value(m.get('text')).strip()))
  # Text messages anchor a product; media-only messages sharing an exact second form one album.
  groups.setdefault(key,[]).append(m)
 imported=0; accessible=0
 with db() as c:
  for tid,name in topics.items(): c.execute('INSERT OR IGNORE INTO categories(telegram_id,name) VALUES(?,?)',(tid,name))
  for (tid,stamp,_),items in groups.items():
   ids=[int(x['id']) for x in items]; primary=ids[0]; m=items[0]
   raw=text_value(m.get('text')).strip(); title=raw.split('\n')[0][:180] if raw else ''
   paths=[]
   for x in items:
    p=x.get('photo') or x.get('file') or ''
    if isinstance(p,str): paths.append(p)
   local=[p for p in paths if (BASE_DIR/p).is_file()]
   if local: accessible+=1
   cat=topics.get(tid,'Sans topic'); cid=c.execute('SELECT id FROM categories WHERE telegram_id=?',(tid,)).fetchone()
   c.execute('''INSERT OR IGNORE INTO products(telegram_message_id,title,stock,active,category,category_id,image_path,media_status,telegram_message_ids,description)
    VALUES(?,?,?,?,?,?,?,?,?,?)''',(primary,title,0,1,cat,cid['id'] if cid else None,local[0] if local else (paths[0] if paths else ''),'accessible' if local else ('telegram_pending' if paths else 'missing'),json.dumps(ids),raw))
   if c.execute('SELECT changes()').fetchone()[0]: imported+=1
 return {'imported':imported,'categories':len(topics),'accessible_photos':accessible}
IMPORT_STATS=import_export()

def admin_ok(): return bool(session.get('admin'))
def user_ok(): return bool(session.get('user_id'))
@app.get('/')
def home(): return send_from_directory(str(SHOP_WEB),'index.html')
@app.get('/health')
def health(): return jsonify(ok=True,**IMPORT_STATS)
@app.get('/<path:filename>')
def static_file(filename): return send_from_directory(str(SHOP_WEB),filename)
@app.get('/api/categories')
def categories():
 with db() as c: return jsonify(categories=[dict(r) for r in c.execute('SELECT c.*,COUNT(p.id) product_count FROM categories c LEFT JOIN products p ON p.category_id=c.id AND p.active=1 GROUP BY c.id ORDER BY c.name')])
@app.get('/api/products')
def products():
 with db() as c: rows=[dict(r) for r in c.execute('SELECT * FROM products WHERE active=1 ORDER BY id DESC')]
 for r in rows:
  r['sizes']=json.loads(r.get('sizes') or '[]'); r['telegram_message_ids']=json.loads(r.get('telegram_message_ids') or '[]'); r['has_accessible_photo']=r.get('media_status')=='accessible'
 return jsonify(products=rows,import_stats=IMPORT_STATS)
@app.post('/api/login')
def login():
 d=request.get_json(force=True); first=(d.get('first_name') or '').strip(); last=(d.get('last_name') or '').strip()
 if not first or not last:return jsonify(error='Nom et prénom obligatoires'),400
 with db() as c:
  cur=c.execute('INSERT INTO users(first_name,last_name,created_at) VALUES(?,?,?)',(first,last,datetime.utcnow().isoformat())); session['user_id']=cur.lastrowid; session['name']=f'{first} {last}'
 return jsonify(ok=True,name=session['name'])
@app.get('/api/me')
def me(): return jsonify(logged=user_ok(),name=session.get('name'))
@app.post('/api/order')
def order():
 if not user_ok(): return jsonify(error='Connexion requise'),401
 items=(request.get_json(force=True).get('items') or []); total=0; normalized=[]
 with db() as c:
  for i in items:
   p=c.execute('SELECT * FROM products WHERE id=? AND active=1',(int(i['product_id']),)).fetchone()
   if not p: continue
   q=max(1,int(i.get('qty',1))); total+=float(p['price'])*q; normalized.append((p,q,str(i.get('size',''))))
  if not normalized:return jsonify(error='Panier vide'),400
  tracking='LCM34-'+datetime.now().strftime('%y%m%d')+'-'+secrets.token_hex(3).upper(); cur=c.execute('INSERT INTO orders(tracking,user_id,status,total,created_at) VALUES(?,?,?,?,?)',(tracking,session['user_id'],'Commande reçue',total,datetime.utcnow().isoformat())); oid=cur.lastrowid
  for p,q,s in normalized:c.execute('INSERT INTO order_items(order_id,product_id,size,qty,price) VALUES(?,?,?,?,?)',(oid,p['id'],s,q,p['price']))
 return jsonify(ok=True,tracking=tracking,total=total,telegram=f'https://t.me/{TELEGRAM_DM}')
@app.get('/api/orders')
def orders():
 if not user_ok():return jsonify(error='Connexion requise'),401
 with db() as c:return jsonify(orders=[dict(r) for r in c.execute('SELECT tracking,status,total,created_at FROM orders WHERE user_id=? ORDER BY id DESC',(session['user_id'],))])
@app.post('/api/admin/login')
def admin_login():
 d=request.get_json(force=True)
 if not ADMIN_PASSWORD or not secrets.compare_digest(str(d.get('password','')),ADMIN_PASSWORD):return jsonify(error='Accès refusé'),403
 session['admin']=True;return jsonify(ok=True)
@app.get('/api/admin/products')
def admin_products():
 if not admin_ok():return jsonify(error='Admin requis'),403
 with db() as c:return jsonify(products=[dict(r) for r in c.execute('SELECT * FROM products ORDER BY id DESC')])
@app.post('/api/admin/product')
def admin_product():
 if not admin_ok():return jsonify(error='Admin requis'),403
 d=request.get_json(force=True); sizes=json.dumps([str(x).strip() for x in d.get('sizes',[]) if str(x).strip()],ensure_ascii=False)
 with db() as c:
  if d.get('id'): c.execute('UPDATE products SET title=?,price=?,stock=?,sizes=?,active=?,category=?,image_path=?,description=? WHERE id=?',(d.get('title',''),float(d.get('price',0)),int(d.get('stock',0)),sizes,int(bool(d.get('active',True))),d.get('category',''),d.get('image_path',''),d.get('description',''),int(d['id'])))
  else:c.execute('INSERT INTO products(title,price,stock,sizes,active,category,image_path,description) VALUES(?,?,?,?,?,?,?,?)',(d.get('title',''),float(d.get('price',0)),int(d.get('stock',0)),sizes,1,d.get('category',''),d.get('image_path',''),d.get('description','')))
 return jsonify(ok=True)
@app.get('/api/admin/orders')
def admin_orders():
 if not admin_ok():return jsonify(error='Admin requis'),403
 with db() as c:return jsonify(orders=[dict(r) for r in c.execute('SELECT o.*,u.first_name,u.last_name FROM orders o JOIN users u ON u.id=o.user_id ORDER BY o.id DESC')])
@app.post('/api/admin/order-status')
def admin_status():
 if not admin_ok():return jsonify(error='Admin requis'),403
 d=request.get_json(force=True); allowed=['Commande reçue','Paiement à confirmer','Payée','Préparation','Expédiée','Livrée','Annulée']
 if d.get('status') not in allowed:return jsonify(error='Statut invalide'),400
 with db() as c:c.execute('UPDATE orders SET status=? WHERE id=?',(d['status'],int(d['id'])))
 return jsonify(ok=True)
if __name__=='__main__': app.run(host='0.0.0.0',port=int(os.environ.get('PORT',8080)))
