import math, json, random
DARK = (8, 11, 30)

def hx(c): c = c.lstrip("#"); return tuple(int(c[i:i+2], 16) for i in (0, 2, 4))
def mix(col, t, base=DARK):
    t = max(0, min(1, t)); a = hx(col)
    return "#%02x%02x%02x" % tuple(round(base[i] + (a[i]-base[i])*t) for i in range(3))
def mixw(col, t): return mix(col, t, base=(255, 255, 255))
def frange(a, b, st):
    out = []; x = a
    while x <= b + 1e-6: out.append(x); x += st
    return out

GOLD = "#FDE68A"

# ---------- footprints (u, v) ----------
def rect(u0, u1, v0, v1): return [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]
def cham(u0, u1, v0, v1, c): return [(u0+c, v0), (u1-c, v0), (u1, v0+c), (u1, v1-c), (u1-c, v1), (u0+c, v1), (u0, v1-c), (u0, v0+c)]
def circ(u, v, r, n=24): return [(u + r*math.cos(2*math.pi*i/n), v + r*math.sin(2*math.pi*i/n)) for i in range(n)]
def grow(fp, d):
    cu = sum(p[0] for p in fp)/len(fp); cv = sum(p[1] for p in fp)/len(fp)
    out = []
    for (u, v) in fp:
        du, dv = u-cu, v-cv; L = math.hypot(du, dv) or 1
        k = (L + d*1.25) / L if len(fp) > 8 else 1
        out.append((cu + du*k, cv + dv*k) if len(fp) > 8 else (u + (d if du > 0 else -d), v + (d if dv > 0 else -d)))
    return out

