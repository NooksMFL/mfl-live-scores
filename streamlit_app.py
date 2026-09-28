import os
import time
from datetime import datetime, timezone

import requests
import streamlit as st

BASE = "https://api.playmfl.com"

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


def secret(name, default=""):
    value = os.getenv(name, "").strip()
    if value:
        return value
    try:
        return str(st.secrets[name]).strip()
    except Exception:
        return default


def refresh_token():
    rt = secret("MFL_REFRESH_TOKEN")
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
    if access is None and isinstance(d.get("data"), dict):
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


def api_get(path, token, params=None, timeout=30):
    r = requests.get(
        BASE + path,
        headers=auth_headers(token),
        params=params,
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


def arr(d):
    if isinstance(d, list):
        return d
    if isinstance(d, dict):
        for k in ("data", "items", "results", "players", "clubs", "matches", "feed"):
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


def club_id_from_match(match, side):
    candidates = [
        nested(match, f"{side}ClubId"),
        nested(match, f"{side}Squad.clubId"),
        nested(match, f"{side}Squad.club.id"),
        nested(match, f"{side}Club.id"),
        nested(match, f"{side}Team.clubId"),
        nested(match, f"{side}Team.club.id"),
    ]
    for value in candidates:
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return str(value)
    return None


@st.cache_data(ttl=300, show_spinner=False)
def owned_clubs(wallet):
    wallet = wallet.strip().lower()
    token = refresh_token()
    found = []

    for params in (
        {"walletAddress": wallet},
        {"walletAddress": wallet, "withStaffContracts": "true"},
    ):
        raw = arr(api_get("/clubs", token, params=params, timeout=20))
        for x in raw:
            if not isinstance(x, dict):
                continue
            if str(x.get("title") or "").strip().upper() != "MFL_OWNER":
                continue
            club = x.get("club") if isinstance(x.get("club"), dict) else x
            cid = club.get("id") or club.get("clubId")
            name = club.get("name") or club.get("clubName")
            if cid is not None and name:
                found.append({"id": int(cid), "name": str(name).strip()})
        if found:
            break

    uniq = []
    seen = set()
    for c in found:
        key = (c["id"], c["name"].casefold())
        if key not in seen:
            seen.add(key)
            uniq.append(c)
    return sorted(uniq, key=lambda x: x["name"].casefold())


@st.cache_data(ttl=10, show_spinner=False)
def get_scores(wallet):
    token = refresh_token()
    clubs = owned_clubs(wallet)
    club_ids = {int(c["id"]) for c in clubs}
    club_names = {c["name"].casefold() for c in clubs}

    collected = {}
    request_debug = []

    # First ask MFL specifically for each owned club. This is much more reliable
    # than assuming the current global match feed happens to contain our fixtures.
    for club in clubs:
        params = {"clubId": club["id"], "limit": 50}
        try:
            raw = arr(api_get("/matches/feed", token, params=params, timeout=20))
            request_debug.append({
                "club": club["name"],
                "club_id": club["id"],
                "returned": len(raw),
            })
            for m in raw:
                if not isinstance(m, dict):
                    continue
                mid = m.get("id") or m.get("matchId") or repr(m)[:120]
                collected[str(mid)] = m
        except Exception as exc:
            request_debug.append({
                "club": club["name"],
                "club_id": club["id"],
                "error": str(exc),
            })

    # Fallback: also inspect the current global feed and keep any record whose
    # embedded club ID or team name matches one of our owned clubs.
    try:
        global_raw = arr(api_get("/matches/feed", token, params={"limit": 250}, timeout=25))
    except Exception:
        global_raw = []

    for m in global_raw:
        if not isinstance(m, dict):
            continue
        row = normalise(m)
        home_id = club_id_from_match(m, "home")
        away_id = club_id_from_match(m, "away")
        name_hit = (
            row["home"].casefold() in club_names
            or row["away"].casefold() in club_names
        )
        id_hit = home_id in club_ids or away_id in club_ids
        if name_hit or id_hit:
            mid = m.get("id") or m.get("matchId") or repr(m)[:120]
            collected[str(mid)] = m

    rows = [normalise(x) for x in collected.values()]

    # Keep newest/current-looking records near the top.
    rows.sort(key=lambda x: str(x.get("start") or ""), reverse=True)
    return clubs, rows, request_debug


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
.clubpill{display:inline-block;background:#0b2028;border:1px solid #173943;border-radius:999px;padding:5px 9px;margin:3px;color:#a8b8bc;font-size:.7rem}
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="title">MFL Live Scores</div>', unsafe_allow_html=True)
st.markdown('<div class="sub">Nooks network · owned-club match feed · refreshes every 10 seconds</div>', unsafe_allow_html=True)

wallet = secret("MFL_WALLET_ADDRESS")

if not wallet:
    wallet = st.text_input(
        "MFL owner wallet address",
        placeholder="Paste the same wallet address you use in the Management Hub",
        help="For permanent hands-free use, add this later as the Streamlit secret MFL_WALLET_ADDRESS.",
    ).strip()

if not wallet:
    st.info("Enter your MFL owner wallet address above so I can identify your clubs.")
    st.stop()

try:
    clubs, matches, request_debug = get_scores(wallet)

    st.markdown(
        f'<span class="live">UPDATED {datetime.now(timezone.utc).strftime("%H:%M:%S UTC")}</span>',
        unsafe_allow_html=True,
    )

    if not clubs:
        st.warning("MFL authenticated, but no MFL_OWNER clubs were found for that wallet.")
    else:
        st.caption(f"Tracking {len(clubs)} owned clubs")
        st.markdown(
            "".join(f'<span class="clubpill">{c["name"]}</span>' for c in clubs),
            unsafe_allow_html=True,
        )

        if not matches:
            st.info("Your clubs were found, but MFL did not return any matches for them yet.")
        else:
            for m in matches:
                hs = "–" if m["home_score"] is None else m["home_score"]
                aws = "–" if m["away_score"] is None else m["away_score"]
                minute = f' · {m["minute"]}' if m["minute"] else ""
                comp = f' · {m["type"]}' if m["type"] else ""
                start = f' · {m["start"]}' if m["start"] else ""
                st.markdown(
                    f'''<div class="card">
                    <div class="row"><span>{m["home"]}</span><span>{hs}</span></div>
                    <div class="row"><span>{m["away"]}</span><span>{aws}</span></div>
                    <div class="meta">{m["status"]}{minute}{comp}{start}</div>
                    </div>''',
                    unsafe_allow_html=True,
                )

        with st.expander("Club feed diagnostic"):
            st.caption("Shows only club IDs/names and match counts. No token is displayed.")
            st.json(request_debug)

except Exception as e:
    st.error(f"MFL error: {e}")

time.sleep(10)
st.rerun()
