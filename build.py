"""Baja noticias de IA por RSS, las resume con Claude y genera el sitio en /public."""
import os, re, json, html, hashlib, sys, calendar
from datetime import datetime, timedelta, timezone

# ---------- CONFIGURACIÓN ----------
SITE_NAME = "IA Noticias"
SITE_URL = "https://i-a.net.ar"
MODEL = "claude-haiku-5-5"
MAX_NEW_PER_RUN = 15        # tope de noticias nuevas por día
KEEP = 400                  # cuántas noticias guardar en total
DAYS_BACK = 2               # solo noticias de los últimos N días
USE_SOURCE_IMAGES = False   # True = usar la foto de la fuente (ver aviso de derechos de autor)
# (nombre, url del feed, ¿filtrar por palabras de IA?)
FEEDS = [
    ("OpenAI", "https://openai.com/news/rss.xml", False),
    ("Google DeepMind", "https://deepmind.google/blog/rss.xml", False),
    ("Hugging Face", "https://huggingface.co/blog/feed.xml", False),
    ("MIT Technology Review", "https://www.technologyreview.com/topic/artificial-intelligence/feed", False),
    ("The Verge", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", False),
    ("TechCrunch", "https://techcrunch.com/category/artificial-intelligence/feed/", False),
    ("VentureBeat", "https://venturebeat.com/category/ai/feed/", False),
]
CATEGORIAS = ["Modelos", "Empresas", "Investigación", "Regulación", "Herramientas", "Otros"]
PROMPT = """Sos editor de un sitio argentino de noticias sobre inteligencia artificial.
Con los datos de abajo, escribí en español neutro y con tus propias palabras (no copies frases del original).
Respondé SOLO con un JSON con estas claves:
"titulo": titular claro de hasta 90 caracteres
"resumen": 2 o 3 oraciones con lo esencial
"importa": 1 oración sobre por qué importa
"categoria": una de {cats}
Si la nota NO trata sobre inteligencia artificial, respondé {{"skip": true}}.

Fuente: {fuente}
Título original: {titulo}
Extracto: {extracto}"""
# -----------------------------------

DATA = "data/articles.json"
MESES = ["enero","febrero","marzo","abril","mayo","junio","julio","agosto","septiembre","octubre","noviembre","diciembre"]
AI_WORDS = re.compile(r"\b(ai|a\.i\.|llm|gpt|chatgpt|claude|gemini|openai|anthropic|deepmind|machine learning|neural|inteligencia artificial)\b", re.I)

def limpiar(t):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t or ""))).strip()

def imagen_de(e):
    for k in ("media_content", "media_thumbnail"):
        for m in e.get(k, []) or []:
            if m.get("url"): return m["url"]
    for l in e.get("links", []):
        if l.get("type", "").startswith("image") and l.get("href"): return l["href"]
    m = re.search(r'<img[^>]+src="([^"]+)"', e.get("summary", "") or "")
    return m.group(1) if m else ""

def fecha_de(e):
    t = e.get("published_parsed") or e.get("updated_parsed")
    return datetime.fromtimestamp(calendar.timegm(t), timezone.utc) if t else datetime.now(timezone.utc)

def cargar():
    try:
        return json.load(open(DATA, encoding="utf-8"))
    except Exception:
        return []

def recolectar(vistos):
    import feedparser
    limite = datetime.now(timezone.utc) - timedelta(days=DAYS_BACK)
    out = []
    for nombre, url, filtrar in FEEDS:
        try:
            feed = feedparser.parse(url)
        except Exception as ex:
            print("Feed con error:", nombre, ex); continue
        for e in feed.entries:
            link = e.get("link", "")
            f = fecha_de(e)
            if not link or link in vistos or f < limite: continue
            titulo = limpiar(e.get("title"))
            extracto = limpiar(e.get("summary") or e.get("description"))[:1500]
            if filtrar and not AI_WORDS.search(titulo + " " + extracto): continue
            out.append(dict(link=link, fecha=f.isoformat(), fuente=nombre, orig=titulo,
                            extracto=extracto, imagen=imagen_de(e) if USE_SOURCE_IMAGES else ""))
    out.sort(key=lambda a: a["fecha"], reverse=True)
    return out[:MAX_NEW_PER_RUN]