class S:
    noground = False
    def __init__(s, col, uid, cx=120, cy=190):
        s.c, s.uid, s.cx, s.cy, s.o = col, uid, cx, cy, []
        s.rnd = random.Random(uid)
        s.hi = mixw(col, .5)
        s.top, s.left, s.right = mix(col, .42), mix(col, .26), mix(col, .12)
        u = uid
        s.defs = (
          '<filter id="nf%s" x="-50%%" y="-50%%" width="200%%" height="200%%"><feGaussianBlur stdDeviation="1.8" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>' % u +
          '<filter id="sf%s" x="-50%%" y="-50%%" width="200%%" height="200%%"><feGaussianBlur stdDeviation="5"/></filter>' % u +
          '<linearGradient id="fd%s" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#000" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity=".5"/></linearGradient>' % u +
          '<linearGradient id="cy%s" x1="0" x2="1"><stop offset="0" stop-color="%s"/><stop offset=".25" stop-color="%s"/><stop offset=".7" stop-color="%s"/><stop offset="1" stop-color="%s"/></linearGradient>' % (u, mix(col, .3), mix(col, .5), mix(col, .18), mix(col, .06)) +
          '<linearGradient id="gs%s" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#FFFFFF" stop-opacity="0"/><stop offset=".42" stop-color="#FFFFFF" stop-opacity=".14"/><stop offset=".5" stop-color="#FFFFFF" stop-opacity="0"/></linearGradient>' % u +
          '<radialGradient id="gl%s"><stop offset="0" stop-color="%s" stop-opacity=".55"/><stop offset="1" stop-color="%s" stop-opacity="0"/></radialGradient>' % (u, col, col) +
          '<linearGradient id="bm%s" x1="0" y1="1" x2="0" y2="0"><stop offset="0" stop-color="#FFFFFF" stop-opacity=".75"/><stop offset=".6" stop-color="%s" stop-opacity=".25"/><stop offset="1" stop-color="%s" stop-opacity="0"/></linearGradient>' % (u, col, col) +
          '<linearGradient id="sd%s" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="%s"/><stop offset="1" stop-color="#03040c"/></linearGradient>' % (u, mix(col, .22)))
    # ---------- basics ----------
    def p(s, u, v, z=0): return (s.cx + (u - v), s.cy + (u + v) / 2 - z)
    def P(s, pts): return " ".join("%.1f,%.1f" % q for q in pts)
    def poly(s, pts, fill, op=1, stroke=None, sop=.6, cls="", extra=""):
        st = ' stroke="%s" stroke-opacity="%s" stroke-width="1"' % (stroke, sop) if stroke else ""
        c = ' class="%s"' % cls if cls else ""
        s.o.append('<polygon%s points="%s" fill="%s" fill-opacity="%s"%s stroke-linejoin="round"%s/>' % (c, s.P(pts), fill, op, st, extra))
    def raw(s, x): s.o.append(x)
    def neon(s, segs, w=1.2, col=None, op=1):
        if not segs: return
        d = " ".join("M%.1f %.1f L%.1f %.1f" % (a + b) for a, b in segs)
        s.raw('<path d="%s" fill="none" stroke="%s" stroke-width="%s" stroke-opacity="%s" stroke-linecap="round" filter="url(#nf%s)"/>' % (d, col or s.hi, w, op, s.uid))
    def line(s, a, b, w=2, col=None, op=1):
        s.raw('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="%s" stroke-opacity="%s" stroke-linecap="round"/>' % (a+b+(col or s.c, w, op)))
    def footglow(s, u, v, rx, ry):
        x, y = s.p(u, v, 0)
        s.raw('<ellipse cx="%.1f" cy="%.1f" rx="%.1f" ry="%.1f" fill="%s" opacity=".5" filter="url(#sf%s)"/>' % (x, y, rx, ry, s.c, s.uid))
    def lface(s, u0, v, z1): x, y = s.p(u0, v, z1); return '<g transform="matrix(1 0.5 0 1 %.1f %.1f)">' % (x, y)
    def rface(s, u, v1, z1): x, y = s.p(u, v1, z1); return '<g transform="matrix(1 -0.5 0 1 %.1f %.1f)">' % (x, y)
    def ell(s, u, v, z, r): x, y = s.p(u, v, z); return x, y, r*math.sqrt(2), r/math.sqrt(2)
    def arc(s, x, y, rx, ry, front=True):
        return "M%.1f %.1f A%.1f %.1f 0 0 %d %.1f %.1f" % (x-rx, y, rx, ry, 0 if front else 1, x+rx, y)

    # ---------- plateau (thick slab with ring road, lamps, underglow) ----------
    def slab(s, r=52, t=10, road=True, lamps=True, col=None):
        if s.noground: return
        col = col or s.c
        x, y = s.p(0, 0, 0)
        s.raw('<ellipse cx="%.1f" cy="%.1f" rx="%.1f" ry="%.1f" fill="url(#gl%s)"/>' % (x, y + t, r*2.7, r*1.35, s.uid))
        # lower offset layer (underglow)
        s.poly([s.p(-r-3, r+3, -t-5), s.p(r+3, r+3, -t-5), s.p(r+3, -r-3, -t-5)], "none", 1)
        s.raw('<polyline points="%s" fill="none" stroke="%s" stroke-width="2" stroke-opacity=".85" filter="url(#nf%s)"/>' % (s.P([s.p(-r-3, r+3, -t-5), s.p(r+3, r+3, -t-5), s.p(r+3, -r-3, -t-5)]), col, s.uid))
        # sides
        s.poly([s.p(-r, r, 0), s.p(r, r, 0), s.p(r, r, -t), s.p(-r, r, -t)], "url(#sd%s)" % s.uid)
        s.poly([s.p(r, -r, 0), s.p(r, r, 0), s.p(r, r, -t), s.p(r, -r, -t)], "#05071a")
        s.neon([(s.p(-r, r, -t), s.p(r, r, -t)), (s.p(r, -r, -t), s.p(r, r, -t))], 1.3, col, .9)
        # top
        s.poly([s.p(-r, -r), s.p(r, -r), s.p(r, r), s.p(-r, r)], "#0c1230")
        if road:
            a, b = r - 4, r - 13
            s.raw('<path d="M%s Z M%s Z" fill="#05070f" fill-rule="evenodd"/>' % (s.P([s.p(-a, -a), s.p(a, -a), s.p(a, a), s.p(-a, a)]), s.P([s.p(-b, -b), s.p(b, -b), s.p(b, b), s.p(-b, b)])))
            m = (a + b) / 2
            s.raw('<polygon points="%s" fill="none" stroke="#FFFFFF" stroke-opacity=".35" stroke-width=".7" stroke-dasharray="3 4"/>' % s.P([s.p(-m, -m), s.p(m, -m), s.p(m, m), s.p(-m, m)]))
            s.raw('<polygon class="flow" points="%s" fill="none" stroke="%s" stroke-width="1.2" stroke-dasharray="2 26" filter="url(#nf%s)"/>' % (s.P([s.p(-m, -m), s.p(m, -m), s.p(m, m), s.p(-m, m)]), col, s.uid))
            s.raw('<polygon points="%s" fill="none" stroke="%s" stroke-opacity=".7" stroke-width="1"/>' % (s.P([s.p(-b, -b), s.p(b, -b), s.p(b, b), s.p(-b, b)]), col))
            ins = b - 3
            for k in (8, 16):
                q = ins - k
                if q > 4: s.raw('<polygon points="%s" fill="none" stroke="%s" stroke-opacity=".14"/>' % (s.P([s.p(-q, -q), s.p(q, -q), s.p(q, q), s.p(-q, q)]), col))
        s.neon([(s.p(-r, r), s.p(r, r)), (s.p(r, -r), s.p(r, r)), (s.p(-r, -r), s.p(-r, r)), (s.p(-r, -r), s.p(r, -r))], 1, col, .8)
        if lamps:
            pts = []
            for t_ in frange(-r+6, r-6, 12):
                pts += [s.p(t_, r-2), s.p(r-2, t_)]
            s.raw("".join('<circle cx="%.1f" cy="%.1f" r="1.4" fill="%s"/>' % (px, py, GOLD) for px, py in pts))
            s.raw('<g filter="url(#nf%s)" opacity=".7">%s</g>' % (s.uid, "".join('<circle cx="%.1f" cy="%.1f" r="1.6" fill="%s"/>' % (px, py, GOLD) for px, py in pts[::2])))
    def ground(s, r=50): s.slab()

    # ---------- generic prism ----------
    def prism(s, fp, z0, z1, style="office", col=None, op=1, smooth=False, inside=None, edges=True, topcol=None, edgecol=None, glow=True):
        col = col or s.c
        n = len(fp)
        cu = sum(p[0] for p in fp)/n; cv = sum(p[1] for p in fp)/n
        faces = []
        for i in range(n):
            a, b = fp[i], fp[(i+1) % n]
            nu, nv = (b[1]-a[1]), -(b[0]-a[0])
            mu, mv = (a[0]+b[0])/2 - cu, (a[1]+b[1])/2 - cv
            if nu*mu + nv*mv < 0: nu, nv = -nu, -nv
            L = math.hypot(nu, nv) or 1; nu, nv = nu/L, nv/L
            faces.append((a, b, nu, nv, (nu + nv) > 1e-6))
        H = z1 - z0
        if z0 == 0 and style not in ("trim",) and glow:
            w = max(abs(p[0]-cu) for p in fp)
            s.footglow(cu, cv, w*2 + 8, w + 4)
        if style == "glass":
            for (a, b, nu, nv, vis) in faces:
                if not vis:
                    s.poly([s.p(a[0], a[1], z0), s.p(b[0], b[1], z0), s.p(b[0], b[1], z1), s.p(a[0], a[1], z1)], col, .07, col, .3)
            s.poly([s.p(u, v, z0) for (u, v) in fp], col, .12, col, .4)
            if inside: inside()
        for (a, b, nu, nv, vis) in faces:
            if not vis: continue
            k = (nv - nu) / math.sqrt(2)
            Q = [s.p(a[0], a[1], z0), s.p(b[0], b[1], z0), s.p(b[0], b[1], z1), s.p(a[0], a[1], z1)]
            base = {"office": .13 + .07*k, "bands": .1 + .06*k, "dark": .05 + .035*k, "ribs": .2 + .1*k, "plain": .26 + .12*k, "gold": .55 + .2*k, "trim": .85 + .1*k}.get(style)
            if style == "glass":
                s.poly(Q, col, .16 + .06*k)
            else:
                s.poly(Q, mix(col, base), op)
                if style not in ("trim", "gold"): s.poly(Q, "url(#fd%s)" % s.uid, op)
            s.face_detail(style, a, b, z0, z1, k, col)
            if style in ("glass", "bands", "dark") and k > 0.3:
                s.poly(Q, "url(#gs%s)" % s.uid, 1)
        # top
        T = [s.p(u, v, z1) for (u, v) in fp]
        if style == "glass":
            s.poly(T, col, .22, col, .7)
        else:
            s.poly(T, topcol or mix(col, {"trim": .95, "gold": .85}.get(style, .32)), op)
            if style not in ("trim",) and H > 12:
                inner = [(cu + (u-cu)*.78, cv + (v-cv)*.78) for (u, v) in fp]
                s.poly([s.p(u, v, z1) for (u, v) in inner], "none", 1, col, .35)
        if not edges: return
        ec = edgecol or (col if style == "dark" else s.hi)
        if style == "trim": ec = mixw(col, .6)
        segs = []
        vis_idx = [i for i, f in enumerate(faces) if f[4]]
        if smooth:
            verts = []
            for i in range(n):
                if faces[i][4] != faces[i-1][4]: verts.append(fp[i])
        else:
            verts = []
            for i in vis_idx:
                verts += [faces[i][0], faces[i][1]]
        for (u, v) in verts: segs.append((s.p(u, v, z0), s.p(u, v, z1)))
        for i in range(n):
            a, b = fp[i], fp[(i+1) % n]
            if smooth and i % 1: continue
            segs.append((s.p(a[0], a[1], z1), s.p(b[0], b[1], z1)))
        s.neon(segs, 1.5 if style == "dark" else 1.0, ec, .95)
        if z0 == 0 or style == "trim":
            s.neon([(s.p(faces[i][0][0], faces[i][0][1], z0), s.p(faces[i][1][0], faces[i][1][1], z0)) for i in vis_idx], .9, col, .7)

    def fpt(s, a, b, t, z): return s.p(a[0] + (b[0]-a[0])*t, a[1] + (b[1]-a[1])*t, z)
    def quad(s, a, b, t0, t1, za, zb): return [s.fpt(a, b, t0, za), s.fpt(a, b, t1, za), s.fpt(a, b, t1, zb), s.fpt(a, b, t0, zb)]
    def qpath(s, qs): return "".join("M" + " L".join("%.1f %.1f" % pt for pt in q) + "Z" for q in qs)
    def face_detail(s, style, a, b, z0, z1, k, col):
        L = math.hypot(b[0]-a[0], b[1]-a[1]); H = z1 - z0
        dim = .55 + .45*(k+1)/2
        if style == "office":
            nc = max(1, int(L / 6.5)); bright, mid = [], []
            for zz in frange(z0 + 5, z1 - 6, 8.5):
                for i in range(nc):
                    r_ = s.rnd.random()
                    if r_ < .18: continue
                    q = s.quad(a, b, (i+.22)/nc, (i+.78)/nc, zz, zz+4.6)
                    (bright if r_ > .55 else mid).append(q)
            s.raw('<path d="%s" fill="%s" fill-opacity="%.2f"/>' % (s.qpath(mid), col, .55*dim))
            s.raw('<path d="%s" fill="%s" fill-opacity="%.2f"/>' % (s.qpath(bright), mixw(col, .55), .95*dim))
        elif style == "bands":
            nc = max(1, int(L / 7)); glass, lines, mull = [], [], ""
            for zz in frange(z0 + 3, z1 - 7, 11):
                glass.append(s.quad(a, b, 0.03, 0.97, zz, zz+7))
                lines.append((s.fpt(a, b, .03, zz+7), s.fpt(a, b, .97, zz+7)))
                for i in range(1, nc):
                    p1, p2 = s.fpt(a, b, i/nc, zz), s.fpt(a, b, i/nc, zz+7)
                    mull += "M%.1f %.1f L%.1f %.1f " % (p1 + p2)
            s.raw('<path d="%s" fill="%s" fill-opacity="%.2f"/>' % (s.qpath(glass), col, .62*dim))
            if mull: s.raw('<path d="%s" stroke="#03050f" stroke-opacity=".55" stroke-width=".9"/>' % mull)
            s.neon(lines, .7, mixw(col, .6), .8*dim)
        elif style == "dark":
            qs = []
            for zz in frange(z0 + 4, z1 - 4, 6.5):
                t = s.rnd.uniform(0, .25)
                while t < .92:
                    ln = s.rnd.uniform(.06, .22)
                    if s.rnd.random() < .45: qs.append(s.quad(a, b, t, min(t+ln, .95), zz, zz+1.8))
                    t += ln + s.rnd.uniform(.05, .2)
            s.raw('<path d="%s" fill="%s" fill-opacity="%.2f"/>' % (s.qpath(qs), mixw(col, .4), .9*dim))
        elif style == "ribs":
            nf = max(2, int(L / 5)); qs = []
            for i in range(nf):
                qs.append(s.quad(a, b, (i+.15)/nf, (i+.5)/nf, z0+1, z1-1))
            s.raw('<path d="%s" fill="%s" fill-opacity="%.2f"/>' % (s.qpath(qs), mixw(col, .15), .5*dim))
            fl = [(s.fpt(a, b, 0, zz), s.fpt(a, b, 1, zz)) for zz in frange(z0+8, z1-3, 8)]
            s.raw('<path d="%s" stroke="#03050f" stroke-opacity=".45" stroke-width="1.2"/>' % "".join("M%.1f %.1f L%.1f %.1f " % (p+q) for p, q in fl))
        elif style == "glass":
            fl = "".join("M%.1f %.1f L%.1f %.1f " % (s.fpt(a, b, 0, zz) + s.fpt(a, b, 1, zz)) for zz in frange(z0+10, z1-2, 10))
            nc = max(1, int(L / 8))
            ml = "".join("M%.1f %.1f L%.1f %.1f " % (s.fpt(a, b, i/nc, z0) + s.fpt(a, b, i/nc, z1)) for i in range(1, nc))
            s.raw('<path d="%s" stroke="%s" stroke-opacity=".45" stroke-width=".8"/>' % (fl + ml, s.hi))

    # ---------- accessories ----------
    def cornice(s, fp, z, h=3, d=2.2, col=None, smooth=False):
        s.prism(grow(fp, d), z, z + h, "trim", col=col or GOLD, smooth=smooth)
    def crown(s, fp, z, col=None):
        col = col or s.hi
        T = [s.p(u, v, z) for (u, v) in fp]
        s.raw('<polygon class="pulse" points="%s" fill="%s" filter="url(#nf%s)" style="animation-duration: 3s"/>' % (s.P(T), col, s.uid))
        x, y = s.p(sum(p[0] for p in fp)/len(fp), sum(p[1] for p in fp)/len(fp), z)
        s.raw('<ellipse cx="%.1f" cy="%.1f" rx="22" ry="12" fill="%s" opacity=".55" filter="url(#sf%s)"/>' % (x, y - 4, col, s.uid))
    def sign(s, a, b, t0, t1, z0, z1, col):
        Q = s.quad(a, b, t0, t1, z0, z1)
        s.poly(Q, "#04050d", 1)
        s.raw('<polygon points="%s" fill="none" stroke="%s" stroke-width="1.3" filter="url(#nf%s)"/>' % (s.P(Q), col, s.uid))
        qs = []
        for zz in frange(z0 + 3, z1 - 5, 5.5):
            w = s.rnd.uniform(.25, .4)
            m = (t0 + t1) / 2
            qs.append(s.quad(a, b, m - (t1-t0)*w, m + (t1-t0)*w, zz, zz + 3))
        s.raw('<path d="%s" fill="%s" filter="url(#nf%s)"/>' % (s.qpath(qs), mixw(col, .35), s.uid))
    def beam(s, u, v, z, h=90, w=7, col=None):
        x, y = s.p(u, v, z)
        s.raw('<polygon points="%.1f,%.1f %.1f,%.1f %.1f,%.1f %.1f,%.1f" fill="url(#bm%s)" opacity=".75"/>' % (x, y, x - w, y - h*.55, x, y - h, x + w, y - h*.55, s.uid))
        s.raw('<circle cx="%.1f" cy="%.1f" r="2.2" fill="#FFFFFF" filter="url(#nf%s)"/>' % (x, y, s.uid))
    def beacon(s, u, v, z, r=3.5):
        x, y = s.p(u, v, z)
        s.raw('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="%s" opacity=".5" filter="url(#sf%s)"/><circle class="pulse" cx="%.1f" cy="%.1f" r="%.1f" fill="#FFFFFF" filter="url(#nf%s)"/>' % (x, y, r*3, s.c, s.uid, x, y, r, s.uid))
    def antenna(s, u, v, z0, z1):
        s.line(s.p(u, v, z0), s.p(u, v, z1), 1.8, s.hi); s.beacon(u, v, z1 + 2)
    def ring(s, u, v, z, r, back=True, w=3):
        x, y, rx, ry = s.ell(u, v, z, r)
        if back: s.raw('<path d="%s" fill="none" stroke="%s" stroke-width="%s" stroke-opacity=".35"/>' % (s.arc(x, y, rx, ry, front=False), s.c, w))
        else: s.raw('<path d="%s" fill="none" stroke="%s" stroke-width="%s" filter="url(#nf%s)"/>' % (s.arc(x, y, rx, ry), s.hi, w*.6, s.uid))
    def dome(s, u, v, r, z, hh=None, fill=None, glass=False):
        x, y, rx, ry = s.ell(u, v, z, r); hh = hh or rx*.9
        s.raw('<path d="M%.1f %.1f A%.1f %.1f 0 0 1 %.1f %.1f A%.1f %.1f 0 0 1 %.1f %.1f Z" fill="%s" fill-opacity="%s"/>' % (x-rx, y, rx, hh, x+rx, y, rx, ry, x-rx, y, fill or "url(#cy%s)" % s.uid, .45 if glass else 1))
        s.raw('<ellipse cx="%.1f" cy="%.1f" rx="%.1f" ry="%.1f" fill="#FFFFFF" fill-opacity=".16"/>' % (x-rx*.35, y-hh*.55, rx*.28, hh*.16))
        mer = "".join('<path d="M%.1f %.1f Q%.1f %.1f %.1f %.1f"/>' % (x-rx*k, y+ry*math.sqrt(1-k*k), x-rx*k*1.05, y-hh*.85, x, y-hh) for k in (.66, .33, 0, -.33, -.66))
        s.raw('<g fill="none" stroke="%s" stroke-opacity=".6" stroke-width=".8">%s</g>' % (s.hi, mer))
        s.raw('<path d="M%.1f %.1f A%.1f %.1f 0 0 1 %.1f %.1f" fill="none" stroke="%s" stroke-width="1.2" filter="url(#nf%s)"/>' % (x-rx, y, rx, hh, x+rx, y, s.hi, s.uid))
    def pyr(s, u, v, a, z0, h, glass=False, col=None):
        col = col or s.c
        ap = s.p(u, v, z0+h); op = .4 if glass else 1
        A, B, C = s.p(u-a, v+a, z0), s.p(u+a, v+a, z0), s.p(u+a, v-a, z0)
        s.poly([A, B, ap], mix(col, .3) if not glass else col, op if not glass else .2)
        s.poly([C, B, ap], mix(col, .14) if not glass else col, op if not glass else .12)
        s.poly([A, B, ap], "url(#gs%s)" % s.uid, 1)
        if glass:
            for t in (.25, .5, .75):
                s.line(s.fpt((u-a, v+a), (u+a, v+a), t, z0), ap, .6, s.hi, .5); s.line(s.fpt((u+a, v-a), (u+a, v+a), t, z0), ap, .6, s.hi, .35)
        s.neon([(A, ap), (B, ap), (C, ap), (A, B), (B, C)], 1.1)
    def dish(s, u, v, z, r):
        x, y = s.p(u, v, z)
        s.line((x, y), (x, y-10), 3, mix(s.c, .5))
        s.raw('<ellipse cx="%.1f" cy="%.1f" rx="%.1f" ry="%.1f" transform="rotate(-28 %.1f %.1f)" fill="url(#cy%s)" stroke="%s" stroke-width="1.3" filter="url(#nf%s)"/>' % (x, y-14, r, r*.45, x, y-14, s.uid, s.hi, s.uid))
        s.line((x, y-14), (x+r*.55, y-14-r*.6), 1.5, s.hi)
        s.raw('<circle class="pulse" cx="%.1f" cy="%.1f" r="2.5" fill="#FFFFFF" filter="url(#nf%s)"/>' % (x+r*.55, y-14-r*.6, s.uid))
    def screen(s, face, u0, v, z1, w, h, col, lines=True):
        g = (s.lface(u0, v, z1) if face == "l" else s.rface(u0, v, z1))
        g += '<rect x="0" y="0" width="%.1f" height="%.1f" rx="1.5" fill="%s" fill-opacity=".9" filter="url(#nf%s)"/>' % (w, h, col, s.uid)
        if lines:
            g += '<rect x="%.1f" y="%.1f" width="%.1f" height="3" fill="#FFFFFF" fill-opacity=".9"/><rect x="%.1f" y="%.1f" width="%.1f" height="2" fill="#FFFFFF" fill-opacity=".55"/>' % (w*.12, h*.18, w*.5, w*.12, h*.3, w*.7)
            for i, hh in enumerate((.25, .4, .55)):
                g += '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="#04050d" fill-opacity=".75"/>' % (w*(.14 + i*.14), h*(.92-hh), w*.09, h*hh)
        s.raw(g + "</g>")
    def svg(s, label):
        return '<svg viewBox="0 -36 240 300" width="100%%" style="display: block" role="img" aria-label="%s"><defs>%s</defs>%s</svg>' % (label, s.defs, "".join(s.o))

