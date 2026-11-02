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
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

match_rotation_index = 0
last_gemini_score = ""
cached_gemini_lines = []
variation_counter = 0


def scrape_cricbuzz_detailed():
    """Robust scraper for Cricbuzz live match scorecards and individual player tables."""
    global match_rotation_index
    try:
        resp = requests.get("https://www.cricbuzz.com/cricket-match/live-scores", headers=HEADERS, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")

        links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/live-cricket-scores/" in href and href not in links:
                links.append(href)

        if not links:
            return None

        chosen_href = links[match_rotation_index % min(len(links), 3)]
        match_url = "https://www.cricbuzz.com" + chosen_href
        m_resp = requests.get(match_url, headers=HEADERS, timeout=10)
        m_soup = BeautifulSoup(m_resp.text, "html.parser")

        title_text = m_soup.title.text.split("|")[0].strip() if m_soup.title else "Live Cricket Match"
        teams = title_text.replace("Live Cricket Score", "").strip(" -•,")

        score_el = m_soup.find("div", class_=re.compile(r"cb-min-bat-rw|cb-scrs-wrp"))
        score_str = score_el.get_text(" ", strip=True) if score_el else teams

        status_el = m_soup.find("div", class_=re.compile(r"cb-text-inprogress|cb-text-stumps|cb-text-lunch|cb-text-tea|cb-text-complete|cb-min-stts"))
        status_str = status_el.get_text(" ", strip=True) if status_el else "Live Match in Progress"

        if any(w in status_str.lower() for w in ["stumps", "won", "complete", "day", "break"]):
            match_rotation_index += 1

        crr_match = re.search(r"CRR:\s*([0-9.]+)", score_str)
        rrr_match = re.search(r"REQ:\s*([0-9.]+)", score_str)
        crr = crr_match.group(1) if crr_match else "4.85"
        rrr = f"REQ: {rrr_match.group(1)}" if rrr_match else "Live / In Play"
        clean_score = re.sub(r"CRR:.*", "", score_str).strip() or score_str

        # Hardened table parser: Extracts rows directly from scorecard tables
        batters = []
        bowlers = []
        for tbl in m_soup.find_all("table"):
            for row in tbl.find_all("tr"):
                cols = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
                if len(cols) >= 6:
                    name = cols[0]
                    # Filter out headers and extra text rows
                    if name and name not in ["Batter", "Batsman", "Bowler", "1st Innings", "2nd Innings", "Total"] and len(name) < 30:
                        # Check if columns 1 & 2 are numeric (Batting stats: Runs, Balls)
                        if cols[1].isdigit() and cols[2].isdigit():
                            if len(batters) < 2:
                                batters.append({
                                    "name": name,
                                    "r": cols[1],
                                    "b": cols[2],
                                    "fours": cols[3] if len(cols) > 3 else "0",
                                    "sixes": cols[4] if len(cols) > 4 else "0",
                                    "sr": cols[5] if len(cols) > 5 else "0.0"
                                })
                        # Check if columns 1 is a float or decimal (Bowling stats: Overs)
                        elif re.match(r"^\d+(\.\d+)?$", cols[1]) and len(bowlers) < 2:
                            bowlers.append({
                                "name": name,
                                "o": cols[1],
                                "m": cols[2] if len(cols) > 2 else "0",
                                "r": cols[3] if len(cols) > 3 else "0",
                                "w": cols[4] if len(cols) > 4 else "0",
                                "econ": cols[5] if len(cols) > 5 else "0.0"
                            })

        # Fallback if specific table tags were missed on this layout
        if not batters:
            batters = [
                {"name": "Opening Batter (1) *", "r": score_str[:15], "b": "--", "fours": "-", "sixes": "-", "sr": "--"},
                {"name": "Opening Batter (2)", "r": "At Crease", "b": "--", "fours": "-", "sixes": "-", "sr": "--"}
            ]
        if not bowlers:
            bowlers = [
                {"name": "Opening Bowler *", "o": "--", "m": "-", "r": "-", "w": "-", "econ": "--"}
            ]

        recent_el = m_soup.find("div", class_=re.compile(r"cb-min-rcnt"))
        recent_balls = ["1", "0", "4", "1", "2", "0"]
        if recent_el:
            raw_balls = recent_el.get_text(" ", strip=True).replace("Recent:", "").replace("|", " ").split()
            if raw_balls:
                recent_balls = raw_balls[-8:]

        return {
            "series": "LIVE INTERNATIONAL & DOMESTIC CRICKET FEED",
            "teams": teams[:48],
            "score": clean_score[:42],
            "status": status_str[:65],
            "crr": crr,
            "rrr": rrr,
            "partnership": "Active Partnership",
            "last_wicket": "See Match Center",
            "batters": batters,
            "bowlers": bowlers,
            "recent_balls": recent_balls,
            "summary": f"{teams} — {clean_score}. Status: {status_str}"
        }
    except Exception as e:
        print(f"Cricbuzz scrape error: {e}", flush=True)
        return None


def fetch_match_data():
    detailed = scrape_cricbuzz_detailed()
    if detailed:
        return detailed

    return {
        "series": "CRICROVER STUDIO LIVE",
        "teams": "INTERNATIONAL CRICKET CENTER",
        "score": "Updating Scorecards...",
        "status": "Scanning Live Stadium Feeds",
        "crr": "--",
        "rrr": "--",
        "partnership": "--",
        "last_wicket": "--",
        "batters": [{"name": "Live Batsman *", "r": "0", "b": "0", "fours": "0", "sixes": "0", "sr": "0.0"}],
        "bowlers": [{"name": "Live Bowler *", "o": "0.0", "m": "0", "r": "0", "w": "0", "econ": "0.0"}],
        "recent_balls": ["•"],
        "summary": "Live studio coverage underway."
    }


async def generate_commentary(match_data):
    global last_gemini_score, cached_gemini_lines, variation_counter
    variation_counter += 1

    b1 = match_data["batters"][0] if match_data.get("batters") else {"name": "the striker", "r": "0", "b": "0", "sr": "0"}
    bw1 = match_data["bowlers"][0] if match_data.get("bowlers") else {"name": "the bowler", "o": "0", "r": "0", "w": "0"}

    if client and (match_data["score"] != last_gemini_score or not cached_gemini_lines):
        prompt = f"""
        You are hosting a live cricket studio show on YouTube.
        Match Data: {json.dumps(match_data)}
        Write 4 COMPLETELY DIFFERENT 2-sentence spoken commentary updates separated by the pipe character (|).
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
            print(f"Gemini notice: {e}", flush=True)

    studio_angles = [
        f"Welcome back to CricRover Live! Looking at the scoreboard, {match_data['score']}, with {match_data['status']}.",
        f"Checking in on the crease, {b1['name']} is batting on {b1['r']} runs off {b1['b']} balls.",
        f"With the ball in hand, {bw1['name']} is keeping the pressure on the batters.",
        f"The current run rate sits at {match_data['crr']} runs per over as both teams battle for momentum.",
        f"If you are just joining our live stream, smash that like button and subscribe to CricRover!",
        f"Scoreboard update for our viewers: {match_data['teams']}, currently at {match_data['score']}."
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
            print(f"[LIVE] {match_data['score']} | [VO] {commentary_text}", flush=True)

            match_data["commentary"] = commentary_text
            with open("obs_state.json", "w") as f:
                json.dump(match_data, f)

            voice_id = VOICES[voice_index % len(VOICES)]
            voice_index += 1
            audio_path = "audio_cache/current_utterance.mp3"

            communicate = edge_tts.Communicate(commentary_text, voice_id)
            await communicate.save(audio_path)

            env = os.environ.copy()
            env["PULSE_SINK"] = "virtual_speaker"
            subprocess.run(
                ["ffplay", "-nodisp", "-autoexit", "-loglevel", "warning", audio_path],
                env=env,
                check=False
            )
        except Exception as e:
            print(f"Main loop error: {e}", flush=True)

        await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main_loop())
