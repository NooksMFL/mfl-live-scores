import os
import time
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, render_template_string

BASE = "https://api.playmfl.com"
REFRESH_TOKEN = os.getenv("MFL_REFRESH_TOKEN", "").strip()

DEFAULT_CLUBS = [
    "Kano", "Riddarholmen",
    "Almeria", "Oran", "Svenborg",
    "Gorzow", "Pickering",
    "Velez", "Supermarine", "Antibes", "Gladbach", "Swindon",
    "David", "Angrense", "Garza", "Goyang", "Halesowen",
]

TRACKED_CLUBS = [
    x.strip() for x in os.getenv("TRACKED_CLUBS", ",".join(DEFAULT_CLUBS)).split(",")
    if x.strip()
]

HEADERS = {
    "Accept": "*/*",
    "Origin": "https://app.playmfl.com",
    "Referer": "https://app.playmfl.com/",
    "User-Agent": "MFL-Live-Scores/1.0",
}

app = Flask(__name__)

_token_cache = {"token": None, "at": 0.0}
_scores_cache = {"rows": None, "at": 0.0}


def _access_token():
    if _token_cache["token"] and time.time() - _token_cache["at"] < 8 * 60:
        return _token_cache["token"]
    if not REFRESH_TOKEN:
        raise RuntimeError("MFL_REFRESH_TOKEN is not configured")

    last = None
    for attempt in range(4):
        try:
            r = requests.post(
                BASE + "/auth/refresh",
                headers=HEADERS,
                json={"refreshToken": REFRESH_TOKEN},
                timeout=20,
            )
            if r.status_code in (429, 500, 502, 503, 504):
                last = f"HTTP {r.status_code}: {r.text[:200]}"
                time.sleep(min(8, 1.5 * (2 ** attempt)))
                continue
            r.raise_for_status()
            data = r.json()
            access = data.get("access")
            if not access and isinstance(data.get("data"), dict):
                access = data["data"].get("access")
            if isinstance(access, dict):
                access = access.get("token")
            if not access:
                raise RuntimeError("MFL returned no access token")
            _token_cache.update(token=access, at=time.time())
            return access
        except (requests.Timeout, requests.ConnectionError) as exc:
            last = str(exc)
            time.sleep(min(8, 1.5 * (2 ** attempt)))
    raise RuntimeError(f"MFL authentication failed: {last}")


def _auth_headers():
    h = dict(HEADERS)
    h["Authorization"] = "Bearer " + _access_token()
    return h


def _array(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "items", "results", "matches", "feed"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                found = _array(value)
                if found:
                    return found
    return []


def _nested(obj, *paths):
    for path in paths:
        cur = obj
        ok = True
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                ok = False
                break
        if ok and cur not in (None, ""):
            return cur
    return None


def _team(match, side):
    cap = side.capitalize()
    name = _nested(
        match,
        f"{side}TeamName",
        f"{side}ClubName",
        f"{side}Squad.name",
        f"{side}Club.name",
        f"{side}Team.name",
        f"{side}.name",
    )
    if name:
        return str(name)
    obj = match.get(f"{side}Squad") or match.get(f"{side}Club") or match.get(f"{side}Team")
    if isinstance(obj, dict):
        return str(obj.get("name") or obj.get("clubName") or f"{cap} team")
    return f"{cap} team"


def _score(match, side):
    value = _nested(
        match,
        f"{side}Score",
        f"score.{side}",
        f"result.{side}",
        f"{side}.score",
    )
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _normalise(match):
    status = str(_nested(match, "status", "matchStatus", "state") or "UNKNOWN")
    minute = _nested(match, "minute", "matchMinute", "clock.minute", "gameTime")
    start = _nested(match, "startDate", "startTime", "scheduledAt", "date")
    return {
        "id": _nested(match, "id", "matchId"),
        "type": _nested(match, "type", "competition.name", "competitionType"),
        "status": status,
        "minute": minute,
        "start": start,
        "home": _team(match, "home"),
        "away": _team(match, "away"),
        "home_score": _score(match, "home"),
        "away_score": _score(match, "away"),
    }


def _is_tracked(row):
    names = f'{row["home"]} {row["away"]}'.lower()
    return any(club.lower() in names for club in TRACKED_CLUBS)


def fetch_scores(force=False):
    if not force and _scores_cache["rows"] is not None and time.time() - _scores_cache["at"] < 8:
        return _scores_cache["rows"]

    params_to_try = [
        {"limit": 250},
        {"status": "LIVE", "limit": 250},
    ]
    last_error = None
    matches = []
    for params in params_to_try:
        try:
            r = requests.get(
                BASE + "/matches/feed",
                headers=_auth_headers(),
                params=params,
                timeout=25,
            )
            if r.status_code == 401:
                _token_cache.update(token=None, at=0.0)
                r = requests.get(
                    BASE + "/matches/feed",
                    headers=_auth_headers(),
                    params=params,
                    timeout=25,
                )
            r.raise_for_status()
            matches = _array(r.json())
            if matches:
                break
        except Exception as exc:
            last_error = exc

    if not matches and last_error:
        raise last_error

    rows = [_normalise(m) for m in matches if isinstance(m, dict)]
    tracked = [r for r in rows if _is_tracked(r)]
    _scores_cache.update(rows=tracked, at=time.time())
    return tracked


def _club_rows(club):
    club_l = club.strip().lower()
    return [
        row for row in fetch_scores(force=True)
        if club_l in row["home"].lower() or club_l in row["away"].lower()
    ]


@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "mfl-live-scores"})