# ================= designs =================
def hq_a(s):
    s.slab()
    f0 = cham(-40, 40, -40, 40, 10); s.prism(f0, 0, 14, "office"); s.cornice(f0, 14, 2.5)
    f1 = cham(-28, 28, -28, 28, 8); s.prism(f1, 16.5, 80, "bands"); s.cornice(f1, 80)
    f2 = cham(-20, 20, -20, 20, 6); s.prism(f2, 83, 128, "bands"); s.cornice(f2, 128)
    f3 = cham(-12, 12, -12, 12, 4); s.prism(f3, 131, 154, "bands"); s.crown(f3, 154)
    s.antenna(0, 0, 154, 188); s.beam(-40, 40, 0, 70); s.beam(40, -40, 0, 70)
def hq_b(s):
    s.slab()
    f0 = rect(-44, 44, -28, 28); s.prism(f0, 0, 22, "office"); s.cornice(f0, 22)
    fl = cham(-40, -14, -13, 13, 4); s.prism(fl, 24.5, 132, "dark"); s.crown(fl, 132)
    s.sign((-36, 13), (-18, 13), .25, .6, 44, 122, "#F472B6")
    s.prism(rect(-14, 14, -6, 6), 86, 98, "glass")
    fr = cham(14, 40, -13, 13, 4); s.prism(fr, 24.5, 112, "dark"); s.crown(fr, 112)
    s.sign((40, -9), (40, 9), .3, .7, 34, 104, "#60A5FA")
    s.antenna(-27, 0, 132, 150); s.beam(-44, 28, 0, 64); s.beam(44, -28, 0, 64)
def hq_c(s):
    s.slab()
    f0 = circ(0, 0, 38); s.prism(f0, 0, 12, "office", smooth=True); s.cornice(f0, 12, 2.5, smooth=True)
    s.ring(0, 0, 104, 44); s.ring(0, 0, 64, 36)
    f1 = circ(0, 0, 20); s.prism(f1, 14.5, 140, "bands", smooth=True); s.cornice(f1, 140, smooth=True)
    s.dome(0, 0, 19, 143, glass=True)
    s.ring(0, 0, 64, 36, back=False, w=2.5); s.ring(0, 0, 104, 44, back=False)
    s.antenna(0, 0, 165, 186)

def re_a(s):
    s.slab()
    f0 = cham(-40, 40, -40, 40, 12); s.prism(f0, 0, 14, "office"); s.cornice(f0, 14, 2.5)
    f1 = circ(0, 0, 28); s.prism(f1, 16.5, 56, "ribs", smooth=True); s.cornice(f1, 56, 2.5, smooth=True)
    s.dome(0, 0, 28, 58.5)
    x, y = s.p(0, 0, 58)
    s.raw('<path d="M%.1f %.1f L%.1f %.1f" stroke="#020309" stroke-width="7" stroke-linecap="round"/>' % (x-4, y-4, x+10, y-30))
    s.line((x+2, y-12), (x+34, y-52), 7, mix(s.c, .6)); s.line((x+30, y-47), (x+40, y-59), 9, mix(s.c, .9))
    s.raw('<circle cx="%.1f" cy="%.1f" r="3" fill="#FFFFFF" filter="url(#nf%s)"/>' % (x+41, y-60, s.uid))
    s.beam(-40, 40, 0, 60)
def re_b(s):
    s.slab()
    def inside():
        for (u, v, r) in [(-18, -10, 9), (10, -20, 11), (-6, 14, 10), (20, 8, 8), (-24, 22, 7)]:
            x, y = s.p(u, v, 6); s.raw('<circle cx="%.1f" cy="%.1f" r="%d" fill="#0f5e4a"/><circle cx="%.1f" cy="%.1f" r="%.1f" fill="#34d399" fill-opacity=".55"/>' % (x, y, r, x-2, y-3, r*.55))
        s.prism(circ(4, 0, 6), 0, 36, "plain", smooth=True, col="#A7F3D0"); s.crown(circ(4, 0, 6), 36, "#FFFFFF")
    f = rect(-36, 36, -32, 32); s.prism(f, 0, 50, "glass", inside=inside); s.cornice(f, 50, 2)
    s.pyr(0, 0, 34, 52, 44, glass=True); s.beacon(0, 0, 98)
def re_c(s):
    s.slab()
    ft = circ(-24, -24, 12); s.prism(ft, 0, 116, "bands", smooth=True); s.cornice(ft, 116, smooth=True); s.dish(-24, -24, 119, 18)
    def inside():
        for (u, v) in [(0, 0), (20, 10), (6, 22)]:
            x, y = s.p(u, v, 8); s.raw('<rect x="%.1f" y="%.1f" width="12" height="8" rx="1" fill="%s" filter="url(#nf%s)"/>' % (x-6, y-8, s.c, s.uid))
    fl = rect(-12, 40, -12, 36); s.prism(fl, 0, 34, "glass", inside=inside); s.cornice(fl, 34)
    s.beam(40, 36, 0, 60)

