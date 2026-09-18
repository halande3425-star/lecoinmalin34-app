import json, os, time, threading, requests, io, gc
from flask import Flask, send_from_directory, jsonify

TOKEN = os.environ.get("BOT_TOKEN", "").strip()
SOURCE_CHAT = os.environ.get("SOURCE_CHAT", "@Lecoinmalin34").strip()
PUBLIC_DOMAIN = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip()
WEBAPP_URL = os.environ.get("WEBAPP_URL", "").strip()
ORDER_URL = os.environ.get("ORDER_URL", "https://t.me/Lecoinmalin34/76").strip()
REVIEWS_URL = os.environ.get("REVIEWS_URL", "https://t.me/Lecoinmalin34/5").strip()
SOURCE_GROUP_URL = os.environ.get("SOURCE_GROUP_URL", "https://t.me/Lecoinmalin34").strip()
if not WEBAPP_URL and PUBLIC_DOMAIN:
    WEBAPP_URL = "https://" + PUBLIC_DOMAIN

API = f"https://api.telegram.org/bot{TOKEN}"
PAGE_SIZE = 20
SOURCE_CHAT_ID = None
BUILD_VERSION = "APP-V1-AUTO"

# --- V5 : marques séparées Homme/Femme + Luxe + recherche de marque/modèle ---
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
VISION_MODEL = os.environ.get("VISION_MODEL", "gpt-5.6-luna").strip()

# V5.18 : vision locale gratuite (aucune API OpenAI).
# Le modèle est téléchargé automatiquement au 1er lancement de /indeximages.
LOCAL_VISION_MODEL = os.environ.get("LOCAL_VISION_MODEL", "openai/clip-vit-base-patch32").strip()
LOCAL_VISION_MIN_SCORE = float(os.environ.get("LOCAL_VISION_MIN_SCORE", "0.10"))
LOCAL_VISION = None
LOCAL_VISION_LOCK = threading.RLock()
INDEX_OWNER_FILE = None

BRAND_TOPIC_IDS = {"64", "3616"}  # Chaussures Homme/Femme + Chaussures de luxe
SEARCH_WAITING = {}  # chat_id -> topic_id
INDEXING_TOPICS = set()
CANCEL_SCAN_TOPICS = set()
INDEXING_LOCK = threading.RLock()

REGULAR_BRANDS = [
    "TN", "Nike", "Jordan", "On Running", "ASICS", "New Balance",
    "Adidas", "Puma", "Salomon", "Autres / À vérifier"
]
LUXURY_BRANDS = [
    "Dior", "Louis Vuitton", "Hermès", "Prada", "Chanel", "Gucci",
    "Balenciaga", "Louboutin", "Autres / À vérifier"
]


SEARCH_ALIASES = {
    "64": {
        "tn": "TN", "tn3": "TN", "air max tn": "TN", "air max": "Nike", "vapormax": "Nike",
        "jordan": "Jordan", "air jordan": "Jordan",
        "samba": "Adidas", "gazelle": "Adidas", "campus": "Adidas",
        "gel nyc": "ASICS", "gel-kayano": "ASICS", "kayano": "ASICS",
        "cloud": "On Running", "cloudtilt": "On Running", "cloudmonster": "On Running",
        "new balance": "New Balance", "nb": "New Balance",
        "salomon": "Salomon", "ugg": "UGG", "crocs": "Crocs",
        "reebok": "Reebok", "converse": "Converse", "lacoste": "Lacoste",
        "puma": "Puma", "adidas": "Adidas", "nike": "Nike", "asics": "ASICS",
        "vans": "Vans", "skechers": "Skechers", "hoka": "Hoka",
        "saucony": "Saucony", "mizuno": "Mizuno", "under armour": "Under Armour",
        "veja": "Veja", "timberland": "Timberland",
    },
    "3616": {
        "b22": "Dior", "b30": "Dior", "dior": "Dior",
        "lv": "Louis Vuitton", "lv runner": "Louis Vuitton", "louis vuitton": "Louis Vuitton",
        "prada cup": "Prada", "prada": "Prada",
        "chanel": "Chanel", "hermes": "Hermès", "hermès": "Hermès",
        "gucci": "Gucci", "balenciaga": "Balenciaga", "louboutin": "Louboutin",
        "versace": "Versace", "valentino": "Valentino", "givenchy": "Givenchy",
        "moncler": "Moncler", "fendi": "Fendi", "bottega": "Bottega Veneta",
        "mcqueen": "Alexander McQueen", "alexander mcqueen": "Alexander McQueen",
        "dolce": "Dolce & Gabbana", "gabbana": "Dolce & Gabbana", "d&g": "Dolce & Gabbana",
        "burberry": "Burberry", "loewe": "Loewe",
        "amiri": "Amiri", "miu miu": "Miu Miu", "margiela": "Maison Margiela",
        "maison margiela": "Maison Margiela", "rick owens": "Rick Owens",
        "golden goose": "Golden Goose", "off white": "Off-White", "off-white": "Off-White",
    }
}

def find_brand_from_query(query, tid):
    """Trouve une marque depuis un nom de marque ou un modèle tapé par le client."""
    tid = str(tid)
    q = (query or "").strip().casefold()
    if not q:
        return None
    allowed = REGULAR_BRANDS if tid == "64" else LUXURY_BRANDS

    # nom exact / partiel
    for brand in allowed:
        if brand == "Autres / À vérifier":
            continue
        if q == brand.casefold() or q in brand.casefold() or brand.casefold() in q:
            return brand

    # alias de modèle
    aliases = SEARCH_ALIASES.get(tid, {})
    # plus long alias d'abord, pour éviter "lv" avant "lv runner"
    for alias in sorted(aliases, key=len, reverse=True):
        if alias.casefold() in q:
            return aliases[alias]

    # réutilise la détection texte existante
    return _canonical_brand_from_text(query, tid)

import shutil
from pathlib import Path
from threading import RLock

BASE_CATALOG = Path("catalog.json")
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
if not DATA_DIR.exists():
    DATA_DIR = Path(".")
INDEX_OWNER_FILE = DATA_DIR / "index_owner_chat_id.txt"
RUNTIME_CATALOG = DATA_DIR / "catalog_runtime.json"
CATALOG_LOCK = RLock()

BASE_BRANDS = Path("brand_catalog.json")
RUNTIME_BRANDS = DATA_DIR / "brand_catalog_runtime.json"
BRAND_LOCK = RLock()

def _load_brands():
    source = RUNTIME_BRANDS if RUNTIME_BRANDS.exists() else BASE_BRANDS
    if source.exists():
        with open(source, encoding="utf-8") as f:
            return json.load(f)
    return {"64": {}, "3616": {}}

BRAND_CATALOG = _load_brands()

