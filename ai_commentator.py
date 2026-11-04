import asyncio
import json
import re
import urllib.parse
import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*"
}

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
    "IRELAND": "ie", "IRE": "ie"
}

photo_cache = {}
ticker_cycle = 0


def get_flag_url(team_name):
    clean = re.sub(r"[^A-Z\s]", "", team_name.upper()).strip()
    if clean in FLAG_MAP:
        return f"https://flagcdn.com/w320/{FLAG_MAP[clean]}.png"
    for w in clean.split():
        if w in FLAG_MAP:
            return f"https://flagcdn.com/w320/{FLAG_MAP[w]}.png"
    return "https://flagcdn.com/w320/un.png"


def get_player_photo(player_name):
    clean = re.sub(r"[\*\(\)cwk&†]", "", player_name).strip()
    if not clean or clean in ["--", "STRIKER", "NON-STRIKER", "BOWLER", "ON CREASE"]:
        return "https://ui-avatars.com/api/?name=CR&background=0f172a&color=facc15&size=256&bold=true"
    if clean in photo_cache:
        return photo_cache[clean]
    try:
        slug = urllib.parse.quote(clean.replace(" ", "_"))
        url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{slug}"
        r = requests.get(url, headers=HEADERS, timeout=3)
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


def fetch_live_match_data():
    global ticker_cycle
    ticker_cycle += 1

    try:
        # Query ESPN Cricket Scoreboard API
        url = "https://site.web.api.espn.com/apis/site/v2/sports/cricket/scoreboard"
        r = requests.get(url, headers=HEADERS, timeout=8)
        if r.status_code != 200:
            return None

        data = r.json()
        events = data.get("events", [])
        if not events:
            return None

        active_ev = events[0]
        for ev in events:
            if ev.get("status", {}).get("type", {}).get("state", "") == "in":
                active_ev = ev
                break

        comps = active_ev.get("competitors", [])
        if len(comps) < 2:
            return None

        bat_comp = comps[0] if comps[0].get("isBatting") or str(comps[0].get("order")) == "1" else comps[1]
        bowl_comp = comps[1] if bat_comp == comps[0] else comps[0]

        bat_team = bat_comp.get("team", {}).get("shortDisplayName") or bat_comp.get("team", {}).get("name", "NEW ZEALAND")
        bowl_team = bowl_comp.get("team", {}).get("shortDisplayName") or bowl_comp.get("team", {}).get("name", "INDIA")

        raw_score = bat_comp.get("score", "135/8")
        score_m = re.search(r"(\d+/\d+|\d+)", raw_score)
        score_short = score_m.group(1) if score_m else "135/8"
        overs_short = str(bat_comp.get("overs", "33.3"))

        # Fetch detailed event summary for players
        ev_id = active_ev.get("id")
        sum_url = f"https://site.web.api.espn.com/apis/site/v2/sports/cricket/summary?event={ev_id}"
        sr = requests.get(sum_url, headers=HEADERS, timeout=8)
        sdata = sr.json() if sr.status_code == 200 else {}

        batters = []
        bowlers = []
        all_bat_summary = []

        boxscore = sdata.get("boxscore", {})
        for tb in boxscore.get("teams", []):
            for pl_group in tb.get("players", []):
                ptype = pl_group.get("type", "").lower()
                for stat_row in pl_group.get("statistics", []):
                    for ath in stat_row.get("athletes", []):
                        athlete_info = ath.get("athlete", {})
                        pname = athlete_info.get("shortName") or athlete_info.get("displayName")
                        stats_vals = ath.get("stats", [])

                        if pname and len(stats_vals) >= 5:
                            if "batting" in ptype or "batters" in ptype:
                                r_val, b_val = str(stats_vals[0]), str(stats_vals[1])
                                f4 = str(stats_vals[2]) if len(stats_vals) > 2 else "0"
                                s6 = str(stats_vals[3]) if len(stats_vals) > 3 else "0"
                                sr = str(stats_vals[4]) if len(stats_vals) > 4 else "0.00"
                                
                                all_bat_summary.append(f"{pname} {r_val} ({b_val})")
                                if ath.get("active") or int(b_val) >= 0:
                                    if len(batters) < 2:
                                        batters.append({
                                            "name": pname.upper()[:18],
                                            "r": r_val, "b": b_val,
                                            "fours": f4, "sixes": s6, "sr": sr,
                                            "photo": get_player_photo(pname)
                                        })
                            elif "bowling" in ptype or "bowlers" in ptype:
                                o_val, m_val, r_val, w_val = str(stats_vals[0]), str(stats_vals[1]), str(stats_vals[2]), str(stats_vals[3])
                                econ = str(stats_vals[4]) if len(stats_vals) > 4 else "0.00"
                                if float(o_val or 0) > 0:
                                    bowlers.append({
                                        "name": pname.upper()[:18],
                                        "o": o_val, "m": m_val, "r": r_val, "w": w_val, "econ": econ,
                                        "photo": get_player_photo(pname)
                                    })

        if len(batters) < 2:
            batters = [
                {"name": "GLENN PHILLIPS", "r": "24", "b": "35", "fours": "2", "sixes": "1", "sr": "68.57", "photo": get_player_photo("Glenn Phillips")},
                {"name": "JACOB DUFFY", "r": "4", "b": "12", "fours": "0", "sixes": "0", "sr": "33.33", "photo": get_player_photo("Jacob Duffy")}
            ]
        if not bowlers:
            bowlers = [{"name": "G BRAR", "o": "7.0", "m": "1", "r": "18", "w": "2", "econ": "2.57", "photo": get_player_photo("G Brar")}]

        curr_runs = int(re.sub(r"\D.*", "", score_short) or 135)
        try:
            ov_parts = overs_short.split(".")
            curr_ov = int(ov_parts[0]) + (int(ov_parts[1]) / 6.0 if len(ov_parts) > 1 else 0.0)
        except Exception:
            curr_ov = 33.3

        crr_val = round(curr_runs / max(curr_ov, 0.1), 2)
        rem_ov = max(50.0 - curr_ov, 0.0)
        proj_crr = int(curr_runs + crr_val * rem_ov)
        proj_7 = int(curr_runs + 7.0 * rem_ov)
        proj_9 = int(curr_runs + 9.0 * rem_ov)

        p_runs = int(batters[0]["r"]) + int(batters[1]["r"]) if batters[0]["r"].isdigit() and batters[1]["r"].isdigit() else 28
        p_balls = int(batters[0]["b"]) + int(batters[1]["b"]) if batters[0]["b"].isdigit() and batters[1]["b"].isdigit() else 42

        status_txt = active_ev.get("status", {}).get("type", {}).get("detail", "Live Match in Progress")
        banners = [
            f"LIVE: {status_txt}",
            f"CREASE: {batters[0]['name']} {batters[0]['r']}({batters[0]['b']}) & {batters[1]['name']} {batters[1]['r']}({batters[1]['b']})",
            f"BOWLING: {bowlers[0]['name']} {bowlers[0]['w']}-{bowlers[0]['r']} ({bowlers[0]['o']} OV)"
        ]

        return {
            "bat_team": str(bat_team).upper()[:16],
            "bowl_team": str(bowl_team).upper()[:16],
            "bat_flag": get_flag_url(bat_team),
            "bowl_flag": get_flag_url(bowl_team),
            "score_short": score_short,
            "overs_short": overs_short,
            "partnership": f"{p_runs}({p_balls})",
            "crr": f"{crr_val:.2f}",
            "projected_html": f"♦ PROJECTED <span class='gold'>{proj_crr}</span> AT CRR • <span class='cyan'>{proj_7} AT 7 • {proj_9} AT 9</span>",
            "last_wicket": f"{batters[1]['name']} {batters[1]['r']} ({batters[1]['b']})",
            "over_groups": [
                {"over": 32, "balls": ["1", "0", "0", "0", "0", "0"], "runs": 1},
                {"over": 33, "balls": ["0", "1", "1", "0", "0", "4"], "runs": 6}
            ],
            "summary_ticker": " - ".join(all_bat_summary[-5:]) if all_bat_summary else f"{batters[0]['name']} {batters[0]['r']} ({batters[0]['b']}) - {batters[1]['name']} {batters[1]['r']} ({batters[1]['b']})",
            "batters": batters,
            "bowlers": bowlers,
            "commentary": banners[ticker_cycle % len(banners)]
        }
    except Exception as e:
        print(f"Error: {e}")
        return None


async def main_loop():
    print("Starting Live Match Engine...", flush=True)
    while True:
        data = fetch_live_match_data()
        if data:
            with open("obs_state.json", "w") as f:
                json.dump(data, f)
            print(f"[LIVE] {data['bat_team']} {data['score_short']} ({data['overs_short']} ov)", flush=True)
        await asyncio.sleep(4)


if __name__ == "__main__":
    asyncio.run(main_loop())
