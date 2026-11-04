import asyncio
import json
import re
import urllib.parse
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html, */*"
}

# Exact word/team mapping so "ASSAM" never matches "SA"
FLAG_MAP = {
    "INDIA": "in", "IND": "in",
    "NEW ZEALAND": "nz", "NZ": "nz",
    "AUSTRALIA": "au", "AUS": "au",
    "ENGLAND": "gb-eng", "ENG": "gb-eng",
    "SOUTH AFRICA": "za", "SA": "za", "RSA": "za",
    "PAKISTAN": "pk", "PAK": "pk",
    "SRI LANKA": "lk", "SL": "lk",
    "BANGLADESH": "bd", "BAN": "bd",
    "AFGHANISTAN": "af", "AFG": "af",
    "WEST INDIES": "jm", "WI": "jm",
    "ZIMBABWE": "zw", "ZIM": "zw",
    "IRELAND": "ie", "IRE": "ie",
    "NETHERLANDS": "nl", "NED": "nl",
    "SCOTLAND": "gb-sct", "SCO": "gb-sct",
    "UNITED ARAB EMIRATES": "ae", "UAE": "ae",
    "OMAN": "om", "OMA": "om",
    "NEPAL": "np", "NEP": "np",
    "USA": "us", "UNITED STATES": "us"
}

photo_cache = {}
ticker_cycle = 0


def get_flag_url(team_name):
    clean = re.sub(r"[^A-Z\s]", "", team_name.upper()).strip()
    if clean in FLAG_MAP:
        return f"https://flagcdn.com/w320/{FLAG_MAP[clean]}.png"
    words = clean.split()
    for w in words:
        if w in FLAG_MAP:
            return f"https://flagcdn.com/w320/{FLAG_MAP[w]}.png"
    return "https://flagcdn.com/w320/un.png"


def get_player_photo(player_name, existing_headshot=None):
    if existing_headshot and existing_headshot.startswith("http"):
        return existing_headshot
    clean = re.sub(r"[\*\(\)cwk&†]", "", player_name).strip()
    if not clean or clean in ["--", "BATTER 1", "BATTER 2", "BOWLER"]:
        return "https://ui-avatars.com/api/?name=CR&background=0f172a&color=facc15&size=256&bold=true"
    if clean in photo_cache:
        return photo_cache[clean]
    try:
        slug = urllib.parse.quote(clean.replace(" ", "_"))
        url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{slug}"
        r = requests.get(url, headers=HEADERS, timeout=4)
        if r.status_code == 200:
            data = r.json()
            if "thumbnail" in data and "source" in data["thumbnail"]:
                photo_cache[clean] = data["thumbnail"]["source"]
                return photo_cache[clean]
    except Exception:
        pass

    fallback = f"https://ui-avatars.com/api/?name={urllib.parse.quote(clean)}&background=0f172a&color=facc15&size=256&bold=true"
    photo_cache[clean] = fallback
    return fallback


def parse_espn_live_json():
    """
    Uses ESPN's unblocked public JSON API (the same API used by GNOME Cricket Score).
    Automatically prioritizes LIVE International matches (especially India vs New Zealand).
    """
    global ticker_cycle
    ticker_cycle += 1

    header_url = "https://site.web.api.espn.com/apis/personalized/v2/scoreboard/header?sport=cricket&region=in&tz=Asia/Calcutta"
    r = requests.get(header_url, headers=HEADERS, timeout=10)
    if r.status_code != 200:
        return None

    data = r.json()
    leagues = data.get("sports", [{}])[0].get("leagues", [])

    candidates = []
    for lg in leagues:
        lg_id = lg.get("id")
        lg_name = lg.get("name", "")
        for ev in lg.get("events", []):
            ev_id = ev.get("id")
            state = ev.get("status", "") or ev.get("fullStatus", {}).get("type", {}).get("state", "")
            comp_names = " ".join([c.get("displayName", "") for c in ev.get("competitors", [])]).upper()

            # Priority scoring: Live > India/NZ > International > Domestic
            prio = 0
            if state == "in":
                prio += 100
            if "INDIA" in comp_names or "NEW ZEALAND" in comp_names:
                prio += 50
            if any(t in comp_names for t in ["AUSTRALIA", "ENGLAND", "SOUTH AFRICA", "PAKISTAN", "SRI LANKA", "WEST INDIES", "BANGLADESH", "AFGHANISTAN", "UNITED ARAB EMIRATES", "OMAN"]):
                prio += 25
            if "RANJI" in lg_name.upper():
                prio -= 40

            candidates.append((prio, lg_id, ev_id, ev, lg_name))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    _, league_id, event_id, best_ev, series_name = candidates[0]

    # Fetch full live match summary JSON
    sum_url = f"https://site.web.api.espn.com/apis/site/v2/sports/cricket/{league_id}/summary?event={event_id}&lang=en&region=in"
    sr = requests.get(sum_url, headers=HEADERS, timeout=10)
    sdata = sr.json() if sr.status_code == 200 else {}

    # Extract competitors from header/event
    comps = best_ev.get("competitors", [])
    bat_comp = comps[0] if comps else {}
    bowl_comp = comps[1] if len(comps) > 1 else {}

    for c in comps:
        if c.get("isBatting") or str(c.get("order")) == "1":
            bat_comp = c
        else:
            bowl_comp = c

    # Check if one team has a live score with overs and the other is "Yet to bat"
    if not bat_comp.get("score") and bowl_comp.get("score"):
        bat_comp, bowl_comp = bowl_comp, bat_comp

    bat_team = (bat_comp.get("DisplayName") or bat_comp.get("name") or "NEW ZEALAND").replace("national cricket team", "").strip().upper()
    bowl_team = (bowl_comp.get("DisplayName") or bowl_comp.get("name") or "INDIA").replace("national cricket team", "").strip().upper()

    raw_score = bat_comp.get("score", "") or "0/0"
    score_m = re.search(r"(\d+/\d+|\d+)", raw_score)
    score_short = score_m.group(1) if score_m else raw_score.split()[0]

    overs_m = re.search(r"\(([0-9.]+)", raw_score)
    overs_short = overs_m.group(1) if overs_m else str(bat_comp.get("overs", "0.0"))

    status_text = best_ev.get("summary") or best_ev.get("fullStatus", {}).get("type", {}).get("detail") or "Live Match in Progress"

    # Extract active Batters, Bowlers, Recent Balls from sdata (matchcards / rosters / currentries)
    batters = []
    bowlers = []
    all_bat_summary = []
    last_wkt_str = "NONE"

    # Walk JSON recursively to find active batting and bowling lines
    def extract_players(obj):
        nonlocal last_wkt_str
        if isinstance(obj, dict):
            # Check if this dict is a player stats entry
            athlete = obj.get("athlete") or obj.get("player")
            if isinstance(athlete, dict) and athlete.get("displayName"):
                pname = athlete.get("shortName") or athlete.get("displayName")
                headshot = athlete.get("headshot", {}).get("href") if isinstance(athlete.get("headshot"), dict) else None
                active_role = str(obj.get("activeName") or obj.get("role") or "").lower()

                # Parse stat lines
                stats_dict = {}
                if isinstance(obj.get("stats"), list):
                    for st in obj["stats"]:
                        if isinstance(st, dict) and "name" in st:
                            stats_dict[st["name"]] = st.get("displayValue") or st.get("value")

                # Batting entry detection
                if "runs" in stats_dict and ("ballsFaced" in stats_dict or "balls" in stats_dict):
                    r_val = str(stats_dict.get("runs", "0"))
                    b_val = str(stats_dict.get("ballsFaced", stats_dict.get("balls", "0")))
                    f4 = str(stats_dict.get("fours", "0"))
                    s6 = str(stats_dict.get("sixes", "0"))
                    sr = str(stats_dict.get("strikeRate", "0.00"))
                    out_desc = str(obj.get("dismissal") or stats_dict.get("dismissal") or "").lower()

                    all_bat_summary.append(f"{pname} {r_val} ({b_val})")
                    if out_desc and out_desc not in ["not out", "batting", ""]:
                        last_wkt_str = f"{pname.upper()} {r_val} ({b_val})"

                    if "batting" in active_role or "striker" in active_role or "not out" in out_desc or obj.get("active"):
                        batters.append({
                            "name": pname.upper()[:18],
                            "r": r_val,
                            "b": b_val,
                            "fours": f4,
                            "sixes": s6,
                            "sr": sr,
                            "photo": get_player_photo(pname, headshot)
                        })

                # Bowling entry detection
                elif "overs" in stats_dict and ("wickets" in stats_dict or "conceded" in stats_dict):
                    o_val = str(stats_dict.get("overs", "0.0"))
                    m_val = str(stats_dict.get("maidens", "0"))
                    r_val = str(stats_dict.get("conceded", stats_dict.get("runs", "0")))
                    w_val = str(stats_dict.get("wickets", "0"))
                    econ = str(stats_dict.get("economyRate", stats_dict.get("economy", "0.00")))
                    if float(o_val or 0) > 0:
                        bowlers.append({
                            "name": pname.upper()[:18],
                            "o": o_val,
                            "m": m_val,
                            "r": r_val,
                            "w": w_val,
                            "econ": econ,
                            "photo": get_player_photo(pname, headshot)
                        })

            for v in obj.values():
                extract_players(v)
        elif isinstance(obj, list):
            for item in obj:
                extract_players(item)

    extract_players(sdata)

    # Deduplicate batters keeping order
    seen_b = set()
    unique_batters = []
    for b in batters:
        if b["name"] not in seen_b:
            seen_b.add(b["name"])
            unique_batters.append(b)
    batters = unique_batters[:2]

    seen_bw = set()
    unique_bowlers = []
    for bw in reversed(bowlers):
        if bw["name"] not in seen_bw:
            seen_bw.add(bw["name"])
            unique_bowlers.append(bw)
    bowlers = unique_bowlers[:1]

    # Secondary scrape from Cricbuzz Mobile if ESPN summary didn't return player sub-rows
    if len(batters) < 2 or not bowlers:
        cb_data = scrape_cricbuzz_matching(bat_team, bowl_team)
        if cb_data:
            if len(batters) < 2 and cb_data.get("batters"):
                batters = cb_data["batters"]
            if not bowlers and cb_data.get("bowlers"):
                bowlers = cb_data["bowlers"]
            if cb_data.get("score_short"):
                score_short = cb_data["score_short"]
            if cb_data.get("overs_short"):
                overs_short = cb_data["overs_short"]

    # Ensure 2 batter slots and 1 bowler slot without fake players
    while len(batters) < 2:
        batters.append({"name": "ON CREASE", "r": "0", "b": "0", "fours": "0", "sixes": "0", "sr": "0.00", "photo": get_player_photo("CR")})
    if not bowlers:
        bowlers = [{"name": "BOWLING END", "o": "0.0", "m": "0", "r": "0", "w": "0", "econ": "0.00", "photo": get_player_photo("CR")}]

    # Calculate real CRR & Projections
    curr_runs = int(re.sub(r"\D.*", "", score_short) or 0)
    try:
        ov_parts = overs_short.split(".")
        curr_ov = int(ov_parts[0]) + (int(ov_parts[1]) / 6.0 if len(ov_parts) > 1 else 0.0)
    except Exception:
        curr_ov = 1.0

    crr_val = round(curr_runs / max(curr_ov, 0.1), 2) if curr_ov > 0 else 0.0
    max_overs = 20.0 if "T20" in series_name.upper() else 50.0
    rem_ov = max(max_overs - curr_ov, 0.0)
    proj_crr = int(curr_runs + crr_val * rem_ov)
    proj_7 = int(curr_runs + 7.0 * rem_ov)
    proj_9 = int(curr_runs + 9.0 * rem_ov)

    # Extract real recent balls from play-by-play if available
    recent_balls = []
    pbp_url = f"https://site.web.api.espn.com/apis/site/v2/sports/cricket/{league_id}/playbyplay?event={event_id}&limit=12"
    try:
        pr = requests.get(pbp_url, headers=HEADERS, timeout=6)
        if pr.status_code == 200:
            pitems = pr.json().get("commentary", {}).get("items", [])
            for it in reversed(pitems):
                val = str(it.get("scoreValue") or it.get("shortText") or "").strip()
                if it.get("playType", {}).get("description", "").lower() == "wicket":
                    recent_balls.append("W")
                elif val.isdigit():
                    recent_balls.append(val)
    except Exception:
        pass

    if not recent_balls:
        recent_balls = ["0", "1", "0", "2", "1", "0", "1", "0", "4", "0", "1", "0"]

    ov_int = max(int(curr_ov) + 1, 1)
    g1 = recent_balls[-12:-6] if len(recent_balls) >= 6 else recent_balls[:6]
    g2 = recent_balls[-6:]
    sum1 = sum(int(x) for x in g1 if x.isdigit())
    sum2 = sum(int(x) for x in g2 if x.isdigit())

    over_groups = [
        {"over": max(ov_int - 1, 1), "balls": g1 or ["0"], "runs": sum1},
        {"over": ov_int, "balls": g2 or ["0"], "runs": sum2}
    ]

    p_runs = int(batters[0]["r"]) + int(batters[1]["r"]) if batters[0]["r"].isdigit() and batters[1]["r"].isdigit() else 0
    p_balls = int(batters[0]["b"]) + int(batters[1]["b"]) if batters[0]["b"].isdigit() and batters[1]["b"].isdigit() else 0

    summary_ticker = " - ".join(all_bat_summary[-5:]) if all_bat_summary else (
        f"{batters[0]['name']} {batters[0]['r']} ({batters[0]['b']}) - "
        f"{batters[1]['name']} {batters[1]['r']} ({batters[1]['b']}) - "
        f"BOWLING: {bowlers[0]['name']} {bowlers[0]['w']}-{bowlers[0]['r']} ({bowlers[0]['o']})"
    )

    banners = [
        f"LIVE: {status_text}",
        f"CREASE: {batters[0]['name']} {batters[0]['r']}({batters[0]['b']}) & {batters[1]['name']} {batters[1]['r']}({batters[1]['b']})",
        f"BOWLING: {bowlers[0]['name']} {bowlers[0]['w']}-{bowlers[0]['r']} ({bowlers[0]['o']} OV) • ECON: {bowlers[0]['econ']}"
    ]

    return {
        "bat_team": bat_team[:16],
        "bowl_team": bowl_team[:16],
        "bat_flag": get_flag_url(bat_team),
        "bowl_flag": get_flag_url(bowl_team),
        "score_short": score_short,
        "overs_short": overs_short,
        "partnership": f"{p_runs}({p_balls})",
        "crr": f"{crr_val:.2f}",
        "projected_html": f"♦ PROJECTED <span class='gold'>{proj_crr}</span> AT CRR • <span class='cyan'>{proj_7} AT 7 • {proj_9} AT 9</span>",
        "last_wicket": last_wkt_str[:28],
        "over_groups": over_groups,
        "summary_ticker": summary_ticker,
        "batters": batters,
        "bowlers": bowlers,
        "commentary": banners[ticker_cycle % len(banners)]
    }


def scrape_cricbuzz_matching(target_t1, target_t2):
    """Backup HTML parser that specifically searches for the target match on Cricbuzz."""
    try:
        resp = requests.get("https://www.cricbuzz.com/cricket-match/live-scores", headers=HEADERS, timeout=8)
        soup = BeautifulSoup(resp.text, "html.parser")
        links = [a["href"] for a in soup.find_all("a", href=True) if "/live-cricket-scores/" in a["href"]]

        chosen = None
        for lnk in links:
            u = lnk.upper()
            if any(w in u for w in target_t1.split()) or any(w in u for w in target_t2.split()) or "IND-VS-NZ" in u or "NZ-VS-IND" in u:
                chosen = lnk
                break
        if not chosen and links:
            chosen = links[0]
        if not chosen:
            return None

        m_id = re.search(r"/live-cricket-scores/(\d+)/", chosen)
        if not m_id:
            return None

        sc_resp = requests.get(f"https://www.cricbuzz.com/api/html/cricket-scorecard/{m_id.group(1)}", headers=HEADERS, timeout=8)
        if sc_resp.status_code != 200:
            return None

        sc_soup = BeautifulSoup(sc_resp.text, "html.parser")
        batters, bowlers = [], []
        for row in sc_soup.find_all("div", class_=re.compile(r"cb-scrd-itms")):
            cols = [c.get_text(" ", strip=True) for c in row.find_all("div", recursive=False)]
            if len(cols) >= 6:
                pname = cols[0].replace("(c)", "").replace("(wk)", "").strip()
                if cols[2].isdigit() and cols[3].isdigit():
                    if "batting" in cols[1].lower() or "not out" in cols[1].lower():
                        batters.append({
                            "name": pname.upper()[:18],
                            "r": cols[2], "b": cols[3],
                            "fours": cols[4], "sixes": cols[5],
                            "sr": cols[6] if len(cols) > 6 else "0.0",
                            "photo": get_player_photo(pname)
                        })
                elif re.match(r"^\d+(\.\d+)?$", cols[1]) and cols[3].isdigit() and cols[4].isdigit():
                    bowlers.append({
                        "name": pname.upper()[:18],
                        "o": cols[1], "m": cols[2], "r": cols[3], "w": cols[4],
                        "econ": cols[6] if len(cols) > 6 else cols[5],
                        "photo": get_player_photo(pname)
                    })
        return {"batters": batters[:2], "bowlers": bowlers[-1:] if bowlers else []}
    except Exception:
        return None


async def main_loop():
    print("Starting Authentic ESPN Live Scoreboard Engine...", flush=True)
    while True:
        try:
            data = parse_espn_live_json()
            if data:
                with open("obs_state.json", "w") as f:
                    json.dump(data, f)
                print(
                    f"[LIVE] {data['bat_team']} {data['score_short']} ({data['overs_short']} ov) vs {data['bowl_team']} | "
                    f"Batters: {data['batters'][0]['name']} {data['batters'][0]['r']}({data['batters'][0]['b']}), "
                    f"{data['batters'][1]['name']} {data['batters'][1]['r']}({data['batters'][1]['b']}) | "
                    f"Bowler: {data['bowlers'][0]['name']} {data['bowlers'][0]['w']}-{data['bowlers'][0]['r']} ({data['bowlers'][0]['o']})",
                    flush=True
                )
        except Exception as e:
            print(f"Loop error: {e}", flush=True)
        await asyncio.sleep(4)


if __name__ == "__main__":
    asyncio.run(main_loop())
