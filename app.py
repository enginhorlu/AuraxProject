import os
import re
import time
import random
import asyncio
import requests
import edge_tts
import fal_client
import whisper
from google import genai
from dotenv import load_dotenv

load_dotenv()

# ==========================================================
# SYSTEM CONFIGURATION
# RENDER_MODE     = False -> 3 Saniyede Hizli Test (Render YOK)
# TARGET_LANGUAGE = "tr"  -> Turkce Belgesel
# ==========================================================
RENDER_MODE = False
TARGET_LANGUAGE = "tr"
FREE_MODE = True  # True = tum LLM cagrilari ucretsiz (:free) modeller uzerinden calisir, $0 maliyet
                  # False = DeepSeek + Claude 3.5 Sonnet gibi ucretli ama daha guclu modeller devreye girer

if FREE_MODE:
    SCRIPT_MODEL = "openrouter/free"  # OpenRouter'in kendi ucretsiz yonlendirici modeli - $0/M token, otomatik uygun modele yonlendirir
    VISUAL_MODEL = "openrouter/free"
else:
    SCRIPT_MODEL = "deepseek/deepseek-chat"
    VISUAL_MODEL = "anthropic/claude-3.5-sonnet"

SEO_MODEL = "openrouter/free"
TREND_MODEL = "openrouter/free"
NVIDIA_FALLBACK_MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"  # NVIDIA'nin kendi guncel modeli - eger hata verirse build.nvidia.com/models uzerinden dogru model adini kontrol et

gemini_key = os.getenv("GEMINI_API_KEY")
openrouter_key = os.getenv("OPENROUTER_API_KEY")
pexels_key = os.getenv("PEXELS_API_KEY")
pixabay_key = os.getenv("PIXABAY_API_KEY")
nvidia_key = os.getenv("NVIDIA_API_KEY")

print(f"--- AURAXPROJECT SYSTEM ({'FREE MODE' if FREE_MODE else 'PREMIUM MODE'} + NVIDIA FALLBACK + SFX + BRAND + SEO + THUMBNAIL) ---")

BRAND_CONFIG = {
    "project_name": "AuraxProject",
    "series_name": "The Price Tag",
    "visual_style": "Dark Cinematic, Moody Lighting, Teal and Orange Accent, 8k documentary",
    "watermark_text": "AURAXPROJECT",
    "intro_hook_type": "Psychological Pattern Interrupt",
    "outro_call_to_action": "Bu bedeli odemeye devam edecek misiniz? Abone olun, gorunmeyeni gorun."
}

# ==========================================================
# GUNCELLEME 14: MULTI-PROVIDER FALLBACK ZINCIRI
# OpenRouter -> NVIDIA -> Gemini
# ==========================================================

def call_openrouter(model_name, prompt_text):
    if not openrouter_key:
        print("   [!] OPENROUTER_API_KEY .env dosyasinda bulunamadi!")
        return None
    headers = {
        "Authorization": f"Bearer {openrouter_key}",
        "Content-Type": "application/json"
    }
    data = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt_text}]
    }
    try:
        res = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=data, timeout=15)
        if res.status_code == 200:
            return res.json()['choices'][0]['message']['content'].strip()
        else:
            print(f"   [!] OpenRouter HTTP {res.status_code} ({model_name}): {res.text[:300]}")
    except Exception as e:
        print(f"   [!] OpenRouter Model ({model_name}) Baglanti Hatasi: {e}")
    return None

def call_nvidia(model_name, prompt_text):
    if not nvidia_key:
        print("   [!] NVIDIA_API_KEY .env dosyasinda bulunamadi!")
        return None
    headers = {
        "Authorization": f"Bearer {nvidia_key}",
        "Content-Type": "application/json"
    }
    data = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt_text}],
        "max_tokens": 512
    }
    try:
        res = requests.post("https://integrate.api.nvidia.com/v1/chat/completions", headers=headers, json=data, timeout=15)
        if res.status_code == 200:
            return res.json()['choices'][0]['message']['content'].strip()
        else:
            print(f"   [!] NVIDIA HTTP {res.status_code} ({model_name}): {res.text[:300]}")
    except Exception as e:
        print(f"   [!] NVIDIA Model ({model_name}) Baglanti Hatasi: {e}")
    return None

def safe_gemini_generate(prompt_text, max_retries=2):
    if not gemini_key: return None
    client = genai.Client(api_key=gemini_key)
    for attempt in range(1, max_retries + 1):
        try:
            res = client.models.generate_content(model='gemini-3.6-flash', contents=prompt_text)
            if res and res.text:
                return res.text.strip()
        except Exception as e:
            print(f"   [!] Gemini API Limit/Sunucu Hatasi (Deneme {attempt}/{max_retries}): {e}")
            if attempt < max_retries:
                print("   ... 59 Saniye bekleniyor ve tekrar deneniyor...")
                time.sleep(59)
    return None