def ma_a(s):
    s.slab()
    f = cham(-26, 26, -26, 26, 5); s.prism(f, 0, 138, "dark"); s.cornice(f, 138)
    s.screen("l", -19, 26, 126, 38, 54, s.c); s.screen("r", 26, 19, 104, 38, 38, "#F472B6")
    s.sign((21, 26), (26, 21), .1, .9, 20, 70, "#FBBF24")
    s.antenna(0, 0, 141, 172); s.beam(-26, 26, 0, 80)
def ma_b(s):
    s.slab()
    f = rect(-38, 38, -30, 30); s.prism(f, 0, 40, "bands"); s.cornice(f, 40)
    b, t = s.p(0, 0, 43), s.p(0, 0, 175)
    s.line((b[0]-12, b[1]), t, 2, s.hi); s.line((b[0]+12, b[1]), t, 2, s.hi)
    for z in range(55, 170, 14):
        y = b[1] - (z-43); w = 12 * (1 - (z-43)/132); s.line((b[0]-w, y), (b[0]+w, y), 1.1, s.hi, .8)
        s.line((b[0]-w, y), (b[0]+w*.7, y-14), .8, s.hi, .45)
    for k, r in enumerate([18, 30, 42]):
        s.raw('<ellipse class="pulse" cx="%.1f" cy="%.1f" rx="%d" ry="%.1f" fill="none" stroke="%s" stroke-width="2" filter="url(#nf%s)" style="animation-delay: %.1fs"/>' % (t[0], t[1]+6, r, r*.35, s.c, s.uid, k*.5))
    s.beacon(0, 0, 177); s.beam(-38, 30, 0, 60); s.beam(38, -30, 0, 60)
def ma_c(s):
    s.slab()
    f = rect(-38, 38, -38, 38); s.prism(f, 0, 24, "office"); s.cornice(f, 24)
    s.line(s.p(-14, -20, 26), s.p(-14, -20, 50), 3, mix(s.c, .5))
    s.screen("l", -34, -20, 96, 40, 50, s.c)
    s.line(s.p(22, 10, 26), s.p(22, 10, 44), 3, mix(s.c, .5))
    s.screen("r", 22, 34, 82, 44, 38, "#FBBF24")
    s.beam(-38, 38, 0, 56)

def ve_a(s):
    s.slab()
    f0 = rect(-38, 38, -38, 38); s.prism(f0, 0, 42, "bands"); s.cornice(f0, 42)
    f1 = rect(-28, 28, -28, 28); s.prism(f1, 45, 88, "bands"); s.cornice(f1, 88)
    f2 = rect(-18, 18, -18, 18); s.prism(f2, 91, 126, "bands"); s.crown(f2, 126)
    x, y = s.p(0, 0, 126)
    s.raw('<path class="pulse" d="M%.1f %.1f l12 -14 l12 14 M%.1f %.1f l12 -14 l12 14" fill="none" stroke="#FFFFFF" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" filter="url(#nf%s)"/>' % (x-12, y-8, x-12, y+2, s.uid))
    s.beam(-38, 38, 0, 70); s.beam(38, -38, 0, 70)
def ve_b(s):
    s.slab()
    z = 0
    for i, a in enumerate([42, 34, 26, 18, 10]):
        f = rect(-a, a, -a, a); s.prism(f, z, z + 15, "ribs" if a > 12 else "dark"); s.cornice(f, z + 15, 2, d=1.5); z += 17
    s.antenna(0, 0, z, z + 22)
def ve_c(s):
    s.slab()
    fl = cham(-38, -12, -13, 13, 5); s.prism(fl, 0, 122, "office"); s.cornice(fl, 122); s.crown(cham(-35, -15, -10, 10, 4), 125)
    s.prism(rect(-12, 12, -6, 6), 74, 86, "glass")
    fr = cham(12, 38, -13, 13, 5); s.prism(fr, 0, 100, "office"); s.cornice(fr, 100); s.crown(cham(15, 35, -10, 10, 4), 103)
    s.beam(-38, 13, 0, 60); s.beam(38, -13, 0, 60)

def su_a(s):
    s.slab()
    f = circ(0, 0, 34); s.prism(f, 0, 44, "bands", smooth=True); s.cornice(f, 44, smooth=True); s.dish(-6, -6, 47, 26)
    s.beam(-38, 38, 0, 50)
def su_b(s):
    s.slab()
    f0 = rect(-30, 30, -30, 30); s.prism(f0, 0, 14, "office"); s.cornice(f0, 14, 2)
    f1 = circ(0, 0, 18); s.prism(f1, 16, 60, "ribs", smooth=True); s.cornice(f1, 60, 2, smooth=True)
    f2 = circ(0, 0, 14); s.prism(f2, 62, 98, "ribs", smooth=True); s.cornice(f2, 98, 2, smooth=True)
    s.prism(circ(0, 0, 10), 100, 120, "dark", smooth=True)
    s.prism(circ(0, 0, 11), 120, 136, "glass", smooth=True); x, y = s.p(0, 0, 128)
    s.raw('<polygon class="pulse" points="%.1f,%.1f %.1f,%.1f %.1f,%.1f" fill="#FFF7D6" fill-opacity=".3"/>' % (x, y, x+110, y-30, x+110, y+12))
    s.raw('<circle cx="%.1f" cy="%.1f" r="6" fill="#FFFFFF" filter="url(#nf%s)"/>' % (x, y, s.uid))
    s.dome(0, 0, 12, 136)
def su_c(s):
    s.slab()
    f = circ(0, 0, 40); s.prism(f, 0, 28, "office", smooth=True); s.cornice(f, 28, smooth=True)
    x, y, rx, ry = s.ell(0, 0, 30.5, 26); s.raw('<ellipse cx="%.1f" cy="%.1f" rx="%.1f" ry="%.1f" fill="#050817" stroke="%s" stroke-opacity=".7"/>' % (x, y, rx, ry, s.c))
    ft = circ(0, 0, 10); s.prism(ft, 30.5, 74, "bands", smooth=True); s.dome(0, 0, 10, 74)
    for t in range(3): s.raw('<ellipse class="pulse" cx="%.1f" cy="%.1f" rx="%d" ry="%.1f" fill="none" stroke="%s" stroke-width="1.5" filter="url(#nf%s)" style="animation-delay: %.1fs"/>' % (x, y-60, 16+t*10, (16+t*10)*.4, s.c, s.uid, t*.4))

def da_a(s):
    s.slab()
    f0 = circ(0, 0, 38); s.prism(f0, 0, 14, "office", smooth=True); s.cornice(f0, 14, 2.5, smooth=True)
    s.ring(0, 0, 50, 32); s.ring(0, 0, 82, 28)
    def inside():
        x0, y0 = s.p(0, 0, 16); x1, y1 = s.p(0, 0, 104)
        s.raw('<rect x="%.1f" y="%.1f" width="16" height="%.1f" rx="8" fill="#FFFFFF" filter="url(#nf%s)"/>' % (x0-8, y1, y0-y1, s.uid))
        s.raw('<rect class="pulse" x="%.1f" y="%.1f" width="34" height="%.1f" rx="17" fill="%s" opacity=".45" filter="url(#sf%s)"/>' % (x0-17, y1, y0-y1, s.c, s.uid))
    f1 = circ(0, 0, 22); s.prism(f1, 16.5, 104, "glass", smooth=True, inside=inside); s.cornice(f1, 104, smooth=True)
    s.ring(0, 0, 50, 32, back=False); s.ring(0, 0, 82, 28, back=False)
    s.beam(-38, 38, 0, 50); s.beam(38, -38, 0, 50)
def da_b(s):
    s.slab()
    for (v0, v1) in [(-38, -22), (-8, 8), (22, 38)]:
        f = rect(-36, 36, v0, v1); s.prism(f, 0, 44, "dark"); s.cornice(f, 44, 2, d=1.2)
        g = s.lface(-36, v1, 44)
        for zz in range(6, 40, 6):
            for k in range(5, 70, 6):
                g += '<rect x="%d" y="%d" width="3" height="2" fill="%s" fill-opacity="%s"/>' % (k, zz, s.c if (k+zz) % 3 else "#34D399", .95 if (k*zz) % 5 else .3)
        s.raw(g + "</g>")
    s.antenna(0, 0, 46, 62)
def da_c(s):
    s.slab()
    f = cham(-24, 24, -24, 24, 6); s.prism(f, 0, 20, "ribs"); s.cornice(f, 20)
    x, y = s.p(0, 0, 22); s.raw('<ellipse cx="%.1f" cy="%.1f" rx="30" ry="15" fill="url(#gl%s)"/>' % (x, y, s.uid))
    zc, r, hh = 92, 30, 44
    T, B = s.p(0, 0, zc+hh), s.p(0, 0, zc-hh); E = [s.p(r, 0, zc), s.p(0, r, zc), s.p(-r, 0, zc), s.p(0, -r, zc)]
    s.raw('<g class="pulse" style="animation-duration: 3.5s">')
    s.poly([T, E[3], E[0]], mix(s.c, .7), .5); s.poly([T, E[2], E[3]], mix(s.c, .9), .55)
    s.poly([T, E[2], E[1]], mix(s.c, .85), .85, "#FFFFFF", .6); s.poly([T, E[1], E[0]], mix(s.c, .6), .85, "#FFFFFF", .6)
    s.poly([B, E[2], E[1]], mix(s.c, .45), .9, "#FFFFFF", .3); s.poly([B, E[1], E[0]], mix(s.c, .3), .9, "#FFFFFF", .3)
    s.raw('</g>')
    s.neon([(T, E[1]), (E[2], E[1]), (E[1], E[0]), (B, E[1])], 1, s.hi, .8)
    s.beam(-24, 24, 20, 50); s.beam(24, -24, 20, 50)

