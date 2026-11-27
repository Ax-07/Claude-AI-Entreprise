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
from bld import S, POLES, office, mixw, mix
# Quartiers par pôle, un bâtiment par agent (décision du propriétaire). Chaque quartier est une grande dalle à la
# couleur de son pôle ; deux terrains sont réservés aux futures divisions. Positions écran : le plateau va de
# x = 0 à 1000 et de y = 80 à 580 ; (dx, dy) = position d'un bâtiment par rapport au centre de son quartier.
QUARTERS = [
 dict(id="direction", name="Direction", col="#818CF8", x=500, y=330, r=82,
      members=[("hq", 0, -32), ("data", 0, 34)]),
 dict(id="commercial", name="Pôle commercial", col="#38BDF8", x=192, y=330, r=72,
      members=[("marketing", 0, -30), ("sales", -56, 12), ("support", 56, 12)]),
 dict(id="production", name="Pôle production", col="#FB923C", x=808, y=330, r=72,
      members=[("research", -40, -6), ("atelier", 40, 6)]),
 dict(id="administration", name="Services partagés", col="#E879F9", x=500, y=500, r=72,
      members=[("legal", 0, -30), ("finance", -56, 12), ("security", 56, 12)]),
]
PLOTS = [dict(id="video", name="Studio vidéo", col="#F43F5E", x=318, y=192, r=60),
         dict(id="ecommerce", name="E-commerce", col="#A3E635", x=682, y=192, r=60)]
INFO = {  # nom, couleur, agent, rôle affiché, description, échelle du bâtiment
 "hq": ("Command HQ", "#818CF8", "pdg", "Orchestration", "Le PDG IA répartit les missions, arbitre les budgets et fait remonter les décisions sensibles.", .62),
 "data": ("Data Core", "#22D3EE", "data", "Mémoire et données", "Le cœur de l’entreprise : mémoire partagée, mesures et amélioration des agents.", .56),
 "marketing": ("Hub Marketing", "#60A5FA", "marketing", "Acquisition", "Positionnement, contenus et pages de vente, dans plusieurs langues.", .52),
 "sales": ("Centre Ventes", "#A78BFA", "ventes", "Revenus", "Prospection, offres, liens de paiement et conversion.", .56),
 "support": ("Zone Support", "#FBBF24", "support", "Clients", "Réponses aux clients, livraisons et suivi des commandes.", .56),
 "research": ("Labo Recherche", "#34D399", "recherche", "R&D et veille", "Marchés, niches, concurrence et nouvelles offres pour toutes les activités.", .56),
 "atelier": ("Atelier", "#FB923C", "atelier", "Production", "Fabrique les livrables vendus aux clients, selon la fiche d’offre.", .56),
 "legal": ("Juridique", "#CBD5E1", "juridique", "Conformité", "CGV, mentions, RGPD, factures : relit les actions avant ta décision.", .56),
 "finance": ("Trésorerie", "#F472B6", "finance", "Finances", "Revenus, coûts d’API et rentabilité de chaque activité.", .56),
 "security": ("Cybersécurité", "#F87171", "securite", "Protection", "Configuration, anomalies et emails piégés : veille défensive quotidienne.", .56),
}
KIND = {"hq": "globe", "data": "city", "marketing": "target", "sales": "bars", "support": "headset", "research": "chart",
        "atelier": "gear", "legal": "scale", "finance": "coin", "security": "shield"}
VARS = {p[0]: p[4] for p in POLES}
DEFAULT = {"hq": "A", "data": "A", "marketing": "A", "sales": "A", "support": "B", "research": "A", "atelier": "A",
           "legal": "A", "finance": "A", "security": "A"}
FOOT = 24          # demi-largeur (en unités du plateau) d'un bâtiment réduit, pour les étiquettes et les robots
TH = 8
CX, CY, GR = 500, 330, 250
AREAS = QUARTERS + PLOTS
B = {}             # position écran de chaque bâtiment
for q in QUARTERS:
    for (pid, dx, dy) in q["members"]:
        B[pid] = (q["x"] + dx, q["y"] + dy, q["id"])

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
rnd = random.Random(11); traces = []
pal = ["#22D3EE", "#C084FC", "#FB923C", "#60A5FA"]
def near_district(x, y, pad=1.2):
    return any(abs(x-a["x"])/(2*a["r"]*pad) + abs(y-a["y"])/(a["r"]*pad) < 1 for a in AREAS)