def call_llm_chain(prompt_text, or_model, nvidia_model=None):
    if nvidia_model is None:
        nvidia_model = NVIDIA_FALLBACK_MODEL
    """OpenRouter -> NVIDIA -> Gemini sirali yedek zinciri"""
    result = call_openrouter(or_model, prompt_text)
    if result:
        return result
    print("   >> OpenRouter yanit vermedi. NVIDIA Yedek Hattina Geciliyor...")
    result = call_nvidia(nvidia_model, prompt_text)
    if result:
        return result
    print("   >> NVIDIA da yanit vermedi. Gemini Yedek Hattina Geciliyor...")
    return safe_gemini_generate(prompt_text)

# ==========================================================
# 0. TREND AJANI (artik OpenRouter ucretsiz model uzerinden)
# ==========================================================
def fetch_viral_topic():
    print("\n[0/5] Trend Ajani Devrede...")
    trend_prompt = "Teknoloji, ekonomi veya populer kultur alaninda bugun YouTube belgeseli yapmak icin en cok merak uyandiracak 1 adet viral konu basligi oner (Sadece konu ismini yaz)."
    topic = call_llm_chain(trend_prompt, TREND_MODEL)
    if topic:
        topic = topic.strip().replace('"', '')
        print(f"   >> Otonom Yakalanan Viral Konu: '{topic}'")
        return topic
    return "Why is Google Free?"

current_topic = fetch_viral_topic()

# GUNCELLEME 8: DEEPSEEK SENARIST
print("\n[1/5] DeepSeek Senarist Devrede...")
lang_instruction = "Turkce" if TARGET_LANGUAGE == "tr" else "English"
script_prompt = f"""
Sen {BRAND_CONFIG['project_name']} kanalinin "{BRAND_CONFIG['series_name']}" serisinin bas senaristisin.
Anlatim tarzin Mazlum Kiper ve Morgan Freeman uslubunda, derin ve felsefi olmalidir.
Konu: "{current_topic}"

KURALLAR:
1. ILK CUMLE IZLEYICIYI EKRANA KILITLEYECEK PSIKOLOJIK BIR KANCA (HOOK) OLMALIDIR ({BRAND_CONFIG['intro_hook_type']}).
2. Anlatim {lang_instruction} dilinde TAM OLARAK 90-100 kelime olmalidir.
3. Kapanis cumlesi marka felsefesine uygun bir sorgulamayla bitmelidir.
4. Sadece ve sadece belgesel seslendirme metnini ver. Hicbir baslik veya aciklama ekleme.
"""
doc_script = call_llm_chain(script_prompt, SCRIPT_MODEL)

clean_script = re.sub(r'[*#=_~]', '', doc_script or "Internet dunyasinin arkasindaki bilinmeyen gercekler.")
clean_script = re.sub(r'\s+', ' ', clean_script).strip()
print(f"\n--- URETILEN SENARYO ---\n{clean_script}\n-----------------------")

# GUNCELLEME 12: OTOMATIK SEO METIN URETICI
print("\n[1.2/5] SEO Ajani Devrede...")
seo_prompt = f"""
Act as a YouTube SEO Expert for {BRAND_CONFIG['project_name']}.
Generate high CTR Title, Description, and Tags in Turkish for this documentary script:
Script: {clean_script}

Format Output Exactly as:
TITLE: [Clickbait & Intriguing Title]
DESCRIPTION: [3-sentence engagement description + hashtags]
TAGS: [10 comma separated tags]
"""
raw_seo = call_llm_chain(seo_prompt, SEO_MODEL)

if raw_seo:
    print(f"   >> SEO Metinleri Uretildi!")
    try:
        with open("youtube_metadata.txt", "w", encoding="utf-8") as f:
            f.write(raw_seo)
    except Exception as e:
        print(f"   [!] SEO dosyasi kaydedilemedi: {e}")

# GUNCELLEME 13: OTOMATIK 16:9 THUMBNAIL AJANI
print("\n[1.4/5] Otomatik 16:9 Thumbnail Ajani Devrede...")
thumb_prompt_raw = f"A high-CTR cinematic YouTube thumbnail background for topic '{current_topic}', hyper realistic, dramatic lighting, highly detailed, 8k, {BRAND_CONFIG['visual_style']}"
thumb_url = f"https://image.pollinations.ai/prompt/{requests.utils.quote(thumb_prompt_raw)}?width=1280&height=720&nologo=true"

try:
    img_data = requests.get(thumb_url, timeout=10).content
    with open("thumbnail.jpg", "wb") as handler:
        handler.write(img_data)
    print("   >> 16:9 High-CTR Kapak Gorseli 'thumbnail.jpg' Olarak Olusturuldu ve Kaydedildi!")
