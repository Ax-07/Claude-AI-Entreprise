"""Analyse SEO technique d'un site, en Python standard et sans coût d'API : des faits mesurés, jamais devinés.

Lit la page d'accueil, robots.txt, le sitemap et quelques pages internes (contact, services, mentions légales…),
puis renvoie les constats classés (critique / important / mineur) avec leur source. L'agent interprète et priorise.
"""
from __future__ import annotations

import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

UA = "Mozilla/5.0 (compatible; AI-Enterprise-Studio SEO audit)"
MAX_BYTES = 1_500_000
PHONE_RE = re.compile(r"(?:\+33\s?|0)[1-9](?:[\s.\-]?\d{2}){4}")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PRIORITY = ("contact", "service", "prestation", "tarif", "a-propos", "about", "mention", "legal")


# ------------------------------------------------------------------ téléchargement (adresses publiques uniquement)
def default_fetch(url: str, timeout: int = 15) -> dict:
    """GET sans suivre les redirections (elles sont suivies et vérifiées par `fetch`), connecté à l'IP publique
    vérifiée : un DNS qui change de réponse entre le contrôle et la connexion ne mène pas au réseau local."""
    from .net import get_once
    return get_once(url, timeout=timeout, max_bytes=MAX_BYTES, headers={"User-Agent": UA})


def fetch(url: str, getter=default_fetch, is_public=None) -> dict:
    """Suit jusqu'à 5 redirections en vérifiant que chaque adresse est publique."""
    from .tools.core import _public_host
    is_public = is_public or _public_host
    chain = []
    for _ in range(6):
        u = urllib.parse.urlparse(url)
        if u.scheme not in ("http", "https") or not u.hostname or not is_public(u.hostname):
            return {"url": url, "status": 0, "error": "adresse non publique ou invalide", "chain": chain, "headers": {}, "body": b"", "ms": 0}
        try:
            r = getter(url)
        except Exception as e:  # réseau, TLS, délai
            return {"url": url, "status": 0, "error": f"{type(e).__name__}: {e}"[:200], "chain": chain, "headers": {}, "body": b"", "ms": 0}
        if r["status"] in (301, 302, 303, 307, 308) and r["headers"].get("location"):
            chain.append((r["status"], url))
            url = urllib.parse.urljoin(url, r["headers"]["location"])
            continue
        return {**r, "url": url, "chain": chain, "error": ""}
    return {"url": url, "status": 0, "error": "trop de redirections", "chain": chain, "headers": {}, "body": b"", "ms": 0}


# ------------------------------------------------------------------ analyse d'une page
class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self._in = "", None
        self.metas, self.links, self.headings, self.imgs, self.anchors, self.jsonld, self.text = [], [], [], [], [], [], []
        self.lang, self._buf, self._skip = "", [], 0

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "html":
            self.lang = a.get("lang", "")
        elif tag == "meta":
            self.metas.append(a)
        elif tag == "link":
            self.links.append(a)
        elif tag == "img":
            self.imgs.append(a)
        elif tag == "a":
            self.anchors.append(a)
        elif tag in ("title", "h1", "h2", "h3") or (tag == "script" and "ld+json" in a.get("type", "")):
            self._in, self._buf = (tag if tag != "script" else "jsonld"), []
        if tag in ("script", "style", "noscript") and self._in != "jsonld":
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self._in != "jsonld" and self._skip:
            self._skip -= 1
        if self._in and (tag == self._in or (self._in == "jsonld" and tag == "script")):
            txt = "".join(self._buf).strip()
            if self._in == "title":
                self.title = " ".join(txt.split())
            elif self._in == "jsonld":
                self.jsonld.append(txt)
            else:
                self.headings.append((self._in, " ".join(txt.split())))
            self._in = None

    def handle_data(self, data):
        if self._in:
            self._buf.append(data)
        if not self._skip or self._in in ("h1", "h2", "h3", "title"):
            self.text.append(data)


def _jsonld_items(blocks: list[str]) -> list[dict]:
    out = []
    for b in blocks:
        try:
            d = json.loads(b)
        except ValueError:
            out.append({"@invalid": True})
            continue
        stack = d if isinstance(d, list) else [d]
        while stack:
            x = stack.pop()
            if isinstance(x, dict):
                if "@graph" in x and isinstance(x["@graph"], list):
                    stack += x["@graph"]
                out.append(x)
    return out


def _norm_phone(p: str) -> str:
    d = re.sub(r"\D", "", p)
    return "0" + d[2:] if d.startswith("33") else d


