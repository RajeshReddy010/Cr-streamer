import asyncio
import json
import re
import urllib.parse
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 14; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
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
    if not clean or clean in ["--", "BATTER 1", "BATTER 2", "BOWLER", "ON CREASE"]:
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
    """Scrapes Cricbuzz mobile match center for real-time live scores and player scorecards."""
    try:
        resp = requests.get("https://m.cricbuzz.com/cricket-match/live-scores", headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            print(f"Cricbuzz mobile HTTP status {resp.status_code}")
            return None

        soup = BeautifulSoup(resp.text, "html.parser")
        match_divs = soup.find_all("h4", class_="ui-header") or soup.find_all("div", class_="cb-mtch-lst")
        
        # Find first live match link
        match_url = None
        for a in soup.find_all("a", href=True):
            if "/live-cricket-scores/" in a["href"]:
                match_url = "https://m.cricbuzz.com" + a["href"]
                break

        if not match_url:
            print("No active live match URL found on Cricbuzz mobile.")
            return None

        m_resp = requests.get(match_url, headers=HEADERS, timeout=10)
        m_soup = BeautifulSoup(m_resp.text, "html.parser")

        # Teams
        title_tag = m_soup.find("title")
        title_text = title_tag.text if title_tag else "Live Match"
        teams_part = title_text.split("Live")[0].split("-")[0].strip()
        
        if " vs " in teams_part.lower():
            parts = re.split(r"\s+vs\s+", teams_part, flags=re.IGNORECASE)
            bat_team, bowl_team = parts[0].strip().upper(), parts[1].strip().upper()
        else:
            bat_team, bowl_team = "TEAM A", "TEAM B"

        # Score & Overs
        score_div = m_soup.find("div", class_=re.compile(r"srs_cnt|cb-min-bat-rw|header-score"))
        score_text = score_div.get_text(" ", strip=True) if score_div else title_text

        score_m = re.search(r"(\d+/\d+|\d+)", score_text)
        score_short = score_m.group(1) if score_m else "0/0"

        overs_m = re.search(r"([0-9.]+)\s*(?:ov|Ovs)", score_text)
        overs_short = overs_m.group(1) if overs_m else "0.0"

        # Parse Batters & Bowlers from Scorecard tables
        batters = []
        bowlers = []
        all_bat_summary = []

        for tbl in m_soup.find_all("table"):
            for row in tbl.find_all("tr"):
                cols = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
                if len(cols) >= 3:
                    pname = cols[0].replace("(c)", "").replace("(wk)", "").strip()
                    if pname and pname not in ["Batsman", "Batter", "Bowler", "Total", "Extras"]:
                        # Check if batting stats
                        if cols[1].isdigit() and cols[2].isdigit():
                            r, b = cols[1], cols[2]
                            f4 = cols[3] if len(cols) > 3 else "0"
                            s6 = cols[4] if len(cols) > 4 else "0"
                            sr = cols[5] if len(cols) > 5 else "0.0"
                            all_bat_summary.append(f"{pname} {r} ({b})")
                            if len(batters) < 2:
                                batters.append({
                                    "name": pname.upper()[:18],
                                    "r": r, "b": b, "fours": f4, "sixes": s6, "sr": sr,
                                    "photo": get_player_photo(pname)
                                })
                        # Check if bowling stats
                        elif re.match(r"^\d+(\.\d+)?$", cols[1]) and len(cols) >= 5 and cols[3].isdigit():
                            o_val, m_val, r_val, w_val = cols[1], cols[2], cols[3], cols[4]
                            econ = cols[5] if len(cols) > 5 else "0.0"
                            if float(o_val or 0) > 0 and len(bowlers) < 1:
                                bowlers.append({
                                    "name": pname.upper()[:18],
                                    "o": o_val, "m": m_val, "r": r_val, "w": w_val, "econ": econ,
                                    "photo": get_player_photo(pname)
                                })

        if len(batters) < 2:
            batters = [
                {"name": "STRIKER", "r": "0", "b": "0", "fours": "0", "sixes": "0", "sr": "0.00", "photo": get_player_photo("Batter")},
                {"name": "NON-STRIKER", "r": "0", "b": "0", "fours": "0", "sixes": "0", "sr": "0.00", "photo": get_player_photo("Batter")}
            ]
        if not bowlers:
            bowlers = [{"name": "BOWLER", "o": "0.0", "m": "0", "r": "0", "w": "0", "econ": "0.00", "photo": get_player_photo("Bowler")}]

        curr_runs = int(re.sub(r"\D.*", "", score_short) or 0)
        try:
            ov_parts = overs_short.split(".")
            curr_ov = int(ov_parts[0]) + (int(ov_parts[1]) / 6.0 if len(ov_parts) > 1 else 0.0)
        except Exception:
            curr_ov = 1.0

        crr_val = round(curr_runs / max(curr_ov, 0.1), 2) if curr_ov > 0 else 0.0
        rem_ov = max(50.0 - curr_ov, 0.0)
        proj_crr = int(curr_runs + crr_val * rem_ov)
        proj_7 = int(curr_runs + 7.0 * rem_ov)
        proj_9 = int(curr_runs + 9.0 * rem_ov)

        p_runs = int(batters[0]["r"]) + int(batters[1]["r"]) if batters[0]["r"].isdigit() and batters[1]["r"].isdigit() else 0
        p_balls = int(batters[0]["b"]) + int(batters[1]["b"]) if batters[0]["b"].isdigit() and batters[1]["b"].isdigit() else 0

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
            "last_wicket": f"{batters[1]['name']} {batters[1]['r']} ({batters[1]['b']})",
            "over_groups": [
                {"over": 12, "balls": ["1", "1", "0", "0", "0", "0"], "runs": 2},
                {"over": 13, "balls": ["0", "1", "1", "0", "6", "3"], "runs": 11}
            ],
            "summary_ticker": " - ".join(all_bat_summary[-5:]) if all_bat_summary else f"{batters[0]['name']} {batters[0]['r']} ({batters[0]['b']}) - {batters[1]['name']} {batters[1]['r']} ({batters[1]['b']})",
            "batters": batters,
            "bowlers": bowlers,
            "commentary": f"LIVE BROADCAST: {bat_team} at {score_short} ({overs_short} overs) vs {bowl_team}."
        }
    except Exception as e:
        print(f"Parser exception: {e}")
        return None


async def main_loop():
    print("Starting Live Cricbuzz Mobile Scraper Engine...", flush=True)
    while True:
        data = fetch_live_match_data()
        if data:
            with open("obs_state.json", "w") as f:
                json.dump(data, f)
            print(f"[LIVE MATCH] {data['bat_team']} {data['score_short']} ({data['overs_short']} ov) | {data['batters'][0]['name']} & {data['batters'][1]['name']}", flush=True)
        else:
            print("Waiting for live match data feed...", flush=True)
        await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main_loop())
