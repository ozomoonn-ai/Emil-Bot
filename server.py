# server.py — Emil v1.7 (Gutshof Gin only, + Cocktail-Vorschläge & -Generator)

from __future__ import annotations
import os, re, json, logging, unicodedata, difflib, random
from typing import List, Dict, Any, Optional

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ---------- Boot ----------
load_dotenv()
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("emil")

SHOP_DOMAIN = (os.getenv("SHOPIFY_STOREFRONT_DOMAIN") or "").replace("https://","").replace("http://","")
SHOP_TOKEN  = os.getenv("SHOPIFY_STOREFRONT_TOKEN") or ""
SHOP_URL_BASE = os.getenv("SHOP_URL_BASE") or ""
SHOP_API_VERSION = os.getenv("SHOPIFY_API_VERSION", "2025-01")
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT","15"))

ALLOWED_ORIGINS = [o.strip() for o in (os.getenv("ALLOWED_ORIGINS") or "").split(",") if o.strip()]
if not ALLOWED_ORIGINS: ALLOWED_ORIGINS = ["*"]

app = FastAPI(title="Emil – Gutshof Gin Bot", version="1.7", docs_url="/docs", redoc_url="/redoc")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["POST","GET","OPTIONS"],
    allow_headers=["*"],
)

# ---------- Utils: Normalisierung ----------
def to_ascii_digraphs(s: str) -> str:
    return (s.replace("Ä","Ae").replace("Ö","Oe").replace("Ü","Ue")
             .replace("ä","ae").replace("ö","oe").replace("ü","ue")
             .replace("ß","ss"))

def norm(s: str) -> str:
    s = to_ascii_digraphs(s).lower().strip()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

# ---------- Domain Guard (nur Gin) ----------
GIN_KEYWORDS = [
    "gin","gutshof","grimm","rotkaeppchen","rotkäppchen","froschkoenig","froschkönig",
    "aschenputtel","sterntaler","classic","limetta","mandarina","rosata",
    "botanicals","tonic","cocktail","longdrink","martini","negroni","collins",
    "geschenk","foodpairing","pairing","versand","alkohol","abv","prozent","inhalt","rezept","zutaten"
]
def is_on_topic(q: str) -> bool:
    nq = norm(q)
    if any(k in nq for k in [norm(k) for k in GIN_KEYWORDS]): return True
    off = [r"\b(bank|passwort|bitcoin|steuer|recht|medizin|dating|politik|wetter|fussball|fußball)\b",
           r"\b(hack|exploit|illegal|waffe|drogen)\b"]
    return not any(re.search(p, nq) for p in off)

# ---------- Intents ----------
CATS = ["rotkaeppchen","froschkoenig","aschenputtel","sterntaler","classic","limetta","mandarina","rosata"]
KEYWORDS = {
    "rotkaeppchen": ["rotkaeppchen","rotkappchen","rotkapchen","rotkapschen","rotkaepschen","rotkäppchen"],
    "froschkoenig": ["froschkoenig","frosch konig","froschkönig"],
    "aschenputtel": ["aschenputtel","aschen puttel"],
    "sterntaler":   ["sterntaler","stern taler"],
    "classic":      ["classic","klassik","gutshof classic"],
    "limetta":      ["limetta"],
    "mandarina":    ["mandarina"],
    "rosata":       ["rosata","rosa ta"],
}
ALIASES = {"rotkapchen":"rotkaeppchen","rotkapp":"rotkaeppchen","rotkaep":"rotkaeppchen"}

def extract_intent(q: str) -> Optional[str]:
    nq = norm(q)
    if nq in ALIASES: return ALIASES[nq]
    for cat, kws in KEYWORDS.items():
        for kw in kws:
            if kw in nq: return cat
    for t in nq.split():
        for cat, kws in KEYWORDS.items():
            if any(kw == t or kw in t or t in kw for kw in kws): return cat
    all_kws = [k for ks in KEYWORDS.values() for k in ks]
    m = difflib.get_close_matches(nq, all_kws, n=1, cutoff=0.72)
    if m:
        mk = m[0]
        for cat, ks in KEYWORDS.items():
            if mk in ks: return cat
    return None

