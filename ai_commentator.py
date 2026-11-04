import asyncio
import json
import os
import re
import subprocess
import urllib.parse
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

FLAG_MAP = {
    "IND": "in", "INDIA": "in", "INDA": "in", "NZ": "nz", "NEW ZEALAND": "nz", "NZA": "nz",
    "AUS": "au", "AUSTRALIA": "au", "ENG": "gb-eng", "ENGLAND": "gb-eng", "SA": "za",
    "SOUTH AFRICA": "za", "PAK": "pk", "PAKISTAN": "pk", "SL": "lk", "SRI LANKA": "lk",
    "BAN": "bd", "BANGLADESH": "bd", "AFG": "af", "AFGHANISTAN": "af", "WI": "jm", "ZIM": "zw"
}

photo_cache = {}
match_rotation_index = 0
last_score_short = ""
ticker_cycle = 0


def get_flag_url(team_name):
    upper = team_name.upper().strip()
    for k, code in FLAG_MAP.items():
        if k in upper:
            return f"https://flagcdn.com/w320/{code}.png"
    return "https://flagcdn.com/w320/un.png"


def get_player_photo(player_name):
    clean = re.sub(r"[\*\(\)cwk&]", "", player_name).strip()
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


def scrape_cricbuzz_pro():
    global match_rotation_index, ticker_cycle
    ticker_cycle += 1
    try:
        resp = requests.get("https://www.cricbuzz.com/cricket-match/live-scores", headers=HEADERS, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")

        match_links = []
        for a in soup.find_all("a", href=True):
            h = a["href"]
            if "/live-cricket-scores/" in h and h not in match_links:
                match_links.append(h)

        if not match_links:
            return None

        chosen_href = match_links[match_rotation_index % min(len(match_links), 4)]
        match_id_m = re.search(r"/live-cricket-scores/(\d+)/", chosen_href)
        match_id = match_id_m.group(1) if match_id_m else None

        m_resp = requests.get("https://www.cricbuzz.com" + chosen_href, headers=HEADERS, timeout=10)
        m_soup = BeautifulSoup(m_resp.text, "html.parser")

        raw_title = m_soup.title.text.split("|")[0] if m_soup.title else "INDIA vs NEW ZEALAND"
        teams_part = raw_title.split("Live")[0].split(",")[0].strip()
        if " vs " in teams_part.lower():
            parts = re.split(r"\s+vs\s+", teams_part, flags=re.IGNORECASE)
            bat_team, bowl_team = parts[0].strip().upper(), parts[1].strip().upper()
        else:
            bat_team, bowl_team = "NEW ZEALAND", "INDIA"

        score_block = m_soup.find("div", class_=re.compile(r"cb-min-bat-rw|cb-scrs-wrp"))
        raw_score_line = score_block.get_text(" ", strip=True) if score_block else "142/3 (24.2)"

        status_el = m_soup.find("div", class_=re.compile(r"cb-text-inprogress|cb-text-stumps|cb-text-lunch|cb-text-tea|cb-text-complete|cb-min-stts"))
        status_str = status_el.get_text(" ", strip=True) if status_el else "Live Match in Progress"

        score_m = re.search(r"(\d+/\d+|\d+)", raw_score_line)
        overs_m = re.search(r"\(([0-9.]+)\s*(?:Ovs|ov)?\)", raw_score_line, re.IGNORECASE)
        score_short = score_m.group(1) if score_m else "142/3"
        overs_short = overs_m.group(1) if overs_m else "24.2"

        crr_m = re.search(r"CRR:\s*([0-9.]+)", raw_score_line)
        crr_val = float(crr_m.group(1)) if crr_m else 5.45

        curr_runs = int(score_short.split("/")[0]) if "/" in score_short else 120
        curr_ov = float(overs_short) if overs_short.replace(".", "").isdigit() else 20.0
        rem_ov = max(50.0 - curr_ov, 0.0) if curr_ov <= 50 else 0.0
        proj_crr = int(curr_runs + crr_val * rem_ov)
        proj_7 = int(curr_runs + 7.0 * rem_ov)
        proj_9 = int(curr_runs + 9.0 * rem_ov)

        batters = []
        bowlers = []
        all_batters_summary = []

        scard_soup = m_soup
        if match_id:
            sc_url = f"https://www.cricbuzz.com/api/html/cricket-scorecard/{match_id}"
            sc_resp = requests.get(sc_url, headers=HEADERS, timeout=8)
            if sc_resp.status_code == 200 and len(sc_resp.text) > 500:
                scard_soup = BeautifulSoup(sc_resp.text, "html.parser")

        for item_row in scard_soup.find_all("div", class_=re.compile(r"cb-scrd-itms|cb-min-itm-rw")):
            cols = [c.get_text(" ", strip=True) for c in item_row.find_all("div", recursive=False)]
            if len(cols) >= 6:
                p_name = cols[0].replace("(c)", "").replace("(wk)", "").strip()
                if p_name.lower() in ["extras", "total", "batter", "batsman", "bowler", "did not bat"]:
                    continue

                if cols[2].isdigit() and cols[3].isdigit():
                    status_col = cols[1].lower()
                    all_batters_summary.append(f"{p_name} {cols[2]} ({cols[3]})")
                    if "batting" in status_col or "not out" in status_col or len(batters) < 2:
                        batters.append({
                            "name": p_name.upper()[:18],
                            "r": cols[2],
                            "b": cols[3],
                            "fours": cols[4] if len(cols) > 4 else "0",
                            "sixes": cols[5] if len(cols) > 5 else "0",
                            "sr": cols[6] if len(cols) > 6 else "100.0",
                            "photo": get_player_photo(p_name)
                        })

                elif re.match(r"^\d+(\.\d+)?$", cols[1]) and cols[3].isdigit() and cols[4].isdigit():
                    bowlers.append({
                        "name": p_name.upper()[:18],
                        "o": cols[1],
                        "m": cols[2],
                        "r": cols[3],
                        "w": cols[4],
                        "econ": cols[6] if len(cols) > 6 else cols[5],
                        "photo": get_player_photo(p_name)
                    })

        batters = batters[:2]
        bowlers = bowlers[-1:] if bowlers else []

        if len(batters) < 2:
            batters = [
                {"name": "TIM SEIFERT", "r": "35", "b": "38", "fours": "3", "sixes": "2", "sr": "92.11", "photo": get_player_photo("Tim Seifert")},
                {"name": "DARYL MITCHELL", "r": "17", "b": "21", "fours": "2", "sixes": "1", "sr": "80.95", "photo": get_player_photo("Daryl Mitchell")}
            ]
        if not bowlers:
            bowlers = [
                {"name": "KULDEEP YADAV", "o": "4.0", "m": "0", "r": "21", "w": "1", "econ": "5.25", "photo": get_player_photo("Kuldeep Yadav")}
            ]

        recent_el = m_soup.find("div", class_=re.compile(r"cb-min-rcnt"))
        balls_flat = ["1", "1", "0", "0", "0", "0", "0", "1", "1", "0", "6", "4"]
        if recent_el:
            extracted = [b for b in recent_el.get_text(" ", strip=True).replace("Recent:", "").replace("|", " ").split() if b != "..."]
            if len(extracted) >= 6:
                balls_flat = extracted[-12:]

        ov_int = int(float(overs_short)) if overs_short.replace(".", "").isdigit() else 13
        g1 = balls_flat[:6]
        g2 = balls_flat[6:12] if len(balls_flat) > 6 else ["0", "1", "4", "1"]
        sum1 = sum(int(x) for x in g1 if x.isdigit())
        sum2 = sum(int(x) for x in g2 if x.isdigit())

        over_groups = [
            {"over": max(ov_int - 1, 1), "balls": g1, "runs": sum1},
            {"over": ov_int, "balls": g2, "runs": sum2}
        ]

        summary_ticker = " - ".join(all_batters_summary[:5]) if all_batters_summary else f"{batters[0]['name']} {batters[0]['r']} ({batters[0]['b']}) - {batters[1]['name']} {batters[1]['r']} ({batters[1]['b']})"

        # Rotate clean on-screen match status messages
        status_banners = [
            f"LIVE STATUS: {status_str}",
            f"ON CREASE: {batters[0]['name']} {batters[0]['r']}({batters[0]['b']}) & {batters[1]['name']} {batters[1]['r']}({batters[1]['b']})",
            f"CURRENT SPELL: {bowlers[0]['name']} {bowlers[0]['w']}-{bowlers[0]['r']} ({bowlers[0]['o']} OV) • ECON: {bowlers[0]['econ']}"
        ]

        return {
            "bat_team": bat_team[:16],
            "bowl_team": bowl_team[:16],
            "bat_flag": get_flag_url(bat_team),
            "bowl_flag": get_flag_url(bowl_team),
            "score_short": score_short,
            "overs_short": overs_short,
            "partnership": f"{int(batters[0]['r']) + int(batters[1]['r'])}({int(batters[0]['b']) + int(batters[1]['b'])})",
            "crr": f"{crr_val:.2f}",
            "projected_html": f"♦ PROJECTED <span class='gold'>{proj_crr}</span> AT CRR • <span class='cyan'>{proj_7} AT 7 • {proj_9} AT 9</span>",
            "last_wicket": all_batters_summary[-2].upper() if len(all_batters_summary) >= 2 else f"{batters[1]['name']} {batters[1]['r']} ({batters[1]['b']})",
            "over_groups": over_groups,
            "summary_ticker": summary_ticker,
            "batters": batters,
            "bowlers": bowlers,
            "commentary": status_banners[ticker_cycle % len(status_banners)],
            "last_ball": balls_flat[-1] if balls_flat else "0"
        }
    except Exception as e:
        print(f"Scraper error: {e}", flush=True)
        return None


async def main_loop():
    global last_score_short
    print("Starting Live Cricket Scoreboard Engine (Stadium Crowd Audio Mode)...", flush=True)

    while True:
        try:
            data = scrape_cricbuzz_pro()
            if data:
                with open("obs_state.json", "w") as f:
                    json.dump(data, f)

                print(f"[LIVE SCORE] {data['bat_team']} {data['score_short']} ({data['overs_short']} ov) vs {data['bowl_team']}", flush=True)

                # Trigger extra crowd cheer sound effect if score changed on a boundary or wicket
                if data["score_short"] != last_score_short:
                    last_score_short = data["score_short"]
                    if any(k in str(data.get("last_ball", "")).upper() for k in ["4", "6", "W"]) and os.path.exists("audio_assets/crowd_cheer.ogg"):
                        env = os.environ.copy()
                        env["PULSE_SINK"] = "virtual_speaker"
                        subprocess.Popen(
                            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", "-af", "volume=0.65", "audio_assets/crowd_cheer.ogg"],
                            env=env
                        )
        except Exception as e:
            print(f"Loop error: {e}", flush=True)

        await asyncio.sleep(4)


if __name__ == "__main__":
    asyncio.run(main_loop())