for _ in range(80):
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
    if len(traces) >= 14: break
tr = ""
for scr, c in traces:
    tr += '<polyline points="%s" fill="none" stroke="%s" stroke-opacity=".55" stroke-width="1"/>' % (G.P(scr), c)
    tr += '<circle cx="%.1f" cy="%.1f" r="2" fill="%s"/>' % (scr[-1] + (c,))
svg += G.o; G.o = []
svg.append('<g filter="url(#nfcity)">%s</g>' % tr)
# routes : depuis la Direction (centre) vers chaque quartier et chaque terrain réservé
hu, hv = uv(500, 330)
roads = []
for a in AREAS[1:]:
    du, dv = uv(a["x"], a["y"])
    pts = [G.p(hu, hv), G.p(du, hv), G.p(du, dv)]
    roads.append(pts)
    path = "M%.1f %.1f L%.1f %.1f L%.1f %.1f" % (pts[0] + pts[1] + pts[2])
    plot = a in PLOTS
    svg.append('<path d="%s" fill="none" stroke="#03050d" stroke-width="16" stroke-linejoin="round"/>' % path)
    svg.append('<path d="%s" fill="none" stroke="%s" stroke-opacity="%s" stroke-width="7" stroke-linejoin="round" filter="url(#sfcity)"/>' % (path, a["col"], ".35" if plot else ".9"))
    svg.append('<path d="%s" fill="none" stroke="%s" stroke-width="4.5" stroke-opacity="%s" stroke-linejoin="round" filter="url(#nfcity)"%s/>' % (path, a["col"], ".5" if plot else "1", ' stroke-dasharray="10 8"' if plot else ""))
    if not plot:
        svg.append('<path d="%s" fill="none" stroke="%s" stroke-width="1.6" stroke-linejoin="round"/>' % (path, mixw(a["col"], .45)))
        svg.append('<path class="flow" d="%s" fill="none" stroke="#FFFFFF" stroke-width="2.6" stroke-linecap="round" stroke-dasharray="1 30" filter="url(#nfcity)"/>' % path)
def on_road(x, y, pad=14):
    return any(seg_dist(x, y, r_[i], r_[i+1]) < pad for r_ in roads for i in range(2))
# dalles des quartiers (sous les bâtiments) et terrains réservés (en pointillés, avec grue)
for a in AREAS:
    plot = a in PLOTS
    s = S(a["col"], "q" + a["id"], cx=a["x"], cy=a["y"])
    s.slab(r=a["r"], t=TH, road=not plot, lamps=not plot)
    svg.append('<defs>%s</defs>%s' % (s.defs, "".join(s.o)))
    if plot:
        r = a["r"] - 12
        svg.append('<polygon points="%s" fill="none" stroke="%s" stroke-opacity=".8" stroke-width="1.5" stroke-dasharray="7 6"/>' % (
            s.P([s.p(-r, -r), s.p(r, -r), s.p(r, r), s.p(-r, r)]), a["col"]))
        bx, by = s.p(-r + 8, -r + 8); tx, ty = s.p(-r + 8, -r + 8, 92)
        svg.append('<g opacity=".85"><line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="3"/>'
                   '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="2.5"/>'
                   '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="1" stroke-dasharray="2 2"/>'
                   '<rect x="%.1f" y="%.1f" width="9" height="7" fill="%s"/></g>' % (
                       bx, by, tx, ty, a["col"], tx - 14, ty, tx + 62, ty + 4, a["col"], tx + 46, ty + 3, tx + 46, ty + 40, mixw(a["col"], .56),
                       tx + 41.5, ty + 40, a["col"]))