import urllib.request, urllib.error, time
GEMINI_MODELS = [m.strip() for m in os.environ.get("GEMINI_MODEL", "").split(",") if m.strip()] or \
    ["gemini-flash-latest", "gemini-3.8-flash", "gemini-flash-lite-latest"]
_ok = {}

def llamar_gemini(prompt):
    clave = os.environ["GEMINI_API_KEY"]
    ultimo = "sin respuesta"
    orden = ([_ok["m"]] if "m" in _ok else []) + [m for m in GEMINI_MODELS if m != _ok.get("m")]
    for modelo in orden:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
        body = json.dumps({"contents": [{"parts": [{"text": prompt}]}],
                           "generationConfig": {"responseMimeType": "application/json"}}).encode()
        for _ in range(3):
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "x-goog-api-key": clave})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    d = json.load(r)
                _ok["m"] = modelo
                time.sleep(6)  # respeta el límite por minuto del plan gratuito
                return "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"])
            except urllib.error.HTTPError as ex:
                ultimo = f"{modelo}: HTTP {ex.code} {ex.read()[:200]}"
                if ex.code in (429, 503):  # saturado o límite: esperar y reintentar
                    time.sleep(25); continue
                break
    raise RuntimeError(ultimo)

def resumir(client, a):
    prompt = PROMPT.format(cats=", ".join(CATEGORIAS), fuente=a["fuente"], titulo=a["orig"], extracto=a["extracto"])
    if client == "gemini":
        txt = llamar_gemini(prompt)
    else:
        msg = client.messages.create(model=MODEL, max_tokens=1500, messages=[{"role": "user", "content": prompt}])
        txt = "".join(b.text for b in msg.content if b.type == "text")
    m = re.search(r"\{.*\}", txt, re.S)
    d = json.loads(m.group(0))
    if d.get("skip"): return None
    cat = d.get("categoria") if d.get("categoria") in CATEGORIAS else "Otros"
    return dict(id=a["link"], link=a["link"], fecha=a["fecha"], fuente=a["fuente"], imagen=a["imagen"],
                titulo=d["titulo"].strip(), resumen=d["resumen"].strip(), importa=d.get("importa", "").strip(),
                categoria=cat, hue=int(hashlib.md5(a["link"].encode()).hexdigest()[:4], 16) % 360)

def fecha_es(iso):
    d = datetime.fromisoformat(iso)
    return f"{d.day} de {MESES[d.month-1]} de {d.year}"

E = lambda s: html.escape(s or "", quote=True)

ICONOS = {
    "Modelos": '<rect x="18" y="18" width="28" height="28" rx="3"/><rect x="26" y="26" width="12" height="12"/><path d="M24 10v8M32 10v8M40 10v8M24 46v8M32 46v8M40 46v8M10 24h8M10 32h8M10 40h8M46 24h8M46 32h8M46 40h8"/>',
    "Empresas": '<path d="M14 52V12h24v40M38 26h12v26M10 52h44M21 20h4M27 20h4M21 28h4M27 28h4M21 36h4M27 36h4M43 34h2M43 42h2"/>',
    "Investigación": '<path d="M26 10h12M28 10v16L16 50a4 4 0 0 0 4 6h24a4 4 0 0 0 4-6L36 26V10M22 40h20"/>',
    "Regulación": '<path d="M32 10v40M20 52h24M14 20h36M14 20L6 36h16zM50 20l-8 16h16z"/>',
    "Herramientas": '<path d="M10 20h44M10 32h44M10 44h44"/><circle cx="22" cy="20" r="4"/><circle cx="42" cy="32" r="4"/><circle cx="28" cy="44" r="4"/>',
    "Otros": '<path d="M32 8l6 18 18 6-18 6-6 18-6-18-18-6 18-6z"/>',
}

def portada(a, grande=False):
    if a.get("imagen"):
        return f'<img class="cover" src="{E(a["imagen"])}" alt="" loading="lazy" referrerpolicy="no-referrer">'
    h = a["hue"]
    ico = ICONOS.get(a["categoria"], ICONOS["Otros"])
    return (f'<div class="cover gen" style="--h:{h}"><svg class="ico" viewBox="0 0 64 64" fill="none" stroke="#fff" '
            f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{ico}</svg>'
            f'<span>{E(a["categoria"])}</span></div>')