def da_d(s):
    """L'Archive circulaire : cylindres de verre empilés, anneaux lumineux, disque et anneau flottant."""
    s.slab()
    for z in (22, 46, 70):
        s.ring(0, 0, z, 40)
    for (r, z0, z1) in [(30, 0, 24), (28, 24, 48), (26, 48, 72), (24, 72, 94)]:
        f = circ(0, 0, r); s.prism(f, z0, z1, "glass", smooth=True); s.cornice(f, z1, 1.8, smooth=True)
    for z in (22, 46, 70):
        s.ring(0, 0, z, 40, back=False)
    x, y, rx, ry = s.ell(0, 0, 96, 20)
    s.raw('<ellipse cx="%.1f" cy="%.1f" rx="%.1f" ry="%.1f" fill="#E0FFFF" filter="url(#nf%s)"/>' % (x, y, rx, ry, s.uid))
    s.raw('<polygon points="%.1f,%.1f %.1f,%.1f %.1f,%.1f %.1f,%.1f" fill="#FFFFFF" fill-opacity=".12"/>' % (x - rx, y, x - rx*.55, y - 38, x + rx*.55, y - 38, x + rx, y))
    s.raw('<ellipse class="pulse" cx="%.1f" cy="%.1f" rx="%.1f" ry="%.1f" fill="none" stroke="%s" stroke-width="3" filter="url(#nf%s)"/>' % (x, y - 44, rx*.6, ry*.6, mixw(s.c, .4), s.uid))
def da_e(s):
    """Le Prisme neural : grand prisme de verre traversé d'un réseau de neurones, livre holographique au-dessus."""
    s.slab()
    zc, r, hh = 70, 40, 62
    T, B = s.p(0, 0, zc + hh), s.p(0, 0, zc - hh)
    E = [s.p(r, 0, zc), s.p(0, r, zc), s.p(-r, 0, zc), s.p(0, -r, zc)]
    s.poly([T, E[2], E[3]], mix(s.c, .25), .55); s.poly([T, E[3], E[0]], mix(s.c, .3), .5)
    cx, cy = s.p(0, 0, zc)
    rnd = random.Random(5); g = ""
    for k in range(14):
        a = k * 2 * math.pi / 14 + rnd.uniform(-.1, .1); L = rnd.uniform(16, 27)
        mx, my = cx + math.cos(a)*L*.55, cy + math.sin(a)*L*.45
        ex, ey = cx + math.cos(a)*L, cy + math.sin(a)*L*.8
        g += '<path d="M%.1f %.1f L%.1f %.1f L%.1f %.1f"/>' % (cx, cy, mx, my, ex, ey)
        g += '<circle cx="%.1f" cy="%.1f" r="1.6" fill="%s" stroke="none"/>' % (ex, ey, mixw(s.c, .5))
        if k % 2:
            g += '<path d="M%.1f %.1f l%.1f %.1f"/>' % (mx, my, math.cos(a + .7)*7, math.sin(a + .7)*6)
    s.raw('<g class="pulse" style="animation-duration: 2.8s" fill="none" stroke="%s" stroke-width="1.3" filter="url(#nf%s)">%s'
          '<circle cx="%.1f" cy="%.1f" r="4" fill="#FFFFFF" stroke="none"/></g>' % (s.c, s.uid, g, cx, cy))
    s.poly([T, E[2], E[1]], mix(s.c, .5), .28, "#FFFFFF", .5); s.poly([T, E[1], E[0]], mix(s.c, .4), .22, "#FFFFFF", .5)
    s.poly([B, E[2], E[1]], mix(s.c, .35), .45, "#FFFFFF", .3); s.poly([B, E[1], E[0]], mix(s.c, .25), .45, "#FFFFFF", .3)
    s.neon([(T, E[1]), (E[2], E[1]), (E[1], E[0]), (B, E[1]), (T, E[2]), (T, E[0])], 1, s.hi, .8)
    bx, by = T[0], T[1] - 34
    s.raw('<polygon points="%.1f,%.1f %.1f,%.1f %.1f,%.1f" fill="#FFFFFF" fill-opacity=".1"/>' % (T[0], T[1], bx - 14, by + 6, bx + 14, by + 6))
    s.raw('<g filter="url(#nf%s)" fill="none" stroke="%s" stroke-width="1.6" stroke-linejoin="round">'
          '<path d="M%.1f %.1f q-7 -4 -14 0 v-11 q7 -4 14 0 z M%.1f %.1f q7 -4 14 0 v-11 q-7 -4 -14 0 z"/></g>' % (s.uid, mixw(s.c, .4), bx, by + 4, bx, by + 4))
def da_f(s):
    """L'Agora en spirale : rampe hélicoïdale lumineuse autour d'une colonne de lumière."""
    s.slab()
    f = circ(0, 0, 30); s.prism(f, 0, 6, "office", smooth=True); s.cornice(f, 6, 1.5, smooth=True)
    pts = []
    for i in range(0, 361):
        th = i / 360 * 4.25 * 2 * math.pi
        u, v, z = 24 * math.cos(th), 24 * math.sin(th), 10 + th / (2 * math.pi) * 30
        pts.append((s.p(u, v, z), math.cos(th) + math.sin(th) >= 0))
    def runs(front):
        out, cur = [], []
        for q, fr in pts:
            if fr == front: cur.append(q)
            elif cur: out.append(cur); cur = []
        if cur: out.append(cur)
        return out
    for seg in runs(False):
        s.raw('<polyline points="%s" fill="none" stroke="%s" stroke-width="7" stroke-opacity=".45" stroke-linecap="round"/>' % (s.P(seg), mix(s.c, .7)))
    x0, y0 = s.p(0, 0, 6); x1, y1 = s.p(0, 0, 170)
    s.raw('<rect x="%.1f" y="%.1f" width="16" height="%.1f" fill="url(#bm%s)" opacity=".8"/>' % (x0 - 8, y1, y0 - y1, s.uid))
    s.raw('<rect class="pulse" x="%.1f" y="%.1f" width="5" height="%.1f" rx="2.5" fill="#FFFFFF" filter="url(#nf%s)"/>' % (x0 - 2.5, y1 + 20, y0 - y1 - 20, s.uid))
    for seg in runs(True):
        s.raw('<polyline points="%s" fill="none" stroke="%s" stroke-width="7" stroke-linecap="round" filter="url(#nf%s)"/>'
              '<polyline points="%s" fill="none" stroke="#FFFFFF" stroke-opacity=".7" stroke-width="1" stroke-dasharray="1 9"/>' % (s.P(seg), s.c, s.uid, s.P(seg)))

def fi_a(s):
    s.slab()
    f = rect(-38, 38, -38, 38); s.prism(f, 0, 62, "dark"); s.cornice(f, 62, 3.5)
    g = s.lface(-38, 38, 62); g += '<circle cx="38" cy="32" r="22" fill="%s" stroke="%s" stroke-width="3" filter="url(#nf%s)"/><circle cx="38" cy="32" r="14" fill="none" stroke="%s" stroke-width="2"/>' % (mix(s.c, .35), s.c, s.uid, GOLD)
    for a in range(0, 360, 60): g += '<line x1="38" y1="32" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="2"/>' % (38 + 14*math.cos(math.radians(a)), 32 + 14*math.sin(math.radians(a)), GOLD)
    g += '<circle cx="38" cy="32" r="4" fill="#FFFFFF"/></g>'; s.raw(g)
    g = s.rface(38, 38, 58) + "".join('<rect x="%d" y="0" width="5" height="54" fill="%s" fill-opacity=".55" filter="url(#nf%s)"/>' % (k, GOLD, s.uid) for k in range(8, 70, 14)); s.raw(g + "</g>")
    s.beam(-38, 38, 0, 56); s.beam(38, -38, 0, 56)
def fi_b(s):
    s.slab()
    s.prism(rect(-44, 44, -44, 44), 0, 7, "plain", col=GOLD); s.prism(rect(-38, 38, -38, 38), 7, 13, "plain", col=GOLD)
    s.prism(rect(-26, 26, -26, 26), 13, 60, "office")
    cols = [(u, 32) for u in range(-30, 31, 12)] + [(32, v) for v in range(-30, 21, 12)]
    for (u, v) in sorted(cols, key=lambda c: c[0] + c[1]): s.prism(circ(u, v, 3.5, 12), 13, 60, "gold", col=GOLD, smooth=True, edges=False)
    s.prism(rect(-38, 38, -38, 38), 60, 68, "gold", col=GOLD); s.pyr(0, 0, 38, 68, 24, col=s.c)
    s.beacon(0, 0, 94)
def fi_c(s):
    s.slab()
    for i in range(8):
        f = circ((i % 2)*2 - 1, 0, 26); s.prism(f, i*11, i*11 + 10, "gold", col="#FBBF24", smooth=True, edges=(i == 7))
    x, y = s.p(0, 0, 128)
    s.raw('<g class="pulse" style="animation-duration: 3s"><g transform="matrix(1 0.5 0 1 %.1f %.1f)"><circle cx="0" cy="0" r="22" fill="#FBBF24" stroke="#FFF1B8" stroke-width="3" filter="url(#nf%s)"/><circle cx="0" cy="0" r="14" fill="none" stroke="#B45309" stroke-width="2"/></g></g>' % (x, y, s.uid))
    s.beam(-40, 40, 0, 60); s.beam(40, -40, 0, 60)

def at_a(s):
    """Manufacture : halle de production et cheminées qui fument."""
    s.slab()
    f = rect(-40, 40, -32, 32); s.prism(f, 0, 36, "office"); s.cornice(f, 36)
    for (u, v, r, h) in [(-22, -14, 7, 104), (-4, -18, 6, 86)]:
        c = circ(u, v, r); s.prism(c, 38.5, h, "bands", smooth=True); s.cornice(c, h, 2, smooth=True)
        x, y = s.p(u, v, h)
        for k in range(3):
            s.raw('<ellipse class="pulse" cx="%.1f" cy="%.1f" rx="%.1f" ry="%.1f" fill="#FFFFFF" fill-opacity=".14" '
                  'style="animation-delay: %.1fs"/>' % (x + k*6, y - 10 - k*12, 7 + k*3, 4 + k*1.5, k*.6))
    s.sign((40, -24), (40, 24), .3, .7, 8, 30, s.c)
    s.beam(-40, 32, 0, 60); s.beam(40, -32, 0, 60)