def analyze_page(url: str, body: bytes, headers: dict | None = None) -> dict:
    ctype = (headers or {}).get("content-type", "")
    m = re.search(r"charset=([\w-]+)", ctype)
    raw = body.decode(m.group(1) if m else "utf-8", "replace")
    p = _Page()
    try:
        p.feed(raw)
    except Exception:
        pass
    meta = lambda name: next((x.get("content", "") for x in p.metas if x.get("name", "").lower() == name or x.get("property", "").lower() == name), None)
    host = urllib.parse.urlparse(url).hostname or ""
    internal, external = set(), 0
    for a in p.anchors:
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        full = urllib.parse.urljoin(url, href).split("#")[0]
        h = urllib.parse.urlparse(full).hostname or ""
        if h.removeprefix("www.") == host.removeprefix("www."):
            internal.add(full)
        else:
            external += 1
    text = html.unescape(" ".join(" ".join(p.text).split()))
    items = _jsonld_items(p.jsonld)
    types = []
    for it in items:
        t = it.get("@type")
        types += t if isinstance(t, list) else [t] if t else []
    NOT_BIZ = ("WebSite", "WebPage", "BreadcrumbList", "ImageObject", "SearchAction", "ListItem", "Person", "FAQPage",
               "Question", "Answer", "PostalAddress", "GeoCoordinates", "OpeningHoursSpecification", "Article", "BlogPosting")
    def _types(it):
        t = it.get("@type")
        return [str(x) for x in (t if isinstance(t, list) else [t] if t else [])]
    # entreprise : tout type schema.org avec adresse ou téléphone (Plumber, Dentist, Restaurant… sont des LocalBusiness)
    biz = next((it for it in items if (it.get("address") or it.get("telephone")) and not all(t in NOT_BIZ for t in _types(it))), None) \
        or next((it for it in items if any(t.endswith(("LocalBusiness", "Organization")) for t in _types(it))), None)
    canonical = next((x.get("href", "") for x in p.links if "canonical" in x.get("rel", "").lower()), None)
    return {
        "url": url, "title": p.title, "description": meta("description"), "robots": meta("robots"),
        "viewport": meta("viewport"), "og_title": meta("og:title"), "lang": p.lang, "canonical": canonical,
        "hreflang": [x.get("hreflang") for x in p.links if x.get("hreflang")],
        "h1": [t for tag, t in p.headings if tag == "h1"], "h2": [t for tag, t in p.headings if tag == "h2"],
        "h3_count": sum(1 for tag, _ in p.headings if tag == "h3"),
        "images": len(p.imgs), "images_no_alt": sum(1 for i in p.imgs if "alt" not in i),
        "internal_links": sorted(internal), "external_links": external,
        "tel_links": sorted({_norm_phone(a["href"][4:]) for a in p.anchors if (a.get("href") or "").startswith("tel:")}),
        "phones": sorted({_norm_phone(x) for x in PHONE_RE.findall(text)}),
        "emails": sorted({e.lower() for e in EMAIL_RE.findall(text) if not e.lower().endswith((".png", ".jpg", ".webp"))})[:5],
        "jsonld_types": sorted({str(t) for t in types}), "jsonld_invalid": any(it.get("@invalid") for it in items),
        "business": {k: biz.get(k) for k in ("name", "address", "telephone", "openingHours", "openingHoursSpecification", "geo", "url")
                     if biz.get(k)} if biz else None,
        "words": len(text.split()), "bytes": len(body),
    }


# ------------------------------------------------------------------ analyse du site
def _pick_pages(home: dict, limit: int) -> list[str]:
    links = [l for l in home["internal_links"] if l.rstrip("/") != home["url"].rstrip("/")
             and not re.search(r"\.(pdf|jpe?g|png|webp|gif|zip|svg)$", l, re.I)]
    prio = [l for l in links if any(k in l.lower() for k in PRIORITY)]
    rest = [l for l in links if l not in prio]
    return (prio + rest)[:limit]