@app.get("/api/scores")
def api_scores():
    try:
        rows = fetch_scores(force=True)
        return jsonify({
            "ok": True,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "tracked_clubs": TRACKED_CLUBS,
            "matches": rows,
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503


@app.get("/api/score/<club>")
def api_score(club):
    try:
        rows = _club_rows(club)
        return jsonify({
            "ok": True,
            "club": club,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "matches": rows,
        })
    except Exception as exc:
        return jsonify({"ok": False, "club": club, "error": str(exc)}), 503


PAGE = r"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>MFL Live Scores</title>
  <style>
    body{font-family:Arial,sans-serif;background:#071116;color:#eef7f5;margin:0;padding:28px}
    .wrap{max-width:900px;margin:auto}.top{display:flex;justify-content:space-between;align-items:end;gap:12px}
    h1{margin:0;font-size:36px}.sub{color:#789198;margin-top:7px}.pill{background:#0c2c28;color:#20dfb5;padding:8px 11px;border-radius:999px;font-weight:700;font-size:12px}
    .grid{display:grid;gap:12px;margin-top:24px}.card{background:#0a1b22;border:1px solid #173943;border-radius:14px;padding:18px}
    .teams{display:grid;grid-template-columns:1fr auto;gap:10px;font-size:20px;font-weight:700}.score{font-size:22px}
    .meta{margin-top:11px;color:#789198;font-size:13px}.empty{margin-top:24px;padding:22px;border:1px dashed #29454d;border-radius:14px;color:#81989d}
    a{color:#20dfb5}.error{color:#ffb4b4}
  </style>
</head>
<body><div class="wrap">
  <div class="top"><div><h1>MFL Live Scores</h1><div class="sub">Nooks network match feed</div></div><div class="pill">AUTO REFRESH · 15s</div></div>
  <div id="content" class="grid"><div class="empty">Loading MFL scores…</div></div>
  <div class="sub" style="margin-top:20px">API: <a href="/api/scores">/api/scores</a></div>
</div>
<script>
async function load(){
  const root=document.getElementById("content");
  try{
    const r=await fetch("/api/scores",{cache:"no-store"}); const d=await r.json();
    if(!d.ok) throw new Error(d.error||"Unknown error");
    if(!d.matches.length){root.innerHTML='<div class="empty">No tracked-club matches were returned by the MFL feed.</div>';return;}
    root.innerHTML=d.matches.map(m=>`<div class="card">
      <div class="teams"><span>${m.home}</span><span class="score">${m.home_score ?? "–"}</span>
      <span>${m.away}</span><span class="score">${m.away_score ?? "–"}</span></div>
      <div class="meta">${m.status}${m.minute ? " · "+m.minute : ""}${m.type ? " · "+m.type : ""}</div>
    </div>`).join("");
  }catch(e){root.innerHTML='<div class="empty error">'+e.message+'</div>'}
}
load(); setInterval(load,15000);
</script></body></html>"""


@app.get("/")
def index():
    return render_template_string(PAGE)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