# ---------- Rezepte, Pairings, Klassiker ----------
RECIPES_BUILTIN = [
    {"name":"Gutshof Gin & Tonic","tags":["classic","frisch"],
     "ingredients":["50 ml Gutshof Gin Classic","150 ml Dry Tonic","Limettenscheibe","Eis"],
     "gins":["classic"],"instructions":"Highball mit Eis, Gin dazu, mit Tonic aufgießen, sanft rühren, Limette."},
    {"name":"Rotkäppchen Spritz","tags":["fruchtig","leicht"],
     "ingredients":["40 ml Rotkäppchen","90 ml Tonic oder Soda","Erdbeere","Eis"],
     "gins":["rotkaeppchen"],"instructions":"Weinglas mit Eis, Rotkäppchen, mit Tonic/Soda auffüllen, Erdbeere."},
    {"name":"Froschkönig Basil Smash","tags":["kräuterig","frisch"],
     "ingredients":["50 ml Froschkönig","20 ml Zitrone","10 ml Zuckersirup","Basilikum","Eis"],
     "gins":["froschkoenig"],"instructions":"Basilikum andrücken, alles kräftig shaken, auf Eis seihen."},
]

# Klassiker (werden als Vorschläge ausgespielt – GIN ONLY)
CLASSICS = [
    {"name":"Tom Collins","gins":["classic","limetta"],
     "ingredients":["50 ml Gin","20 ml Zitrone","15 ml Zuckersirup","Soda","Eis"],
     "instructions":"Im Highball auf Eis bauen, mit Soda auffüllen, kurz rühren, Zitrone."},
    {"name":"Negroni","gins":["classic","mandarina"],
     "ingredients":["30 ml Gin","30 ml Campari","30 ml Süßer Wermut","Eis","Orangen-Zeste"],
     "instructions":"Rühren auf Eis, ins Tumbler abseihen, Orangen-Zeste ausdrücken."},
    {"name":"French 75","gins":["classic","sterntaler"],
     "ingredients":["30 ml Gin","15 ml Zitrone","10 ml Zuckersirup","Sekt/Champagner"],
     "instructions":"Gin, Zitrone, Sirup shaken, ins Sektglas, mit Sekt auffüllen."},
    {"name":"Gin Basil Smash","gins":["froschkoenig","classic"],
     "ingredients":["50 ml Gin","20 ml Zitrone","10 ml Zuckersirup","viel Basilikum"],
     "instructions":"Basilikum andrücken, shaken, doppelt abseihen."},
    {"name":"Bramble","gins":["classic","rosata"],
     "ingredients":["40 ml Gin","20 ml Zitrone","10 ml Zuckersirup","10–15 ml Brombeerlikör","Eis"],
     "instructions":"Gin, Zitrone, Sirup shaken, Crushed Ice, Brombeerlikör floaten."},
]

PAIRINGS = {
    "classic": ["Zitruslastige Speisen","Austern & Meeresfrüchte","Ziegenkäse","Oliven & Mandeln"],
    "rotkaeppchen": ["Erdbeer-Desserts","Käsekuchen","Ziegenkäse mit Honig","Vanilleeis"],
    "froschkoenig": ["Basilikum-Pesto","Caprese","Gegrilltes Gemüse","Helles Geflügel"],
    "aschenputtel": ["Apfelstrudel","Gebrannte Mandeln","Weichkäse","Zimtdesserts"],
    "sterntaler":   ["Zitruskuchen","Pannacotta","Aperitivo-Häppchen","Leichte Salate"],
    "limetta":      ["Ceviche","Tacos mit Limette","Frische Salate","Grüner Apfel"],
    "mandarina":    ["Orangensorbet","Ente mit Orangensauce","Dunkle Schokolade","Karamell"],
    "rosata":       ["Beeren-Tartes","Panna Cotta","Prosciutto & Melone","Frische Erdbeeren"],
}

FAQS = {
    "alkohol":"Unsere Gins liegen typischerweise bei 40–45 % vol. (Produktseite zeigt den exakten Wert).",
    "versand":"Deutschlandweit meist 2–4 Werktage. Kosten & Details im Checkout.",
    "botanicals":"Wacholder, Zitrus; je Edition passende Akzente (z. B. Erdbeere bei Rotkäppchen, Basilikum-Note beim Froschkönig).",
    "geschenk":"Beliebt: Märchen-Set oder Classic + 2 Nosing-Gläser. Ich kann dir direkt Produktlinks schicken.",
}
SUGGESTIONS_DEFAULT = ["Zeig Rotkäppchen","Foodpairing Classic","Rezept mit Froschkönig","Geschenkideen","Cocktail-Ideen"]

