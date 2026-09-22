import json, os, sqlite3, secrets, threading
from pathlib import Path
from datetime import datetime
import requests
from flask import Flask, jsonify, request, session, send_from_directory

BASE=Path(__file__).resolve().parent
WEB=BASE/'shop_web'; EXPORT=BASE/'result(2)(1).json'
DATA=Path(os.environ.get('DATA_DIR','/data' if Path('/data').exists() else str(BASE/'data'))); DATA.mkdir(parents=True,exist_ok=True)
MEDIA=DATA/'telegram_media'; MEDIA.mkdir(parents=True,exist_ok=True)
DB=Path(os.environ.get('SHOP_DB',str(DATA/'shop.db'))); DB.parent.mkdir(parents=True,exist_ok=True)
TOKEN=os.environ.get('BOT_TOKEN','').strip(); SOURCE=os.environ.get('TELEGRAM_SOURCE_CHAT','@Lecoinmalin34').strip(); SYNC_CHAT=os.environ.get('MEDIA_SYNC_CHAT_ID','').strip()
app=Flask(__name__,static_folder=str(WEB),static_url_path=''); app.secret_key=os.environ.get('SHOP_SECRET_KEY') or secrets.token_hex(32)

def db():
 c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

def txt(v):
 if isinstance(v,str): return v
 if isinstance(v,list): return ''.join(txt(x.get('text','') if isinstance(x,dict) else x) for x in v)
 return ''

def media_ref(m):
 for k in ('photo','file','video','animation','document'):
  if isinstance(m.get(k),str) and m[k]: return m[k]
 return ''

def load():
 try:data=json.loads(EXPORT.read_text(encoding='utf-8'))
 except Exception:return [],{},{}
 ms=data.get('messages',[]); by={m.get('id'):m for m in ms if isinstance(m.get('id'),int)}
 topics={int(m['id']):str(m['title']).strip() for m in ms if m.get('type')=='service' and m.get('action') in ('topic_created','topic_edited') and m.get('title')}
 def topic(reply):
  seen=set()
  while isinstance(reply,int) and reply not in seen:
   if reply in topics:return reply
   seen.add(reply); reply=(by.get(reply) or {}).get('reply_to_message_id')
  return None
 return ms,topics,by,topic

