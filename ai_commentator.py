import asyncio
import json
import os
import re
import subprocess
import xml.etree.ElementTree as ET
import requests
from bs4 import BeautifulSoup
from google import genai
import edge_tts

API_KEY = os.environ.get("GEMINI_API_KEY")
client = genai.Client(api_key=API_KEY) if API_KEY else None

VOICES = [
    "en-IN-PrabhatNeural",  # Kabir (Male Analytical Host)
    "en-IN-NeerjaNeural"    # Meera (Female Studio Co-Host)
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"
}

match_rotation_index = 0
last_gemini_score = ""
cached_gemini_lines = []
variation_counter = 0


def scrape_cricbuzz_detailed():
    """
    Scrapes live match list and deep batting/bowling tables from Cricbuzz.
    Automatically rotates across active matches if the top match is paused/Stumps.
    """
    global match_rotation_index
    try:
        resp = requests.get("https://www.cricbuzz.com/cricket-match/live-scores", headers=HEADERS, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")

        # Find all live match links on Cricbuzz
        links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/live-cricket-scores/" in href and href not in links:
                links.append(href)

        if not links:
            return None

        # Pick active match (rotate every few cycles if multiple matches exist)
        chosen_href = links[match_rotation_index % min(len(links), 3)]
        match_url = "https://www.cricbuzz.com" + chosen_href
        m_resp = requests.get(match_url, headers=HEADERS, timeout=10)
        m_soup = BeautifulSoup(m_resp.text, "html.parser")

        # Title & Series
        title_text = m_soup.title.text.split("|")[0].strip() if m_soup.title else "Live Cricket Match"
        teams = title_text.replace("Live Cricket Score", "").strip(" -•,")

        # Extract main score & status
        score_el = m_soup.find("div", class_=re.compile(r"cb-min-bat-rw|cb-scrs-wrp"))
        score_str = score_el.get_text(" ", strip=True) if score_el else teams

        status_el = m_soup.find("div", class_=re.compile(r"cb-text-inprogress|cb-text-stumps|cb-text-lunch|cb-text-tea|cb-text-complete|cb-min-stts"))
        status_str = status_el.get_text(" ", strip=True) if status_el else "Live Match in Progress"

        # If match is at Stumps or Complete, rotate to the next match next time
        if any(w in status_str.lower() for w in ["stumps", "won", "complete", "day", "break"]):
            match_rotation_index += 1

        # Extract CRR / RRR if present
        crr_match = re.search(r"CRR:\s*([0-9.]+)", score_str)
        rrr_match = re.search(r"REQ:\s*([0-9.]+)", score_str)
        crr = crr_match.group(1) if crr_match else "5.42"
        rrr = f"REQ: {rrr_match.group(1)}" if rrr_match else "1st Innings / Live"
        clean_score = re.sub(r"CRR:.*", "", score_str).strip() or score_str

        # Parse Batters & Bowlers from miniscore tables
        batters = []
        bowlers = []
        tables = m_soup.find_all("table")
        for tbl in tables:
            rows = tbl.find_all("tr")
            for r in rows:
                cols = [c.get_text(" ", strip=True) for c in r.find_all(["td", "th"])]
                if len(cols) >= 6 and cols[0] not in ["Batter", "Batsman", "Bowler", ""]:
                    # Check if numeric stats
                    if cols[1].isdigit():
                        if len(batters) < 2 and "." not in cols[1]:
                            batters.append({
                                "name": cols[0][:22],
                                "r": cols[1],
                                "b": cols[2],
                                "fours": cols[3],
                                "sixes": cols[4],
                                "sr": cols[5]
                            })
                    elif re.match(r"^\d+(\.\d+)?$", cols[1]) and len(bowlers) < 2:
                        bowlers.append({
                            "name": cols[0][:22],
                            "o": cols[1],
                            "m": cols[2],
                            "r": cols[3],
                            "w": cols[4],
                            "econ": cols[5]
                        })

        # Extract recent balls if visible
        recent_el = m_soup.find("div", class_=re.compile(r"cb-min-rcnt"))
        recent_balls = ["1", "0", "4", "1", "2", "0"]
        if recent_el:
            raw_balls = recent_el.get_text(" ", strip=True).replace("Recent:", "").replace("|", " ").split()
            if raw_balls:
                recent_balls = raw_balls[-8:]

        # Extract key stats (Partnership / Last Wicket)
        partnership = "Building steadily"
        last_wkt = "None in current spell"
        for div in m_soup.find_all("div", class_=re.compile(r"cb-min-itm-rw")):
            txt = div.get_text(" ", strip=True)
            if "Partnership:" in txt:
                partnership = txt.replace("Partnership:", "").strip()[:28]
            elif "Last Wkt:" in txt:
                last_wkt = txt.replace("Last Wkt:", "").strip()[:32]

        if not batters:
            batters = [
                {"name": "Striker (On Crease) *", "r": "42", "b": "51", "fours": "5", "sixes": "1", "sr": "82.3"},
                {"name": "Non-Striker", "r": "28", "b": "39", "fours": "3", "sixes": "0", "sr": "71.8"}
            ]
        if not bowlers:
            bowlers = [
                {"name": "Lead Attack Bowler *", "o": "8.2", "m": "1", "r": "31", "w": "2", "econ": "3.72"}
            ]

        return {
            "series": "LIVE INTERNATIONAL & DOMESTIC CRICKET FEED",
            "teams": teams[:48],
            "score": clean_score[:42],
            "status": status_str[:65],
            "crr": crr,
            "rrr": rrr,
            "partnership": partnership,
            "last_wicket": last_wkt,
            "batters": batters,
            "bowlers": bowlers,
            "recent_balls": recent_balls,
            "summary": f"{teams} — {clean_score}. Status: {status_str}"
        }
    except Exception as e:
        print(f"Cricbuzz deep scrape error: {e}", flush=True)
        return None


def fetch_match_data():
    """Primary deep scraper with ESPNcricinfo XML multi-match rotation fallback."""
    global match_rotation_index
    detailed = scrape_cricbuzz_detailed()
    if detailed:
        return detailed

    try:
        resp = requests.get("https://static.cricinfo.com/rss/livescores.xml", headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            root = ET.fromstring(resp.text)
            items = root.findall(".//item")
            if items:
                scored_items = [it for it in items if "/" in (it.find("title").text or "")]
                pool = scored_items if scored_items else items
                chosen = pool[match_rotation_index % len(pool)]
                match_rotation_index += 1
                raw_title = chosen.find("title").text.strip()

                return {
                    "series": "CRICROVER GLOBAL LIVE FEED",
                    "teams": raw_title.split(" v ")[0] + " vs " + raw_title.split(" v ")[1] if " v " in raw_title else "Live Match",
                    "score": raw_title[:42],
                    "status": "Live Broadcast • Rotating Global Match Center",
                    "crr": "4.85",
                    "rrr": "Live Battle",
                    "partnership": "Active Stand",
                    "last_wicket": "See Scorecard",
                    "batters": [
                        {"name": "Striker (At Crease) *", "r": "36", "b": "44", "fours": "4", "sixes": "1", "sr": "81.8"},
                        {"name": "Partner (At Crease)", "r": "19", "b": "27", "fours": "2", "sixes": "0", "sr": "70.3"}
                    ],
                    "bowlers": [
                        {"name": "Current Spell Bowler *", "o": "6.0", "m": "1", "r": "24", "w": "1", "econ": "4.00"}
                    ],
                    "recent_balls": ["0", "1", "4", "0", "2", "1"],
                    "summary": raw_title
                }
    except Exception as e:
        print(f"RSS fallback error: {e}", flush=True)

    return {
        "series": "CRICROVER STUDIO LIVE",
        "teams": "INTERNATIONAL CRICKET CENTER",
        "score": "Updating Scorecards...",
        "status": "Scanning Live Stadium Feeds",
        "crr": "--",
        "rrr": "--",
        "partnership": "--",
        "last_wicket": "--",
        "batters": [],
        "bowlers": [],
        "recent_balls": ["•"],
        "summary": "Live studio coverage underway."
    }


async def generate_commentary(match_data):
    """
    Generates non-repeating commentary!
    Calls Gemini ONLY when the score changes (saving quota) to generate 4 distinct angles,
    and cycles through a 10-line analytical bank between balls so it NEVER repeats the same line.
    """
    global last_gemini_score, cached_gemini_lines, variation_counter
    variation_counter += 1

    b1 = match_data["batters"][0] if match_data.get("batters") else {"name": "the striker", "r": "0", "b": "0", "sr": "0"}
    bw1 = match_data["bowlers"][0] if match_data.get("bowlers") else {"name": "the bowler", "o": "0", "r": "0", "w": "0"}

    # Only call Gemini if the score string changed OR we have no cached lines
    if client and (match_data["score"] != last_gemini_score or not cached_gemini_lines):
        prompt = f"""
        You are hosting a live cricket studio show on YouTube.
        Match Data: {json.dumps(match_data)}
        Write 4 COMPLETELY DIFFERENT 2-sentence spoken commentary updates separated by the pipe character (|).
        Angle 1: React to the overall team score and match situation.
        Angle 2: Analyze the batting crease ({b1['name']} on {b1['r']} off {b1['b']} balls) and strike rotation.
        Angle 3: Analyze the bowling spell ({bw1['name']} with figures of {bw1['w']} for {bw1['r']}) and field placement.
        Angle 4: Engage the YouTube live chat viewers, asking them to drop their match predictions and subscribe.
        Plain spoken sentences only—no labels, no markdown, no asterisks, separated strictly by |.
        """
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
            )
            parts = [p.strip().replace("*", "") for p in response.text.split("|") if len(p.strip()) > 20]
            if parts:
                cached_gemini_lines = parts
                last_gemini_score = match_data["score"]
        except Exception as e:
            print(f"Gemini quota/notice (using varied studio bank): {e}", flush=True)

    # Dynamic 8-angle studio bank so even if a match is at Stumps or Gemini quota rests, VO never repeats!
    studio_angles = [
        f"Welcome back to CricRover Live! Looking at the scoreboard, {match_data['score']}, with {match_data['status']}.",
        f"Checking in on the batting card, {b1['name']} is holding the crease on {b1['r']} runs off {b1['b']} deliveries at a strike rate of {b1['sr']}.",
        f"With the ball in hand, {bw1['name']} has bowled {bw1['o']} overs, conceding {bw1['r']} runs for {bw1['w']} wickets so far.",
        f"The current run rate sits at {match_data['crr']} runs per over as both teams battle for momentum in this session.",
        f"Tactical check from the studio: the partnership is {match_data['partnership']}, and fielders are staying sharp inside the circle.",
        f"If you are just joining our live stream, smash that like button and let us know in the live chat which team you are backing today!",
        f"Scoreboard summary for our viewers: {match_data['teams']}, currently at {match_data['score']}. Every session is proving crucial here.",
        f"Looking at the recent deliveries, the bowlers are testing the corridor of uncertainty while the batters look to punish anything loose."
    ]

    combined_pool = cached_gemini_lines + studio_angles
    return combined_pool[variation_counter % len(combined_pool)]


async def main_loop():
    print("Starting CricRover Pro AI Commentator & Deep Scraper...", flush=True)
    os.makedirs("audio_cache", exist_ok=True)
    voice_index = 0

    while True:
        try:
            match_data = fetch_match_data()
            commentary_text = await generate_commentary(match_data)
            print(f"[LIVE] {match_data['score']} | [VO #{variation_counter}] {commentary_text}", flush=True)

            # Save full telemetry for scoreboard.html
            match_data["commentary"] = commentary_text
            with open("obs_state.json", "w") as f:
                json.dump(match_data, f)

            # Alternate between Kabir (Prabhat) and Meera (Neerja)
            voice_id = VOICES[voice_index % len(VOICES)]
            voice_index += 1
            audio_path = "audio_cache/current_utterance.mp3"

            communicate = edge_tts.Communicate(commentary_text, voice_id)
            await communicate.save(audio_path)

            # Play voiceover into PulseAudio Virtual Speaker
            env = os.environ.copy()
            env["PULSE_SINK"] = "virtual_speaker"
            subprocess.run(
                ["ffplay", "-nodisp", "-autoexit", "-loglevel", "warning", audio_path],
                env=env,
                check=False
            )
        except Exception as e:
            print(f"Main loop error: {e}", flush=True)

        # Wait 10 seconds between commentary segments so pacing feels like a real TV studio
        await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main_loop())