def check_site(url: str, max_pages: int = 5, getter=default_fetch, is_public=None) -> dict:
    url = url.strip()
    if not re.match(r"https?://", url):
        url = "https://" + url
    start = urllib.parse.urlparse(url)
    root = f"{start.scheme}://{start.netloc}"
    res = {"requested": url, "checked_at": time.strftime("%Y-%m-%d %H:%M"), "pages": [], "issues": []}
    issue = lambda level, msg, src="": res["issues"].append({"level": level, "msg": msg, "source": src})

    home_r = fetch(url, getter, is_public)
    res["home_status"], res["final_url"], res["redirects"], res["home_ms"] = home_r["status"], home_r["url"], home_r["chain"], home_r["ms"]
    if home_r["status"] != 200:
        issue("critique", f"Page d'accueil inaccessible ({home_r['status'] or home_r['error']})", url)
        return res
    home = analyze_page(home_r["url"], home_r["body"], home_r["headers"])
    res["pages"].append(home)
    final = urllib.parse.urlparse(home_r["url"])
    root = f"{final.scheme}://{final.netloc}"
    res["https"] = final.scheme == "https"
    if not res["https"]:
        issue("critique", "Le site n'est pas servi en HTTPS", home_r["url"])
    else:
        h = fetch(f"http://{final.netloc}/", getter, is_public)
        res["http_redirects_to_https"] = h["url"].startswith("https://") if h["status"] == 200 else None
        if res["http_redirects_to_https"] is False:
            issue("important", "La version http:// ne redirige pas vers https://", f"http://{final.netloc}/")

    rb = fetch(root + "/robots.txt", getter, is_public)
    robots = rb["body"].decode("utf-8", "replace") if rb["status"] == 200 else ""
    res["robots_txt"] = rb["status"] == 200
    if re.search(r"(?im)^\s*user-agent:\s*\*\s*$(?:\n(?!\s*user-agent).*)*?\n\s*disallow:\s*/\s*$", robots):
        issue("critique", "robots.txt bloque tout le site aux moteurs de recherche (Disallow: /)", root + "/robots.txt")
    sitemaps = [urllib.parse.urljoin(root + "/", x) for x in re.findall(r"(?im)^\s*sitemap:\s*(\S+)", robots)] or [root + "/sitemap.xml"]
    sm = fetch(sitemaps[0], getter, is_public)
    res["sitemap"] = sitemaps[0] if sm["status"] == 200 and b"<" in sm["body"][:200] else None
    res["sitemap_urls"] = len(re.findall(rb"<loc>", sm["body"])) if res["sitemap"] else 0
    if not res["sitemap"]:
        issue("important", "Aucun sitemap XML trouvé (robots.txt ou /sitemap.xml)", sitemaps[0])
    if not res["robots_txt"]:
        issue("mineur", "Pas de fichier robots.txt", root + "/robots.txt")

    broken = []
    for link in _pick_pages(home, max_pages - 1):
        r = fetch(link, getter, is_public)
        if r["status"] == 200 and "html" in r["headers"].get("content-type", "html"):
            res["pages"].append(analyze_page(r["url"], r["body"], r["headers"]))
        elif r["status"] >= 400 or r["status"] == 0:
            broken.append((link, r["status"] or r["error"]))
    for link, st in broken:
        issue("important", f"Lien interne cassé ({st})", link)

    titles, descs = {}, {}
    utility = lambda u: re.search(r"(?i)mention|legal|cgv|cgu|confidentialit|privacy|cookie|contact|plan-du-site", u)
    for pg in res["pages"]:
        src = pg["url"]
        if not pg["title"]:
            issue("critique", "Balise title absente", src)
        elif not 20 <= len(pg["title"]) <= 65:
            issue("important", f"Title de {len(pg['title'])} caractères (idéal 30 à 60) : « {pg['title'][:80]} »", src)
        if not pg["description"]:
            issue("important", "Meta description absente", src)
        elif not 70 <= len(pg["description"]) <= 170:
            issue("mineur", f"Meta description de {len(pg['description'])} caractères (idéal 120 à 160)", src)
        if len(pg["h1"]) == 0:
            issue("important", "Aucun titre H1", src)
        elif len(pg["h1"]) > 1:
            issue("mineur", f"{len(pg['h1'])} titres H1 (un seul recommandé)", src)
        if not pg["viewport"]:
            issue("critique", "Pas de balise viewport : affichage mobile non adapté", src)
        if not pg["lang"]:
            issue("mineur", "Langue de la page non déclarée (<html lang>)", src)
        if pg["robots"] and "noindex" in pg["robots"].lower() and not utility(src):   # normal pour les mentions légales
            issue("critique", f"Page exclue de Google (meta robots « {pg['robots']} »)", src)
        if pg["images_no_alt"]:
            issue("mineur", f"{pg['images_no_alt']} image(s) sur {pg['images']} sans texte alternatif", src)
        if pg["words"] < 150 and not utility(src):
            issue("important", f"Contenu très court ({pg['words']} mots)", src)
        if pg["jsonld_invalid"]:
            issue("important", "Données structurées JSON-LD invalides (erreur de syntaxe)", src)
        if pg["bytes"] > 1_000_000:
            issue("mineur", f"Page HTML lourde ({pg['bytes'] // 1024} Ko)", src)
        if pg["title"]:
            titles.setdefault(pg["title"], []).append(src)
        if pg["description"]:
            descs.setdefault(pg["description"], []).append(src)
    for t, urls in titles.items():
        if len(urls) > 1:
            issue("important", f"Title identique sur {len(urls)} pages : « {t[:70]} »", ", ".join(urls))
    for d, urls in descs.items():
        if len(urls) > 1:
            issue("mineur", f"Meta description identique sur {len(urls)} pages", ", ".join(urls))
    if not home["canonical"]:
        issue("mineur", "Pas d'URL canonique déclarée sur l'accueil", home["url"])

    biz = next((pg["business"] for pg in res["pages"] if pg["business"]), None)
    res["business"] = biz
    if not biz:
        issue("important", "Aucune donnée structurée d'entreprise locale (LocalBusiness / Organization)", home["url"])
    else:
        for k, label in (("address", "adresse"), ("telephone", "téléphone"), ("openingHours", "horaires")):
            if not biz.get(k) and not (k == "openingHours" and biz.get("openingHoursSpecification")):
                issue("mineur", f"Données structurées sans {label}", home["url"])
    phones = sorted({p for pg in res["pages"] for p in pg["phones"] + pg["tel_links"]})
    res["phones"] = phones
    res["emails"] = sorted({e for pg in res["pages"] for e in pg["emails"]})[:5]
    if not phones:
        issue("important", "Aucun numéro de téléphone visible sur les pages analysées", home["url"])
    elif len(phones) > 2:
        issue("mineur", f"{len(phones)} numéros différents sur le site : {', '.join(phones)} (cohérence NAP à vérifier)", home["url"])
    if biz and biz.get("telephone") and phones and _norm_phone(str(biz["telephone"])) not in phones:
        issue("important", f"Le téléphone des données structurées ({biz['telephone']}) n'apparaît pas sur les pages", home["url"])
    res["mentions_legales"] = any("mention" in l.lower() or "legal" in l.lower() for l in home["internal_links"])
    if not res["mentions_legales"]:
        issue("important", "Pas de lien vers les mentions légales depuis l'accueil (obligatoire en France)", home["url"])
    if home_r["ms"] > 2500:
        issue("mineur", f"Accueil lent à répondre ({home_r['ms']} ms)", home["url"])
    weights = {"critique": 15, "important": 6, "mineur": 2}
    res["score"] = max(0, 100 - sum(weights[i["level"]] for i in res["issues"]))
    return res


