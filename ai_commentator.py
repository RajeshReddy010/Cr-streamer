import asyncio
import json
import os
import random
import subprocess
import requests
from bs4 import BeautifulSoup
from google import genai
import edge_tts

API_KEY = os.environ.get("GEMINI_API_KEY")
client = genai.Client(api_key=API_KEY)

VOICES = {
    "Kabir": "en-IN-PrabhatNeural",  # Analytical expert
    "Meera": "en-IN-NeetaNeural"    # Enthusiastic co-host
}

def fetch_match_data():
    try:
        url = "https://www.cricbuzz.com/live-cricket-scores"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        match_card = soup.find('div', class_='cb-mtch-lst')
        if match_card:
            title_elem = match_card.find('a', class_='cb-lv-scrs-well-link')
            teams = title_elem.text.strip() if title_elem else "Live Cricket Match"
            
            score_elem = match_card.find('div', class_='cb-scr-wrd')
            score_text = score_elem.text.strip() if score_elem else "Match in progress"
            
            return {
                "teams": teams,
                "score": score_text,
                "overs": "Live Action",
                "summary": score_text
            }
    except Exception as e:
        print(f"Scraper notice: {e}")
        
    return {
        "teams": "IND vs AUS • Live",
        "score": "164/4",
        "overs": "17.2",
        "summary": "The batters are looking to accelerate in the death overs!"
    }

async def generate_commentary(match_data):
    prompt = f"""
    You are Kabir and Meera, energetic cricket commentators broadcasting live. 
    Current Match State: {json.dumps(match_data)}
    Write a short, exciting 2-sentence commentary update reacting to this state.
    Do not include speaker labels like 'Kabir:' or 'Meera:'—just write the spoken words cleanly.
    """
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
        )
        return response.text.strip()
    except Exception:
        return "What a thrilling contest we have on our hands today! Every single delivery is keeping the fans on the edge of their seats."

async def main_loop():
    print("Starting Free-Scraping AI Commentator Engine...")
    os.makedirs("audio_cache", exist_ok=True)
    
    while True:
        match_data = fetch_match_data()
        commentary_text = await generate_commentary(match_data)
        print(f"\n[AI Commentary]: {commentary_text}")

        state = {
            "teams": match_data.get("teams", "Match Live"),
            "score": match_data.get("score", "0/0"),
            "overs": match_data.get("overs", "Live"),
            "commentary": commentary_text
        }
        with open("obs_state.json", "w") as f:
            json.dump(state, f)

        speaker = random.choice(list(VOICES.keys()))
        voice_id = VOICES[speaker]
        audio_path = "audio_cache/current_utterance.mp3"
        
        communicate = edge_tts.Communicate(commentary_text, voice_id)
        await communicate.save(audio_path)

        # Play the generated MP3 straight into the PulseAudio Virtual Speaker
        subprocess.run(
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", audio_path],
            check=False
        )

        await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main_loop())