def at_b(s):
    """Atelier à sheds : toits en dents de scie vitrés et pont roulant."""
    s.slab()
    def inside():
        for (u, v) in [(-20, -12), (4, 6), (22, -8)]:
            x, y = s.p(u, v, 6); s.raw('<rect x="%.1f" y="%.1f" width="14" height="9" rx="2" fill="%s" filter="url(#nf%s)"/>' % (x-7, y-9, s.c, s.uid))
    f = rect(-40, 40, -34, 34); s.prism(f, 0, 32, "glass", inside=inside); s.cornice(f, 32, 2)
    for v0 in (-34, -11, 12):
        s.prism(rect(-40, 40, v0, v0 + 22), 34, 46, "ribs"); s.crown(rect(-40, 40, v0 + 18, v0 + 22), 46)
    a, b = s.p(-46, -30, 66), s.p(46, -30, 66)
    s.line(s.p(-46, -30, 0), a, 3, mix(s.c, .6)); s.line(s.p(46, -30, 0), b, 3, mix(s.c, .6)); s.line(a, b, 4, s.c)
    x, y = s.p(6, -30, 66); s.line((x, y), (x, y + 26), 1.5, s.hi); s.raw('<rect x="%.1f" y="%.1f" width="10" height="7" fill="%s"/>' % (x-5, y+26, mixw(s.c, .3)))
    s.beacon(46, -30, 68)
def at_c(s):
    """Tour-atelier : socle, fût vitré et engrenage lumineux au sommet."""
    s.slab()
    f0 = cham(-34, 34, -34, 34, 8); s.prism(f0, 0, 18, "office"); s.cornice(f0, 18, 2.5)
    f1 = circ(0, 0, 20); s.prism(f1, 20.5, 104, "bands", smooth=True); s.cornice(f1, 104, smooth=True)
    x, y = s.p(0, 0, 134)
    teeth = "".join('<rect x="-3" y="-30" width="6" height="9" rx="1" fill="%s" transform="rotate(%d)"/>' % (s.c, a) for a in range(0, 360, 30))
    s.raw('<g class="pulse" style="animation-duration: 3s"><g transform="translate(%.1f %.1f) matrix(1 0.5 0 1 0 0)" filter="url(#nf%s)">'
          '%s<circle r="23" fill="%s" fill-opacity=".35" stroke="%s" stroke-width="4"/><circle r="9" fill="#FFFFFF"/></g></g>'
          % (x, y, s.uid, teeth, mix(s.c, .5), s.c))
    s.line(s.p(0, 0, 106), (x, y + 18), 3, mix(s.c, .6))
    s.beam(-34, 34, 0, 56); s.beam(34, -34, 0, 56)

def ju_a(s):
    """Palais de justice : colonnade, fronton et coupole."""
    s.slab()
    s.prism(rect(-42, 42, -36, 36), 0, 8, "plain", col=s.c); s.prism(rect(-36, 36, -30, 30), 8, 14, "plain", col=s.c)
    s.prism(rect(-26, 26, -22, 22), 14, 58, "office")
    cols = [(u, 28) for u in range(-30, 31, 12)] + [(32, v) for v in range(-24, 25, 12)]
    for (u, v) in sorted(cols, key=lambda c: c[0] + c[1]):
        s.prism(circ(u, v, 3.2, 12), 14, 58, "plain", col="#E2E8F0", smooth=True, edges=False)
    f = rect(-36, 36, -30, 30); s.prism(f, 58, 66, "plain", col=s.c); s.cornice(f, 66, 2)
    s.dome(0, 0, 18, 66, glass=True); s.beacon(0, 0, 88)
    s.beam(-42, 36, 0, 56); s.beam(42, -36, 0, 56)
def ju_b(s):
    """Tour de la balance : tour élancée surmontée d'une balance lumineuse."""
    s.slab()
    f0 = cham(-30, 30, -30, 30, 8); s.prism(f0, 0, 16, "office"); s.cornice(f0, 16, 2.5)
    f1 = cham(-16, 16, -16, 16, 5); s.prism(f1, 18.5, 120, "bands"); s.cornice(f1, 120)
    x, y = s.p(0, 0, 122)
    g = ('<g filter="url(#nf%s)" stroke="%s" stroke-width="3" fill="none" stroke-linecap="round">'
         '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f"/><line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f"/>'
         '<path d="M%.1f %.1f l-9 18 h18 z M%.1f %.1f l-9 18 h18 z"/>'
         '<path d="M%.1f %.1f q9 8 18 0 M%.1f %.1f q9 8 18 0" fill="%s" fill-opacity=".35"/></g>') % (
        s.uid, mixw(s.c, .3), x, y, x, y - 40, x - 30, y - 34, x + 30, y - 40,
        x - 30, y - 34, x + 30, y - 40, x - 39, y - 16, x + 21, y - 22, s.c)
    s.raw('<g class="pulse" style="animation-duration: 3.5s">%s</g>' % g)
    s.beam(-30, 30, 0, 60)
def ju_c(s):
    """Archives scellées : salle des registres et sceau officiel flottant."""
    s.slab()
    f = rect(-40, 40, -34, 34); s.prism(f, 0, 30, "dark"); s.cornice(f, 30, 3)
    for k, v in enumerate((-22, -6, 10)):
        s.prism(rect(-30, 30, v, v + 10), 32.5, 40 + k*6, "glass")
    x, y = s.p(0, 0, 96)
    s.raw('<g class="pulse" style="animation-duration: 3s"><g transform="matrix(1 0.5 0 1 %.1f %.1f)" filter="url(#nf%s)">'
          '<circle r="22" fill="%s" fill-opacity=".3" stroke="%s" stroke-width="3"/><circle r="15" fill="none" stroke="#FFFFFF" '
          'stroke-width="1.5" stroke-dasharray="3 3"/><path d="M-8 0 l5 6 l11 -12" fill="none" stroke="#FFFFFF" stroke-width="3"/></g></g>'
          % (x, y, s.uid, mix(s.c, .5), s.c))
    s.line(s.p(0, 0, 52), (x, y + 22), 1.5, s.hi, .6)
    s.beam(-40, 34, 0, 56); s.beam(40, -34, 0, 56)

def se_a(s):
    """Bouclier : coupole de verre sous un bouclier hexagonal lumineux."""
    s.slab()
    f = circ(0, 0, 34); s.prism(f, 0, 16, "office", smooth=True); s.cornice(f, 16, 2, smooth=True)
    s.dome(0, 0, 28, 18.5, glass=True)
    x, y = s.p(0, 0, 60)
    hexa = " ".join("%.1f,%.1f" % (x + 30*math.cos(math.radians(a)), y + 30*math.sin(math.radians(a))*0.9) for a in range(30, 390, 60))
    s.raw('<g class="pulse" style="animation-duration: 3s" filter="url(#nf%s)"><polygon points="%s" fill="%s" fill-opacity=".14" stroke="%s" stroke-width="2.5"/>'
          '<path d="M%.1f %.1f l0 -12 l10 -4 l10 4 l0 12 q-10 10 -10 10 q0 0 -10 -10 z" transform="translate(-10 0)" fill="none" stroke="#FFFFFF" stroke-width="2"/></g>'
          % (s.uid, hexa, s.c, mixw(s.c, .3), x, y + 4))
    s.beam(-34, 34, 0, 50)
def se_b(s):
    """Pare-feu : muraille de panneaux lumineux devant une tour de contrôle."""
    s.slab()
    for k, u in enumerate((-30, -10, 10, 30)):
        f = rect(u - 8, u + 8, 18, 30); s.prism(f, 0, 30 + (k % 2) * 8, "dark"); s.crown(f, 30 + (k % 2) * 8)
    f = cham(-18, 18, -32, 4, 5); s.prism(f, 0, 96, "bands"); s.cornice(f, 96); s.crown(cham(-14, 14, -28, 0, 4), 99)
    for u in (-30, -10, 10, 30):
        x, y = s.p(u, 30, 22)
        s.raw('<path class="pulse" d="M%.1f %.1f q4 -10 0 -18 q8 6 4 18 z" fill="%s" fill-opacity=".85" filter="url(#nf%s)" style="animation-delay: %.1fs"/>' % (x, y, s.c, s.uid, (u + 30) / 40))
    s.antenna(0, -14, 99, 122)
def se_c(s):
    """Centre de veille : salle d'écrans et radar qui balaie l'horizon."""
    s.slab()
    f = rect(-38, 38, -30, 30); s.prism(f, 0, 34, "office"); s.cornice(f, 34)
    s.screen("l", -30, 30, 30, 40, 22, s.c); s.screen("r", 38, 22, 30, 36, 22, mixw(s.c, .2))
    c = circ(-16, -12, 8); s.prism(c, 36.5, 70, "bands", smooth=True); s.dish(-16, -12, 73, 20)
    x, y = s.p(-16, -12, 90)
    s.raw('<g filter="url(#nf%s)"><ellipse cx="%.1f" cy="%.1f" rx="34" ry="14" fill="none" stroke="%s" stroke-opacity=".5"/>'
          '<path class="pulse" d="M%.1f %.1f L%.1f %.1f A34 14 0 0 1 %.1f %.1f Z" fill="%s" fill-opacity=".35"/></g>' % (s.uid, x, y, s.c, x, y, x + 34, y, x + 24, y + 10, s.c))
    s.beam(-38, 30, 0, 56)

def _code_holo(s, x, y, size=1.0):
    """Symbole « </> » lumineux flottant."""
    k = size
    return ('<g class="pulse" style="animation-duration: 3s" filter="url(#nf%s)"><rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="4" '
            'fill="%s" fill-opacity=".22" stroke="%s" stroke-width="2"/><path d="M%.1f %.1f l-9 9 l9 9 M%.1f %.1f l9 9 l-9 9 M%.1f %.1f l-6 22" '
            'fill="none" stroke="#FFFFFF" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" transform="translate(%.1f %.1f) scale(%.2f) translate(%.1f %.1f)"/></g>') % (
        s.uid, x - 30*k, y - 18*k, 60*k, 36*k, s.c, mixw(s.c, .3), -8, -9, 8, -9, 3, -11, x, y, k, 0, 0)
def de_a(s):
    """Tour du code : tour sombre couverte d'écrans de code et symbole </> flottant."""
    s.slab()
    f = cham(-24, 24, -24, 24, 5); s.prism(f, 0, 128, "dark"); s.cornice(f, 128)
    s.screen("l", -18, 24, 118, 36, 50, s.c); s.screen("r", 24, 18, 96, 36, 40, mixw(s.c, .25))
    s.screen("l", -18, 24, 56, 36, 30, mix(s.c, .8))
    x, y = s.p(0, 0, 160); s.raw(_code_holo(s, x, y))
    s.line(s.p(0, 0, 131), (x, y + 18), 1.5, s.hi, .6)
    s.beam(-24, 24, 0, 70)
