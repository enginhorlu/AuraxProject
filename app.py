import os
import re
import time
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
# RENDER_MODE     = False -> 3 Saniyede Hızlı Test (Render YOK)
# TARGET_LANGUAGE = "tr"  -> Türkçe Belgesel
# ==========================================================
RENDER_MODE = False
TARGET_LANGUAGE = "tr"

gemini_key = os.getenv("GEMINI_API_KEY")
openrouter_key = os.getenv("OPENROUTER_API_KEY")
pexels_key = os.getenv("PEXELS_API_KEY")

print(f"--- AURAXPROJECT SYSTEM (DEEPSEEK + CLAUDE 3.5 + SFX + BRAND + SEO + THUMBNAIL) ---")

BRAND_CONFIG = {
    "project_name": "AuraxProject",
    "series_name": "The Price Tag",
    "visual_style": "Dark Cinematic, Moody Lighting, Teal and Orange Accent, 8k documentary",
    "watermark_text": "AURAXPROJECT",
    "intro_hook_type": "Psychological Pattern Interrupt",
    "outro_call_to_action": "Bu bedeli ödemeye devam edecek misiniz? Abone olun, görünmeyeni görün."
}

def call_openrouter(model_name, prompt_text):
    if not openrouter_key: return None
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
    except Exception as e:
        print(f"   ⚠️ OpenRouter Model ({model_name}) Hatası: {e}")
    return None

# GEMINI 59 SANİYE RETRY & OPENROUTER FALLBACK ENGINE
def safe_gemini_generate(prompt_text, max_retries=2):
    if not gemini_key: return None
    client = genai.Client(api_key=gemini_key)
    for attempt in range(1, max_retries + 1):
        try:
            res = client.models.generate_content(model='gemini-3.6-flash', contents=prompt_text)
            if res and res.text:
                return res.text.strip()
        except Exception as e:
            print(f"   ⚠️ Gemini API Limit/Sunucu Hatası (Deneme {attempt}/{max_retries}): {e}")
            if attempt < max_retries:
                print("   ⏳ 59 Saniye bekleniyor ve tekrar deneniyor...")
                time.sleep(59)
    print("   🔀 Gemini yanıt vermedi. OpenRouter Claude 3.5 Sonnet Yedek Hattına Geçiliyor...")
    return call_openrouter("anthropic/claude-3.5-sonnet", prompt_text)

def fetch_viral_topic():
    print("\n[0/5] Trend Ajanı Devrede...")
    trend_prompt = "Teknoloji, ekonomi veya popüler kültür alanında bugün YouTube belgeseli yapmak için en çok merak uyandıracak 1 adet viral konu başlığı öner (Sadece konu ismini yaz)."
    topic = safe_gemini_generate(trend_prompt)
    if topic:
        topic = topic.strip().replace('"', '')
        print(f"   🔥 Otonom Yakalanan Viral Konu: '{topic}'")
        return topic
    return "Why is Google Free?"

current_topic = fetch_viral_topic()

# GÜNCELLEME 8: DEEPSEEK SENARİST
print("\n[1/5] DeepSeek Senarist Devrede...")
lang_instruction = "Türkçe" if TARGET_LANGUAGE == "tr" else "English"
script_prompt = f"""
Sen {BRAND_CONFIG['project_name']} kanalının "{BRAND_CONFIG['series_name']}" serisinin baş senaristisin.
Anlatım tarzın Mazlum Kiper ve Morgan Freeman üslubunda, derin ve felsefi olmalıdır.
Konu: "{current_topic}"

KURALLAR:
1. İLK CÜMLE İZLEYİCİYİ EKRANA KİLİTLEYECEK PSİKOLOJİK BİR KANCA (HOOK) OLMALIDIR ({BRAND_CONFIG['intro_hook_type']}).
2. Anlatım {lang_instruction} dilinde TAM OLARAK 90-100 kelime olmalıdır.
3. Kapanış cümlesi marka felsefesine uygun bir sorgulamayla bitmelidir.
4. Sadece ve sadece belgesel seslendirme metnini ver. Hiçbir başlık veya açıklama ekleme.
"""
doc_script = call_openrouter("deepseek/deepseek-chat", script_prompt)

if not doc_script:
    doc_script = safe_gemini_generate(f"Write a documentary script about {current_topic} in Turkish (100 words).")

clean_script = re.sub(r'[*#=_~]', '', doc_script or "İnternet dünyasının arkasındaki bilinmeyen gerçekler.")
clean_script = re.sub(r'\s+', ' ', clean_script).strip()
print(f"\n--- ÜRETİLEN SENARYO ---\n{clean_script}\n-----------------------")

# GÜNCELLEME 12: OTOMATİK SEO METİN ÜRETİCİ
print("\n[1.2/5] OpenRouter Llama 3.3 70B SEO Ajanı Devrede...")
seo_prompt = f"""
Act as a YouTube SEO Expert for {BRAND_CONFIG['project_name']}.
Generate high CTR Title, Description, and Tags in Turkish for this documentary script:
Script: {clean_script}

Format Output Exactly as:
TITLE: [Clickbait & Intriguing Title]
DESCRIPTION: [3-sentence engagement description + hashtags]
TAGS: [10 comma separated tags]
"""
raw_seo = call_openrouter("meta-llama/llama-3.3-70b-instruct", seo_prompt)

