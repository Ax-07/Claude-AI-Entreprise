"""Client réseau sortant sécurisé (audit SEC-02) : un agent ne peut atteindre que des adresses publiques d'Internet.

- Chaque adresse est vérifiée, y compris à CHAQUE redirection (au plus 5).
- Le nom est résolu une seule fois et la connexion se fait vers l'IP vérifiée (le nom reste utilisé pour TLS et
  l'en-tête Host) : un DNS qui répond une adresse publique au contrôle puis une adresse locale à la connexion
  (« DNS rebinding ») ne passe pas.
- Refus de tout ce qui n'est pas routable sur Internet : réseau local, boucle locale, lien local, plage des opérateurs
  (100.64/10), adresses réservées ou de documentation, multicast, IPv6 locales et IPv4 encapsulées dans de l'IPv6.
- Pas d'identifiants dans l'URL, http/https uniquement, taille de réponse et délai bornés.

Les appels vers des services fixes et connus (API Stripe, GitHub, Brave, fournisseurs d'IA) ne passent pas par ici :
leur adresse est écrite dans le code, pas choisie par un agent.
"""
from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import time
import urllib.parse

MAX_REDIRECTS = 5
DEFAULT_UA = "Mozilla/5.0 (compatible; AI-Enterprise-Studio)"


class NetError(Exception):
    """Adresse refusée ou invalide : rien n'a été téléchargé."""


def ip_allowed(ip: str) -> bool:
    """Vrai seulement pour une adresse publique routable sur Internet."""
    return _allowed(ip)


def _allowed(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(a, ipaddress.IPv6Address):
        mapped = a.ipv4_mapped or a.sixtofour or (a.teredo[1] if a.teredo else None)
        if mapped is not None and not _allowed(str(mapped)):    # IPv4 encapsulée : jugée sur sa vraie adresse
            return False
        if a in ipaddress.ip_network("2002::/16") or a in ipaddress.ip_network("2001::/32"):
            return False                                           # 6to4 / Teredo : jamais nécessaire pour un site
    return a.is_global and not a.is_multicast and not a.is_reserved


def resolve(host: str, port: int) -> list[str]:
    """Adresses IP du nom. Toutes doivent être publiques (une seule adresse privée suffit à refuser)."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as e:
        raise NetError(f"nom introuvable : {host}") from e
    ips = list(dict.fromkeys(i[4][0] for i in infos))
    if not ips or not all(ip_allowed(ip) for ip in ips):        # (ip_allowed : remplaçable dans les tests)
        raise NetError(f"adresse non publique : {host}")
    return ips


def is_public_host(host: str) -> bool:
    try:
        resolve(host, 443)
        return True
    except NetError:
        return False


def _check_url(url: str) -> urllib.parse.SplitResult:
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise NetError("URL invalide (http/https uniquement)")
    if u.username or u.password:
        raise NetError("URL avec identifiants refusée")
    try:
        u.port
    except ValueError as e:
        raise NetError("port invalide") from e
    return u


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host, port, ip, timeout):
        super().__init__(host, port, timeout=timeout)
        self._ip = ip

    def connect(self):
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, port, ip, timeout):
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._ip = ip

    def connect(self):
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)   # certificat vérifié pour le NOM


def get_once(url: str, timeout: float = 20, max_bytes: int = 1_500_000, headers: dict | None = None) -> dict:
    """Un seul GET, sans suivre de redirection, vers une IP publique vérifiée. Lève NetError si l'adresse est refusée."""
    u = _check_url(url)
    port = u.port or (443 if u.scheme == "https" else 80)
    ips = resolve(u.hostname, port)
    path = (u.path or "/") + (f"?{u.query}" if u.query else "")
    hdrs = {"User-Agent": DEFAULT_UA, "Accept": "text/html,application/xhtml+xml,*/*", "Accept-Encoding": "identity",
            "Connection": "close", **(headers or {})}
    cls = _PinnedHTTPS if u.scheme == "https" else _PinnedHTTP
    t = time.time()
    last = None
    for ip in ips[:2]:                                  # deuxième adresse si la première ne répond pas
        conn = cls(u.hostname, port, ip, timeout)
        try:
            conn.request("GET", path, headers=hdrs)
            r = conn.getresponse()
            body = r.read(max_bytes)
            return {"status": r.status, "headers": {k.lower(): v for k, v in r.getheaders()}, "body": body,
                    "ms": int((time.time() - t) * 1000)}
        except (OSError, http.client.HTTPException) as e:
            last = e
        finally:
            conn.close()
    raise last if last else NetError("aucune adresse joignable")


def safe_get(url: str, timeout: float = 20, max_bytes: int = 1_500_000, headers: dict | None = None,
             max_redirects: int = MAX_REDIRECTS, getter=None) -> dict:
    """GET qui suit les redirections en revérifiant chacune. Renvoie aussi l'URL finale et la chaîne de redirections."""
    getter = getter or (lambda x: get_once(x, timeout, max_bytes, headers))
    chain = []
    for _ in range(max_redirects + 1):
        r = getter(url)
        loc = r["headers"].get("location")
        if r["status"] in (301, 302, 303, 307, 308) and loc:
            chain.append((r["status"], url))
            url = urllib.parse.urljoin(url, loc)
            _check_url(url)
            continue
        return {**r, "url": url, "chain": chain}
    raise NetError("trop de redirections")