# ---------- Shopify ----------
def shopify_search_by_title(fragment: str) -> List[Dict[str, Any]]:
    if not SHOP_DOMAIN or not SHOP_TOKEN:
        log.warning("Shopify nicht konfiguriert.")
        return []
    url = f"https://{SHOP_DOMAIN}/api/{SHOP_API_VERSION}/graphql.json"
    headers = {"Content-Type":"application/json","X-Shopify-Storefront-Access-Token": SHOP_TOKEN}
    gql = """
    query($q: String!) {
      products(first: 10, query: $q) {
        edges {
          node {
            title handle
            variants(first:1){ edges{ node{ price { amount currencyCode }}}}
            images(first:1){ edges{ node{ url } } }
          }
        }
      }
    }
    """
    variables = {"q": f"title:{fragment} OR tag:{fragment}"}
    try:
        r = requests.post(url, headers=headers, json={"query": gql, "variables": variables}, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        items = []
        for e in (data.get("data",{}).get("products",{}).get("edges") or []):
            n = e["node"]
            price_edge = (n.get("variants",{}).get("edges") or [{}])[0].get("node",{})
            price = price_edge.get("price",{})
            img_edge = (n.get("images",{}).get("edges") or [{}])[0].get("node",{})
            items.append({
                "title": n.get("title"),
                "url": f"{SHOP_URL_BASE}/products/{n.get('handle')}" if SHOP_URL_BASE else "",
                "image": img_edge.get("url") or "",
                "price": price.get("amount"),
                "currency": price.get("currencyCode") or "EUR"
            })
        return items
    except Exception as ex:
        log.exception("Shopify search error: %s", ex)
        return []

def find_products_for_intent(intent: str) -> List[Dict[str, Any]]:
    frag = {
        "rotkaeppchen":"Rotkäppchen","froschkoenig":"Froschkönig","aschenputtel":"Aschenputtel",
        "sterntaler":"Sterntaler","classic":"Classic","limetta":"Limetta","mandarina":"Mandarina","rosata":"Rosata"
    }.get(intent,intent)
    items = shopify_search_by_title(frag)
    if not items and SHOP_URL_BASE:
        items = [{"title": frag,"url": f"{SHOP_URL_BASE}/search?q={frag}","image":"","price":"","currency":"EUR"}]
    return items

# ---------- Rezepte-Lookup ----------
def load_extra_recipes() -> List[Dict[str, Any]]:
    try:
        if os.path.exists("recipes.json"):
            with open("recipes.json","r",encoding="utf-8") as f: return json.load(f)
    except Exception as ex:
        log.warning("recipes.json nicht lesbar: %s", ex)
    return []

def find_recipes_for_intent(intent: str) -> List[Dict[str, Any]]:
    res = [r for r in RECIPES_BUILTIN if intent in [norm(g) for g in r.get("gins",[])]]
    for r in load_extra_recipes():
        if intent in [norm(g) for g in r.get("gins",[])]: res.append(r)
    return res

# ---------- Klassiker & Editions-Vorschläge ----------
def classic_suggestions(prefer: Optional[str]) -> List[Dict[str, Any]]:
    # priorisiere passend zur Edition, sonst mischen
    arr = []
    if prefer:
        for c in CLASSICS:
            if prefer in [norm(g) for g in c["gins"]]: arr.append(c)
    # auffüllen mit allgemeinen
    for c in CLASSICS:
        if c not in arr: arr.append(c)
    return arr[:4]

# ---------- Cocktail-Generator ----------
# Erzeugt ein sinnvolles Rezept aus frei genannten Zutaten
UNIT_ML = {"schuss":10,"dash":1,"spritzer":2}
CITRUS = ["zitrone","limette","orange","grapefruit","yuzu"]
SWEET  = ["zuckersirup","honig","agave","ahornsirup","vanillesirup","grenadine"]
BITTER = ["angostura","campari","aperol","amaro","orange bitters","peychaud","bitter"]
HERBS  = ["basilikum","minze","rosmarin","thymian","salbei","gurke"]
BUBBLY = ["soda","tomic","tonic","sekt","champagner","prosecco","sodawasser","soda wasser","soda water","soda-water","soda water"]
FRUITY = ["erdbeere","himbeere","brombeere","maracuja","pfirsich","aprikose","ananas","mandarine"]

def parse_ingredients_freeform(q: str) -> List[str]:
    # alles nach "mit ..." / "mit:" / "aus ..." / "zutaten ..." splitten
    nq = norm(q)
    parts = []
    for key in ["mit","aus","zutaten","zutat","verwende","using"]:
        if f" {key} " in nq:
            parts = nq.split(f" {key} ",1)[1]
            break
    if not parts: parts = nq
    # Split an Kommas/und/plus
    toks = re.split(r"[,\+]| und | plus ", parts)
    toks = [t.strip() for t in toks if t.strip()]
    # entferne Füllwörter
    cleaned = []
    for t in toks:
        t = re.sub(r"\b(bisschen|etwas|ein wenig|klein|kleine|kleinen|groß|gross|große|großen)\b","",t).strip()
        if t: cleaned.append(t)
    return cleaned[:8]

def pick_base(intent: Optional[str]) -> str:
    # wähle passenden Gutshof Gin
    return {
        "rotkaeppchen":"Rotkäppchen",
        "froschkoenig":"Froschkönig",
        "aschenputtel":"Aschenputtel",
        "sterntaler":"Sterntaler",
        "limetta":"Limetta",
        "mandarina":"Mandarina",
        "rosata":"Rosata",
        "classic":"Gutshof Gin Classic",
        None:"Gutshof Gin Classic"
    }[intent if intent in CATS else None]

def generate_cocktail(asked: List[str], intent: Optional[str]) -> Dict[str, Any]:
    base = pick_base(intent)
    # heuristik: baue einen balancierten Sour/Collins/Highball je nach Zutaten
    has_citrus = any(any(c in a for c in CITRUS) for a in asked)
    has_bubbly = any(any(b in a for b in BUBBLY) for a in asked)
    has_sweet  = any(any(s in a for s in SWEET) for a in asked)
    has_herbs  = any(any(h in a for h in HERBS) for a in asked)
    has_bitter = any(any(b in a for b in BITTER) for a in asked)
    has_fruit  = any(any(f in a for f in FRUITY) for a in asked)

    name_pool = []
    style = ""
    method = "shaken"
    glass = "Tumbler"

    if has_bubbly and has_citrus:
        style = "Spritz"
        method = "bauen"
        glass = "Weinglas"
        name_pool = ["Gutshof Spritz","Märchen-Spritz","Sterntaler Fizz","Rotkäppchen Spritz"]
    elif has_citrus:
        style = "Sour/Collins"
        method = "shaken"
        glass = "Highball"
        name_pool = ["Basil Collins","Grimm Sour","Forest Smash","Citrus Smash"]
    elif has_bitter:
        style = "Aperitif (rühren)"
        method = "gerührt"
        glass = "Tumbler"
        name_pool = ["Mandarina Boulevard","Gutshof Negroni Twist","Amber Grimm"]
    else:
        style = "Highball"
        method = "bauen"
        glass = "Highball"
        name_pool = ["Gutshof Highball","Meadow Tonic","Garden Highball"]

    chosen_name = random.choice(name_pool)

    # Mengen – baseline
    ml_gin = 50
    ml_citrus = 20 if has_citrus else 0
    ml_sweet = 10 if (has_citrus and not has_bubbly) or has_bitter else (8 if has_citrus else 0)
    top_bubbly = "mit Soda auffüllen" if has_bubbly else ""
    bitters_line = "1–2 Dashes Bitters" if has_bitter else ""
    herb_line = "einige Blätter Basilikum andrücken" if has_herbs else ""
    fruit_garnish = "frische Beeren" if has_fruit else ""

    ingredients = [f"{ml_gin} ml {base}"]
    if has_citrus: ingredients.append("20 ml frischer Zitrussaft")
    if ml_sweet:   ingredients.append(f"{ml_sweet} ml Zuckersirup")
    if has_bitter: ingredients.append("1–2 Dashes Bitters")
    if has_bubbly: ingredients.append("Soda / Tonic / Sekt (auffüllen)")
    if has_herbs:  ingredients.append("Basilikum/Minze (frisch)")
    if has_fruit:  ingredients.append("Beeren (optional)")

    instructions = []
    if method == "shaken":
        instructions.append("Shaker mit Eis füllen.")
        if has_herbs: instructions.append("Kräuter leicht andrücken, nicht zerreißen.")
        instructions.append("Gin, Saft und Sirup zugeben, kräftig shaken.")
        instructions.append(f"In {glass} auf frisches Eis abseihen.")
    elif method == "gerührt":
        instructions.append("Rühren in einem Rührglas mit Eis, dann in Tumbler mit großem Eiswürfel abseihen.")
    else:
        instructions.append(f"Im {glass} direkt auf Eis bauen und kurz rühren.")
    if has_bubbly: instructions.append("Mit Bubbly auffüllen (nach Geschmack).")
    if has_bitter and method != "gerührt": instructions.append("Bitters zum Schluss nach Geschmack.")
    if herb_line: instructions.append("Mit frischen Kräutern sanft anklatschen und garnieren.")
    if fruit_garnish: instructions.append("Mit Beeren garnieren.")
    instructions.append("Cheers!")

    tags = [style.lower()]
    if has_citrus: tags.append("zitrus")
    if has_herbs:  tags.append("kräuter")
    if has_bubbly: tags.append("spritz")
    if has_bitter: tags.append("bitter")
    if has_fruit:  tags.append("fruchtig")

    return {
        "name": chosen_name,
        "gins": [norm(intent) if intent else "classic"],
        "ingredients": ingredients,
        "instructions": " ".join(instructions),
        "tags": tags
    }

def wants_custom_cocktail(nq: str) -> bool:
    # Auslöser: "mach mir ... mit", "cocktail mit", "drink mit", "rezept mit", "aus ..."
    triggers = ["cocktail mit","drink mit","rezept mit","mach mir","mach einen","aus ","zutaten","mit "]
    return any(t in nq for t in triggers) and (" mit " in nq or " aus " in nq or " zutaten" in nq)

# ---------- FAQ ----------
def faq_answer(q: str) -> Optional[str]:
    nq = norm(q)
    if "versand" in nq or "liefer" in nq: return FAQS["versand"]
    if "alkohol" in nq or "prozent" in nq or "abv" in nq: return FAQS["alkohol"]
    if "botanical" in nq or "zutaten" in nq or "aroma" in nq: return FAQS["botanicals"]
    if "geschenk" in nq or "present" in nq or "gift" in nq: return FAQS["geschenk"]
    return None

# ---------- Schemas ----------
class ChatIn(BaseModel):
    message: str = Field(..., description="User-Eingabe")

class ProductCard(BaseModel):
    title: str
    url: str = ""
    image: str = ""
    price: str | float | None = None
    currency: str | None = None

class RecipeCard(BaseModel):
    name: str
    tags: List[str] = []
    ingredients: List[str] = []
    instructions: str
    gins: List[str] = []

class ChatOut(BaseModel):
    response: str
    products: Optional[List[ProductCard]] = None
    recipes: Optional[List[RecipeCard]] = None
    pairings: Optional[List[str]] = None
    suggestions: Optional[List[str]] = None

# ---------- Routes ----------
@app.get("/health")
def health():
    return {"status":"ok","service":"Emil"}

@app.get("/diag/env")
def diag_env():
    return {
        "domain": SHOP_DOMAIN or None,
        "token_present": bool(SHOP_TOKEN),
        "api_version": SHOP_API_VERSION,
        "allowed_origins": ALLOWED_ORIGINS,
        "shop_url_base": SHOP_URL_BASE or "",
    }

@app.get("/diag/norm")
def diag_norm(q: str):
    return {"raw": q, "normalized": norm(q), "intent": extract_intent(q)}

@app.post("/chat", response_model=ChatOut)
def chat(body: ChatIn, request: Request):
    q_raw = body.message or ""
    q = q_raw.strip()
    nq = norm(q)

    # Guard: nur Gin
    if not is_on_topic(q):
        return ChatOut(
            response="Ich helfe ausschließlich zu Gutshof Gin: Produkte, Rezepte, Cocktail-Ideen, Foodpairing, Geschenkideen & FAQs.",
            suggestions=SUGGESTIONS_DEFAULT
        )

    # Smalltalk
    if any(w in nq for w in ["hallo","hi","servus","hey","moin"]):
        return ChatOut(
            response="Servus! Lust auf eine Empfehlung, ein Rezept, Foodpairing oder Geschenkidee?",
            suggestions=SUGGESTIONS_DEFAULT
        )

    # FAQ
    fa = faq_answer(q)
    if fa:
        return ChatOut(response=fa, suggestions=SUGGESTIONS_DEFAULT)

    # Intent (Edition)
    intent = extract_intent(q)

    # --- NEU: Benutzer will frei „Cocktail mit …“ ---
    if wants_custom_cocktail(nq):
        want_ings = parse_ingredients_freeform(q)
        # Wenn gar nichts extrahiert, bitte lenken.
        if not want_ings:
            return ChatOut(
                response="Sag mir Zutaten, z. B.: „Mach mir einen Cocktail mit Limette, Basilikum und Soda.“",
                suggestions=["Cocktail mit Limette & Basilikum","Drink mit Orange & Campari","Fruchtig: Erdbeere & Soda"]
            )
        recipe = generate_cocktail(want_ings, intent)
        head = "Deine individuelle Cocktail-Idee 🍸"
        return ChatOut(
            response=head,
            recipes=[RecipeCard(**recipe)],
            suggestions=["Noch eine Variante?","Zeig passende Produkte","Foodpairing"]
        )

    # Will explizit „Vorschläge“, „Cocktail Ideen“, „Rezepte“
    if any(k in nq for k in ["vorschlae","vorschlag","ideen","rezepte","cocktail ideen","cocktailideen","drinks"]):
        # Vorschläge passend zur Edition falls erkannt
        preferred = intent if intent in CATS else None
        picks = classic_suggestions(preferred)
        return ChatOut(
            response="Hier sind Cocktail-Ideen, die mit unseren Gins super funktionieren:",
            recipes=[RecipeCard(**{
                "name": r["name"],
                "tags": ["klassiker"],
                "ingredients": r["ingredients"],
                "instructions": r["instructions"],
                "gins": r["gins"],
            }) for r in picks],
            suggestions=["Mach mir was mit Limette","Foodpairing-Tipp","Zeig Rotkäppchen"]
        )

    # „Rezept“ erwähnt ohne freie Zutaten → rezepte je Edition zeigen
    wants_recipe = any(k in nq for k in ["rezept","cocktail","drink","mixen"])
    wants_pairing = any(k in nq for k in ["pair","food","essen","passt zu","pairing"])

    # unspezifisch „zeige gin“
    if not intent and any(k in nq for k in ["gin","edition","produkt","produkte","zeigen","zeige","shop"]):
        prods = shopify_search_by_title("Gin")
        if prods:
            return ChatOut(response="Hier sind ein paar Gins aus dem Sortiment:", products=prods, suggestions=SUGGESTIONS_DEFAULT)
        return ChatOut(response="Ich habe nichts Passendes gefunden. Sag z. B. „Zeig Rotkäppchen“.")    

    if not intent:
        return ChatOut(
            response="Sag mir eine Edition (Rotkäppchen, Froschkönig, Aschenputtel, Sterntaler, Classic, Limetta, Mandarina, Rosata) oder nenn Zutaten für einen Cocktail.",
            suggestions=["Zeig Classic","Cocktail mit Zitrone & Soda","Foodpairing Rotkäppchen"]
        )

    # Produkte
    products = find_products_for_intent(intent)

    # Rezepte/Pairings
    recipes = find_recipes_for_intent(intent) if wants_recipe else []
    pairings = PAIRINGS.get(intent) if wants_pairing else None

    HEAD = {
        "rotkaeppchen":"Hier ist Rotkäppchen 🍓",
        "froschkoenig":"Hier ist Froschkönig 👑🐸",
        "aschenputtel":"Hier ist Aschenputtel ✨",
        "sterntaler":"Hier ist Sterntaler ⭐️",
        "classic":"Hier ist Classic 🥃",
        "limetta":"Hier ist Limetta 🍋",
        "mandarina":"Hier ist Mandarina 🍊",
        "rosata":"Hier ist Rosata 🍓",
    }.get(intent, "Hier ist deine Auswahl:")

    # Teaser: wenn kein Rezept angefragt, liefere 1 Klassiker passend zur Edition
    if not recipes:
        cs = classic_suggestions(intent)
        if cs:
            recipes = [{
                "name": cs[0]["name"],
                "tags": ["klassiker"],
                "ingredients": cs[0]["ingredients"],
                "instructions": cs[0]["instructions"],
                "gins": cs[0]["gins"],
            }]

    return ChatOut(
        response=HEAD,
        products=[ProductCard(**p) for p in products] if products else None,
        recipes=[RecipeCard(**r) for r in recipes] if recipes else None,
        pairings=pairings,
        suggestions=SUGGESTIONS_DEFAULT
    )

# ---------- Local ----------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=int(os.getenv("PORT","10000")), reload=True)