import os
import requests
import streamlit as st

BASE = "https://api.playmfl.com"
HEADERS = {
    "Accept": "*/*",
    "Origin": "https://app.playmfl.com",
    "Referer": "https://app.playmfl.com/",
    "User-Agent": "Mozilla/5.0",
}

st.set_page_config(page_title="MFL Contract Probe", page_icon="🔎", layout="wide")

def secret(name, default=""):
    value = os.getenv(name, "").strip()
    if value:
        return value
    try:
        return str(st.secrets[name]).strip()
    except Exception:
        return default

def access_token():
    rt = secret("MFL_REFRESH_TOKEN")
    if not rt:
        raise RuntimeError("MFL_REFRESH_TOKEN missing")
    r = requests.post(BASE + "/auth/refresh", headers=HEADERS, json={"refreshToken": rt}, timeout=20)
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

def get(path, params=None):
    h = dict(HEADERS)
    h["Authorization"] = "Bearer " + access_token()
    r = requests.get(BASE + path, headers=h, params=params, timeout=25)
    return r.status_code, (r.json() if "json" in r.headers.get("content-type","") else r.text[:1000])

def as_list(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data","items","results","clubs"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                nested = as_list(value)
                if nested:
                    return nested
    return []

def first(d,*keys):
    if not isinstance(d,dict):
        return None
    for k in keys:
        if d.get(k) not in (None,""):
            return d.get(k)
    return None

def find_revshares(obj, path=""):
    hits=[]
    if isinstance(obj,dict):
        for k,v in obj.items():
            p=f"{path}.{k}" if path else k
            lk=k.lower()
            if any(word in lk for word in ("revenue","share","contract","coach","staff","player","loan")):
                if not isinstance(v,(dict,list)):
                    hits.append((p,v))
            hits.extend(find_revshares(v,p))
    elif isinstance(obj,list):
        for i,v in enumerate(obj):
            hits.extend(find_revshares(v,f"{path}[{i}]"))
    return hits

st.title("MFL Contract Source Probe")
st.caption("Diagnostic only: finds the endpoints needed for exact rewards deductions.")

wallet=st.text_input("Flow / Dapper wallet", value=secret("MFL_WALLET_ADDRESS"), placeholder="0x...").strip().lower()
if not wallet:
    st.stop()

status,payload=get("/clubs", {"walletAddress":wallet, "withStaffContracts":"true"})
if status != 200:
    st.error(f"/clubs returned HTTP {status}")
    st.json(payload)
    st.stop()

owned=[]
for row in as_list(payload):
    if not isinstance(row,dict) or str(row.get("title") or "").upper()!="MFL_OWNER":
        continue
    club=row.get("club") if isinstance(row.get("club"),dict) else row
    cid=first(club,"id","clubId")
    name=first(club,"name","clubName")
    if cid is not None:
        owned.append((int(cid),str(name or cid)))

st.success(f"Found {len(owned)} owned clubs")

candidates = [
    "/clubs/{id}",
    "/clubs/{id}/players",
    "/clubs/{id}/coaches",
    "/clubs/{id}/staff",
    "/clubs/{id}/contracts",
    "/clubs/{id}/squads",
]

for cid,name in owned:
    with st.expander(f"{name} · {cid}", expanded=False):
        for template in candidates:
            path=template.format(id=cid)
            try:
                code,data=get(path)
                hits=find_revshares(data) if code==200 else []
                st.markdown(f"**{path}** — HTTP {code} — {len(hits)} contract/share-like fields")
                if hits:
                    st.dataframe([{"path":p,"value":str(v)} for p,v in hits], use_container_width=True, hide_index=True)
                elif code==200:
                    with st.expander("Raw payload"):
                        st.json(data)
            except Exception as exc:
                st.write(path, "→", exc)
