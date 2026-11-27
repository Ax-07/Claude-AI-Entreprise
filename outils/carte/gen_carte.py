"""Génère la carte isométrique du tableau de bord (ai_studio/web/carte.svg + carte.json).

Plateau « ville » : grille de 5 × 5 îlots, Data Core sur l'îlot central, un îlot par agent regroupé par division. À relancer seulement si on change le dessin :
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
# Plateau « ville » (choix du propriétaire, d'après son image de référence) : une grille de 5 × 5 îlots (nombre impair,
# pour que le Data Core occupe la case du milieu), un îlot par agent regroupé par division, deux îlots en chantier pour
# les futures divisions, des immeubles de remplissage, des voitures et une ligne d'horizon. Coordonnées du plateau (u, v) :
# écran x = 500 + (u - v), y = 330 + (u + v) / 2 ; le plateau va de -250 à 250 dans les deux sens.
CX, CY, GR = 500, 330, 250
E = [-250, -150, -50, 50, 150, 250]  # limites des îlots : rues sur les axes intérieurs, bord du plateau à ±250
N = len(E) - 1                       # 5 îlots par côté ; l'îlot (2, 2) est au centre exact
HW = 12                              # demi-largeur d'une rue
TH = 5                               # hauteur des trottoirs
DIVS = {  # division : nom, couleur (bordure lumineuse des îlots)
 "direction": ("Direction", "#818CF8"), "commercial": ("Pôle commercial", "#38BDF8"),
 "production": ("Pôle production", "#FB923C"), "administration": ("Services partagés", "#E879F9")}
# (i, j) = îlot entre E[i]..E[i+1] en u et E[j]..E[j+1] en v. À l'écran : i croissant = vers la droite et l'avant,
# j croissant = vers la gauche et l'avant. Le Data Core occupe la case centrale (2, 2) ; chaque pôle occupe un coin de
# 2 × 2 cases (4 emplacements de bâtiment) ; les 8 cases entre les coins sont de petits parcs.
ZONES = {"direction": (0, 0), "production": (3, 0), "commercial": (0, 3), "administration": (3, 3)}   # coin haut-gauche de chaque zone
LOTS = {"data": (2, 2, "direction"), "hq": (1, 1, "direction"),
        "marketing": (0, 4, "commercial"), "sales": (1, 3, "commercial"), "support": (1, 4, "commercial"),
        "research": (3, 0, "production"), "atelier": (3, 1, "production"),
        "legal": (3, 4, "administration"), "security": (4, 3, "administration"), "finance": (4, 4, "administration")}
PLOTS = [dict(id="video", name="Studio vidéo", col="#F43F5E", ij=(4, 0)),
         dict(id="ecommerce", name="E-commerce", col="#A3E635", ij=(4, 1))]
PARKS = {(i, j) for i in range(N) for j in range(N) if (i == 2) != (j == 2)}   # la croix entre les 4 coins
used = {(i, j) for (i, j, d) in LOTS.values()} | {a["ij"] for a in PLOTS} | PARKS
FREE = {(i, j): d for d, (i0, j0) in ZONES.items() for i in (i0, i0 + 1) for j in (j0, j0 + 1) if (i, j) not in used}
FILL = {**{ij: "park" for ij in PARKS}, **{ij: "towers" for ij in FREE}}   # emplacements libres : immeubles éclairés
INFO = {  # nom, couleur, agent, rôle affiché, description, échelle du bâtiment
 "hq": ("Command HQ", "#818CF8", "pdg", "Orchestration", "Le PDG IA répartit les missions, arbitre les budgets et fait remonter les décisions sensibles.", .62),
 "data": ("Data Core", "#22D3EE", "data", "Mémoire et données", "Le cœur de l’entreprise : mémoire partagée, mesures et amélioration des agents.", .6),
 "marketing": ("Hub Marketing", "#60A5FA", "marketing", "Acquisition", "Positionnement, contenus et pages de vente, dans plusieurs langues.", .56),
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
FOOT = 25

G = S("#A78BFA", "city", cx=CX, cy=CY)
p = G.p
def PP(pts): return " ".join("%.1f,%.1f" % q for q in pts)
def poly(pts, fill, extra=""): return '<polygon points="%s" fill="%s"%s/>' % (PP(pts), fill, extra)
def lot_bounds(i, j, pad=0):
    u0 = E[i] + (HW if i else 8) + pad; u1 = E[i+1] - (HW if i < N - 1 else 8) - pad
    v0 = E[j] + (HW if j else 8) + pad; v1 = E[j+1] - (HW if j < N - 1 else 8) - pad
    return u0, u1, v0, v1
def lot_center(i, j): u0, u1, v0, v1 = lot_bounds(i, j); return (u0 + u1) / 2, (v0 + v1) / 2

def ubox(u0, u1, v0, v1, z0, z1, top, left, right, stroke=None):
    """Pavé dans le repère du plateau : dessus, face avant-gauche (v = v1) et face avant-droite (u = u1)."""
    out = poly([p(u0, v1, z0), p(u1, v1, z0), p(u1, v1, z1), p(u0, v1, z1)], left)
    out += poly([p(u1, v0, z0), p(u1, v1, z0), p(u1, v1, z1), p(u1, v0, z1)], right)
    out += poly([p(u0, v0, z1), p(u1, v0, z1), p(u1, v1, z1), p(u0, v1, z1)], top,
                ' stroke="%s" stroke-opacity=".7" stroke-width=".8"' % stroke if stroke else "")
    return out

WINCOL = ["#FDE68A", "#FDE68A", "#93C5FD", "#67E8F9", "#FBCFE8"]
def windows(rnd, x, y, length, height, mat, step=(6, 7)):
    """Fenêtres éclairées sur une face (repère local : x le long de la façade, y vers le bas)."""
    r = []
    for yy in range(4, int(height) - 4, step[1]):
        for xx in range(3, int(length) - 3, step[0]):
            if rnd.random() < .42: continue
            r.append('<rect x="%d" y="%d" width="3" height="2.6" fill="%s" fill-opacity="%.2f"/>' % (xx, yy, rnd.choice(WINCOL), rnd.uniform(.35, .95)))
    return '<g transform="matrix(%s %.1f %.1f)">%s</g>' % (mat, x, y, "".join(r))

def tower(rnd, u, v, a, b, h, col="#334155"):
    out = ubox(u - a, u + a, v - b, v + b, 0, h, mix(col, .55), mix(col, .4), mix(col, .22), stroke=mixw(col, .3))
    x, y = p(u - a, v + b, h); out += windows(rnd, x, y, 2 * a, h, "1 .5 0 1")
    x, y = p(u + a, v + b, h); out += windows(rnd, x, y, 2 * b, h, "1 -.5 0 1")
    return out

def tree(x, y, s=1):
    return ('<g><ellipse cx="%.1f" cy="%.1f" rx="%.1f" ry="%.1f" fill="#000" opacity=".4"/><line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#3f2d20" stroke-width="1.6"/>'
            '<circle cx="%.1f" cy="%.1f" r="%.1f" fill="#0f3b33"/><circle cx="%.1f" cy="%.1f" r="%.1f" fill="#22c58f" fill-opacity=".55"/></g>') % (
        x, y, 6*s, 2.5*s, x, y, x, y - 6*s, x, y - 9*s, 6.5*s, x - 1.5*s, y - 11*s, 3.6*s)

def lamp(x, y):
    return ('<g><line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#64748b" stroke-width="1.1"/>'
            '<circle cx="%.1f" cy="%.1f" r="4.5" fill="#FDE68A" opacity=".18"/><circle cx="%.1f" cy="%.1f" r="1.7" fill="#FEF3C7"/></g>') % (x, y, x, y - 15, x, y - 16, x, y - 16)

CARCOL = ["#F43F5E", "#38BDF8", "#FBBF24", "#E2E8F0", "#A78BFA", "#34D399", "#F472B6", "#64748B"]
def car(u, v, along_v, sign, col):
    """Petite voiture isométrique ; phares blancs à l'avant, feux rouges à l'arrière."""
    L, Wd = 6, 3.2
    if along_v: u0, u1, v0, v1 = u - Wd, u + Wd, v - L, v + L
    else:       u0, u1, v0, v1 = u - L, u + L, v - Wd, v + Wd
    out = '<g><ellipse cx="%.1f" cy="%.1f" rx="9" ry="4" fill="#000" opacity=".35"/>' % p(u, v, 0)
    out += ubox(u0, u1, v0, v1, 0, 3.4, mixw(col, .15), mix(col, .75), mix(col, .5))
    cu0, cu1, cv0, cv1 = (u0 + .6, u1 - .6, v0 + 3, v1 - 3) if along_v else (u0 + 3, u1 - 3, v0 + .6, v1 - .6)
    out += ubox(cu0, cu1, cv0, cv1, 3.4, 6, "#1e293b", "#0f172a", "#111827")
    fu, fv = (u, v + sign * L) if along_v else (u + sign * L, v)
    bu, bv = (u, v - sign * L) if along_v else (u - sign * L, v)
    fx, fy = p(fu, fv, 2); bx, by = p(bu, bv, 2)
    out += '<circle cx="%.1f" cy="%.1f" r="5" fill="#FEF9C3" opacity=".22"/><circle cx="%.1f" cy="%.1f" r="1.3" fill="#FFFFFF"/>' % (fx, fy, fx, fy)
    out += '<circle cx="%.1f" cy="%.1f" r="1.2" fill="#EF4444"/></g>' % (bx, by)
    return out

rnd = random.Random(11)
svg = ['<svg viewBox="0 0 1000 620" width="100%" style="display: block" aria-hidden="true"><defs>', G.defs,
       '<radialGradient id="sky"><stop offset="0" stop-color="#3b2a8a" stop-opacity=".55"/><stop offset="1" stop-color="#070b1a" stop-opacity="0"/></radialGradient>',
       '<linearGradient id="haze" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#070b1a" stop-opacity="0"/><stop offset="1" stop-color="#6d28d9" stop-opacity=".25"/></linearGradient>',
       '<radialGradient id="holo"><stop offset="0" stop-color="#22D3EE" stop-opacity=".55"/><stop offset="1" stop-color="#22D3EE" stop-opacity="0"/></radialGradient>',
       '</defs>', '<ellipse cx="500" cy="300" rx="600" ry="340" fill="url(#sky)"/>']
# --- fond : seulement le halo (pas de silhouettes d'immeubles, choix du propriétaire)
svg.append('<rect x="0" y="0" width="1000" height="330" fill="url(#haze)"/>')
# --- dalle du plateau (bords lumineux) puis chaussée
G.slab(r=GR, t=22, road=False, lamps=False, col="#C084FC")
svg += G.o; G.o = []
svg.append(poly([p(-GR, -GR), p(GR, -GR), p(GR, GR), p(-GR, GR)], "#0a0e22"))
# marquage : lignes médianes en pointillés, hors carrefours
marks = []
for c in E[1:-1]:
    for a, b in zip(E[:-1], E[1:]):
        a0 = a + (HW + 6 if a > -GR else 6); b0 = b - (HW + 6 if b < GR else 6)
        if b0 <= a0: continue
        marks.append("M%.1f %.1f L%.1f %.1f" % (p(c, a0) + p(c, b0)))
        marks.append("M%.1f %.1f L%.1f %.1f" % (p(a0, c) + p(b0, c)))
svg.append('<path d="%s" stroke="#FDE68A" stroke-opacity=".55" stroke-width="1" stroke-dasharray="5 5" fill="none"/>' % " ".join(marks))
# passages piétons autour de chaque carrefour
cw = []
for cu in E[1:-1]:
    for cv in E[1:-1]:
        for sgn in (-1, 1):
            vv = cv + sgn * (HW + 2)
            for t in range(int(cu - HW + 3), int(cu + HW - 3), 4):
                cw.append(poly([p(t, vv), p(t + 2, vv), p(t + 2, vv + sgn * 5), p(t, vv + sgn * 5)], "#E2E8F0", ' fill-opacity=".45"'))
            uu = cu + sgn * (HW + 2)
            for t in range(int(cv - HW + 3), int(cv + HW - 3), 4):
                cw.append(poly([p(uu, t), p(uu, t + 2), p(uu + sgn * 5, t + 2), p(uu + sgn * 5, t)], "#E2E8F0", ' fill-opacity=".45"'))
svg.append("".join(cw))
# --- îlots : trottoir surélevé, cour intérieure teintée et bordure lumineuse à la couleur de la division
objs = []     # (profondeur écran, svg) dessinés du fond vers l'avant
B = {}        # position écran de chaque bâtiment d'agent
def sidewalk(i, j, col, dashed=False):
    u0, u1, v0, v1 = lot_bounds(i, j)
    out = ubox(u0, u1, v0, v1, 0, TH, "#1a2142", "#141a36", "#0d1229")
    q = [p(u0 + 5, v0 + 5, TH), p(u1 - 5, v0 + 5, TH), p(u1 - 5, v1 - 5, TH), p(u0 + 5, v1 - 5, TH)]
    if dashed:
        out += poly(q, mix(col, .08), ' stroke="%s" stroke-opacity=".85" stroke-width="1.4" stroke-dasharray="7 6"' % col)
    else:
        out += poly(q, mix(col, .14), ' stroke="%s" stroke-opacity=".9" stroke-width="1.2" filter="url(#nfcity)"' % col)
    return out
ground = []
for pid, (i, j, d) in LOTS.items():
    if pid == "data":   # case centrale : halo holographique cyan sous le Data Core
        x0, y0 = p(*lot_center(i, j))
        ground.append('<ellipse cx="%.1f" cy="%.1f" rx="150" ry="75" fill="url(#holo)"/>' % (x0, y0))
    ground.append(sidewalk(i, j, INFO[pid][1] if pid == "data" else DIVS[d][1]))
    u, v = lot_center(i, j); x, y = p(u, v, TH); B[pid] = (x, y, d)
for a in PLOTS:
    ground.append(sidewalk(*a["ij"], a["col"], dashed=True))
for (i, j), kind in FILL.items():   # parcs en vert ; emplacements libres bordés à la couleur de leur pôle
    ground.append(sidewalk(i, j, "#22c58f" if kind == "park" else DIVS[FREE[(i, j)]][1]))
svg.append("".join(ground))
# chantiers : grue et palissade
for a in PLOTS:
    u0, u1, v0, v1 = lot_bounds(*a["ij"], pad=12)
    bx, by = p(u1 - 4, v0 + 4, TH); tx, ty = p(u1 - 4, v0 + 4, 104)   # grue au coin droit, flèche vers la gauche
    col = a["col"]
    objs.append((by, '<g opacity=".9"><line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="3"/>'
                     '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="2.5"/>'
                     '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="1" stroke-dasharray="2 2"/>'
                     '<rect x="%.1f" y="%.1f" width="10" height="8" fill="%s"/><circle cx="%.1f" cy="%.1f" r="2" fill="#F43F5E" class="pulse"/></g>' % (
        bx, by, tx, ty, col, tx + 14, ty, tx - 64, ty + 5, col, tx - 48, ty + 4, tx - 48, ty + 48, mixw(col, .56), tx - 53, ty + 48, col, tx, ty - 3)))
    cu, cv = lot_center(*a["ij"])
    objs.append((p(cu, cv)[1], ubox(cu - 4, cu + 22, cv - 10, cv + 14, TH, TH + 7, mix(col, .35), mix(col, .22), mix(col, .12), stroke=col)))
# remplissage : immeubles éclairés et parc
for (i, j), kind in FILL.items():
    u0, u1, v0, v1 = lot_bounds(i, j, pad=10)
    if kind == "park":
        # petit parc : allées en croix, bassin au centre et quelques arbres
        cu, cv = lot_center(i, j)
        svg.append('<path d="M%.1f %.1f L%.1f %.1f M%.1f %.1f L%.1f %.1f" stroke="#3b4a6b" stroke-width="3" stroke-linecap="round"/>' % (
            p(u0, cv, TH) + p(u1, cv, TH) + p(cu, v0, TH) + p(cu, v1, TH)))
        bx, by, rx, ry = G.ell(cu, cv, TH, 9)
        svg.append('<ellipse cx="%.1f" cy="%.1f" rx="%.1f" ry="%.1f" fill="#0e7490" stroke="#67E8F9" stroke-opacity=".8"/>' % (bx, by, rx, ry))
        for (a_, b_) in [(.2, .2), (.8, .2), (.2, .8), (.8, .8), (.5, .15), (.15, .5)]:
            u, v = u0 + (u1 - u0) * a_, v0 + (v1 - v0) * b_; x, y = p(u, v, TH)
            objs.append((y, tree(x, y, rnd.uniform(.75, 1))))
        continue
    cy_ = p(*lot_center(i, j))[1]
    hmax = 30 if cy_ > 400 else 44 if cy_ < 160 else 62   # bas au premier plan, pour ne rien masquer
    cells = [(u0 + (u1 - u0) * a, v0 + (v1 - v0) * b) for a in (.28, .72) for b in (.28, .72)]
    for (u, v) in cells:
        if rnd.random() < .08:
            x, y = p(u, v, TH); objs.append((y, tree(x, y))); continue
        a_, b_ = rnd.uniform(9, 13), rnd.uniform(9, 13); h = rnd.uniform(20, hmax)
        G2 = tower(rnd, u, v, a_, b_, h, rnd.choice(["#334155", "#3b3363", "#1e3a5f", "#3f3f46"]))
        objs.append((p(u + a_, v + b_)[1] - 4, '<g transform="translate(0 %d)">%s</g>' % (-TH, G2)))
# lampadaires aux angles des îlots
for i in range(N):
    for j in range(N):
        u0, u1, v0, v1 = lot_bounds(i, j)
        for (u, v) in [(u1 - 3, v1 - 3), (u0 + 3, v1 - 3), (u1 - 3, v0 + 3)]:
            x, y = p(u, v, TH); objs.append((y, lamp(x, y)))
# voitures sur les rues (pas dans les carrefours)
for _ in range(60):
    c = rnd.choice(E[1:-1]); along_v = rnd.random() < .5; t = rnd.uniform(-238, 238)
    if any(abs(t - e) < HW + 8 for e in E[1:-1]) : continue
    lane = rnd.choice([-1, 1])
    u, v = (c + lane * 6.5, t) if along_v else (t, c + lane * 6.5)
    x, y = p(u, v)
    objs.append((y, car(u, v, along_v, lane, rnd.choice(CARCOL))))
    if sum(1 for o in objs if "EF4444" in o[1]) >= 16: break
# enseignes : le nom du pôle sur un panneau à l'avant de son îlot (comme sur une façade)
for pid, (i, j, d) in LOTS.items():
    name, c = INFO[pid][0], INFO[pid][1]
    u0, u1, v0, v1 = lot_bounds(i, j)
    a, b, vv = u0 + 4, u1 - 4, v1 - 5
    est = len(name) * 5.3 + 4
    fit = ' textLength="%d" lengthAdjust="spacingAndGlyphs"' % (b - a - 10) if est > b - a - 10 else ""
    board = poly([p(a, vv, TH), p(b, vv, TH), p(b, vv, TH + 11), p(a, vv, TH + 11)], "#070a1c", ' stroke="%s" stroke-opacity=".8" stroke-width=".8"' % c)
    x, y = p((a + b) / 2, vv, TH + 11)
    board += ('<g transform="matrix(1 .5 0 1 %.1f %.1f)"><text x="0" y="8.1" text-anchor="middle" font-family="Space Grotesk, Arial, sans-serif" '
              'font-size="7.2" font-weight="700" letter-spacing=".6" fill="%s"%s filter="url(#nfcity)">%s</text></g>') % (x, y, mixw(c, .35), fit, name.upper())
    objs.append((B[pid][1] + 18, board))
# --- bâtiments des agents (une variante visible à la fois), bureaux et robots
SPOTS = [(1.0, -.35), (1.05, .15), (.7, -.8), (1.1, -.75), (.75, .5), (.3, -1.1)]   # sur le côté droit, hors de l'enseigne
for pid, (x, y, d) in B.items():
    name, c, agent, role, desc, k = INFO[pid]
    groups = ""
    for (Lt, vn, vd, fn) in VARS[pid]:
        s = S(c, "m" + pid + Lt, cx=0, cy=0); s.noground = True; fn(s)
        groups += '<g class="bld" data-pole="%s" data-variant="%s" style="display:none" transform="translate(%.1f %.1f) scale(%.2f)"><defs>%s</defs>%s</g>' % (pid, Lt, x, y, k, s.defs, "".join(s.o))
    so = S(c, "o" + pid, cx=0, cy=0); office(so, 24, KIND[pid], hq=False)
    groups += '<g class="office" data-pole="%s" style="display:none" transform="translate(%.1f %.1f)"><defs>%s</defs>%s</g>' % (pid, x, y, so.defs, "".join(so.o))
    objs.append((y, groups))
    foot = FOOT
    bots = "".join('<g class="bot" data-pole="%s" data-i="%d" style="display:none">%s</g>' % (pid, i, robot(*iso(x, y, 2*foot, u, v), c)) for i, (u, v) in enumerate(SPOTS))
    objs.append((y + foot * .9, '<g class="bots">%s</g>' % bots))
objs.sort(key=lambda o: o[0])
svg += [o[1] for o in objs]
svg.append('</svg>')
SVG = "".join(svg)

# --- métadonnées pour le tableau de bord (étiquettes HTML posées sur la carte)
W, H = 1000, 620
poles_js = []
for pid, (x, y, d) in B.items():
    name, c, agent, role, desc, k = INFO[pid]
    ly = y - 4          # voyant posé sur le bâtiment (les îlots de devant masqueraient le pied)
    poles_js.append(dict(id=pid, name=name, color=c, agent=agent, quarter=d, division=d,
                         left="%.2f%%" % (x / W * 100), top="%.2f%%" % (ly / H * 100),
                         role=DIVS[d][0] + " · " + role, desc=desc,
                         variants=[dict(L=v[0], name=v[1], text=v[2]) for v in VARS[pid]]))
QLBL = {"direction": (500, 48), "commercial": (130, 425), "production": (905, 440), "administration": (500, 600)}
quarters_js = [dict(id=q, name=n, color=c, planned=False, poles=[pid for pid in B if B[pid][2] == q], free=sum(1 for d in FREE.values() if d == q),
                    left="%.2f%%" % (QLBL[q][0] / W * 100), top="%.2f%%" % (QLBL[q][1] / H * 100)) for q, (n, c) in DIVS.items()]
for a in PLOTS:
    u, v = lot_center(*a["ij"]); x, y = p(u, v, TH)
    quarters_js.append(dict(id=a["id"], name=a["name"], color=a["col"], planned=True, poles=[],
                            left="%.2f%%" % (x / W * 100), top="%.2f%%" % ((y + 8) / H * 100)))
AGENT = {pid: INFO[pid][2] for pid in B}
SVG_OUT = SVG.replace('<svg viewBox="0 0 1000 620" width="100%" style="display: block" aria-hidden="true">',
                      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 620" width="100%" style="display:block" aria-hidden="true">', 1)
assert "{{" not in SVG_OUT, "trou de gabarit restant"
(OUT / "carte.svg").write_text(SVG_OUT, encoding="utf-8")
meta = {"default_variants": DEFAULT, "agent_of_pole": AGENT, "agents_of_pole": {p_: [a] for p_, a in AGENT.items()},
        "quarters": quarters_js, "divisions": [], "poles": poles_js}
(OUT / "carte.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
print("carte.svg :", len(SVG_OUT) // 1024, "Ko")
