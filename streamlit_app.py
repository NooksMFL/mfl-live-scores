import os
import time
from datetime import datetime, timezone

import requests
import streamlit as st

BASE = "https://api.playmfl.com"

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
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/152.0.0.0 Safari/537.36 Edg/152.0.0.0"
    ),
}

st.set_page_config(page_title="MFL Live Scores", page_icon="⚽", layout="wide")


def refresh_token():
    rt = os.getenv("MFL_REFRESH_TOKEN", "").strip()
    if not rt:
        try:
            rt = str(st.secrets["MFL_REFRESH_TOKEN"]).strip()
        except Exception:
            pass
    if not rt:
        raise RuntimeError("MFL_REFRESH_TOKEN missing")

    r = requests.post(
        BASE + "/auth/refresh",
        headers=HEADERS,
        json={"refreshToken": rt},
        timeout=20,
    )
    r.raise_for_status()
    d = r.json()
    access = d.get("access")
    if not access and isinstance(d.get("data"), dict):
        access = d["data"].get("access")
    if isinstance(access, dict):
        access = access.get("token")
    if not access:
        raise RuntimeError("No access token returned")
    return access


def auth_headers(token):
    h = dict(HEADERS)
    h["Authorization"] = "Bearer " + token
    return h


def arr(d):
    if isinstance(d, list):
        return d
    if isinstance(d, dict):
        for k in ("data", "items", "results", "matches", "feed"):
            x = d.get(k)
            if isinstance(x, list):
                return x
            if isinstance(x, dict):
                y = arr(x)
                if y:
                    return y
    return []


def nested(obj, *paths):
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


def team(match, side):
    name = nested(
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
    return f"{side.title()} team"


def score(match, side):
    value = nested(
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


def normalise(match):
    return {
        "id": nested(match, "id", "matchId"),
        "type": nested(match, "type", "competition.name", "competitionType"),
        "status": str(nested(match, "status", "matchStatus", "state") or "UNKNOWN"),
        "minute": nested(match, "minute", "matchMinute", "clock.minute", "gameTime"),
        "start": nested(match, "startDate", "startTime", "scheduledAt", "date"),
        "home": team(match, "home"),
        "away": team(match, "away"),
        "home_score": score(match, "home"),
        "away_score": score(match, "away"),
    }


@st.cache_data(ttl=10, show_spinner=False)
def get_scores():
    token = refresh_token()
    r = requests.get(
        BASE + "/matches/feed",
        headers=auth_headers(token),
        params={"limit": 250},
        timeout=30,
    )
    r.raise_for_status()
    raw = arr(r.json())
    rows = [normalise(x) for x in raw if isinstance(x, dict)]
    out = []
    for row in rows:
        names = f'{row["home"]} {row["away"]}'.lower()
        if any(club.lower() in names for club in TRACKED_CLUBS):
            out.append(row)

    debug = []
    for x in raw[:12]:
        if not isinstance(x, dict):
            continue
        debug.append({
            "id": x.get("id") or x.get("matchId"),
            "status": x.get("status") or x.get("matchStatus") or x.get("state"),
            "type": x.get("type") or x.get("competitionType"),
            "homeTeamName": x.get("homeTeamName"),
            "awayTeamName": x.get("awayTeamName"),
            "homeSquad": x.get("homeSquad"),
            "awaySquad": x.get("awaySquad"),
            "homeClub": x.get("homeClub"),
            "awayClub": x.get("awayClub"),
            "keys": ", ".join(sorted(x.keys())),
        })
    return out, len(raw), rows[:20], debug


st.markdown("""
<style>
.stApp {background:#071116;color:#eef7f5}
.block-container{max-width:1000px;padding-top:2rem}
.title{font-size:2.2rem;font-weight:900}
.sub{color:#789198;margin-top:-8px;margin-bottom:20px}
.card{background:#0a1b22;border:1px solid #173943;border-radius:14px;padding:18px;margin-bottom:12px}
.row{display:flex;justify-content:space-between;font-size:1.15rem;font-weight:800}
.meta{color:#789198;font-size:.8rem;margin-top:10px}
.live{display:inline-block;background:#0c2c28;color:#20dfb5;padding:5px 9px;border-radius:999px;font-size:.72rem;font-weight:800}
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="title">MFL Live Scores</div>', unsafe_allow_html=True)
st.markdown('<div class="sub">Nooks network match feed · refreshes every 10 seconds</div>', unsafe_allow_html=True)

try:
    matches, raw_count, sample_rows, debug_rows = get_scores()
    st.markdown(f'<span class="live">UPDATED {datetime.now(timezone.utc).strftime("%H:%M:%S UTC")}</span>', unsafe_allow_html=True)

    if not matches:
        st.info(f"No tracked-club matches were recognised yet. MFL returned {raw_count} match record(s).")
        if sample_rows:
            st.caption("Normalised sample from MFL")
            st.dataframe(sample_rows, use_container_width=True, hide_index=True)
        if debug_rows:
            with st.expander("MFL feed diagnostic"):
                st.caption("Safe match-field diagnostic — no authentication token is shown.")
                st.json(debug_rows)
    else:
        for m in matches:
            hs = "–" if m["home_score"] is None else m["home_score"]
            aws = "–" if m["away_score"] is None else m["away_score"]
            minute = f' · {m["minute"]}' if m["minute"] else ""
            comp = f' · {m["type"]}' if m["type"] else ""
            st.markdown(
                f'''<div class="card">
                <div class="row"><span>{m["home"]}</span><span>{hs}</span></div>
                <div class="row"><span>{m["away"]}</span><span>{aws}</span></div>
                <div class="meta">{m["status"]}{minute}{comp}</div>
                </div>''',
                unsafe_allow_html=True,
            )
except Exception as e:
    st.error(f"MFL error: {e}")

time.sleep(10)
st.rerun()
