import asyncio
import json
import os
import random
import subprocess
import xml.etree.ElementTree as ET
import requests
from bs4 import BeautifulSoup
from google import genai
import edge_tts

API_KEY = os.environ.get("GEMINI_API_KEY")
client = genai.Client(api_key=API_KEY) if API_KEY else None

VOICES = [
    "en-IN-PrabhatNeural",  # Kabir (Male Indian English)
    "en-IN-NeerjaNeural"    # Meera (Female Indian English)
]

def fetch_match_data():
    """
    Fetches real-time live cricket scores from ESPNcricinfo's public XML feed
    (which never blocks GitHub Actions IPs) with NDTV Sports HTML fallback.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }

    # Source 1: ESPNcricinfo Official Live Score XML Feed
    try:
        resp = requests.get("https://static.cricinfo.com/rss/livescores.xml", headers=headers, timeout=10)
        if resp.status_code == 200:
            root = ET.fromstring(resp.text)
            items = root.findall(".//item")
            if items:
                # Pick a match that currently has scores (contains '/' or '*')
                live_items = [item for item in items if "/" in (item.find("title").text or "")]
                chosen = live_items[0] if live_items else items[0]
                raw_title = chosen.find("title").text.strip()
                
                # Example raw_title: "India 212/4 (18.2 ov) vs Australia"
                return {
                    "teams": raw_title.split(" v ")[0] + " vs " + raw_title.split(" v ")[1] if " v " in raw_title else raw_title[:38],
                    "score": raw_title,
                    "overs": "Live Broadcast • Real-Time Feed",
                    "summary": raw_title
                }
    except Exception as e:
        print(f"RSS fetch error: {e}", flush=True)

    # Source 2: NDTV Sports Live Cricket Page Fallback
    try:
        resp = requests.get("https://sports.ndtv.com/cricket/live-scores", headers=headers, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")
        team_blocks = soup.find_all("div", class_="scr_tm-wrp")
        if len(team_blocks) >= 2:
            t1 = team_blocks[0].get_text(" ", strip=True)
            t2 = team_blocks[1].get_text(" ", strip=True)
            return {
                "teams": f"{t1.split()[0]} vs {t2.split()[0]}",
                "score": f"{t1} | {t2}",
                "overs": "Live Match Update",
                "summary": f"{t1} against {t2}"
            }
    except Exception as e:
        print(f"Fallback scraper error: {e}", flush=True)

    return {
        "teams": "LIVE CRICKET STUDIO",
        "score": "Scanning Live Fixtures...",
        "overs": "Live Feed Active",
        "summary": "Fetching the latest ball-by-ball updates from around the globe."
    }

async def generate_commentary(match_data):
    if not client:
        return f"Live update from the studio: {match_data['summary']}. Stay tuned as the action unfolds!"
    
    prompt = f"""
    You are an energetic Indian cricket commentator on a live YouTube broadcast.
    Current live match score: {json.dumps(match_data)}
    Give a crisp, exciting 2-sentence live spoken update about this score.
    Write ONLY the spoken words—no speaker labels, no asterisks, no markdown.
    """
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        return response.text.strip().replace("*", "")
    except Exception as e:
        print(f"Gemini error: {e}", flush=True)
        return f"Here is the latest live score update: {match_data['summary']}. What a contest we have here today!"

async def main_loop():
    print("Starting AI Commentator & Live Score Engine...", flush=True)
    os.makedirs("audio_cache", exist_ok=True)
    voice_index = 0

    while True:
        try:
            match_data = fetch_match_data()
            commentary_text = await generate_commentary(match_data)
            print(f"[LIVE UPDATE] {match_data['score']} | [VO] {commentary_text}", flush=True)

            # Update JSON state for scoreboard.html
            state = {
                "teams": match_data["teams"],
                "score": match_data["score"],
                "overs": match_data["overs"],
                "commentary": commentary_text
            }
            with open("obs_state.json", "w") as f:
                json.dump(state, f)

            # Generate voice MP3 using alternating voices (Prabhat & Neerja)
            voice_id = VOICES[voice_index % len(VOICES)]
            voice_index += 1
            audio_path = "audio_cache/current_utterance.mp3"

            communicate = edge_tts.Communicate(commentary_text, voice_id)
            await communicate.save(audio_path)

            # Play audio explicitly into the PulseAudio virtual_speaker sink
            env = os.environ.copy()
            env["PULSE_SINK"] = "virtual_speaker"
            subprocess.run(
                ["ffplay", "-nodisp", "-autoexit", "-loglevel", "warning", audio_path],
                env=env,
                check=False
            )
        except Exception as e:
            print(f"Loop error: {e}", flush=True)

        await asyncio.sleep(6)

if __name__ == "__main__":
    asyncio.run(main_loop())