def tarjeta(a, destacada=False):
    imp = f'<p class="imp"><b>Por qué importa:</b> {E(a["importa"])}</p>' if a.get("importa") else ""
    return (f'<article class="{"top" if destacada else "card"}">{portada(a)}<div class="txt">'
            f'<p class="meta">{E(a["categoria"])}, {E(a["fuente"])}, {fecha_es(a["fecha"])}</p>'
            f'<h2>{E(a["titulo"])}</h2><p>{E(a["resumen"])}</p>{imp}'
            f'<a class="src" href="{E(a["link"])}" target="_blank" rel="noopener nofollow">Leer la nota original en {E(a["fuente"])}</a>'
            f'</div></article>')

CSS = """
:root{--bg:#f2f4f7;--ink:#0f1b2d;--mut:#55627a;--line:#d5dbe6;--acc:#1f4bff;--card:#fff;box-sizing:border-box;
padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}
@media(prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#0c1220;--ink:#e8edf7;--mut:#9aa8c2;--line:#26324a;--acc:#7b97ff;--card:#131c30}}
*{box-sizing:border-box}html{scroll-padding-top:env(safe-area-inset-top,0px)}
body{margin:0;background:var(--bg);color:var(--ink);font:17px/1.6 "Source Serif 4",Georgia,serif}
a{color:inherit}header,main,footer{max-width:1040px;margin:0 auto;padding:0 20px}
header{display:flex;justify-content:space-between;align-items:baseline;gap:16px;padding-top:28px;padding-bottom:18px;border-bottom:2px solid var(--ink)}
.logo{font:800 30px/1 "Bricolage Grotesque",Arial,sans-serif;text-decoration:none;letter-spacing:-.02em}
.logo i{font-style:normal;color:var(--acc)}
nav a{font:15px "Bricolage Grotesque",Arial,sans-serif;color:var(--mut);margin-left:16px}
h1.lead{font:700 15px "Bricolage Grotesque",Arial,sans-serif;color:var(--mut);margin:22px 0 0}
h3.dia{font:700 22px "Bricolage Grotesque",Arial,sans-serif;margin:36px 0 14px;padding-bottom:8px;border-bottom:1px solid var(--line)}
h2{font:700 21px/1.25 "Bricolage Grotesque",Arial,sans-serif;margin:6px 0 8px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:22px}
.card,.top{background:var(--card);border:1px solid var(--line);border-radius:6px;overflow:hidden;display:flex;flex-direction:column}
.top{margin-top:16px;flex-direction:row}.top .cover{width:46%;min-height:280px}.top h2{font-size:30px}
.top .txt{padding:24px}.card .txt{padding:16px 18px 20px}
.cover{display:block;width:100%;aspect-ratio:16/9;object-fit:cover}
.gen{position:relative;overflow:hidden;background:radial-gradient(circle at 85% 15%,rgba(255,255,255,.22),transparent 55%),linear-gradient(135deg,hsl(var(--h) 75% 48%),hsl(calc(var(--h) + 55) 70% 28%));display:flex;align-items:flex-end;padding:14px}\n.ico{position:absolute;right:6%;top:50%;transform:translateY(-55%);width:46%;max-width:190px;opacity:.38}
.gen span{font:700 22px "Bricolage Grotesque",Arial,sans-serif;color:#fff;text-shadow:0 1px 8px rgba(0,0,0,.35)}
.meta{font:14px "Bricolage Grotesque",Arial,sans-serif;color:var(--mut);margin:0}
.txt p{margin:0 0 10px}.imp{font-size:15px;color:var(--mut)}
.src{font:600 15px "Bricolage Grotesque",Arial,sans-serif;color:var(--acc)}a:focus-visible{outline:3px solid var(--acc);outline-offset:2px}
.ad{margin:28px 0;min-height:90px}footer{padding-top:40px;padding-bottom:40px;color:var(--mut);font-size:14px}
@media(max-width:720px){.top{flex-direction:column}.top .cover{width:100%;min-height:0}.top h2{font-size:24px}}
"""

