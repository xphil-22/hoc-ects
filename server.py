#!/usr/bin/env python3
"""Lokaler Proxy für next.hoc.kit.edu mit ECTS-Filter.

Start:  python3 server.py [--host 127.0.0.1] [--port 8765] [--term 0xD25D...]
Dann:   http://localhost:8765
"""
import argparse
import html
import json
import os
import re
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

BASE = "https://next.hoc.kit.edu"
TERM = None  # None = aktuelles Semester (über /de/events/current-term)
CACHE_FILE = os.environ.get("CACHE_FILE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache.json")
CACHE_TTL = 6 * 3600
REFRESH_COOLDOWN = 10 * 60  # "neu laden" höchstens alle 10 min pro Semester
MAX_TERMS = 20
UA = "hoc-ects (+https://github.com/xphil-22/hoc-ects)"

_lock = threading.Lock()
_cache = {}  # term -> {"ts": float, "events": [...], "term_name": str}
_term_locks = {}  # term -> Lock, damit parallele Anfragen nur einen Crawl auslösen
_current = {"ts": 0.0, "term": None}


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8")


def current_term():
    with _lock:
        if _current["term"] and time.time() - _current["ts"] < 3600:
            return _current["term"]
    req = urllib.request.Request(f"{BASE}/de/events/current-term", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        m = re.search(r"/events/(0x[0-9A-Fa-f]{32})", r.geturl())
    if not m:
        raise RuntimeError("aktuelles Semester nicht gefunden")
    with _lock:
        _current.update(ts=time.time(), term=m.group(1))
    return m.group(1)


def rsc_payload(page):
    """Next.js-Flight-Daten aus den self.__next_f.push-Skripten zusammensetzen."""
    chunks = re.findall(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)', page)
    return "".join(json.loads('"' + c + '"') for c in chunks)


def rsc_text_rows(payload):
    """Text-Zeilen der Form '41:T<hexlen>,<text>' auflösen (für "$41"-Referenzen)."""
    rows = {}
    data = payload.encode("utf-8")
    for m in re.finditer(rb"(?:^|\n)([0-9a-f]+):T([0-9a-f]+),", data):
        start = m.end()
        rows[m.group(1).decode()] = data[start:start + int(m.group(2), 16)].decode("utf-8", "replace")
    return rows


def json_field(seg, key):
    m = re.search(r'"%s":("(?:[^"\\]|\\.)*"|-?\d+|null)' % re.escape(key), seg)
    return json.loads(m.group(1)) if m else None


def known(seg, key):
    m = re.search(r'"%s":\{"kind":"known","value":"([^"]*)"' % re.escape(key), seg)
    return m.group(1) if m else None


def strip_html(s):
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    return re.sub(r"\s+", " ", s).strip()


ECTS_RE = re.compile(r"(?<![\d.,])(\d(?:[.,]5)?)\s*(?:ECTS|LP\b|Leistungspunkte?)", re.I)


def parse_term(term):
    page = fetch(f"{BASE}/de/events/{term}")
    data = rsc_payload(page)
    title = re.search(r"<title>([^<]*)", page)
    events = {}
    for m in re.finditer(r'\{"id":"(0x[0-9A-F]{32})","uniqueId"', data):
        seg = data[m.start():m.start() + 3000]
        eid = m.group(1)
        if eid in events:
            continue
        lect = re.search(r'"lecturers":(\[[^\]]*\])', seg)
        events[eid] = {
            "id": eid,
            "url": f"{BASE}/de/event/{eid}",
            "number": json_field(seg, "eventNumber"),
            "title": (json_field(seg, "title") or "").strip(),
            "titleEn": (json_field(seg, "titleEn") or "").strip(),
            "type": known(seg, "type"),
            "format": known(seg, "format"),
            "language": known(seg, "language"),
            "lecturers": json.loads(lect.group(1)) if lect else [],
            "places": json_field(seg, "places"),
            "registrations": json_field(seg, "registrations"),
            "start": (json_field(seg, "startDate") or "").lstrip("$D"),
            "registrationStart": (json_field(seg, "registrationStart") or "").lstrip("$D"),
            "registrationEnd": (json_field(seg, "registrationEnd") or "").lstrip("$D"),
        }
    name = html.unescape(title.group(1)).split(" - ")[0] if title else term
    return name, list(events.values())


def parse_event_ects(eid):
    data = rsc_payload(fetch(f"{BASE}/de/event/{eid}"))
    rows = rsc_text_rows(data)
    i = data.find('"eventNumber"')
    seg = data[i:] if i >= 0 else ""
    texts = []
    for key in ("note", "noteEn"):
        v = json_field(seg, key)
        if isinstance(v, str) and re.fullmatch(r"\$[0-9a-f]+", v):
            v = rows.get(v[1:], "")
        texts.append(strip_html(v))
    text = " | ".join(t for t in texts if t)
    vals = sorted({float(v.replace(",", ".")) for v in ECTS_RE.findall(text)})
    vals = [int(v) if v.is_integer() else v for v in vals if 0 < v <= 10]
    snippets = []
    for m in ECTS_RE.finditer(texts[0] or text):
        snippets.append(text_snip(texts[0] or text, m))
    return {"ects": vals, "ectsText": snippets[:4]}


def text_snip(t, m):
    end = t.find(" ", m.end() + 70)
    return t[m.start():end if end > 0 else len(t)].strip()


def load_disk():
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_disk():
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_cache, f, ensure_ascii=False)
    except OSError:
        pass