def init():
 with db() as c:
  c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,first_name TEXT,last_name TEXT,created_at TEXT);
  CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY,tracking TEXT UNIQUE,user_id INTEGER,status TEXT,total REAL,created_at TEXT);
  CREATE TABLE IF NOT EXISTS order_items(id INTEGER PRIMARY KEY,order_id INTEGER,product_id INTEGER,size TEXT,qty INTEGER,price REAL);
  CREATE TABLE IF NOT EXISTS telegram_media(message_id INTEGER,asset_index INTEGER DEFAULT 0,path TEXT NOT NULL,created_at TEXT,PRIMARY KEY(message_id,asset_index));''')
  cols={r['name'] for r in c.execute('PRAGMA table_info(telegram_media)')}
  if 'asset_index' not in cols:
   c.execute('ALTER TABLE telegram_media RENAME TO telegram_media_old')
   c.execute('CREATE TABLE telegram_media(message_id INTEGER,asset_index INTEGER DEFAULT 0,path TEXT NOT NULL,created_at TEXT,PRIMARY KEY(message_id,asset_index))')
   c.execute('INSERT OR IGNORE INTO telegram_media(message_id,asset_index,path,created_at) SELECT message_id,0,path,created_at FROM telegram_media_old')
init()

def mirror():
 ms,topics,by,topic=load(); out={str(k):{'id':k,'name':v,'messages':[]} for k,v in topics.items()}; un=[]
 for m in ms:
  if m.get('type')=='service' or not isinstance(m.get('id'),int):continue
  tid=topic(m.get('reply_to_message_id')); rec={'id':m['id'],'date':m.get('date',''),'text':txt(m.get('text')),'media_ref':media_ref(m),'photo':bool(m.get('photo')),'video':bool(m.get('video') or m.get('file')),'width':m.get('width'),'height':m.get('height'),'telegram_url':f'https://t.me/{SOURCE.lstrip("@").split("/")[0]}/{m["id"]}'}
  if tid is None:un.append(rec)
  else:out[str(tid)]['messages'].append(rec)
 return out,un,len(ms)

def telegram(method,**payload):
 if not TOKEN:return {'ok':False,'description':'BOT_TOKEN absent'}
 try:return requests.post(f'https://api.telegram.org/bot{TOKEN}/{method}',json=payload,timeout=60).json()
 except Exception as e:return {'ok':False,'description':str(e)}

def sync_message(message_id):
 if not TOKEN or not SYNC_CHAT:return False
 f=telegram('forwardMessage',chat_id=SYNC_CHAT,from_chat_id=SOURCE,message_id=message_id); m=f.get('result') or {}
 files=[]
 if m.get('photo'):files.append(m['photo'][-1].get('file_id'))
 for key in ('video','document','animation'):
  obj=m.get(key) or {}; thumb=obj.get('thumbnail') or obj.get('thumb') or {}
  if thumb.get('file_id'):files.append(thumb['file_id'])
 recovered=0
 for index,fid in enumerate(x for x in files if x):
  info=telegram('getFile',file_id=fid); path=(info.get('result') or {}).get('file_path')
  if not path:continue
  try:
   r=requests.get(f'https://api.telegram.org/file/bot{TOKEN}/{path}',timeout=90); r.raise_for_status(); dest=MEDIA/f'{message_id}-{index}{Path(path).suffix or ".jpg"}'; dest.write_bytes(r.content)
   with db() as c:c.execute('INSERT OR REPLACE INTO telegram_media(message_id,asset_index,path,created_at) VALUES(?,?,?,?)',(message_id,index,str(dest.relative_to(DATA)),datetime.utcnow().isoformat()))
   recovered+=1
  except Exception:continue
 return recovered

@app.get('/')
def home():return send_from_directory(str(WEB),'index.html')
@app.get('/<path:name>')
def serve_shop_file(name):return send_from_directory(str(WEB),name)
@app.get('/media/<path:name>')
def media_file(name):return send_from_directory(str(MEDIA),name)
@app.get('/health')
def health():
 topics,un,total=mirror()
 with db() as c:recovered=c.execute('SELECT COUNT(*) n FROM telegram_media').fetchone()['n']
 return jsonify(ok=True,total_messages=total,topics=len(topics),unassigned=len(un),recovered_media=recovered,bot_configured=bool(TOKEN),sync_configured=bool(SYNC_CHAT))
@app.get('/api/telegram/topics')
def telegram_topics():
 topics,un,total=mirror();return jsonify(topics=[{'id':x['id'],'name':x['name'],'count':len(x['messages'])} for x in topics.values()],unassigned_count=len(un),total_messages=total)
@app.get('/api/telegram/topic/<int:tid>')
def telegram_topic(tid):
 topics,_,_=mirror();item=topics.get(str(tid))
 if not item:return jsonify(error='Topic introuvable'),404
 with db() as c:
  for m in item['messages']:
   rows=c.execute('SELECT path FROM telegram_media WHERE message_id=? ORDER BY asset_index',(m['id'],)).fetchall();m['image_urls']=['/media/'+Path(r['path']).name for r in rows];m['image_url']=m['image_urls'][0] if m['image_urls'] else None
 return jsonify(topic={'id':item['id'],'name':item['name'],'messages':item['messages']})
@app.post('/api/admin/login')
def admin_login():
 d=request.get_json(force=True);password=os.environ.get('ADMIN_PASSWORD','')
 if not password or not secrets.compare_digest(str(d.get('password','')),password):return jsonify(error='Accès refusé'),403
 session['admin']=True;return jsonify(ok=True)
@app.post('/api/admin/sync-media')
def sync_media():
 if not session.get('admin'):return jsonify(error='Admin requis'),403
 topics,_,_=mirror();ids=[m['id'] for x in topics.values() for m in x['messages'] if m.get('media_ref')];recovered=sum(sync_message(i) for i in ids);return jsonify(ok=True,attempted=len(ids),recovered=recovered)
if __name__=='__main__':app.run(host='0.0.0.0',port=int(os.environ.get('PORT','8080')))
