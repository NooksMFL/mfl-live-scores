import os
import re
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
        "Chrome/154.0.0.0 Safari/537.36 Edg/154.0.0.0"
    ),
}



def secret(name, default=""):
    value = os.getenv(name, "").strip()
    if value:
        return value
    try:
        return str(st.secrets[name]).strip()
    except Exception:
        return default


@st.cache_data(ttl=480, show_spinner=False)
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
    data = r.json()
    access = data.get("access")
    if access is None and isinstance(data.get("data"), dict):
        access = data["data"].get("access")
    if isinstance(access, dict):
        access = access.get("token")
    if not access:
        raise RuntimeError("No access token returned")
    return access


def auth_headers():
    h = dict(HEADERS)
    h["Authorization"] = "Bearer " + refresh_token()
    return h


@st.cache_data(ttl=60, show_spinner=False)
def api_get(path, params=None):
    r = requests.get(
        BASE + path,
        headers=auth_headers(),
        params=params,
        timeout=30,
    )
    if r.status_code == 401:
        refresh_token.clear()
        r = requests.get(
            BASE + path,
            headers=auth_headers(),
            params=params,
            timeout=30,
        )
    r.raise_for_status()
    return r.json()


def as_list(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "items", "results", "clubs", "players", "competitions"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                nested = as_list(value)
                if nested:
                    return nested
    return []


def to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def first(mapping, *keys):
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        if mapping.get(key) not in (None, ""):
            return mapping.get(key)
    return None


def club_id(value):
    direct = to_int(value)
    if direct is not None:
        return direct
    if not isinstance(value, dict):
        return None
    for key in ("clubId", "club_id", "id"):
        direct = to_int(value.get(key))
        if direct is not None:
            return direct
    if isinstance(value.get("club"), dict):
        return club_id(value["club"])
    return None


def match_club_id(match, side):
    for key in (
        f"{side}ClubId",
        f"{side}_club_id",
        f"{side}Club",
        f"{side}_club",
        f"{side}Squad",
    ):
        resolved = club_id(match.get(key))
        if resolved is not None:
            return resolved
    return None


def normalise_share(raw):
    """MFL contract revenueShare is normally basis points: 500 = 5%."""
    value = to_float(raw)
    if value is None:
        return 0.0
    if value > 100:
        return value / 10000.0
    return value / 100.0


_AMOUNT = re.compile(r"([0-9]+(?:[.,][0-9]+)?)")


def reward_amount(value):
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for key in ("amount", "value", "reward", "prize"):
            amount = reward_amount(value.get(key))
            if amount is not None:
                return amount
        return None
    if value is None:
        return None
    text = str(value).replace(",", "")
    match = _AMOUNT.search(text)
    return float(match.group(1)) if match else None


def reward_label(row):
    if not isinstance(row, dict):
        return str(row or "")
    rank = first(row, "ranks", "rank", "position", "label", "name")
    if rank not in (None, ""):
        return str(rank).strip()
    return ""


def reward_value(row):
    if not isinstance(row, dict):
        return reward_amount(row)
    lines = row.get("lines")
    if isinstance(lines, list) and lines:
        for line in lines:
            amount = reward_amount(line)
            if amount is not None:
                return amount
    for key in ("reward", "prize", "value", "amount"):
        amount = reward_amount(row.get(key))
        if amount is not None:
            return amount
    return None


def numeric_rank_range(label):
    if not label:
        return None
    m = re.fullmatch(r"\s*(\d+)\s*(?:[-–—]\s*(\d+))?\s*", str(label))
    if not m:
        return None
    lo = int(m.group(1))
    hi = int(m.group(2) or m.group(1))
    return lo, hi


def competition_stages(detail):
    schedule = detail.get("schedule") if isinstance(detail, dict) else {}
    if not isinstance(schedule, dict):
        return []
    stages = schedule.get("stages")
    return [x for x in stages if isinstance(x, dict)] if isinstance(stages, list) else []


def competition_members(detail):
    ids = set()
    for stage in competition_stages(detail):
        groups = stage.get("groups")
        if isinstance(groups, list):
            for group in groups:
                if not isinstance(group, dict):
                    continue
                for member in group.get("members") or []:
                    resolved = club_id(member)
                    if resolved is not None:
                        ids.add(resolved)
        for owner in ([stage] + [g for g in (groups or []) if isinstance(g, dict)]):
            for round_data in owner.get("rounds") or []:
                if not isinstance(round_data, dict):
                    continue
                for match in round_data.get("matches") or []:
                    if not isinstance(match, dict):
                        continue
                    for side in ("home", "away"):
                        resolved = match_club_id(match, side)
                        if resolved is not None:
                            ids.add(resolved)
    return ids


def find_standing(detail, wanted_club_id):
    for stage in competition_stages(detail):
        for group in stage.get("groups") or []:
            if not isinstance(group, dict):
                continue
            rows = None
            for key in ("standings", "ranking", "rankings", "table"):
                if isinstance(group.get(key), list):
                    rows = group[key]
                    break
            for index, row in enumerate(rows or [], start=1):
                if not isinstance(row, dict):
                    continue
                rid = club_id(row.get("club")) or club_id(row.get("squad")) or club_id(first(row, "clubId", "club_id"))
                if rid == wanted_club_id:
                    return to_int(first(row, "position", "rank", "ranking")) or index
    return None


def league_reward(detail, position):
    if position is None:
        return 0.0
    for row in detail.get("rewards") or []:
        label = reward_label(row)
        ranks = numeric_rank_range(label)
        if ranks and ranks[0] <= position <= ranks[1]:
            return reward_value(row) or 0.0
    return 0.0


def group_stage_wins(detail, wanted_club_id):
    wins = 0
    seen = set()
    for stage in competition_stages(detail):
        stage_name = str(first(stage, "name", "label", "type") or "").lower()
        # If the API names the first cup stage generically, still count group-round wins.
        groups = stage.get("groups") or []
        if "group" not in stage_name and not groups:
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            for round_data in group.get("rounds") or []:
                if not isinstance(round_data, dict):
                    continue
                for match in round_data.get("matches") or []:
                    if not isinstance(match, dict):
                        continue
                    mid = first(match, "matchId", "id")
                    if mid in seen:
                        continue
                    seen.add(mid)
                    status = str(match.get("status") or "").upper()
                    if status not in ("ENDED", "FINISHED", "FT", "FORFEITED"):
                        continue
                    hid = match_club_id(match, "home")
                    aid = match_club_id(match, "away")
                    hs = to_int(match.get("homeScore"))
                    aws = to_int(match.get("awayScore"))
                    if hs is None or aws is None:
                        continue
                    if hid == wanted_club_id and hs > aws:
                        wins += 1
                    elif aid == wanted_club_id and aws > hs:
                        wins += 1
    return wins


def group_win_reward(detail):
    for row in detail.get("rewards") or []:
        label = reward_label(row).lower()
        if "group" in label and "win" in label:
            return reward_value(row) or 0.0
    # Some payloads may put the label inside lines/name.
    for row in detail.get("rewards") or []:
        text = str(row).lower()
        if "group" in text and "win" in text:
            return reward_value(row) or 0.0
    return 0.0


def knockout_reward(detail, wanted_club_id):
    """Best-effort: award the latest named knockout stage reached if its label matches a reward row."""
    reward_rows = []
    for row in detail.get("rewards") or []:
        label = reward_label(row)
        if not numeric_rank_range(label):
            reward_rows.append((label.lower(), reward_value(row) or 0.0))

    best = 0.0
    for stage in competition_stages(detail):
        stage_name = str(first(stage, "name", "label") or "").lower()
        if not stage_name:
            continue
        if wanted_club_id not in competition_members({"schedule": {"stages": [stage]}}):
            continue
        for label, amount in reward_rows:
            if label and (label in stage_name or stage_name in label):
                best = max(best, amount)
    return best


@st.cache_data(ttl=120, show_spinner=False)
def wallet_relationships(wallet):
    rows = as_list(api_get("/clubs", {"walletAddress": wallet, "withStaffContracts": "true"}))
    owned, staff = [], []
    for row in rows:
        if not isinstance(row, dict):
            continue
        club = row.get("club") if isinstance(row.get("club"), dict) else row
        cid = to_int(first(club, "id", "clubId"))
        name = first(club, "name", "clubName")
        if cid is None or not name:
            continue
        title = str(row.get("title") or "").upper()
        item = {
            "id": cid,
            "name": str(name),
            "share": normalise_share(first(row, "revenueShare", "share", "rewardShare")),
            "raw": row,
        }
        if title == "MFL_OWNER":
            owned.append(item)
        else:
            staff.append(item)

    def unique(items):
        out, seen = [], set()
        for item in items:
            key = (item["id"], item["name"].casefold(), item["share"])
            if key not in seen:
                seen.add(key)
                out.append(item)
        return out

    return unique(owned), unique(staff)


@st.cache_data(ttl=120, show_spinner=False)
def wallet_players(wallet):
    players = as_list(api_get("/players", {"ownerWalletAddress": wallet, "limit": 500}))
    result = []
    for player in players:
        if not isinstance(player, dict):
            continue
        owner = player.get("ownedBy") if isinstance(player.get("ownedBy"), dict) else {}
        owner_wallet = str(owner.get("walletAddress") or "").lower()
        if owner_wallet and owner_wallet != wallet.lower():
            continue
        meta = player.get("metadata") if isinstance(player.get("metadata"), dict) else {}
        contract = player.get("activeContract") if isinstance(player.get("activeContract"), dict) else {}
        club = contract.get("club") if isinstance(contract.get("club"), dict) else {}
        result.append({
            "id": first(player, "id", "playerId"),
            "name": (str(meta.get("firstName") or "") + " " + str(meta.get("lastName") or "")).strip(),
            "club_id": to_int(first(club, "id", "clubId")),
            "club_name": str(first(club, "name", "clubName") or ""),
            "share": normalise_share(contract.get("revenueShare")),
        })
    return result


@st.cache_data(ttl=120, show_spinner=False)
def club_detail(cid):
    payload = api_get(f"/clubs/{cid}")
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        return payload["data"]
    return payload if isinstance(payload, dict) else {}


def current_competition_ids(club):
    found = []
    for key in ("currentCompetitionIds", "competitionIds", "currentCompetitions"):
        value = club.get(key) if isinstance(club, dict) else None
        if isinstance(value, list):
            for item in value:
                cid = club_id(item)
                if cid is None and isinstance(item, dict):
                    cid = to_int(first(item, "competitionId", "id"))
                if cid is not None:
                    found.append(cid)
    # Some club payloads expose competition IDs under metadata/current season blocks.
    def walk(obj, path=""):
        if isinstance(obj, dict):
            for key, value in obj.items():
                p = f"{path}.{key}" if path else key
                lk = key.lower()
                if "competition" in lk and lk.endswith("ids") and isinstance(value, list):
                    for item in value:
                        cid = to_int(item) or (to_int(first(item, "id", "competitionId")) if isinstance(item, dict) else None)
                        if cid is not None:
                            found.append(cid)
                elif isinstance(value, (dict, list)):
                    walk(value, p)
        elif isinstance(obj, list):
            for value in obj:
                if isinstance(value, (dict, list)):
                    walk(value, path)
    walk(club)
    return sorted(set(found))


@st.cache_data(ttl=90, show_spinner=False)
def competition_detail(cid):
    payload = api_get(f"/competitions/{cid}")
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        return payload["data"]
    return payload if isinstance(payload, dict) else {}


def project_club(club):
    detail = club_detail(club["id"])
    comp_ids = current_competition_ids(detail)
    rows = []
    gross = 0.0

    for competition_id in comp_ids:
        comp = competition_detail(competition_id)
        if club["id"] not in competition_members(comp):
            # Some league payloads only reveal the club through standings.
            if find_standing(comp, club["id"]) is None:
                continue

        ctype = str(comp.get("type") or "").upper()
        cname = str(comp.get("name") or f"Competition {competition_id}")
        amount = 0.0
        note = ""

        if ctype == "LEAGUE":
            position = find_standing(comp, club["id"])
            amount = league_reward(comp, position)
            note = f"Rank {position}" if position else "Rank unavailable"
        elif ctype == "CUP":
            wins = group_stage_wins(comp, club["id"])
            win_value = group_win_reward(comp)
            stage_value = knockout_reward(comp, club["id"])
            amount = wins * win_value + stage_value
            note = f"Group wins {wins}"
            if stage_value:
                note += " + knockout"
        else:
            continue

        gross += amount
        rows.append({
            "competition": cname,
            "type": ctype,
            "note": note,
            "projected": amount,
        })

    return gross, rows, detail


st.markdown("""
<style>
.stApp{background:#071116;color:#eef7f5}
.block-container{max-width:1150px;padding-top:2rem}
.hero{font-size:2.15rem;font-weight:900}
.sub{color:#789198;margin-top:-6px;margin-bottom:18px}
.metricbox{background:#0a1b22;border:1px solid #173943;border-radius:14px;padding:16px}
.small{color:#789198;font-size:.82rem}
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="hero">MFL Rewards Calculator</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="sub">Wallet-based projected season rewards · owned clubs · staff shares · players on loan</div>',
    unsafe_allow_html=True,
)

default_wallet = secret("MFL_WALLET_ADDRESS")
wallet = st.text_input(
    "Flow / Dapper wallet address",
    value=default_wallet,
    placeholder="0x...",
).strip().lower()

if st.button("Calculate", type="primary", use_container_width=False):
    st.cache_data.clear()

if not wallet:
    st.info("Paste a Flow wallet address to calculate projected rewards.")
    st.stop()

try:
    with st.spinner("Loading wallet, clubs, players and live competition data…"):
        owned, staff = wallet_relationships(wallet)
        players = wallet_players(wallet)

        owned_ids = {c["id"] for c in owned}
        club_rows = []
        owned_gross = 0.0
        owned_net_known = 0.0

        for club in owned:
            gross, competitions, raw_detail = project_club(club)
            owned_gross += gross
            # Known owner-side deductions available directly in this first build are
            # the wallet's own players/staff relationships. External player/coach
            # deductions are surfaced as "pending source" rather than guessed.
            club_rows.append({
                "club": club["name"],
                "gross": gross,
                "competitions": competitions,
                "raw_detail": raw_detail,
            })

        staff_total = 0.0
        staff_rows = []
        for relation in staff:
            gross, competitions, _ = project_club(relation)
            cut = gross * relation["share"]
            staff_total += cut
            staff_rows.append({
                "club": relation["name"],
                "share": relation["share"],
                "gross": gross,
                "cut": cut,
                "competitions": competitions,
            })

        loan_total = 0.0
        loan_rows = []
        gross_cache = {}
        for player in players:
            cid = player["club_id"]
            if cid is None or cid in owned_ids or player["share"] <= 0:
                continue
            if cid not in gross_cache:
                synthetic = {"id": cid, "name": player["club_name"] or f"Club {cid}", "share": 0.0}
                gross_cache[cid] = project_club(synthetic)[0]
            club_gross = gross_cache[cid]
            cut = club_gross * player["share"]
            loan_total += cut
            loan_rows.append({
                "player": player["name"] or f"Player {player['id']}",
                "club": player["club_name"] or f"Club {cid}",
                "share": player["share"],
                "club_gross": club_gross,
                "cut": cut,
            })

    projected_known = owned_gross + staff_total + loan_total

    a, b, c, d = st.columns(4)
    a.metric("Known projected earnings", f"{projected_known:,.2f} $MFL")
    b.metric("Owned club gross", f"{owned_gross:,.2f} $MFL")
    c.metric("Staff earnings", f"{staff_total:,.2f} $MFL")
    d.metric("Owned players out", f"{loan_total:,.2f} $MFL")

    st.caption(
        f"{len(owned)} owned clubs · {len(staff)} staff club relationships · "
        f"{len(loan_rows)} contracted owned players outside your clubs · "
        f"updated {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}"
    )

    st.warning(
        "This first build calculates live competition gross rewards plus your incoming "
        "staff/player revenue shares. Exact owned-club NET still needs one final source: "
        "all external player and coach revenue-share contracts attached to each owned club. "
        "The calculator deliberately does not guess those deductions."
    )

    st.subheader("Owned clubs")
    if not club_rows:
        st.info("No owned clubs found for this wallet.")
    for row in sorted(club_rows, key=lambda x: x["gross"], reverse=True):
        with st.expander(f"{row['club']} · {row['gross']:,.2f} $MFL", expanded=False):
            if row["competitions"]:
                st.dataframe(row["competitions"], use_container_width=True, hide_index=True)
            else:
                st.caption("No current reward-bearing competitions resolved for this club.")
                with st.expander("Club API diagnostic"):
                    st.json(row["raw_detail"])

    st.subheader("Staff earnings")
    if staff_rows:
        display = [{
            "Club": r["club"],
            "Share": f"{r['share']*100:.2f}%",
            "Club gross": round(r["gross"], 2),
            "Your cut": round(r["cut"], 2),
        } for r in sorted(staff_rows, key=lambda x: x["cut"], reverse=True)]
        st.dataframe(display, use_container_width=True, hide_index=True)
    else:
        st.caption("No staff revenue-share relationships found.")

    st.subheader("Players on loan / contracted elsewhere")
    if loan_rows:
        display = [{
            "Player": r["player"],
            "Club": r["club"],
            "Share": f"{r['share']*100:.2f}%",
            "Club gross": round(r["club_gross"], 2),
            "Projected cut": round(r["cut"], 2),
        } for r in sorted(loan_rows, key=lambda x: x["cut"], reverse=True)]
        st.dataframe(display, use_container_width=True, hide_index=True)
    else:
        st.caption("No revenue-share player contracts outside your owned clubs were resolved.")

except Exception as exc:
    st.error(f"MFL rewards error: {exc}")
    st.caption(
        "If this is the first run, the app may have discovered an MFL payload shape "
        "we have not mapped yet. The calculator is intentionally fail-closed rather "
        "than inventing a reward value."
    )