def report(res: dict) -> str:
    """Version texte pour l'agent : faits, constats classés, limites."""
    L = [f"ANALYSE SEO TECHNIQUE — {res.get('final_url') or res['requested']} (mesurée le {res['checked_at']}, sans coût)"]
    if res.get("home_status") != 200:
        return "\n".join(L + [f"- {i['level'].upper()} : {i['msg']}" for i in res["issues"]])
    h = res["pages"][0]
    L += [f"Score technique : {res['score']}/100 (100 − 15 par point critique, 6 par point important, 2 par point mineur)",
          f"Accueil : HTTP {res['home_status']} en {res['home_ms']} ms" + (f", redirections : {' → '.join(u for _, u in res['redirects'])}" if res["redirects"] else ""),
          f"HTTPS : {'oui' if res.get('https') else 'non'} ; robots.txt : {'oui' if res['robots_txt'] else 'non'} ; "
          f"sitemap : {res['sitemap'] + ' (' + str(res['sitemap_urls']) + ' URL)' if res['sitemap'] else 'non trouvé'}",
          f"Title : « {h['title']} » ({len(h['title'])} car.)",
          f"Meta description : " + (f"« {h['description']} » ({len(h['description'])} car.)" if h["description"] else "absente"),
          f"H1 : {h['h1'] or 'aucun'} ; H2 : {len(h['h2'])} ; H3 : {h['h3_count']} ; mots : {h['words']}",
          f"Langue : {h['lang'] or 'non déclarée'} ; viewport : {'oui' if h['viewport'] else 'non'} ; canonique : {h['canonical'] or 'non'}",
          f"Données structurées : {', '.join(h['jsonld_types']) or 'aucune'}" + (f" ; entreprise : {json.dumps(res['business'], ensure_ascii=False)[:400]}" if res.get("business") else ""),
          f"Téléphones trouvés : {', '.join(res['phones']) or 'aucun'} ; emails : {', '.join(res.get('emails') or []) or 'aucun'} ; lien mentions légales : {'oui' if res['mentions_legales'] else 'non'}",
          f"Pages analysées ({len(res['pages'])}) : " + ", ".join(p["url"] for p in res["pages"]), "", "CONSTATS (source entre crochets) :"]
    order = {"critique": 0, "important": 1, "mineur": 2}
    for i in sorted(res["issues"], key=lambda x: order[x["level"]]):
        L.append(f"- {i['level'].upper()} : {i['msg']} [{i['source']}]")
    if not res["issues"]:
        L.append("- Aucun problème technique détecté sur les pages analysées.")
    L += ["", "LIMITES : pas de mesure de vitesse réelle (Core Web Vitals), pas d'accès à la fiche Google Business Profile ni "
          "aux positions dans Google ; seules les pages listées ont été analysées. Ne présente jamais ces éléments comme mesurés."]
    return "\n".join(L)
