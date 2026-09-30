#!/usr/bin/env python3
"""Local server for the Spotify Album Poster tool.

Serves index.html AND provides /api/album?url=<spotify link> which reads the
album data server-side (no CORS limits): exact title/artist/tracklist from
Spotify's embed page, plus release date + hi-res (1600px) cover from iTunes.

Run:  python3 server.py            (defaults to port 8888)
      python3 server.py 9000       (custom port)
Then open http://127.0.0.1:<port>/
"""
import http.server, socketserver, urllib.parse, urllib.request, json, re, os, sys

DIR = os.path.dirname(os.path.abspath(__file__))
UA  = {"User-Agent": "Mozilla/5.0 (poster-tool)"}


def fetch(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode("utf-8", "replace")


def album_id(s):
    m = re.search(r"album[:/]([A-Za-z0-9]{22})", s) or re.search(r"([A-Za-z0-9]{22})", s)
    return m.group(1) if m else None


def _find_album(o):
    if isinstance(o, dict):
        if o.get("type") == "album" and "trackList" in o:
            return o
        for v in o.values():
            r = _find_album(v)
            if r:
                return r
    elif isinstance(o, list):
        for v in o:
            r = _find_album(v)
            if r:
                return r
    return None


def spotify_album(aid):
    html = fetch(f"https://open.spotify.com/embed/album/{aid}")
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
    if not m:
        raise ValueError("Could not read Spotify album data")
    e = _find_album(json.loads(m.group(1)))
    if not e:
        raise ValueError("Album data not found on Spotify")
    artist = e.get("subtitle") or ""
    tracks = [
        {"n": i + 1, "name": t.get("title"), "ms": t.get("duration", 0)}
        for i, t in enumerate(e.get("trackList", []))
    ]
    cover_sp = ""
    imgs = (e.get("visualIdentity") or {}).get("image") or []
    if imgs:
        cover_sp = max(imgs, key=lambda i: i.get("maxWidth", 0)).get("url", "")
        cover_sp = cover_sp.replace("ab67616d00001e02", "ab67616d0000b273")  # force 640px
    return {"title": e.get("title"), "artist": artist, "tracks": tracks, "coverSpotify": cover_sp}


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def itunes_meta(artist, title, ntracks=0):
    """Release date + hi-res cover, matched by artist, title AND track count
    so we lock onto the exact edition (not standard-vs-deluxe) the user linked."""
    q = urllib.parse.quote(f"{artist} {title}")
    try:
        data = json.loads(fetch(f"https://itunes.apple.com/search?entity=album&limit=25&term={q}"))
    except Exception:
        return {}
    res = [r for r in data.get("results", []) if r.get("collectionName")]
    if not res:
        return {}
    nt, na = _norm(title), _norm(artist)
    best = None
    for r in res:
        cn, an = _norm(r["collectionName"]), _norm(r.get("artistName"))
        score = 0
        if cn == nt: score += 4
        elif cn.startswith(nt) or nt.startswith(cn): score += 2
        elif nt in cn or cn in nt: score += 1
        if an == na: score += 3
        elif na and (na in an or an in na): score += 1
        tc = r.get("trackCount") or 0
        if ntracks and tc == ntracks: score += 3
        elif ntracks and abs(tc - ntracks) <= 1: score += 1
        if best is None or score > best[0]:
            best = (score, r)
    # Too weak a match -> don't trust iTunes art; let the client use the exact Spotify cover.
    if best[0] < 5:
        return {"releaseDate": (best[1].get("releaseDate") or "")[:10]}
    r = best[1]
    art = r.get("artworkUrl100", "")
    hi = re.sub(r"/\d+x\d+bb\.(jpg|png)", r"/1600x1600bb.\1", art) if art else ""
    return {"releaseDate": (r.get("releaseDate") or "")[:10], "cover": hi}


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=DIR, **k)

    def log_message(self, *a):
        pass  # quiet

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/album"):
            try:
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                aid = album_id((qs.get("url") or [""])[0])
                if not aid:
                    raise ValueError("Couldn't find an album ID in that link")
                sp = spotify_album(aid)
                it = itunes_meta(sp["artist"], sp["title"], len(sp["tracks"]))
                self._json({"id": aid, **sp, **it})
            except Exception as ex:
                self._json({"error": str(ex)})
            return
        return super().do_GET()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PORT", 8888))
    bind = os.environ.get("BIND", "127.0.0.1")  # containers set BIND=0.0.0.0
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer((bind, port), Handler) as s:
        print(f"Spotify Album Poster  ->  http://{bind}:{port}/", flush=True)
        try:
            s.serve_forever()
        except KeyboardInterrupt:
            pass