def de_b(s):
    """Campus tech : open-space vitré, terrasse et baies de serveurs sur le toit."""
    s.slab()
    def inside():
        for (u, v) in [(-22, -10), (-2, 8), (18, -6)]:
            x, y = s.p(u, v, 6); s.raw('<rect x="%.1f" y="%.1f" width="12" height="8" rx="1.5" fill="%s" filter="url(#nf%s)"/>' % (x-6, y-8, s.c, s.uid))
    f = rect(-42, 42, -34, 34); s.prism(f, 0, 30, "glass", inside=inside); s.cornice(f, 30, 2)
    f2 = rect(-42, 0, -34, 0); s.prism(f2, 32.5, 62, "office"); s.cornice(f2, 62)
    for k, u in enumerate((8, 18, 28)):
        r = rect(u - 4, u + 4, -26, -6); s.prism(r, 32.5, 46, "dark"); s.crown(r, 46)
        x, y = s.p(u + 4, -16, 40); s.raw('<circle class="pulse" cx="%.1f" cy="%.1f" r="1.6" fill="%s" style="animation-delay: %.1fs"/>' % (x, y, s.c, k*.4))
    x, y = s.p(-21, -17, 96); s.raw(_code_holo(s, x, y, .8))
    s.beam(-42, 34, 0, 56); s.beam(42, -34, 0, 56)
def de_c(s):
    """Pile logicielle : couches empilées et décalées, comme une architecture en couches, sous une antenne de déploiement."""
    s.slab()
    for k, (d, h0, h1) in enumerate([(36, 0, 22), (30, 24.5, 46), (24, 48.5, 70), (18, 72.5, 94)]):
        o = (k % 2) * 4 - 2
        f = cham(-d + o, d + o, -d - o, d - o, 4)
        s.prism(f, h0, h1, ("glass", "bands", "glass", "bands")[k]); s.cornice(f, h1, 2)
    s.antenna(2, -2, 96.5, 124)
    x, y = s.p(0, 0, 150); s.raw(_code_holo(s, x, y, .7))
    s.beam(-36, 36, 0, 56); s.beam(36, -36, 0, 56)

POLES = [
 ("hq", "Command HQ", "Orchestration", "#818CF8", [("A", "Spire de commandement", "Tour à pans coupés, bandeaux vitrés lumineux et couronne qui brille.", hq_a), ("B", "Citadelle", "Deux tours noires à enseignes néon reliées par une passerelle de verre.", hq_b), ("C", "Anneau orbital", "Tour cylindrique vitrée entourée d’anneaux de données en orbite.", hq_c)]),
 ("research", "Labo Recherche", "Veille et opportunités", "#34D399", [("A", "Observatoire", "Rotonde nervurée, coupole et télescope pointé vers les marchés.", re_a), ("B", "Serre de verre", "Laboratoire transparent : on voit les idées pousser à l’intérieur.", re_b), ("C", "Labo à antenne", "Labo vitré aux écrans allumés et tour équipée d’une parabole.", re_c)]),
 ("marketing", "Hub Marketing", "Acquisition", "#60A5FA", [("A", "Tour média", "Tour noire couverte d’écrans géants et d’enseignes néon.", ma_a), ("B", "Studio de diffusion", "Pylône émetteur qui propage les ondes de la marque.", ma_b), ("C", "Place des panneaux", "Panneaux publicitaires flottants au-dessus d’un studio.", ma_c)]),
 ("sales", "Centre Ventes", "Revenus", "#A78BFA", [("A", "Tour de verre", "Gradins vitrés, corniches dorées et flèche de croissance.", ve_a), ("B", "Ziggurat des deals", "Pyramide à étages : chaque niveau est une étape du pipeline.", ve_b), ("C", "Tours jumelles", "Deux tours de bureaux illuminées reliées par un pont de verre.", ve_c)]),
 ("support", "Service Client", "Clients", "#FBBF24", [("A", "Centre d’écoute", "Rotonde à bandeaux lumineux surmontée d’une parabole.", su_a), ("B", "Phare", "Le phare qui guide les clients, 24 h/24.", su_b), ("C", "Hub en anneau", "Bâtiment circulaire autour d’une tour relais qui émet.", su_c)]),
 ("data", "Data Core", "Mémoire et données", "#F1F5F9", [("A", "Réacteur de données", "Cœur lumineux sous verre, entouré d’anneaux d’énergie.", da_a), ("B", "Ferme de serveurs", "Rangées de baies noires et leurs voyants qui clignotent.", da_b), ("C", "Cristal de mémoire", "Cristal flottant au-dessus de son socle : la mémoire partagée.", da_c), ("D", "L’Archive circulaire", "Cylindres de verre empilés, cerclés d’anneaux lumineux, disque et anneau flottant.", da_d), ("E", "Le Prisme neural", "Grand prisme de verre traversé d’un réseau de neurones, livre holographique au sommet.", da_e), ("F", "L’Agora en spirale", "Rampe hélicoïdale lumineuse qui s’enroule autour d’une colonne de lumière.", da_f)]),
 ("atelier", "Atelier", "Production", "#FB923C", [("A", "Manufacture", "Halle de production et cheminées qui fument : on fabrique les livrables.", at_a), ("B", "Atelier à sheds", "Toits vitrés en dents de scie et pont roulant au-dessus des postes.", at_b), ("C", "Tour-atelier", "Fût vitré surmonté d’un engrenage lumineux qui tourne.", at_c)]),
 ("legal", "Juridique", "Conformité", "#CBD5E1", [("A", "Palais de justice", "Colonnade, fronton et coupole de verre : le droit au cœur de la cité.", ju_a), ("B", "Tour de la balance", "Tour élancée surmontée d’une balance lumineuse.", ju_b), ("C", "Archives scellées", "Salle des registres sous un sceau officiel flottant.", ju_c)]),
 ("security", "Cybersécurité", "Protection", "#F87171", [("A", "Bouclier", "Coupole de verre sous un bouclier hexagonal lumineux.", se_a), ("B", "Pare-feu", "Muraille de panneaux en flammes devant une tour de contrôle.", se_b), ("C", "Centre de veille", "Salle d’écrans et radar qui balaie l’horizon 24 h/24.", se_c)]),
 ("dev", "Développement", "Sites et applications", "#22D3EE", [("A", "Tour du code", "Tour sombre couverte d’écrans de code, symbole </> flottant au sommet.", de_a), ("B", "Campus tech", "Open-space vitré et baies de serveurs qui clignotent sur le toit.", de_b), ("C", "Pile logicielle", "Couches empilées comme une architecture logicielle, sous une antenne de déploiement.", de_c)]),
 ("finance", "Trésorerie", "Finances", "#F472B6", [("A", "Coffre-fort", "Bloc noir, porte de coffre circulaire et lames dorées.", fi_a), ("B", "Temple financier", "Colonnade dorée et fronton : la solidité des comptes.", fi_b), ("C", "Tour des pièces", "Pile de pièces géante et pièce d’or flottante.", fi_c)]),
]


# ================= open-space (vue bureaux) =================
def _bot(s, u, v, z=0, col=None, k=1.0):
    col = col or s.c; x, y = s.p(u, v, z)
    return ('<g><ellipse cx="%.1f" cy="%.1f" rx="%.1f" ry="%.1f" fill="#000" opacity=".45"/>'
            '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="%.1f" fill="#c9d3ff"/>'
            '<circle cx="%.1f" cy="%.1f" r="%.1f" fill="#eef1ff"/>'
            '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="1" fill="%s"/></g>') % (
        x, y, 5*k, 2*k, x-3.2*k, y-8*k, 6.4*k, 7*k, 2.2*k, x, y-11.5*k, 4*k, x-2.6*k, y-12.4*k, 5.2*k, 2*k, col)

def _plane_quad(s, axis, const, a0, a1, z0, z1):
    if axis == "v": return [s.p(a0, const, z0), s.p(a1, const, z0), s.p(a1, const, z1), s.p(a0, const, z1)]
    return [s.p(const, a0, z0), s.p(const, a1, z0), s.p(const, a1, z1), s.p(const, a0, z1)]

def _wallscreen(s, axis, const, a0, a1, z0, z1, col, kind):
    Q = _plane_quad(s, axis, const, a0, a1, z0, z1)
    s.poly(Q, "#050816", .95, col, .9)
    s.poly(Q, col, .12)
    def at(t, h): 
        a = a0 + (a1 - a0) * t; z = z0 + (z1 - z0) * h
        return s.p(a, const, z) if axis == "v" else s.p(const, a, z)
    if kind == 0:
        pts = [at(.08 + i*.12, .25 + .5*abs(math.sin(i*1.7 + const))) for i in range(8)]
        s.raw('<polyline points="%s" fill="none" stroke="%s" stroke-width="1.3" filter="url(#nf%s)"/>' % (s.P(pts), mixw(col, .4), s.uid))
    else:
        for i in range(6):
            h = .2 + .6*abs(math.sin(i*2.3 + const))
            s.poly([at(.1 + i*.14, .12), at(.18 + i*.14, .12), at(.18 + i*.14, .12 + h*.75), at(.1 + i*.14, .12 + h*.75)], mixw(col, .3), .9)
    s.neon([(Q[0], Q[1]), (Q[1], Q[2]), (Q[2], Q[3]), (Q[3], Q[0])], .8, col, .9)

def _desk(s, u, v, col):
    s.raw(_bot(s, u, v - 4.5, 0, col, .75))
    s.prism(rect(u - 5, u + 5, v - 2.2, v + 2.2), 0, 5, "plain", col="#475569", edges=False, glow=False)
    Q = _plane_quad(s, "v", v - 1.4, u - 3.6, u + 3.6, 6, 11.5)
    s.poly(_plane_quad(s, "v", v - 1.4, u - 4.4, u + 4.4, 5.4, 12.2), col, .3)
    s.poly(Q, mixw(col, .35), .95)
    s.line(s.p(u, v - 1.4, 5), s.p(u, v - 1.4, 6), 1.2, "#94a3b8")