def pagina(titulo, desc, cuerpo):
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{E(titulo)}</title><meta name="description" content="{E(desc)}">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='.9em' font-size='90'%3E%F0%9F%A4%96%3C/text%3E%3C/svg%3E">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:wght@500;700;800&family=Source+Serif+4:wght@400;600&display=swap" rel="stylesheet">
<!-- AQUÍ va el script de Google AdSense cuando te aprueben -->
<style>{CSS}</style></head><body>
<header><a class="logo" href="/">{SITE_NAME.split()[0]}<i> {SITE_NAME.split()[1]}</i></a>
<nav><a href="/">Inicio</a><a href="/acerca.html">Acerca y privacidad</a></nav></header>
<main>{cuerpo}</main>
<footer>{SITE_NAME}. Resúmenes automáticos hechos con inteligencia artificial a partir de fuentes públicas; cada nota enlaza al original, que pertenece a su autor.</footer>
</body></html>"""

def sitio(arts):
    arts = sorted(arts, key=lambda a: a["fecha"], reverse=True)[:KEEP]
    cuerpo = '<h1 class="lead">Las noticias de inteligencia artificial, resumidas en español</h1>'
    if not arts:
        cuerpo += "<p>Todavía no hay noticias. Volvé mañana.</p>"
    else:
        cuerpo += tarjeta(arts[0], True) + '<div class="ad"><!-- ANUNCIO 1 --></div>'
        dia, grid = None, []
        for a in arts[1:]:
            d = a["fecha"][:10]
            if d != dia:
                if grid: cuerpo += '<div class="grid">' + "".join(grid) + "</div>"
                grid = []; dia = d
                cuerpo += f'<h3 class="dia">{fecha_es(a["fecha"])}</h3>'
            grid.append(tarjeta(a))
        if grid: cuerpo += '<div class="grid">' + "".join(grid) + "</div>"
    os.makedirs("public", exist_ok=True)
    open("public/index.html", "w", encoding="utf-8").write(pagina(f"{SITE_NAME}: noticias de inteligencia artificial", "Noticias diarias de inteligencia artificial resumidas en español, con enlace a la fuente.", cuerpo))
    acerca = f"""<h1 class="lead">Acerca de este sitio</h1><h2>Cómo funciona</h2>
<p>{SITE_NAME} reúne cada día novedades de inteligencia artificial publicadas por medios y laboratorios. Un sistema de IA escribe un resumen breve en español y siempre enlazamos a la nota original.</p>
<h2>Política de privacidad</h2><p>Este sitio no pide registro ni guarda datos personales. Si se muestra publicidad (por ejemplo, Google AdSense), terceros pueden usar cookies para mostrar anuncios según visitas anteriores. Podés desactivar la publicidad personalizada desde la configuración de anuncios de Google.</p>
<h2>Contacto</h2><p>Escribinos a: <b>(poné acá tu email)</b></p>"""
    open("public/acerca.html", "w", encoding="utf-8").write(pagina(f"Acerca de {SITE_NAME}", "Cómo funciona el sitio y política de privacidad.", acerca))
    open("public/sitemap.xml", "w").write(f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>{SITE_URL}/</loc></url></urlset>')
    open("public/robots.txt", "w").write(f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}/sitemap.xml\n")

def main():
    arts = cargar()
    if "--solo-sitio" not in sys.argv:
        if os.environ.get("ANTHROPIC_API_KEY"):
            import anthropic
            client = anthropic.Anthropic()
        elif os.environ.get("GEMINI_API_KEY"):
            client = "gemini"
        else:
            sys.exit("Falta la clave: cargá GEMINI_API_KEY o ANTHROPIC_API_KEY en los Secrets de GitHub.")
        for a in recolectar({x["id"] for x in arts}):
            try:
                r = resumir(client, a)
                if r: arts.append(r); print("OK:", r["titulo"])
            except Exception as ex:
                print("Falló una nota:", a["orig"], ex)
        arts = sorted(arts, key=lambda a: a["fecha"], reverse=True)[:KEEP]
        os.makedirs("data", exist_ok=True)
        json.dump(arts, open(DATA, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    sitio(arts)

if __name__ == "__main__":
    main()