def save_brands():
    with BRAND_LOCK:
        tmp = RUNTIME_BRANDS.with_suffix(".tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(BRAND_CATALOG, f, ensure_ascii=False, indent=2)
        tmp.replace(RUNTIME_BRANDS)

def _load_catalog():
    # V5.19 : le runtime reste prioritaire, mais les nouveaux topics livrés
    # dans catalog.json sont fusionnés automatiquement au démarrage.
    with open(BASE_CATALOG, encoding="utf-8") as f:
        base = json.load(f)

    if not RUNTIME_CATALOG.exists():
        return base

    try:
        with open(RUNTIME_CATALOG, encoding="utf-8") as f:
            runtime = json.load(f)
    except Exception:
        runtime = {}

    for tid, seed in base.items():
        if tid not in runtime:
            runtime[tid] = seed
        else:
            # Garde les médias/counts runtime mais prend le vrai titre du seed
            # quand le runtime n'avait qu'un placeholder.
            title = str(runtime[tid].get("title") or "")
            if title.startswith("Nouveau catalogue "):
                runtime[tid]["title"] = seed.get("title", title)

    return runtime

CATALOG = _load_catalog()

with open("menu.json", encoding="utf-8") as f:
    MENU = json.load(f)

def configured_topic_ids():
    ids = set()
    for g in MENU.get("groups", []):
        for tid in g.get("topics", []):
            ids.add(str(tid))
    return ids

def topics_for_group(group):
    """
    V5.19 : les topics connus gardent leur groupe.
    Tout nouveau topic Telegram détecté est automatiquement visible dans 'Voir plus'.
    """
    topics = [str(x) for x in group.get("topics", [])]
    if group.get("id") == "more":
        known = configured_topic_ids()
        extras = [str(tid) for tid in CATALOG.keys() if str(tid) not in known]
        extras.sort(key=lambda x: int(x) if x.isdigit() else 10**12)
        topics.extend(extras)
    return topics

def save_catalog():
    """Persist the live catalogue safely. Use a Railway Volume mounted at /data."""
    with CATALOG_LOCK:
        tmp = RUNTIME_CATALOG.with_suffix(".tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(CATALOG, f, ensure_ascii=False, indent=2)
        tmp.replace(RUNTIME_CATALOG)

def resolve_source_chat_id():
    global SOURCE_CHAT_ID
    try:
        r = requests.post(f"{API}/getChat", json={"chat_id": SOURCE_CHAT}, timeout=30).json()
        if r.get("ok"):
            SOURCE_CHAT_ID = r["result"]["id"]
            print(f"SOURCE OK: {SOURCE_CHAT} -> {SOURCE_CHAT_ID}", flush=True)
        else:
            print("SOURCE getChat error:", r, flush=True)
    except Exception as e:
        print("SOURCE resolve error:", repr(e), flush=True)

def is_source_message(m):
    chat = m.get("chat") or {}
    cid = chat.get("id")
    if SOURCE_CHAT_ID is not None:
        return cid == SOURCE_CHAT_ID
    username = chat.get("username")
    if SOURCE_CHAT.startswith("@") and username:
        return ("@" + username).lower() == SOURCE_CHAT.lower()
    return False

def message_kind(m):
    if m.get("photo"):
        return "photo"
    if m.get("video") or m.get("animation") or m.get("video_note"):
        return "video"
    doc = m.get("document") or {}
    mime = (doc.get("mime_type") or "").lower()
    if mime.startswith("image/"):
        return "photo"
    if mime.startswith("video/"):
        return "video"
    if m.get("text"):
        return "text"
    return None

def _canonical_brand_from_text(text, topic_id):
    """Détection gratuite depuis le texte/caption avant d'appeler la vision."""
    t = (text or "").lower()
    replacements = [
        (["new balance", "nb "], "New Balance"),
        (["on running", "on cloud", "cloudtilt", "cloudmonster", "cloud 5"], "On Running"),
        ([" tn ", " tn3 ", "air max tn"], "TN"),
        (["nike", "air max", "vapormax"], "Nike"),
        (["jordan", "air jordan"], "Jordan"),
        (["adidas", "samba", "gazelle", "campus"], "Adidas"),
        (["asics", "gel-kayano", "gel nyc"], "ASICS"),
        (["puma"], "Puma"), (["salomon"], "Salomon"), (["ugg"], "UGG"),
        (["crocs"], "Crocs"), (["reebok"], "Reebok"), (["converse"], "Converse"),
        (["lacoste"], "Lacoste"), (["vans"], "Vans"), (["skechers"], "Skechers"),
        (["hoka"], "Hoka"), (["saucony"], "Saucony"), (["mizuno"], "Mizuno"),
        (["under armour"], "Under Armour"), (["veja"], "Veja"), (["timberland"], "Timberland"),
        (["dior", "b22", "b30"], "Dior"),
        (["louis vuitton", " lv ", "lv runner"], "Louis Vuitton"),
        (["prada", "prada cup"], "Prada"),
        (["chanel"], "Chanel"), (["hermès", "hermes"], "Hermès"),
        (["gucci"], "Gucci"), (["balenciaga"], "Balenciaga"),
        (["louboutin"], "Louboutin"), (["versace"], "Versace"),
        (["valentino"], "Valentino"), (["givenchy"], "Givenchy"),
        (["moncler"], "Moncler"), (["fendi"], "Fendi"),
        (["bottega"], "Bottega Veneta"), (["mcqueen", "alexander mcqueen"], "Alexander McQueen"),
        (["dolce", "gabbana", "d&g"], "Dolce & Gabbana"),
        (["burberry"], "Burberry"), (["loewe"], "Loewe"),
        (["amiri"], "Amiri"), (["miu miu"], "Miu Miu"),
        (["margiela", "maison margiela"], "Maison Margiela"),
        (["rick owens"], "Rick Owens"), (["golden goose"], "Golden Goose"),
        (["off-white", "off white"], "Off-White"),
    ]
    allowed = REGULAR_BRANDS if str(topic_id) == "64" else LUXURY_BRANDS
    padded = f" {t} "
    for keys, brand in replacements:
        if brand not in allowed:
            continue
        if any(k in padded for k in keys):
            return brand
    return None

def _telegram_visual_file_id(m):
    """Photo HD ou miniature d'une vidéo."""
    photos = m.get("photo") or []
    if photos:
        return photos[-1].get("file_id")
    for field in ("video", "animation", "video_note", "document"):
        obj = m.get(field) or {}
        thumb = obj.get("thumbnail") or obj.get("thumb") or {}
        if thumb.get("file_id"):
            return thumb["file_id"]
    return None

def _download_telegram_file(file_id):
    if not file_id:
        return None, None
    info = api("getFile", file_id=file_id)
    if not info.get("ok"):
        return None, None
    path = (info.get("result") or {}).get("file_path")
    if not path:
        return None, None
    try:
        r = requests.get(f"https://api.telegram.org/file/bot{TOKEN}/{path}", timeout=60)
        r.raise_for_status()
        ext = Path(path).suffix.lower()
        mime = "image/png" if ext == ".png" else "image/jpeg"
        return r.content, mime
    except Exception as e:
        print("VISION download:", repr(e), flush=True)
        return None, None

def _extract_response_text(payload):
    # Responses API also exposes output_text in some SDKs, but raw HTTP returns output[].
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"].strip()
    pieces = []
    for item in payload.get("output", []):
        for content in item.get("content", []) if isinstance(item, dict) else []:
            if isinstance(content, dict) and content.get("type") == "output_text":
                pieces.append(content.get("text", ""))
    return " ".join(pieces).strip()


VISION_LABELS = {
    # Homme / Femme
    "TN": "Nike Air Max Plus TN sneakers",
    "Nike": "Nike sneakers",
    "Jordan": "Air Jordan sneakers",
    "On Running": "On Running Cloud sneakers",
    "ASICS": "ASICS Gel sneakers",
    "New Balance": "New Balance sneakers",
    "Adidas": "Adidas sneakers",
    "Puma": "Puma sneakers",
    "Salomon": "Salomon sneakers",
    # Luxe
    "Dior": "Dior sneakers B22 B30",
    "Louis Vuitton": "Louis Vuitton sneakers LV Trainer",
    "Hermès": "Hermes luxury sneakers",
    "Prada": "Prada luxury sneakers",
    "Chanel": "Chanel luxury sneakers",
    "Gucci": "Gucci luxury sneakers",
    "Balenciaga": "Balenciaga sneakers",
    "Louboutin": "Christian Louboutin sneakers",
}

LUXURY_SET = {"Dior","Louis Vuitton","Hermès","Prada","Chanel","Gucci","Balenciaga","Louboutin"}
REGULAR_SET = {"TN","Nike","Jordan","On Running","ASICS","New Balance","Adidas","Puma","Salomon"}

def _get_local_vision():
    """Charge CLIP localement uniquement quand une indexation image est lancée."""
    global LOCAL_VISION
    with LOCAL_VISION_LOCK:
        if LOCAL_VISION is not None:
            return LOCAL_VISION
        print(f"VISION LOCALE ⏳ chargement {LOCAL_VISION_MODEL}", flush=True)
        try:
            from transformers import pipeline
            LOCAL_VISION = pipeline(
                "zero-shot-image-classification",
                model=LOCAL_VISION_MODEL,
                device=-1,
            )
            print("VISION LOCALE ✅ modèle chargé", flush=True)
            return LOCAL_VISION
        except Exception as e:
            print("VISION LOCALE ❌ chargement:", repr(e), flush=True)
            return None

def _local_brand_from_image(m):
    """
    Analyse réellement les pixels de la photo/miniature vidéo.
    Retourne (marque, score). Toutes les marques Homme/Femme + Luxe sont comparées ensemble.
    """
    file_id = _telegram_visual_file_id(m)
    if not file_id:
        return None, 0.0

    raw, _mime = _download_telegram_file(file_id)
    if not raw:
        return None, 0.0

    model = _get_local_vision()
    if model is None:
        return None, 0.0

    try:
        from PIL import Image
        image = Image.open(io.BytesIO(raw)).convert("RGB")
        labels = list(VISION_LABELS.values())
        results = model(image, candidate_labels=labels)
        if not results:
            return None, 0.0
        best = results[0]
        label = best.get("label")
        score = float(best.get("score") or 0.0)
        reverse = {v: k for k, v in VISION_LABELS.items()}
        brand = reverse.get(label)
        print(f"VISION IMAGE 🔎 -> {brand} score={score:.3f}", flush=True)
        if brand and score >= LOCAL_VISION_MIN_SCORE:
            return brand, score
    except Exception as e:
        print("VISION IMAGE ERROR:", repr(e), flush=True)
    return None, 0.0

def classify_brand_with_vision(m, topic_id, force_image=False):
    """
    V5.18 :
    - force_image=True : analyse réellement l'image avec CLIP local.
    - sinon : texte/légende d'abord pour les nouveaux messages, puis image.
    """
    text = " ".join([str(m.get("caption") or ""), str(m.get("text") or "")]).strip()

    if not force_image:
        by_text = _canonical_brand_from_text(text, topic_id)
        if by_text:
            return by_text

    by_image, score = _local_brand_from_image(m)
    if by_image:
        return by_image

    # Dernier secours texte, même pendant l'indexation image.
    by_text = _canonical_brand_from_text(text, topic_id)
    return by_text or "Autres / À vérifier"

def _brand_destination_topic(brand, original_topic):
    """
    Classe selon ce que montre la chaussure :
    luxe -> topic logique 3616
    classique -> topic logique 64
    """
    if brand in LUXURY_SET:
        return "3616"
    if brand in REGULAR_SET:
        return "64"
    return str(original_topic)

def _remove_message_from_all_brand_buckets(message_id):
    for tid, topic_map in BRAND_CATALOG.items():
        for brand, ids in list(topic_map.items()):
            if message_id in ids:
                topic_map[brand] = [x for x in ids if x != message_id]

def register_brand_message(m, topic_id, message_id, kind, force_image=False):
    """Analyse puis reclasse une photo/vidéo dans Homme/Femme OU Luxe selon l'image."""
    if str(topic_id) not in BRAND_TOPIC_IDS or kind not in ("photo", "video"):
        return

    brand = classify_brand_with_vision(m, topic_id, force_image=force_image)
    dest_tid = _brand_destination_topic(brand, topic_id)

    with BRAND_LOCK:
        _remove_message_from_all_brand_buckets(message_id)
        topic_map = BRAND_CATALOG.setdefault(dest_tid, {})
        ids = topic_map.setdefault(brand, [])
        if message_id not in ids:
            ids.append(message_id)
            ids.sort()
        save_brands()

    print(
        f"MARQUE IMAGE ✅ origine={topic_id} destination={dest_tid} "
        f"message={message_id} -> {brand}",
        flush=True
    )

def register_new_content(m):
    """Ajoute automatiquement les NOUVEAUX textes, photos et vidéos du forum source."""
    if not is_source_message(m):
        return False

    kind = message_kind(m)
    if not kind:
        return False

    # In a Telegram forum, every message posted inside a topic carries message_thread_id.
    thread_id = m.get("message_thread_id")
    message_id = m.get("message_id")
    if not isinstance(thread_id, int) or not isinstance(message_id, int):
        print(f"AUTO IGNORE: message {message_id}, aucun message_thread_id", flush=True)
        return False

    key = str(thread_id)
    with CATALOG_LOCK:
        if key not in CATALOG:
            CATALOG[key] = {
                "title": f"Nouveau catalogue {thread_id}",
                "message_ids": [],
                "photos": 0,
                "videos": 0,
                "texts": 0,
            }

        c = CATALOG[key]
        ids = c.setdefault("message_ids", [])
        if message_id in ids:
            # Une ancienne publication modifiée/reçue de nouveau peut être reclassée par vision.
            if key in BRAND_TOPIC_IDS and kind in ("photo", "video"):
                register_brand_message(m, key, message_id, kind)
            return False

        ids.append(message_id)
        ids.sort()

        if kind == "photo":
            c["photos"] = int(c.get("photos", 0)) + 1
        elif kind == "video":
            c["videos"] = int(c.get("videos", 0)) + 1
        else:
            c["texts"] = int(c.get("texts", 0)) + 1

        save_catalog()

    print(
        f"AUTO AJOUT ✅ topic={thread_id} message={message_id} type={kind}",
        flush=True
    )

    # V6 FINAL : le catalogue enregistre immédiatement le média.
    # L’ancienne analyse IA chaussures est désactivée : plus de recherche marque,
    # démarrage/déploiement beaucoup plus léger.

    return True

def register_topic_title(m):
    # Nouveau topic ou renommage de topic.
    created = m.get("forum_topic_created") or {}
    edited = m.get("forum_topic_edited") or {}
    if not created and not edited:
        return False

    thread_id = m.get("message_thread_id") or m.get("message_id")
    if not isinstance(thread_id, int):
        return False

    key = str(thread_id)
    title = (
        created.get("name")
        or edited.get("name")
        or (CATALOG.get(key) or {}).get("title")
        or f"Catalogue {thread_id}"
    ).strip()

    with CATALOG_LOCK:
        if key not in CATALOG:
            CATALOG[key] = {
                "title": title,
                "message_ids": [],
                "photos": 0,
                "videos": 0,
                "texts": 0,
            }
        else:
            CATALOG[key]["title"] = title
        save_catalog()

    print(f"TOPIC AUTO ✅ id={thread_id} titre={title}", flush=True)
    return True

app = Flask(__name__, static_folder="web", static_url_path="")

def api(method, **data):
    try:
        r = requests.post(f"{API}/{method}", json=data, timeout=90)
        j = r.json()
    except Exception as e:
        print("HTTP error:", method, repr(e), flush=True)
        return {"ok": False, "description": str(e)}
    if not j.get("ok"):
        print("Telegram error:", method, j, flush=True)
    return j

def catalog_totals():
    topics = len(CATALOG)
    items = sum(len(c.get("message_ids", [])) for c in CATALOG.values())
    photos = sum(int(c.get("photos", 0)) for c in CATALOG.values())
    videos = sum(int(c.get("videos", 0)) for c in CATALOG.values())
    return topics, items, photos, videos

def send_start(chat_id):
    topics, items, photos, videos = catalog_totals()
    caption = (
        "✨ <b>LE COIN MALIN 34 — CATALOGUE OFFICIEL</b> 🖤💛\n\n"
        "Bienvenue 👋 Découvrez nos produits, photos et vidéos directement depuis le catalogue.\n\n"
        "🆕 <b>Nouveautés</b> : les derniers articles ajoutés\n"
        "📦 <b>Sur place</b> : les produits disponibles immédiatement\n"
        "🚚 <b>Prêts à expédier</b> : accès rapide aux produits concernés\n\n"
        f"📊 <b>{topics} rubriques · {items} publications</b>\n"
        "🔄 <i>Catalogue mis à jour automatiquement.</i>\n\n"
        "👇 <b>Choisissez une rubrique :</b>"
    )
    rows = [
        [{"text":"🆕  Nouveautés","callback_data":"new:0"}],
        [{"text":"📦  Articles disponibles sur place","callback_data":"articlesplace"}],
        [{"text":"👟  Chaussures","callback_data":"group:shoes"}],
        [{"text":"👕  Vêtements","callback_data":"group:clothes"}],
        [{"text":"📱  High-Tech","callback_data":"group:tech"}],
        [{"text":"⌚  Montres & Bijoux","callback_data":"group:watches"}],
        [{"text":"👜  Accessoires","callback_data":"group:accessories"}],
        [{"text":"🚚  Produits prêts à être expédiés","callback_data":"group:ready"}],
        [{"text":"🛒  Commander / Comment ça marche","callback_data":"group:order"}],
        [{"text":"💬  Avis clients","callback_data":"group:reviews"}],
        [{"text":"📣  Voir le groupe LE COIN MALIN 34","url":SOURCE_GROUP_URL}],
        [{"text":"✨  Voir plus","callback_data":"group:more"}],
    ]
    if WEBAPP_URL:
        rows.insert(1, [{"text":"🛍️  Catalogue Premium","web_app":{"url":WEBAPP_URL}}])
    kb = {"inline_keyboard": rows}
    try:
        logo_path = "web/logo.png" if os.path.exists("web/logo.png") else "logo.png"
        with open(logo_path, "rb") as f:
            rr = requests.post(f"{API}/sendPhoto", data={
                "chat_id": str(chat_id), "caption": caption, "parse_mode": "HTML",
                "reply_markup": json.dumps(kb, ensure_ascii=False)
            }, files={"photo": ("logo.png", f, "image/png")}, timeout=90).json()
            if rr.get("ok"):
                return rr
    except Exception as e:
        print("sendPhoto local error:", repr(e), flush=True)
    return api("sendMessage", chat_id=chat_id, text=caption, parse_mode="HTML", reply_markup=kb)

def latest_message_ids():
    # Un message peut éventuellement exister dans plusieurs vues : on le déduplique.
    ids = set()
    for c in CATALOG.values():
        for mid in c.get("message_ids", []):
            if isinstance(mid, int):
                ids.add(mid)
    return sorted(ids, reverse=True)

def send_new_page(chat_id, offset=0):
    ids = latest_message_ids()
    page = ids[offset:offset + PAGE_SIZE]
    if not page:
        return api("sendMessage", chat_id=chat_id, text="ℹ️ Aucune nouveauté pour le moment.",
                   reply_markup={"inline_keyboard":[[{"text":"🏠 Accueil","callback_data":"home"}]]})
    # copyMessages respecte l’ordre fourni ; nouveautés = plus récent d’abord.
    api("sendMessage", chat_id=chat_id,
        text=f"🆕 <b>NOUVEAUTÉS</b>\nLes {len(page)} dernières publications du catalogue 👇",
        parse_mode="HTML")
    res = api("copyMessages", chat_id=chat_id, from_chat_id=SOURCE_CHAT, message_ids=page)
    if not res.get("ok"):
        # Certains clients Telegram/API préfèrent un ordre croissant : repli robuste.
        res = api("copyMessages", chat_id=chat_id, from_chat_id=SOURCE_CHAT, message_ids=sorted(page))
    nav=[]
    if offset > 0:
        nav.append({"text":"⬅️ Plus récentes","callback_data":f"new:{max(0,offset-PAGE_SIZE)}"})
    if offset + PAGE_SIZE < len(ids):
        nav.append({"text":"Plus anciennes ➡️","callback_data":f"new:{offset+PAGE_SIZE}"})
    rows=[nav] if nav else []
    rows.append([
        {"text":"🛒 Commander","url":ORDER_URL},
        {"text":"💬 Avis clients","url":REVIEWS_URL}
    ])
    rows.append([{"text":"🏠 Accueil","callback_data":"home"}])
    return api("sendMessage", chat_id=chat_id, text="✨ <b>Catalogue actualisé automatiquement</b>",
               parse_mode="HTML", reply_markup={"inline_keyboard":rows})

def clean_title(t):
    mapping = {
        "📦 Articles disponibles":"📦 Articles disponibles sur place",
        "💬 Avis clients":"Avis clients",
        "Sacoche 👜 et sac":"Sacs & Sacoches",
        "Chaussures homme femme":"Homme / Femme",
        "comment passer commande":"Comment commander",
        "stone et cp":"Stone & CP",
        "casquette de marque":"Casquettes de marque",
        "dyson sur commande":"Dyson",
        "Bracelets, bracelets et joncs Cartier":"Bracelets & Joncs Cartier",
        "Gshock⌚️":"G‑Shock",
        "Électronique🎧":"Électronique",
        "Pack fournisseur exploitation":"Pack fournisseurs",
        "paire de lunette tout marque":"Lunettes de marque",
        "chaussure de football":"Football",
        "Parfum homme femme":"Parfums Homme / Femme",
        "montre de marque":"Montres de marque",
        "maillot de foot":"Maillots de foot",
        "UG":"UGG",
        "crocs":"Crocs",
        "Casque arai sur commande 🚀":"Casques Arai",
        "Ensemble essentials":"Essentials",
        "Coque de téléphone 📱":"Coques téléphone",
        "Alo femme 👩":"Alo Femme",
        "Alo homme 👨":"Alo Homme",
        "vêtements all":"Sport & Été",
        "vêtements luxe":"Vêtements Luxe",
        "⚡️moto électrique":"Moto électrique",
        "Lunnete meta qui flime":"Lunettes Meta",
        "commande client":"Commandes clients",
        "📦 VENTE EN GROS SUR COMMANDE 📦":"Vente en gros",
        "Chaussures de luxe 🤩":"Luxe & Claquettes",
        "Chaussures pour enfants 👶":"Chaussures Enfants",
        "Basic fit offre":"Basic‑Fit — Offre",
        "Location de voiture -40%":"Location voiture -40 %",
        "Déblocage snap":"Déblocage Snap",
        "Doudoune hiver ❄️":"Doudoune hiver",
        "Ensemble haut et bas":"Ensemble haut & bas",
        "Valise 🧳":"Valises",
        "Pc gamer 💻":"PC Gamer",
        "Canapé 🛋️ bubble sur commande":"Canapé Bubble",
        "Bonnet d’hiver homme femme ☃️":"Bonnet hiver",
        "Chaise 🪑 gaming 🤩🤩":"Chaise 🪑 Gaming"
    }
    return mapping.get(t, t)

@app.get("/")
def index():
    return send_from_directory("web", "index.html")

@app.get("/api/catalog")
def catalog_api():
    groups = []
    for g in MENU["groups"]:
        items = []
        for tid in topics_for_group(g):
            c = CATALOG.get(str(tid))
            if not c:
                continue
            items.append({
                "id": str(tid),
                "title": clean_title(c["title"]),
                "photos": c.get("photos", 0),
                "videos": c.get("videos", 0),
                "texts": c.get("texts", 0),
                "count": len(c.get("message_ids", []))
            })
        if items:
            groups.append({
                "id":g["id"], "emoji":g["emoji"], "title":g["title"],
                "subtitle":g["subtitle"], "items":items
            })
    return jsonify({"groups":groups})

def group_for_topic(tid):
    tid = str(tid)
    for g in MENU.get("groups", []):
        if tid in [str(x) for x in topics_for_group(g)]:
            return g.get("id")
    return None

def group_keyboard(group_id):
    g = next((x for x in MENU["groups"] if x["id"] == group_id), None)
    if not g:
        return {"inline_keyboard":[[{"text":"🏠 Accueil","callback_data":"home"}]]}

    rows=[]

    for tid in topics_for_group(g):
        c=CATALOG.get(str(tid))
        if not c:
            continue
        p,v,t=c.get("photos",0),c.get("videos",0),c.get("texts",0)
        suffix=f"📷 {p}"
        if v:
            suffix += f" · 🎬 {v}"
        if t:
            suffix += f" · 📝 {t}"
        callback = f"cat:{tid}:0"
        rows.append([{"text":f"{clean_title(c['title'])}  |  {suffix}","callback_data":callback}])

    rows.append([{"text":"🏠 Accueil","callback_data":"home"}])
    return {"inline_keyboard":rows}

def brand_keyboard(tid):
    tid = str(tid)
    label = "Homme / Femme" if tid == "64" else "Luxe"
    return {"inline_keyboard":[
        [{"text":f"🔎 Rechercher dans {label}","callback_data":f"searchbrand:{tid}"}],
        [{"text":"👟 Voir tous les modèles","callback_data":f"cat:{tid}:0"}],
        [{"text":"⬅️ Retour","callback_data":"group:shoes"}],
        [{"text":"🏠 Accueil","callback_data":"home"}]
    ]}

def send_brand_root(chat_id, tid):
    tid = str(tid)
    c = CATALOG.get(tid)
    if not c:
        return
    title = clean_title(c["title"])
    if tid == "64":
        intro = (
            "👟 <b>CHAUSSURES HOMME / FEMME</b>\n"
            "Choisissez directement votre marque 👇\n\n"
            "Nike · New Balance · On Running · Adidas · ASICS · Jordan · Puma…\n\n"
            "🔎 Vous pouvez aussi rechercher une marque ou un modèle."
        )
    else:
        intro = (
            "💎 <b>CHAUSSURES DE LUXE</b>\n"
            "Choisissez directement votre marque 👇\n\n"
            "Dior · Louis Vuitton · Prada · Chanel · Hermès · Gucci · Balenciaga…\n\n"
            "🔎 Vous pouvez aussi rechercher une marque ou un modèle."
        )
    return api(
        "sendMessage",
        chat_id=chat_id,
        text=intro,
        parse_mode="HTML",
        reply_markup=brand_keyboard(tid)
    )


def _already_classified_ids(tid):
    """Tous les IDs déjà classés, toutes marques confondues."""
    topic_map = BRAND_CATALOG.get(str(tid), {})
    out = set()
    for ids in topic_map.values():
        for mid in ids:
            if isinstance(mid, int):
                out.add(mid)
    return out

def _scan_old_topic_for_brand(chat_id, tid, target_brand, brand_index):
    """
    Récupère les anciens messages grâce à forwardMessage, analyse leur image,
    puis supprime immédiatement la copie temporaire du chat privé.
    Le scan s'arrête après 20 résultats de la marque recherchée ou à la fin.
    """
    tid = str(tid)
    try:
        ids = list(reversed((CATALOG.get(tid) or {}).get("message_ids", [])))[:250]
        already = _already_classified_ids(tid)
        found_before = len(BRAND_CATALOG.get(tid, {}).get(target_brand, []))
        scanned_media = 0
        checked = 0

        print(
            f"SCAN HISTORIQUE ▶ topic={tid} marque={target_brand} "
            f"ids={len(ids)} deja_classes={len(already)}",
            flush=True
        )

        for source_message_id in ids:
            with INDEXING_LOCK:
                if tid in CANCEL_SCAN_TOPICS:
                    print(f"SCAN ANNULÉ ⛔ topic={tid} marque={target_brand}", flush=True)
                    api("sendMessage", chat_id=chat_id, text="⛔ Scan annulé.")
                    return
            if source_message_id in already:
                # Si on a déjà assez de résultats pour cette marque, inutile d'aller plus loin.
                if len(BRAND_CATALOG.get(tid, {}).get(target_brand, [])) >= 999999:
                    break
                continue

            checked += 1
            if checked % 20 == 0:
                time.sleep(0.01)
            with INDEXING_LOCK:
                if tid in CANCEL_SCAN_TOPICS:
                    print(f"SCAN ANNULÉ ⛔ topic={tid} marque={target_brand}", flush=True)
                    api("sendMessage", chat_id=chat_id, text="⛔ Scan annulé.")
                    return

            fr = api(
                "forwardMessage",
                chat_id=chat_id,
                from_chat_id=SOURCE_CHAT,
                message_id=source_message_id,
                disable_notification=True
            )

            with INDEXING_LOCK:
                if tid in CANCEL_SCAN_TOPICS:
                    temp_cancel_id = (fr.get("result") or {}).get("message_id") if fr.get("ok") else None
                    if temp_cancel_id:
                        api("deleteMessage", chat_id=chat_id, message_id=temp_cancel_id)
                    print(f"SCAN ANNULÉ ⛔ topic={tid} marque={target_brand}", flush=True)
                    api("sendMessage", chat_id=chat_id, text="⛔ Scan annulé.")
                    return
            if not fr.get("ok"):
                continue

            forwarded = fr.get("result") or {}
            temp_id = forwarded.get("message_id")
            try:
                kind = message_kind(forwarded)
                if kind in ("photo", "video"):
                    scanned_media += 1
                    register_brand_message(forwarded, tid, source_message_id, kind)
                    already.add(source_message_id)
            finally:
                if temp_id:
                    api("deleteMessage", chat_id=chat_id, message_id=temp_id)

            # Dès qu'on a une page complète de la marque demandée, on peut répondre.
            if len(BRAND_CATALOG.get(tid, {}).get(target_brand, [])) >= 999999:
                break

            # Petite pause pour éviter de brusquer Telegram.

        total_found = len(BRAND_CATALOG.get(tid, {}).get(target_brand, []))
        print(
            f"SCAN HISTORIQUE ✅ topic={tid} marque={target_brand} "
            f"messages_testes={checked} medias_analyses={scanned_media} trouves={total_found}",
            flush=True
        )

        if total_found > 0:
            api(
                "sendMessage",
                chat_id=chat_id,
                text=(
                    f"✅ <b>{target_brand}</b> trouvé. "
                    f"J’ai classé les anciennes photos disponibles et je t’affiche les résultats 👇"
                ),
                parse_mode="HTML"
            )
            send_brand_page(chat_id, tid, brand_index, 0, allow_scan=False)
        else:
            api(
                "sendMessage",
                chat_id=chat_id,
                text=(
                    f"🔎 Scan terminé pour <b>{target_brand}</b>.\n\n"
                    "Je n’ai trouvé aucune photo identifiable de cette marque "
                    "dans les anciens messages accessibles."
                ),
                parse_mode="HTML",
                reply_markup={"inline_keyboard":[
                    [{"text":"⬅️ Marques","callback_data":f"brandroot:{tid}"}],
                    [{"text":"🏠 Accueil","callback_data":"home"}]
                ]}
            )
    except Exception as e:
        print("SCAN HISTORIQUE ERROR:", repr(e), flush=True)
        api(
            "sendMessage",
            chat_id=chat_id,
            text="⚠️ Le scan des anciennes chaussures a rencontré une erreur. Regarde les logs Railway.",
        )
    finally:
        with INDEXING_LOCK:
            INDEXING_TOPICS.discard(tid)
            CANCEL_SCAN_TOPICS.discard(tid)

def start_old_brand_scan(chat_id, tid, target_brand, brand_index):
    tid = str(tid)

    with INDEXING_LOCK:
        if tid in INDEXING_TOPICS:
            return api(
                "sendMessage",
                chat_id=chat_id,
                text="⏳ Je suis déjà en train d’analyser les anciennes chaussures de cette rubrique. Réessaie dans quelques instants."
            )
        INDEXING_TOPICS.add(tid)
        CANCEL_SCAN_TOPICS.discard(tid)

    api(
        "sendMessage",
        chat_id=chat_id,
        text=(
            f"🔎 <b>{target_brand}</b>\n\n"
            "🔎 Recherche de tous les modèles de la marque…\n"
            "Les copies temporaires sont supprimées automatiquement. C’est 100 % gratuit.\n\n"
            "Je cherche tous les articles de cette marque par lots rapides."
        ),
        parse_mode="HTML",
        reply_markup={"inline_keyboard":[
            [{"text":"⛔ Annuler le scan","callback_data":f"cancelscan:{tid}"}]
        ]}
    )
    threading.Thread(
        target=_scan_old_topic_for_brand,
        args=(chat_id, tid, target_brand, brand_index),
        daemon=True
    ).start()
    return None

def send_brand_page(chat_id, tid, brand_index, offset=0, allow_scan=True):
    tid = str(tid)
    allowed = REGULAR_BRANDS if tid == "64" else LUXURY_BRANDS
    if brand_index < 0 or brand_index >= len(allowed):
        return
    brand = allowed[brand_index]
    ids = BRAND_CATALOG.get(tid, {}).get(brand, [])
    total = len(ids)
    if total == 0:
        return api(
            "sendMessage",
            chat_id=chat_id,
            text=f"👟 <b>{brand}</b>\n\nAucun article indexé dans cette marque pour le moment.",
            parse_mode="HTML",
            reply_markup={"inline_keyboard":[
                [{"text":"⬅️ Marques","callback_data":f"brandroot:{tid}"}],
                [{"text":"🏠 Accueil","callback_data":"home"}]
            ]}
        )

    page = ids[offset:offset+PAGE_SIZE]
    end = offset + len(page)
    api("sendMessage", chat_id=chat_id,
        text=f"👟 <b>{brand}</b>\nAffichage {offset+1}–{end} sur {total}",
        parse_mode="HTML")
    res = api("copyMessages", chat_id=chat_id, from_chat_id=SOURCE_CHAT, message_ids=page)
    if not res.get("ok"):
        return api("sendMessage", chat_id=chat_id,
                   text=f"⚠️ Impossible d'afficher ces articles.\n<code>{res.get('description','Erreur')}</code>",
                   parse_mode="HTML")
    nav=[]
    if offset > 0:
        nav.append({"text":"⬅️ Précédents","callback_data":f"brand:{tid}:{brand_index}:{max(0,offset-PAGE_SIZE)}"})
    if end < total:
        nav.append({"text":"Suivants ➡️","callback_data":f"brand:{tid}:{brand_index}:{end}"})
    rows=[nav] if nav else []
    rows.append([{"text":"⬅️ Marques","callback_data":f"brandroot:{tid}"}])
    rows.append([{"text":"🏠 Accueil","callback_data":"home"}])
    return api("sendMessage", chat_id=chat_id,
        text=f"✅ <b>{end} / {total} articles {brand}</b>",
        parse_mode="HTML", reply_markup={"inline_keyboard":rows})

def send_page(chat_id, tid, offset=0):
    c=CATALOG.get(str(tid))
    if not c:
        return
    ids=c.get("message_ids",[])
    page=ids[offset:offset+PAGE_SIZE]
    if not page:
        return
    total=len(ids)
    end=offset+len(page)
    p,v,t=c.get("photos",0),c.get("videos",0),c.get("texts",0)
    title=clean_title(c["title"])
    api("sendMessage", chat_id=chat_id,
        text=f"✨ <b>{title}</b>\n📷 {p} photos  ·  🎬 {v} vidéos  ·  📝 {t} textes\n\nAffichage {offset+1}–{end} sur {total}",
        parse_mode="HTML")

    res=api("copyMessages", chat_id=chat_id, from_chat_id=SOURCE_CHAT, message_ids=page)
    if not res.get("ok"):
        return api("sendMessage", chat_id=chat_id,
                   text=f"⚠️ Telegram n’a pas pu copier ces médias.\n<code>{res.get('description','Erreur inconnue')}</code>",
                   parse_mode="HTML")

    nav=[]
    if offset>0:
        nav.append({"text":"⬅️ Précédents","callback_data":f"cat:{tid}:{max(0,offset-PAGE_SIZE)}"})
    if end<total:
        nav.append({"text":"Suivants ➡️","callback_data":f"cat:{tid}:{end}"})
    rows=[nav] if nav else []
    rows.append([
        {"text":"🛒 Commander","url":ORDER_URL},
        {"text":"💬 Avis clients","url":REVIEWS_URL}
    ])
    rows.append([{"text":"🏠 Accueil","callback_data":"home"}])
    api("sendMessage", chat_id=chat_id,
        text=f"✅ <b>{end} / {total} éléments affichés</b>",
        parse_mode="HTML", reply_markup={"inline_keyboard":rows})


def _index_owner_ok(chat_id):
    """Premier utilisateur qui lance /indeximages devient propriétaire de l'indexation."""
    try:
        if INDEX_OWNER_FILE and INDEX_OWNER_FILE.exists():
            return INDEX_OWNER_FILE.read_text(encoding="utf-8").strip() == str(chat_id)
        if INDEX_OWNER_FILE:
            INDEX_OWNER_FILE.parent.mkdir(parents=True, exist_ok=True)
            INDEX_OWNER_FILE.write_text(str(chat_id), encoding="utf-8")
        return True
    except Exception:
        return True

def _clear_previous_shoe_index():
    """Supprime les anciens classements chaussures avant une analyse complète."""
    with BRAND_LOCK:
        BRAND_CATALOG["64"] = {}
        BRAND_CATALOG["3616"] = {}
        save_brands()

def handle(u):
    # Telegram can deliver group content as message/edited_message/channel_post.
    m = (
        u.get("message")
        or u.get("edited_message")
        or u.get("channel_post")
        or u.get("edited_channel_post")
    )

    if m and (m.get("chat") or {}).get("type") in ("supergroup", "group", "channel"):
        register_topic_title(m)
        register_new_content(m)

    # Commands/web-app data are only handled from normal private messages.
    pm = u.get("message")
    if pm and (pm.get("chat") or {}).get("type") == "private":
        txt = pm.get("text", "")
        chat_id = pm["chat"]["id"]

        # V5 : réponse à la recherche marque/modèle
        if chat_id in SEARCH_WAITING and txt and not txt.startswith("/"):
            tid = SEARCH_WAITING.pop(chat_id)
            brand = find_brand_from_query(txt, tid)
            allowed = REGULAR_BRANDS if str(tid) == "64" else LUXURY_BRANDS
            if brand in allowed:
                idx = allowed.index(brand)
                return send_brand_page(chat_id, str(tid), idx, 0)
            return api(
                "sendMessage",
                chat_id=chat_id,
                text=(
                    "❌ Je n’ai pas trouvé cette marque ou ce modèle.\n\n"
                    "Exemples : <b>Nike</b>, <b>TN</b>, <b>New Balance</b>, "
                    "<b>On Running</b>, <b>Dior B30</b>, <b>LV Runner</b>."
                ),
                parse_mode="HTML",
                reply_markup={"inline_keyboard":[
                    [{"text":"🔎 Réessayer","callback_data":f"searchbrand:{tid}"}],
                    [{"text":"⬅️ Retour","callback_data":("articlesplace" if tid == "2" else f"brandroot:{tid}")}],
                    [{"text":"🏠 Accueil","callback_data":"home"}]
                ]}
            )

        if txt.startswith("/indeximages"):
            if not _index_owner_ok(chat_id):
                return api("sendMessage", chat_id=chat_id, text="⛔ Cette commande est réservée au propriétaire du catalogue.")
            _clear_previous_shoe_index()
            api(
                "sendMessage",
                chat_id=chat_id,
                text=(
                    "🧠 <b>Analyse complète des chaussures lancée</b>\n\n"
                    "Je vais analyser les pixels de toutes les photos/miniatures accessibles dans :\n"
                    "• Chaussures Homme / Femme\n"
                    "• Chaussures de luxe\n\n"
                    "Dior/LV/etc. seront rangées dans Luxe ; TN/Nike/ASICS/etc. dans Homme/Femme.\n"
                    "Les résultats sont sauvegardés pour rendre les recherches instantanées ensuite."
                ),
                parse_mode="HTML"
            )
            start_background_index(chat_id, "64", force_image=True, silent_start=True)
            start_background_index(chat_id, "3616", force_image=True, silent_start=True)
            return

        if txt.startswith("/cancel"):
            SEARCH_WAITING.pop(chat_id, None)
            stopped = []
            with INDEXING_LOCK:
                for tid in list(INDEXING_TOPICS):
                    CANCEL_SCAN_TOPICS.add(tid)
                    stopped.append(tid)
            if stopped:
                return api(
                    "sendMessage",
                    chat_id=chat_id,
                    text="⛔ Recherche + indexation en cours : arrêt demandé."
                )
            return api("sendMessage", chat_id=chat_id, text="✅ Recherche annulée.")

        if txt.startswith(("/start", "/menu")):
            return send_start(pm["chat"]["id"])
        if txt.startswith("/status"):
            total_new = 0
            runtime = RUNTIME_CATALOG.exists()
            return api(
                "sendMessage",
                chat_id=pm["chat"]["id"],
                text=(
                    "✅ <b>Détection automatique active</b>\n"
                    "📝 Textes + 📷 Photos + 🎬 Vidéos\n"
                    "⚡ Mode catalogue : RAPIDE + AUTOMATIQUE\n"
                    f"📡 Source : <code>{SOURCE_CHAT}</code>\n"
                    f"💾 Sauvegarde persistante : {'oui' if runtime else 'à initialiser au 1er ajout'}"
                ),
                parse_mode="HTML"
            )
        wad = pm.get("web_app_data")
        if wad:
            try:
                d = json.loads(wad.get("data", "{}"))
                if d.get("action") == "open_topic":
                    return send_page(pm["chat"]["id"], str(d["topic_id"]), 0)
            except Exception as e:
                print("web_app_data:", repr(e), flush=True)

    q = u.get("callback_query")
    if not q:
        return
    api("answerCallbackQuery", callback_query_id=q["id"])
    chat_id = q["message"]["chat"]["id"]
    d = q.get("data", "")
    if d == "home":
        return send_start(chat_id)
    if d.startswith("new:"):
        try:
            return send_new_page(chat_id, int(d.split(":", 1)[1]))
        except Exception:
            return send_new_page(chat_id, 0)
    if d.startswith("group:"):
        gid = d.split(":", 1)[1]
        g = next((x for x in MENU["groups"] if x["id"] == gid), None)
        if g:
            return api(
                "sendMessage",
                chat_id=chat_id,
                text=f"{g['emoji']} <b>{g['title']}</b>\n<i>{g['subtitle']}</i>",
                parse_mode="HTML",
                reply_markup=group_keyboard(gid)
            )
    if d.startswith("cancelscan:"):
        tid = d.split(":", 1)[1]
        with INDEXING_LOCK:
            if tid in INDEXING_TOPICS:
                CANCEL_SCAN_TOPICS.add(tid)
                return api("sendMessage", chat_id=chat_id, text="⛔ Annulation demandée. Le scan va s’arrêter immédiatement.")
        return api("sendMessage", chat_id=chat_id, text="ℹ️ Aucun scan en cours.")

    if d == "articlesplace":
        return api(
            "sendMessage",
            chat_id=chat_id,
            text="📦 <b>Articles disponibles sur place</b>\n\nChoisissez :",
            parse_mode="HTML",
            reply_markup={"inline_keyboard":[
                [{"text":"🔎 Rechercher un article","callback_data":"searchbrand:2"}],
                [{"text":"📦 Voir tous les articles","callback_data":"cat:2:0"}],
                [{"text":"🏠 Accueil","callback_data":"home"}]
            ]}
        )
    if d.startswith("searchbrand:"):
        tid = d.split(":", 1)[1]
        SEARCH_WAITING[chat_id] = tid
        if tid == "2":
            title = "🔎 <b>Recherche — Articles disponibles sur place</b>"
            instruction = "Écrivez le nom de l'<b>article</b>, de la <b>marque</b> ou du <b>modèle</b> recherché."
            examples = "Exemples : sacoche, parfum, Nike, montre, casque."
        else:
            label = "Homme / Femme" if tid == "64" else "Luxe"
            title = f"🔎 <b>Recherche chaussures {label}</b>"
            instruction = "Écrivez maintenant une <b>marque</b> ou un <b>modèle</b>."
            examples = "Exemples : Nike, TN, New Balance, On Running, Dior B30, LV Runner."
        return api(
            "sendMessage",
            chat_id=chat_id,
            text=f"{title}\n\n{instruction}\n{examples}\n\nPour annuler : /cancel",
            parse_mode="HTML"
        )
    if d.startswith("brandroot:"):
        tid = d.split(":", 1)[1]
        return send_brand_root(chat_id, tid)
    if d.startswith("brand:"):
        _, tid, brand_index, off = d.split(":", 3)
        return send_brand_page(chat_id, tid, int(brand_index), int(off))
    if d.startswith("cat:"):
        _, tid, off = d.split(":", 2)
        return send_page(chat_id, tid, int(off))



def start_background_index(chat_id, tid, force_image=False, silent_start=False):
    """
    Indexe les anciens médias.
    force_image=True = analyse réellement chaque image avec le modèle local.
    """
    tid = str(tid)
    with INDEXING_LOCK:
        if tid in INDEXING_TOPICS:
            if not silent_start:
                return api("sendMessage", chat_id=chat_id, text="⏳ Indexation déjà en cours.")
            return None
        INDEXING_TOPICS.add(tid)
        CANCEL_SCAN_TOPICS.discard(tid)

    def worker():
        classified = 0
        media_seen = 0
        errors = 0
        try:
            ids = list(reversed((CATALOG.get(tid) or {}).get("message_ids", [])))
            total_ids = len(ids)
            print(f"INDEX IMAGE ▶ topic={tid} ids={total_ids} force={force_image}", flush=True)

            for pos, source_message_id in enumerate(ids, start=1):
                with INDEXING_LOCK:
                    if tid in CANCEL_SCAN_TOPICS:
                        api("sendMessage", chat_id=chat_id, text=f"⛔ Indexation {tid} arrêtée.")
                        return

                fr = api(
                    "forwardMessage",
                    chat_id=chat_id,
                    from_chat_id=SOURCE_CHAT,
                    message_id=source_message_id,
                    disable_notification=True
                )
                if not fr.get("ok"):
                    errors += 1
                    continue

                forwarded = fr.get("result") or {}
                temp_id = forwarded.get("message_id")
                try:
                    kind = message_kind(forwarded)
                    if kind in ("photo", "video"):
                        media_seen += 1
                        register_brand_message(
                            forwarded, tid, source_message_id, kind,
                            force_image=force_image
                        )
                        classified += 1
                except Exception as e:
                    errors += 1
                    print("INDEX IMAGE item error:", source_message_id, repr(e), flush=True)
                finally:
                    if temp_id:
                        api("deleteMessage", chat_id=chat_id, message_id=temp_id)

                if media_seen and media_seen % 25 == 0:
                    print(
                        f"INDEX IMAGE … topic={tid} medias={media_seen} "
                        f"progress={pos}/{total_ids}",
                        flush=True
                    )
                    time.sleep(0.15)

            api(
                "sendMessage",
                chat_id=chat_id,
                text=(
                    f"✅ Analyse terminée pour {'Homme/Femme' if tid == '64' else 'Luxe'} : "
                    f"{classified} médias analysés.\n"
                    "Les résultats sont maintenant enregistrés dans le cache."
                )
            )
        finally:
            with INDEXING_LOCK:
                INDEXING_TOPICS.discard(tid)
                CANCEL_SCAN_TOPICS.discard(tid)

    threading.Thread(target=worker, daemon=True).start()
    if silent_start:
        return None
    return api(
        "sendMessage",
        chat_id=chat_id,
        text="🧠 Analyse image lancée en arrière-plan.",
        reply_markup={"inline_keyboard":[[
            {"text":"⛔ Arrêter l’indexation","callback_data":f"cancelscan:{tid}"}
        ]]}
    )

def poll():
    offset=0
    while True:
        try:
            r=requests.get(
                f"{API}/getUpdates",
                params={"timeout": 50, "offset": offset},
                timeout=60
            ).json()
            for u in r.get("result",[]):
                offset=u["update_id"]+1
                handle(u)
        except Exception as e:
            print("Polling:",repr(e),flush=True)
            time.sleep(3)

def main():
    if not TOKEN:
        raise SystemExit("BOT_TOKEN manquant")
    resolve_source_chat_id()
    print(f"Catalogue dynamique: {RUNTIME_CATALOG}", flush=True)
    print(f"AUTO {BUILD_VERSION}: accueil amélioré + recherche chaussures supprimée + topics auto = ACTIVÉ", flush=True)
    print("MODE GRATUIT: VISION LOCALE CLIP + CACHE = ACTIVÉ", flush=True)
    print("TOPIC 2: Articles disponibles sur place = ACTIVÉ", flush=True)
    print("OPENAI API: NON UTILISÉE", flush=True)
    print(f"MODÈLE LOCAL: {LOCAL_VISION_MODEL}", flush=True)
    threading.Thread(target=poll,daemon=True).start()
    port=int(os.environ.get("PORT","8080"))
    app.run(host="0.0.0.0",port=port,threaded=True)

if __name__=="__main__":
    main()