def _plant(s, u, v):
    s.prism(circ(u, v, 2.2, 10), 0, 4, "plain", col="#64748b", smooth=True, edges=False, glow=False)
    x, y = s.p(u, v, 7)
    s.raw('<circle cx="%.1f" cy="%.1f" r="4.2" fill="#14532d"/><circle cx="%.1f" cy="%.1f" r="2.6" fill="#22c55e" fill-opacity=".7"/>' % (x, y, x - 1, y - 1.5))

def _holo(s, u, v, kind, col, size=1.0):
    x, y = s.p(u, v, 0)
    s.raw('<ellipse cx="%.1f" cy="%.1f" rx="%.1f" ry="%.1f" fill="%s" fill-opacity=".25" stroke="%s" stroke-width="1.2" filter="url(#nf%s)"/>' % (x, y, 14*size, 7*size, col, mixw(col, .4), s.uid))
    s.raw('<ellipse cx="%.1f" cy="%.1f" rx="%.1f" ry="%.1f" fill="none" stroke="%s" stroke-opacity=".6" stroke-dasharray="2 3"/>' % (x, y, 19*size, 9.5*size, col))
    s.raw('<polygon points="%.1f,%.1f %.1f,%.1f %.1f,%.1f %.1f,%.1f" fill="url(#bm%s)" opacity=".55"/>' % (x - 12*size, y, x - 6*size, y - 40*size, x + 6*size, y - 40*size, x + 12*size, y, s.uid))
    cy = y - 30*size; R = 13*size
    g = '<g class="pulse" style="animation-duration: 3.2s" filter="url(#nf%s)">' % s.uid
    hc = mixw(col, .45)
    if kind == "globe":
        g += '<circle cx="%.1f" cy="%.1f" r="%.1f" fill="%s" fill-opacity=".28" stroke="%s" stroke-width="1.4"/>' % (x, cy, R*1.5, col, hc)
        for k in (.35, .7, 1.0): g += '<ellipse cx="%.1f" cy="%.1f" rx="%.1f" ry="%.1f" fill="none" stroke="%s" stroke-opacity=".75" stroke-width=".8"/>' % (x, cy, R*1.5*k, R*1.5, hc)
        for k in (-.5, 0, .5): g += '<ellipse cx="%.1f" cy="%.1f" rx="%.1f" ry="%.1f" fill="none" stroke="%s" stroke-opacity=".75" stroke-width=".8"/>' % (x, cy + R*1.5*k, R*1.5*math.sqrt(1 - k*k), R*.35, hc)
        g += '<ellipse cx="%.1f" cy="%.1f" rx="%.1f" ry="%.1f" fill="none" stroke="%s" stroke-width="1.2" transform="rotate(-18 %.1f %.1f)"/>' % (x, cy, R*2.3, R*.55, hc, x, cy)
    elif kind == "chart":
        g += '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="2" fill="%s" fill-opacity=".18" stroke="%s"/>' % (x - R*1.3, cy - R, R*2.6, R*2, col, hc)
        pts = [(x - R*1.1 + i*R*.44, cy + R*.6 - [0, .5, .3, .9, .7, 1.3][i]*R*.9) for i in range(6)]
        g += '<polyline points="%s" fill="none" stroke="%s" stroke-width="1.6"/>' % (" ".join("%.1f,%.1f" % q for q in pts), hc)
    elif kind == "target":
        for k in (1, .66, .33): g += '<circle cx="%.1f" cy="%.1f" r="%.1f" fill="none" stroke="%s" stroke-width="1.6"/>' % (x, cy, R*k, hc)
        g += '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#FFFFFF" stroke-width="1.8"/>' % (x + R*1.1, cy - R*1.1, x, cy)
    elif kind == "bars":
        for i, h in enumerate((.5, .8, .65, 1.2)):
            g += '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s" fill-opacity=".8"/>' % (x - R + i*R*.55, cy + R*.7 - h*R, R*.38, h*R, hc)
        g += '<path d="M%.1f %.1f L%.1f %.1f l-4 0 m4 0 l0 4" fill="none" stroke="#FFFFFF" stroke-width="1.5"/>' % (x - R, cy + R*.1, x + R*1.1, cy - R*.9)
    elif kind == "headset":
        g += '<path d="M%.1f %.1f A%.1f %.1f 0 0 1 %.1f %.1f" fill="none" stroke="%s" stroke-width="2.2"/>' % (x - R, cy + R*.3, R, R, x + R, cy + R*.3, hc)
        g += '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="2" fill="%s"/><rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="2" fill="%s"/>' % (x - R*1.2, cy, R*.5, R*.8, hc, x + R*.7, cy, R*.5, R*.8, hc)
    elif kind == "gear":
        for a in range(0, 360, 45):
            g += '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="1" fill="%s" transform="rotate(%d %.1f %.1f)"/>' % (x - R*.18, cy - R*1.25, R*.36, R*.4, hc, a, x, cy)
        g += '<circle cx="%.1f" cy="%.1f" r="%.1f" fill="%s" fill-opacity=".3" stroke="%s" stroke-width="2"/><circle cx="%.1f" cy="%.1f" r="%.1f" fill="#FFFFFF"/>' % (x, cy, R*.95, col, hc, x, cy, R*.3)
    elif kind == "scale":
        g += '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="2"/>' % (x, cy + R, x, cy - R, hc)
        g += '<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="2"/>' % (x - R*1.2, cy - R*.7, x + R*1.2, cy - R*.9, hc)
        for sx, sy in ((x - R*1.2, cy - R*.7), (x + R*1.2, cy - R*.9)):
            g += '<path d="M%.1f %.1f l-5 9 h10 z" fill="%s" fill-opacity=".4" stroke="%s"/>' % (sx, sy, col, hc)
    elif kind == "shield":
        g += '<path d="M%.1f %.1f l%.1f %.1f v%.1f q0 %.1f %.1f %.1f q%.1f %.1f %.1f %.1f v%.1f z" fill="%s" fill-opacity=".3" stroke="%s" stroke-width="2"/>' % (
            x, cy - R, R, R*.35, R*.8, R*.9, -R, R*.9, -R, -R*.9, -R, -R*.9, -R*.8, col, hc)
    elif kind == "code":
        g += '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="3" fill="%s" fill-opacity=".18" stroke="%s"/>' % (x - R*1.4, cy - R, R*2.8, R*2, col, hc)
        g += '<path d="M%.1f %.1f l-%.1f %.1f l%.1f %.1f M%.1f %.1f l%.1f %.1f l-%.1f %.1f M%.1f %.1f l-%.1f %.1f" fill="none" stroke="#FFFFFF" stroke-width="1.8" stroke-linecap="round"/>' % (
            x - R*.45, cy - R*.5, R*.5, R*.5, R*.5, R*.5, x + R*.45, cy - R*.5, R*.5, R*.5, R*.5, R*.5, x + R*.15, cy - R*.6, R*.3, R*1.2)
    elif kind == "coin":
        g += '<ellipse cx="%.1f" cy="%.1f" rx="%.1f" ry="%.1f" fill="#FBBF24" stroke="#FFF1B8" stroke-width="1.5"/><ellipse cx="%.1f" cy="%.1f" rx="%.1f" ry="%.1f" fill="none" stroke="#B45309" stroke-width="1.2"/>' % (x, cy, R*.9, R*1.1, x, cy, R*.55, R*.7)
    g += '</g>'
    s.raw(g)
    if kind == "city":
        for (du, dv, h) in [(-5, -5, 22), (2, -6, 30), (-6, 2, 16), (3, 3, 26), (7, -1, 18), (-1, 7, 14), (0, 0, 36)]:
            s.prism(rect(u + du - 2, u + du + 2, v + dv - 2, v + dv + 2), 2, 2 + h*size, "glass", col=col, glow=False)

def office(s, R, kind, hq=False):
    col = s.c
    # floor grid
    fl = "".join("M%.1f %.1f L%.1f %.1f " % (s.p(t, -R) + s.p(t, R)) + "M%.1f %.1f L%.1f %.1f " % (s.p(-R, t) + s.p(R, t)) for t in frange(-R + 6, R - 6, 6))
    s.raw('<path d="%s" stroke="%s" stroke-opacity=".12" stroke-width=".8"/>' % (fl, col))
    # back walls (glass) with screens
    s.prism(rect(-R, R, -R, -R + 2.5), 0, 24, "glass", glow=False)
    s.prism(rect(-R, -R + 2.5, -R + 2.5, R), 0, 24, "glass", glow=False)
    n = 3 if not hq else 4
    for i in range(n):
        a0 = -R + 5 + i*(2*R - 10)/n; a1 = a0 + (2*R - 10)/n - 3
        _wallscreen(s, "v", -R + 2.6, a0, a1, 7, 21, col if i % 2 == 0 else mixw(col, .1), i % 2)
        _wallscreen(s, "u", -R + 2.6, a0 + 2.5, a1 + 2.5, 7, 21, col, (i + 1) % 2)
    items = []
    if hq:
        for k in range(7):
            ang = math.pi*2*k/7 + .3
            items.append(("desk", R*.68*math.cos(ang), R*.68*math.sin(ang)))
        items.append(("holo", 0, 0))
        items += [("plant", R*.85, -R*.85), ("plant", -R*.85, R*.85), ("bot", R*.35, R*.05), ("bot", .05*R, R*.38)]
    else:
        for (fu, fv) in [(-.45, -.55), (.05, -.55), (.55, -.55), (-.55, -.05), (-.55, .45)]:
            items.append(("desk", fu*R, fv*R))
        items.append(("holo", .25*R, .25*R))
        items += [("plant", .82*R, -.82*R), ("plant", -.82*R, .82*R), ("bot", .7*R, .15*R), ("bot", .05*R, .72*R)]
    items.sort(key=lambda it: it[1] + it[2])
    for (t, u, v) in items:
        if t == "desk": _desk(s, u, v, col)
        elif t == "plant": _plant(s, u, v)
        elif t == "bot": s.raw(_bot(s, u, v, 0, col, 1.0))
        elif t == "holo": _holo(s, u, v, kind, col, 1.25 if hq else .9)

OFFICE_KIND = {"hq": "globe", "research": "chart", "marketing": "target", "sales": "bars", "support": "headset", "data": "city", "finance": "coin", "atelier": "gear", "legal": "scale"}