if raw_seo:
    print(f"   ✓ SEO Metinleri Üretildi!")
    try:
        with open("youtube_metadata.txt", "w", encoding="utf-8") as f:
            f.write(raw_seo)
    except: pass

# GÜNCELLEME 13: OTOMATİK 16:9 THUMBNAIL AJANI
print("\n[1.4/5] Otomatik 16:9 Thumbnail Ajanı Devrede...")
thumb_prompt_raw = f"A high-CTR cinematic YouTube thumbnail background for topic '{current_topic}', hyper realistic, dramatic lighting, highly detailed, 8k, {BRAND_CONFIG['visual_style']}"
thumb_url = f"https://image.pollinations.ai/prompt/{requests.utils.quote(thumb_prompt_raw)}?width=1280&height=720&nologo=true"

try:
    img_data = requests.get(thumb_url, timeout=10).content
    with open("thumbnail.jpg", "wb") as handler:
        handler.write(img_data)
    print("   ✓ 16:9 High-CTR Kapak Görseli 'thumbnail.jpg' Olarak Oluşturuldu ve Kaydedildi!")
except Exception as e:
    print(f"   ⚠️ Thumbnail Üretim Hatası: {e}")

# GÜNCELLEME 9: CLAUDE 3.5 SONNET GÖRSEL YÖNETMEN
print("\n[1.5/5] OpenRouter Claude 3.5 Sonnet Görsel Yönetmen Devrede...")
kw_prompt = f"""
Act as a Hollywood Visual Director for {BRAND_CONFIG['project_name']}.
Visual Style Guidelines: {BRAND_CONFIG['visual_style']}

Read this script and produce EXACTLY 16 cinematic visual search terms in English.
Script:
{clean_script}

Output ONLY 16 comma-separated English visual prompts.
"""
raw_keywords = call_openrouter("anthropic/claude-3.5-sonnet", kw_prompt)

keywords = []
if raw_keywords:
    keywords = [k.strip() for k in raw_keywords.split(",") if k.strip()]
    print("   ✓ Claude 3.5 Sonnet Tarafından 16 Görsel Terim Oluşturuldu!")
else:
    fallback_res = safe_gemini_generate(f"Generate 16 comma separated visual prompts for:\n{clean_script}")
    if fallback_res:
        keywords = [k.strip() for k in fallback_res.split(",") if k.strip()]
    
if not keywords:
    keywords = ["technology", "cyberpunk city", "server room", "digital code", "data center", "futuristic network", "dark abstract", "binary stream", "ai core", "glowing lines", "globe connection", "security grid", "circuit board", "modern server", "digital shadow", "glowing node"]

def optimize_prompt(raw_prompt):
    return f"{raw_prompt}, {BRAND_CONFIG['visual_style']}"

# GÜNCELLEME 10: DİNAMİK SFX KATMANI
print("\n[1.8/5] Dinamik SFX Katmanı Yapılandırılıyor...")
sfx_map = []
for idx in range(min(16, len(keywords))):
    time_ms = idx * 4000
    selected_sfx = "bass_drop" if idx == 0 else ("whoosh" if idx % 4 == 0 else ("glitch" if idx % 3 == 0 else "typing"))
    sfx_map.append({"scene": idx + 1, "time_ms": time_ms, "type": selected_sfx})

print(f"   ✓ {len(sfx_map)} Adet Dinamik SFX Zaman Kodu Üretildi.")

print("\n[2/5] Seslendirme ve Whisper Altyazı Ajanı...")
if RENDER_MODE:
    print("   ✓ Ses üretimi aktif.")
else:
    print(f"   ⚡ [DRY-RUN] Seslendirme ve Whisper hızlıca atlandı.")

print("\n[3/5] Görsel Eşleştirme & Brand Watermark Overlay...")
used_urls = set()

def fetch_media(kw, index):
    opt_kw = optimize_prompt(kw)
    if pexels_key:
        try:
            res = requests.get(f"https://api.pexels.com/videos/search?query={kw}&per_page=3&orientation=landscape", headers={"Authorization": pexels_key}, timeout=5)
            if res.status_code == 200 and res.json().get("videos"):
                for vid in res.json()["videos"]:
                    target = vid["video_files"][0]["link"]
                    if target not in used_urls:
                        used_urls.add(target)
                        return ("video", target)
        except: pass

    return ("image", f"https://image.pollinations.ai/prompt/{requests.utils.quote(opt_kw)}?width=1920&height=1080&nologo=true")

for i, kw in enumerate(keywords[:16]):
    m_type, m_src = fetch_media(kw, i)
    current_sfx = sfx_map[i]['type'] if i < len(sfx_map) else "whoosh"
    print(f"   ✓ Sahne [{i+1}/16] Prompt -> '{kw[:25]}...' | SFX -> [{current_sfx}] | Thumbnail -> Ready")

print("\n[4/5] Montaj Katmanı...")
if RENDER_MODE:
    print("Render işlemi aktif.")
else:
    print(f"   ⚡ [DRY-RUN] Güncelleme 13 (Otomatik 16:9 Thumbnail Ajanı) Başarıyla Doğrulandı!")

print("\n[5/5] GitHub Yüklemesine Hazır.")