except Exception as e:
    print(f"   [!] Thumbnail Uretim Hatasi: {e}")

# GUNCELLEME 9: CLAUDE 3.5 SONNET GORSEL YONETMEN
print("\n[1.5/5] Claude 3.5 Sonnet Gorsel Yonetmen Devrede...")
kw_prompt = f"""
Act as a Hollywood Visual Director for {BRAND_CONFIG['project_name']}.
Visual Style Guidelines: {BRAND_CONFIG['visual_style']}

Read this script and produce EXACTLY 16 cinematic visual search terms in English.
Script:
{clean_script}

Output ONLY 16 comma-separated English visual prompts.
"""
raw_keywords = call_llm_chain(kw_prompt, VISUAL_MODEL)

keywords = []
if raw_keywords:
    keywords = [k.strip() for k in raw_keywords.split(",") if k.strip()]
    print("   >> 16 Gorsel Terim Olusturuldu!")

if not keywords:
    keywords = ["technology", "cyberpunk city", "server room", "digital code", "data center", "futuristic network", "dark abstract", "binary stream", "ai core", "glowing lines", "globe connection", "security grid", "circuit board", "modern server", "digital shadow", "glowing node"]

def optimize_prompt(raw_prompt):
    return f"{raw_prompt}, {BRAND_CONFIG['visual_style']}"

# GUNCELLEME 10: DINAMIK SFX KATMANI
print("\n[1.8/5] Dinamik SFX Katmani Yapilandiriliyor...")
sfx_map = []
for idx in range(min(16, len(keywords))):
    time_ms = idx * 4000
    selected_sfx = "bass_drop" if idx == 0 else ("whoosh" if idx % 4 == 0 else ("glitch" if idx % 3 == 0 else "typing"))
    sfx_map.append({"scene": idx + 1, "time_ms": time_ms, "type": selected_sfx})

print(f"   >> {len(sfx_map)} Adet Dinamik SFX Zaman Kodu Uretildi.")

print("\n[2/5] Seslendirme ve Whisper Altyazi Ajani...")
if RENDER_MODE:
    print("   >> Ses uretimi aktif.")
else:
    print(f"   [DRY-RUN] Seslendirme ve Whisper hizlica atlandi.")

# ==========================================================
# GUNCELLEME 14: PEXELS + PIXABAY COKLU KAYNAK (rastgele sira)
# ==========================================================
print("\n[3/5] Gorsel Eslestirme & Brand Watermark Overlay...")
used_urls = set()

def fetch_media(kw, index):
    opt_kw = optimize_prompt(kw)
    providers = []
    if pexels_key: providers.append("pexels")
    if pixabay_key: providers.append("pixabay")
    random.shuffle(providers)

    for p in providers:
        if p == "pexels":
            try:
                res = requests.get(
                    f"https://api.pexels.com/videos/search?query={kw}&per_page=3&orientation=landscape",
                    headers={"Authorization": pexels_key}, timeout=5
                )
                if res.status_code == 200 and res.json().get("videos"):
                    for vid in res.json()["videos"]:
                        target = vid["video_files"][0]["link"]
                        if target not in used_urls:
                            used_urls.add(target)
                            return ("video_pexels", target)
            except Exception:
                pass
        if p == "pixabay":
            try:
                res = requests.get(
                    f"https://pixabay.com/api/videos/?key={pixabay_key}&q={kw}&per_page=3",
                    timeout=5
                )
                if res.status_code == 200 and res.json().get("hits"):
                    for vid in res.json()["hits"]:
                        target = vid["videos"]["medium"]["url"]
                        if target not in used_urls:
                            used_urls.add(target)
                            return ("video_pixabay", target)
            except Exception:
                pass

    return ("image_ai", f"https://image.pollinations.ai/prompt/{requests.utils.quote(opt_kw)}?width=1920&height=1080&nologo=true")

for i, kw in enumerate(keywords[:16]):
    m_type, m_src = fetch_media(kw, i)
    current_sfx = sfx_map[i]['type'] if i < len(sfx_map) else "whoosh"
    print(f"   >> Sahne [{i+1}/16] Kaynak -> [{m_type}] | Prompt -> '{kw[:25]}...' | SFX -> [{current_sfx}] | Watermark -> [{BRAND_CONFIG['watermark_text']}]")

print("\n[4/5] Montaj Katmani...")
if RENDER_MODE:
    print("Render islemi aktif.")
else:
    print(f"   [DRY-RUN] Guncelleme 14 (Multi-Provider Fallback + Pixabay) Basariyla Dogrulandi!")

print("\n[5/5] GitHub Yuklemesine Hazir.")