objs = []
for _ in range(220):
    u, v = rnd.uniform(-228, 228), rnd.uniform(-228, 228)
    x, y = G.p(u, v)
    if not (5 < x < 995): continue
    if near_district(x, y, 1.1) or on_road(x, y): continue
    if rnd.random() < .78:
        objs.append((y, '<g><ellipse cx="%.1f" cy="%.1f" rx="6" ry="2.5" fill="#000" opacity=".45"/><circle cx="%.1f" cy="%.1f" r="6" fill="#0f3b33"/><circle cx="%.1f" cy="%.1f" r="3.5" fill="#22c58f" fill-opacity=".55"/></g>' % (x, y, x, y-7, x-1.5, y-9)))
    else:
        objs.append((y, '<g><line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#6b7280" stroke-width="1.2"/><circle cx="%.1f" cy="%.1f" r="2.2" fill="#FDE68A" filter="url(#nfcity)"/></g>' % (x, y, x, y-14, x, y-15)))
    if len(objs) > 60: break
for (bu, bv) in [(-232, 232), (240, -240)]:
    G.beam(bu, bv, 0, 170, 10)
beams = "".join(G.o); G.o = []
SPOTS = [(.95, .35), (.35, .95), (.95, -.25), (-.25, .95), (.8, .8), (.6, 1.05)]
for pid, (x, y, qid) in B.items():
    name, c, agent, role, desc, k = INFO[pid]
    groups = ""
    for (Lt, vn, vd, fn) in VARS[pid]:
        s = S(c, "m" + pid + Lt, cx=0, cy=0); s.noground = True; fn(s)
        groups += '<g class="bld" data-pole="%s" data-variant="%s" style="display:none" transform="translate(%d %d) scale(%.2f)"><defs>%s</defs>%s</g>' % (pid, Lt, x, y, k, s.defs, "".join(s.o))
    so = S(c, "o" + pid, cx=0, cy=0); office(so, 26, KIND[pid], hq=False)
    groups += '<g class="office" data-pole="%s" style="display:none" transform="translate(%d %d)"><defs>%s</defs>%s</g>' % (pid, x, y, so.defs, "".join(so.o))
    objs.append((y, groups))
    bots = "".join('<g class="bot" data-pole="%s" data-i="%d" style="display:none">%s</g>' % (pid, i, robot(*iso(x, y, 2*FOOT, u, v), c)) for i, (u, v) in enumerate(SPOTS))
    objs.append((y + FOOT*.9, '<g class="bots">%s</g>' % bots))
objs.sort(key=lambda o: o[0])
svg += [o[1] for o in objs]
svg.append(beams)
svg.append('</svg>')
SVG = "".join(svg)

LABEL_DY = {}       # décalages éventuels d'étiquettes (écran)
poles_js = []
for pid, (x, y, qid) in B.items():
    name, c, agent, role, desc, k = INFO[pid]
    q = next(q for q in QUARTERS if q["id"] == qid)
    poles_js.append(dict(id=pid, name=name, color=c, agent=agent, quarter=qid, division=qid,
                         left="%.2f%%" % (x / W * 100), top="%.2f%%" % ((y + FOOT * .55 + 4 + LABEL_DY.get(pid, 0)) / H * 100),
                         role=q["name"] + " · " + role, desc=desc,
                         variants=[dict(L=v[0], name=v[1], text=v[2]) for v in VARS[pid]]))
quarters_js = [dict(id=a["id"], name=a["name"], color=a["col"], planned=a in PLOTS,
                    poles=[m[0] for m in a.get("members", [])],
                    left="%.2f%%" % (a["x"] / W * 100), top="%.2f%%" % ((a["y"] + a["r"] / 2 + TH + 6 + (16 if a["id"] == "direction" else 0)) / H * 100))
               for a in AREAS]
AGENT = {pid: INFO[pid][2] for pid in B}
SVG_OUT = SVG.replace('<svg viewBox="0 0 1000 620" width="100%" style="display: block" aria-hidden="true">',
                      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 620" width="100%" style="display:block" aria-hidden="true">', 1)
assert "{{" not in SVG_OUT, "trou de gabarit restant"
(OUT / "carte.svg").write_text(SVG_OUT, encoding="utf-8")
meta = {"default_variants": DEFAULT, "agent_of_pole": AGENT, "agents_of_pole": {p: [a] for p, a in AGENT.items()},
        "quarters": quarters_js, "divisions": [], "poles": poles_js}
(OUT / "carte.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
print("carte.svg :", len(SVG_OUT) // 1024, "Ko")
