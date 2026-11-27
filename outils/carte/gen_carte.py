"""Génère la carte isométrique du tableau de bord (ai_studio/web/carte.svg + carte.json).

Mêmes bâtiments, open-spaces et plateau que la maquette validée. À relancer seulement si on change le dessin :
    python outils/carte/gen_carte.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
OUT = Path(__file__).resolve().parents[2] / "ai_studio" / "web"
import random, json, os
random.seed(7)

W, H = 1000, 620
def P(pts): return " ".join("%.1f,%.1f" % p for p in pts)

def tile(cx, cy, r): return [(cx, cy-r/2), (cx+r, cy), (cx, cy+r/2), (cx-r, cy)]

def box(cx, cy, w, h, col, win=True, top="#1d2754", left="#141b3d", right="#0b1029"):
    s = []
    T = [(cx, cy-w/2-h), (cx+w, cy-h), (cx, cy+w/2-h), (cx-w, cy-h)]
    L = [(cx-w, cy-h), (cx, cy+w/2-h), (cx, cy+w/2), (cx-w, cy)]
    R = [(cx+w, cy-h), (cx, cy+w/2-h), (cx, cy+w/2), (cx+w, cy)]
    s.append('<polygon points="%s" fill="%s"/>' % (P(L), left))
    s.append('<polygon points="%s" fill="%s"/>' % (P(R), right))
    s.append('<polygon points="%s" fill="%s" stroke="%s" stroke-opacity=".7" stroke-width="1"/>' % (P(T), top, col))
    if win:
        rows = max(1, int(h // 11))
        for i in range(rows):
            yy = 7 + i*11
            if yy > h-6: break
            for k in (0.25, 0.6):
                # right face
                x1 = cx + w*k; y1 = cy + w/2*(1-k) - h + yy
                op = random.choice([".9", ".55", ".25", ".85"])
                s.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-opacity="%s" stroke-width="2.2"/>' % (x1, y1, x1+w*0.22, y1-w*0.11, col, op))
                x2 = cx - w*k - w*0.22; y2 = cy + w/2*(1-k) - h + yy - w*0.11
                op = random.choice([".7", ".35", ".2", ".6"])
                s.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-opacity="%s" stroke-width="2.2"/>' % (x2, y2, x2+w*0.22, y2+w*0.11, col, op))
    return "".join(s)

def robot(x, y, col):
    return ('<g><ellipse cx="%.1f" cy="%.1f" rx="7" ry="3" fill="#000" opacity=".45"/>'
            '<circle cx="%.1f" cy="%.1f" r="11" fill="%s" opacity=".18"/>'
            '<rect x="%.1f" y="%.1f" width="9" height="10" rx="3" fill="#c9d3ff"/>'
            '<circle cx="%.1f" cy="%.1f" r="5.5" fill="#eef1ff"/>'
            '<rect x="%.1f" y="%.1f" width="7" height="2.6" rx="1.3" fill="%s"/></g>') % (
        x, y, x, y-9, col, x-4.5, y-11, x, y-16, x-3.5, y-17.2, col)

def iso(cx, cy, r, u, v): return (cx + (u-v)*r/2, cy + (u+v)*r/4)

import math
from bld import S, POLES, frange as _fr, office, OFFICE_KIND, mixw
D = [
 dict(id="research", name="Labo Recherche", col="#34D399", x=310, y=245, ag=3),
 dict(id="marketing", name="Hub Marketing", col="#60A5FA", x=690, y=245, ag=5),
 dict(id="hq", name="Command HQ", col="#818CF8", x=500, y=330, ag=1),
 dict(id="sales", name="Centre Ventes", col="#A78BFA", x=160, y=340, ag=4),
 dict(id="support", name="Zone Support", col="#FBBF24", x=840, y=340, ag=6),
 dict(id="data", name="Data Core", col="#22D3EE", x=330, y=425, ag=2),
 dict(id="finance", name="Trésorerie", col="#F472B6", x=670, y=425, ag=2),
]
DEFAULT = {"hq": "A", "research": "A", "marketing": "A", "sales": "A", "support": "B", "data": "A", "finance": "A"}
VARS = {p[0]: p[4] for p in POLES}
RU = {d["id"]: (56 if d["id"] == "hq" else 46) for d in D}
TH = 8
CX, CY, GR = 500, 330, 250

def uv(x, y): dx, dy = x - CX, y - CY; return ((dx + 2*dy) / 2, (2*dy - dx) / 2)
def seg_dist(px, py, a, b):
    ax, ay = a; bx, by = b; vx, vy = bx-ax, by-ay; L = vx*vx + vy*vy or 1
    t = max(0, min(1, ((px-ax)*vx + (py-ay)*vy) / L)); qx, qy = ax + vx*t, ay + vy*t
    return math.hypot(px-qx, py-qy)

G = S("#A78BFA", "city", cx=CX, cy=CY)
svg = ['<svg viewBox="0 0 1000 620" width="100%" style="display: block" aria-hidden="true"><defs>', G.defs,
       '<radialGradient id="sky"><stop offset="0" stop-color="#3b2a8a" stop-opacity=".5"/><stop offset="1" stop-color="#070b1a" stop-opacity="0"/></radialGradient>',
       '</defs>', '<ellipse cx="500" cy="330" rx="560" ry="330" fill="url(#sky)"/>']
G.slab(r=GR, t=22, road=True, lamps=True, col="#C084FC")
# circuit traces on the plateau
rnd = random.Random(11); traces = []
pal = ["#22D3EE", "#C084FC", "#FB923C", "#60A5FA"]
def near_district(x, y, pad=1.25):
    return any(abs(x-d["x"])/(2*RU[d["id"]]*pad) + abs(y-d["y"])/(RU[d["id"]]*pad) < 1 for d in D)
for _ in range(60):
    u, v = rnd.uniform(-225, 225), rnd.uniform(-225, 225)
    pts = [(u, v)]; ax = rnd.random() < .5
    for k in range(rnd.randint(2, 4)):
        L = rnd.choice([-1, 1]) * rnd.uniform(14, 40)
        u, v = (u + L, v) if ax else (u, v + L); ax = not ax
        if max(abs(u), abs(v)) > 230: break
        pts.append((u, v))
    scr = [G.p(a, b) for a, b in pts]
    if len(scr) < 2 or any(near_district(x, y) for x, y in scr): continue
    traces.append((scr, rnd.choice(pal)))
    if len(traces) >= 18: break
tr = ""
for scr, c in traces:
    tr += '<polyline points="%s" fill="none" stroke="%s" stroke-opacity=".55" stroke-width="1"/>' % (G.P(scr), c)
    tr += '<circle cx="%.1f" cy="%.1f" r="2" fill="%s"/>' % (scr[-1] + (c,))
svg += G.o; G.o = []
svg.append('<g filter="url(#nfcity)">%s</g>' % tr)
# roads: L-shaped along the iso axes
hq = [d for d in D if d["id"] == "hq"][0]
hu, hv = uv(hq["x"], hq["y"])
roads = []
for d in D:
    if d["id"] == "hq": continue
    du, dv = uv(d["x"], d["y"])
    pts = [G.p(hu, hv), G.p(du, hv), G.p(du, dv)]
    roads.append(pts)
    path = "M%.1f %.1f L%.1f %.1f L%.1f %.1f" % (pts[0] + pts[1] + pts[2])
    hc = mixw(d["col"], .45)
    svg.append('<path d="%s" fill="none" stroke="#03050d" stroke-width="16" stroke-linejoin="round"/>' % path)
    svg.append('<path d="%s" fill="none" stroke="%s" stroke-opacity=".9" stroke-width="7" stroke-linejoin="round" filter="url(#sfcity)"/>' % (path, d["col"]))
    svg.append('<path d="%s" fill="none" stroke="%s" stroke-width="4.5" stroke-linejoin="round" filter="url(#nfcity)"/>' % (path, d["col"]))
    svg.append('<path d="%s" fill="none" stroke="%s" stroke-width="1.6" stroke-linejoin="round"/>' % (path, hc))
    svg.append('<path class="flow" d="%s" fill="none" stroke="#FFFFFF" stroke-width="2.6" stroke-linecap="round" stroke-dasharray="1 30" filter="url(#nfcity)"/>' % path)
    for q in pts[::2]:
        svg.append('<circle cx="%.1f" cy="%.1f" r="3.2" fill="#FFFFFF" filter="url(#nfcity)"/>' % q)
def on_road(x, y, pad=14):
    return any(seg_dist(x, y, r_[i], r_[i+1]) < pad for r_ in roads for i in range(2))
objs = []
for _ in range(220):
    u, v = rnd.uniform(-228, 228), rnd.uniform(-228, 228)
    x, y = G.p(u, v)
    if not (5 < x < 995): continue
    if near_district(x, y, 1.18) or on_road(x, y): continue
    if rnd.random() < .78:
        objs.append((y, '<g><ellipse cx="%.1f" cy="%.1f" rx="6" ry="2.5" fill="#000" opacity=".45"/><circle cx="%.1f" cy="%.1f" r="6" fill="#0f3b33"/><circle cx="%.1f" cy="%.1f" r="3.5" fill="#22c58f" fill-opacity=".55"/></g>' % (x, y, x, y-7, x-1.5, y-9)))
    else:
        objs.append((y, '<g><line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#6b7280" stroke-width="1.2"/><circle cx="%.1f" cy="%.1f" r="2.2" fill="#FDE68A" filter="url(#nfcity)"/></g>' % (x, y, x, y-14, x, y-15)))
    if len(objs) > 75: break
for (bu, bv) in [(-230, 230), (230, -230), (-238, -238)]:
    G.beam(bu, bv, 0, 170, 10)
beams = "".join(G.o); G.o = []
SPOTS = [(.85, .35), (.35, .85), (.8, .8), (.95, -.35), (-.35, .95), (.55, .95), (.95, .55)]
for d in D:
    cx, cy, c, pid = d["x"], d["y"], d["col"], d["id"]
    ishq = pid == "hq"; ru = RU[pid]
    k = 0.86 if ishq else 0.7
    pl = S(c, "pl" + pid, cx=cx, cy=cy); pl.slab(r=ru, t=TH, road=True, lamps=True)
    groups = '<defs>%s</defs>%s' % (pl.defs, "".join(pl.o))
    for (Lt, vn, vd, fn) in VARS[pid]:
        s = S(c, "m" + pid + Lt, cx=0, cy=0); s.noground = True; fn(s)
        groups += '<g class="bld" data-pole="%s" data-variant="%s" style="display:none" transform="translate(%d %d) scale(%.2f)"><defs>%s</defs>%s</g>' % (pid, Lt, cx, cy, k, s.defs, "".join(s.o))
    so = S(c, "o" + pid, cx=0, cy=0); office(so, ru - 14, OFFICE_KIND[pid], hq=ishq)
    groups += '<g class="office" data-pole="%s" style="display:none" transform="translate(%d %d)"><defs>%s</defs>%s</g>' % (pid, cx, cy, so.defs, "".join(so.o))
    objs.append((cy, groups))
    n_ag = 5 if ishq else d["ag"]
    bots = "".join('<g class="bot" data-pole="%s" data-i="%d" style="display:none">%s</g>' % (pid, i, robot(*iso(cx, cy, 2*ru, u, v), c)) for i, (u, v) in enumerate(SPOTS[:6]))
    objs.append((cy + ru*.9, '<g class="bots">%s</g>' % bots))
objs.sort(key=lambda o: o[0])
svg += [o[1] for o in objs]
svg.append(beams)
svg.append('</svg>')
SVG = "".join(svg)

districts_js = []
desc = {
 "hq": ("Orchestration", "Le PDG IA répartit les missions, arbitre les budgets et valide les décisions sensibles.", ["Priorisation des missions du jour", "Validation du budget API"]),
 "research": ("Veille et opportunités", "Analyse des marchés, détection de niches rentables et étude de la concurrence.", ["Étude de niche : outils B2B", "Analyse concurrentielle"]),
 "marketing": ("Acquisition", "Création de contenus, campagnes et pages de vente dans plusieurs langues.", ["Article SEO en 3 langues", "Séquence email de lancement"]),
 "sales": ("Revenus", "Prospection, qualification des leads et préparation des offres.", ["Qualification de 40 leads", "Proposition commerciale"]),
 "support": ("Clients", "Réponses aux clients 24 h/24, gestion des retours et satisfaction.", ["Tickets en attente : 3", "FAQ mise à jour"]),
 "data": ("Mémoire et données", "Base de connaissances partagée, métriques et apprentissage des agents.", ["Indexation des rapports", "Tableau de bord des KPI"]),
 "finance": ("Finances", "Suivi des revenus, contrôle des coûts API et rentabilité par activité.", ["Contrôle du plafond API", "Rapport de rentabilité"]),
}
for d in D:
    ru = RU[d["id"]]
    districts_js.append(dict(id=d["id"], name=d["name"], color=d["col"], agents=d["ag"] if d["id"] != "hq" else 1,
        left="%.2f%%" % (d["x"]/W*100), top="%.2f%%" % ((d["y"] + ru + TH + 6)/H*100),
        role=desc[d["id"]][0], desc=desc[d["id"]][1], tasks=desc[d["id"]][2],
        variants=[dict(L=v[0], name=v[1], text=v[2]) for v in VARS[d["id"]]]))
DEFAULT_JS = json.dumps(DEFAULT)


AGENT = {"hq": "pdg", "research": "recherche", "marketing": "marketing", "sales": "ventes", "support": "support", "data": "data", "finance": "finance"}
SVG_OUT = SVG.replace('<svg viewBox="0 0 1000 620" width="100%" style="display: block" aria-hidden="true">',
                      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 620" width="100%" style="display:block" aria-hidden="true">', 1)
assert "{{" not in SVG_OUT, "trou de gabarit restant"
(OUT / "carte.svg").write_text(SVG_OUT, encoding="utf-8")
meta = {"default_variants": DEFAULT, "agent_of_pole": AGENT,
        "poles": [{**{k: v for k, v in d.items() if k not in ("agents", "tasks")}, "agent": AGENT[d["id"]]} for d in districts_js]}
(OUT / "carte.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
print("carte.svg :", len(SVG_OUT) // 1024, "Ko")