def get_term(term, refresh=False):
    with _lock:
        if not _cache:
            _cache.update(load_disk())
        term_lock = _term_locks.setdefault(term, threading.Lock())
    with term_lock:
        hit = _cache.get(term)
        if hit:
            age = time.time() - hit["ts"]
            if age < (REFRESH_COOLDOWN if refresh else CACHE_TTL):
                return hit
        return crawl(term)


def crawl(term):
    name, events = parse_term(term)

    def enrich(ev):
        try:
            ev.update(parse_event_ects(ev["id"]))
        except Exception as e:  # einzelne Seite kaputt -> trotzdem weiter
            ev.update({"ects": [], "ectsText": [], "error": str(e)})
        return ev

    with ThreadPoolExecutor(max_workers=6) as pool:
        events = list(pool.map(enrich, events))
    events.sort(key=lambda e: (e["start"] or "", e["title"]))
    entry = {"ts": time.time(), "term_name": name, "events": events}
    with _lock:
        _cache[term] = entry
        for old in sorted(_cache, key=lambda t: _cache[t]["ts"])[:-MAX_TERMS]:
            del _cache[old]
        save_disk()
    return entry


INDEX = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html"), encoding="utf-8").read()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        b = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/":
            self._send(200, INDEX, "text/html")
        elif u.path == "/api/events":
            term = q.get("term", [TERM])[0]
            if term and not re.fullmatch(r"0x[0-9A-Fa-f]{32}", term):
                return self._send(400, '{"error":"bad term"}', "application/json")
            try:
                term = term or current_term()
                data = get_term(term, refresh="refresh" in q)
            except Exception as e:
                return self._send(502, json.dumps({"error": str(e)}), "application/json")
            self._send(200, json.dumps({"term": term, **data}, ensure_ascii=False), "application/json")
        else:
            self._send(404, "not found", "text/plain")

    def log_message(self, format, *args):
        print("[%s] %s" % (self.log_date_time_string(), format % args))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"),
                    help="0.0.0.0 um im Netzwerk/Container erreichbar zu sein")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8765)))
    ap.add_argument("--term", default=os.environ.get("HOC_TERM"),
                    help="Semester-ID aus der HoC-URL (Standard: aktuelles Semester)")
    a = ap.parse_args()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    global TERM
    TERM = a.term
    print(f"HoC-ECTS-Proxy läuft auf http://{a.host}:{a.port}  (Semester {a.term or 'aktuell'})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
