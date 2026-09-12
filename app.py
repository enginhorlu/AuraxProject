import os
import re
import sys
import time
import random
import math
import json
import asyncio
import subprocess
import difflib
import requests
import numpy as np
from datetime import datetime, timedelta
import edge_tts
import fal_client
import whisper
from google import genai
from dotenv import load_dotenv

load_dotenv()

# GUNCELLEME 51: Terminal kapanirsa/PC kapatilirsa cikti kayboluyordu. Artik her
# calistirmada tum terminal ciktisi AYNI ZAMANDA "last_run_log.txt" dosyasina da
# yaziliyor - pencereyi kapatsaniz bile klasorde son calistirmanin tam kaydi kalir.
class _TeeOutput:
    def __init__(self, *streams):
        self.streams = streams
    def write(self, data):
        for s in self.streams:
            s.write(data)
            s.flush()
    def flush(self):
        for s in self.streams:
            s.flush()

_log_file = open("last_run_log.txt", "w", encoding="utf-8")
sys.stdout = _TeeOutput(sys.stdout, _log_file)
sys.stderr = _TeeOutput(sys.stderr, _log_file)

try:
    import imageio_ffmpeg
    import shutil
    _src_ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    _bin_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_ffmpeg_bin")
    os.makedirs(_bin_dir, exist_ok=True)
    _dst_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    _dst_ffmpeg = os.path.join(_bin_dir, _dst_name)
    if not os.path.exists(_dst_ffmpeg):
        shutil.copy(_src_ffmpeg, _dst_ffmpeg)
    os.environ["PATH"] = _bin_dir + os.pathsep + os.environ.get("PATH", "")
except Exception as e:
    print(f"   [!] FFmpeg hazirlama uyarisi: {e}")

RENDER_MODE = True
MANUAL_REVIEW = True  # GUNCELLEME 52: True iken uretimden once/sonra durup terim ve gorsel onayi ister
TARGET_LANGUAGE = "tr"
FREE_MODE = True

SCRIPT_MODEL = "deepseek/deepseek-chat"
VISUAL_MODEL = "anthropic/claude-sonnet-4.6"
SEO_MODEL = "openrouter/free"
TREND_MODEL = "deepseek/deepseek-chat"
NVIDIA_FALLBACK_MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"  # su an zincirde kullanilmiyor, ileride tekrar denemek icin duruyor

gemini_key = os.getenv("GEMINI_API_KEY")
openrouter_key = os.getenv("OPENROUTER_API_KEY")
pexels_key = os.getenv("PEXELS_API_KEY")
pixabay_key = os.getenv("PIXABAY_API_KEY")
nvidia_key = os.getenv("NVIDIA_API_KEY")
jamendo_client_id = os.getenv("JAMENDO_CLIENT_ID")
leonardo_api_key = os.getenv("LEONARDO_API_KEY")
LEONARDO_PHOENIX_MODEL_ID = "de7d3faf-762f-48e0-b3b7-9d0ac3a3fcf3"

if leonardo_api_key:
    print(f"   >> Leonardo API key yuklendi ({len(leonardo_api_key)} karakter).")
else:
    print("   [!] LEONARDO_API_KEY .env dosyasinda bulunamadi! Leonardo gorselleri uretilemeyecek, Pollinations'a dusulecek.")

def get_openrouter_credit_info():
    if not openrouter_key:
        return None
    headers = {"Authorization": f"Bearer {openrouter_key}"}
    try:
        res = requests.get("https://openrouter.ai/api/v1/key", headers=headers, timeout=10)
        if res.status_code == 200:
            return res.json().get("data", {})
    except Exception as e:
        print(f"   [!] OpenRouter kredi bilgisi alinamadi: {e}")
    return None

_or_baseline_info = get_openrouter_credit_info()
_or_baseline_usage = _or_baseline_info.get("usage") if _or_baseline_info else None
if _or_baseline_info:
    _rem = _or_baseline_info.get("limit_remaining")
    print(f"   >> OpenRouter (baslangic) - Toplam kullanim: ${_or_baseline_info.get('usage', 0):.4f}" +
          (f" | Kalan kredi: ${_rem:.4f}" if _rem is not None else " | Kalan kredi: sinirsiz/ucretsiz"))

print(f"--- AURAXPROJECT SYSTEM ({'FREE MODE' if FREE_MODE else 'PREMIUM MODE'} + SFX + BRAND + SEO + THUMBNAIL) ---")

BRAND_CONFIG = {
    "project_name": "AuraxProject",
    "series_name": "The Price Tag",
    "visual_style": "Kodak Ektar 100 vivid color film look, rich saturated warm tones, soft directional natural light with dappled shadow patterns through blinds or foliage, visually striking attractive people, sharp detailed eyes, symmetrical well-defined facial features, natural candid unposed behavior, genuine warm radiant expression, energetic happy lively demeanor, natural skin texture, high quality editorial photography, realistic, 35mm lens",
    "watermark_text": "AURAXPROJECT",
    "intro_hook_type": "Psychological Pattern Interrupt",
    "outro_call_to_action": "Bu bedeli odemeye devam edecek misiniz? Abone olun, gorunmeyeni gorun.",
    "music_mood": "downtempo chillout instrumental calm"
}

# GUNCELLEME 53: MANUAL_IMAGES_DIR artik burada, dosyanin basinda tanimlaniyor -
# asagidaki oturum (session) devam ettirme kontrolunun bu klasoru okuyabilmesi icin.
MANUAL_IMAGES_DIR = "manual_images"
os.makedirs(MANUAL_IMAGES_DIR, exist_ok=True)

# GUNCELLEME 53: KALDIGI YERDEN DEVAM SISTEMI - Checkpoint A'dan hemen sonra
# senaryo/segment/terim/hareket bilgisi session.json'a yazilir. PC kapanip acilsa
# bile bu dosya + manual_images klasorundeki gorseller sayesinde program nereden
# devam edecegini kendi kendine anlar. Video basariyla render edilince silinir.
SESSION_FILE = "session.json"

def load_session():
    if not os.path.exists(SESSION_FILE):
        return None
    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def save_session(data):
    try:
        with open(SESSION_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"   [!] Oturum kaydedilemedi: {e}")

def delete_session():
    try:
        if os.path.exists(SESSION_FILE):
            os.remove(SESSION_FILE)
    except Exception:
        pass

def slugify_topic(text, max_len=40):
    slug = re.sub(r'[^\w\s-]', '', text or "video", flags=re.UNICODE).strip().lower()
    slug = re.sub(r'[\s]+', '_', slug)
    return slug[:max_len] if slug else "video"

def call_openrouter(model_name, prompt_text):
    if not openrouter_key:
        print("   [!] OPENROUTER_API_KEY .env dosyasinda bulunamadi!")
        return None
    headers = {"Authorization": f"Bearer {openrouter_key}", "Content-Type": "application/json"}
    data = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt_text}],
        "max_tokens": 1500
    }
    try:
        res = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=data, timeout=15)
        if res.status_code == 200:
            content_text = res.json()['choices'][0]['message'].get('content')
            if content_text:
                return content_text.strip()
            print(f"   [!] OpenRouter bos yanit dondurdu ({model_name})")
        else:
            print(f"   [!] OpenRouter HTTP {res.status_code} ({model_name}): {res.text[:300]}")
    except Exception as e:
        print(f"   [!] OpenRouter Model ({model_name}) Baglanti Hatasi: {e}")
    return None

def call_nvidia(model_name, prompt_text):
    # Su an fallback zincirinde kullanilmiyor (surekli timeout veriyordu),
    # ileride ayri bir amac icin tekrar denemek uzere fonksiyon burada birakildi.
    if not nvidia_key:
        print("   [!] NVIDIA_API_KEY .env dosyasinda bulunamadi!")
        return None
    headers = {"Authorization": f"Bearer {nvidia_key}", "Content-Type": "application/json"}
    data = {"model": model_name, "messages": [{"role": "user", "content": prompt_text}], "max_tokens": 512}
    try:
        res = requests.post("https://integrate.api.nvidia.com/v1/chat/completions", headers=headers, json=data, timeout=15)
        if res.status_code == 200:
            content_text = res.json()['choices'][0]['message'].get('content')
            if content_text:
                return content_text.strip()
            print(f"   [!] NVIDIA bos yanit dondurdu ({model_name})")
        else:
            print(f"   [!] NVIDIA HTTP {res.status_code} ({model_name}): {res.text[:300]}")
    except Exception as e:
        print(f"   [!] NVIDIA Model ({model_name}) Baglanti Hatasi: {e}")
    return None

def safe_gemini_generate(prompt_text, max_retries=1):
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

# GUNCELLEME 38: NVIDIA zincirden cikarildi (surekli timeout veriyordu).
# Yeni sira: OpenRouter -> Gemini (2 asamali, daha hizli).
def call_llm_chain(prompt_text, or_model, nvidia_model=None, prefer_gemini_first=False):
    if prefer_gemini_first:
        result = safe_gemini_generate(prompt_text)
        if result:
            return result
        print("   >> Gemini yanit vermedi. OpenRouter'a Geciliyor...")
        return call_openrouter(or_model, prompt_text)
    else:
        result = call_openrouter(or_model, prompt_text)
        if result:
            return result
        print("   >> OpenRouter yanit vermedi. Gemini Yedek Hattina Geciliyor...")
        return safe_gemini_generate(prompt_text)

def parse_topic_options(raw):
    options = []
    if raw:
        for line in raw.split("\n"):
            line = line.strip()
            m = re.match(r'^\d+[:.\)]\s*(.+)$', line)
            if m:
                topic = m.group(1).strip().replace('"', '')
                if topic:
                    options.append(topic)
    return options

# GUNCELLEME 46: KONU GECMISI HAFIZASI - gosterilen 5 secenegin TAMAMI (sadece
# secilen degil), 14 gunluk bir pencerede tekrar onerilmesin diye kaydediliyor.
TOPIC_HISTORY_FILE = "topic_history.json"
TOPIC_HISTORY_DAYS = 14

def load_recent_topic_history(days=TOPIC_HISTORY_DAYS):
    if not os.path.exists(TOPIC_HISTORY_FILE):
        return []
    try:
        with open(TOPIC_HISTORY_FILE, "r", encoding="utf-8") as f:
            entries = json.load(f)
    except Exception:
        return []
    cutoff = datetime.now() - timedelta(days=days)
    recent = []
    for entry in entries:
        try:
            entry_date = datetime.fromisoformat(entry.get("date", ""))
            if entry_date >= cutoff:
                recent.append(entry.get("topic", ""))
        except Exception:
            continue
    return [t for t in recent if t]

def append_topics_to_history(topics):
    entries = []
    if os.path.exists(TOPIC_HISTORY_FILE):
        try:
            with open(TOPIC_HISTORY_FILE, "r", encoding="utf-8") as f:
                entries = json.load(f)
        except Exception:
            entries = []
    today = datetime.now().isoformat()
    for topic in topics:
        entries.append({"date": today, "topic": topic})
    # Cok eski kayitlari da temizleyelim, dosya sonsuza kadar buyumesin
    cutoff = datetime.now() - timedelta(days=TOPIC_HISTORY_DAYS * 3)
    cleaned = []
    for entry in entries:
        try:
            if datetime.fromisoformat(entry.get("date", "")) >= cutoff:
                cleaned.append(entry)
        except Exception:
            continue
    try:
        with open(TOPIC_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(cleaned, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"   [!] Konu gecmisi kaydedilemedi: {e}")

def fetch_viral_topic():
    print("\n[0/5] Trend Ajani Devrede...")
    recent_topics = load_recent_topic_history()
    avoid_block = ""
    if recent_topics:
        avoid_list = "\n".join(f"- {t}" for t in recent_topics)
        avoid_block = f"""

SON 14 GUNDE ZATEN GOSTERILEN KONULAR (bunlari veya cok benzer fikirleri KESINLIKLE tekrar onerme):
{avoid_list}
"""
    trend_prompt = f"""
Sen {BRAND_CONFIG['project_name']} kanali icin konu arastirmacisisin.
Kanalin TEK ve SABIT temasi: modern hayatin "gizli bedelleri" -- rahatlik, teknoloji, hiz veya kolaylik ugruna
odedigimiz ama fark etmedigimiz fiziksel, psikolojik, sosyal, ekonomik veya cevresel bedeller.

Ornek kategoriler (ilham icin, birebir kopyalama, benzer ruhta yeni bir konu bul):
Ucretsiz uygulamalarin bedeli, hizli modanin bedeli, aninda tatminin bedeli, kuresel tedarik zincirinin
insani maliyeti, yapay zeka kolayliginin bedeli, sosyal medyada onay aramanin bedeli, ucuz elektroniklerin
bedeli, 7/24 baglantili olmanin bedeli, endustriyel tarimin bedeli, kripto madenciliginin enerji bedeli,
bulut depolamanin bedeli, otomasyonun is gucune bedeli, dikkat ekonomisinin bedeli, dijital gozetlemenin bedeli.

Bu temanin SINIRLARI ICINDE kalarak, 5 adet ozgun ve merak uyandirici belgesel konusu oner.
Her konu MUTLAKA "X'in gizli/gorunmeyen bedeli" fikrini tasimali -- temanin disina asla cikma.
5 konu birbirinden farkli alt temalar kapsasin (ayni fikri tekrarlama).
{avoid_block}
TON KURALI (ONEMLI): Konu basliklarini "Merakli Kasif" tonunda yaz -- Kurzgesagt tarzi kanallar gibi,
ciddi konulari bile "vay be, bilmiyordum" hissiyle sunan, meraklandirici ve sasirtici bir kesif dili
kullan. "tuzak", "tehlike", "kayip", "yikim", "cigilik", "surgun", "yok olus", "karanlik sir",
"kabus" gibi alarmist/korku kelimelerinden KESINLIKLE kacin. Bunun yerine "X bize ne yapiyor?",
"X'in gercekte bize odettigi sey nedir?" tarzi sorgulayici ama tedirgin etmeyen bir cerceve kullan.
Ornek: "Yalnizlasan zihinler, kaybedilen farkindalik" DEGIL -- "Kulakligini taktigin an beynin
aslinda ne yapiyor?" gibi.

UZUNLUK VE ENERJI KURALI (ONEMLI): Her konu basligi KISA olmali -- en fazla 12-15 kelime, TEK carpici
cumle. Uzun, agdali, cok maddeli, alt cumlecikli aciklamali basliklardan KESINLIKLE kacin -- bunlar
enerjiyi olduruyor. Baslik, birine sozlu olarak merakla sorulan tek bir soru gibi okunmali, akademik
bir makale ozeti gibi degil.
Ornek COK UZUN (KULLANMA): "Muzik ve video platformlarinin algoritmayla kisisellestirdigi icerik
akislarina teslim oldugumuzda, beynimizin beklenmedik surprizlere verdigi norolojik tepkiler ve
kesif hissi nasil degisiyor?"
Ornek DOGRU UZUNLUK (KULLAN): "Spotify'in senin icin secim yapmasi, beynindeki surpriz hissini
nasil oldurur?"

ENERJI KURALI (ONEMLI, GUNCELLEME 53): Baslik okundugunda hissedilen tempo hafif, canli ve merak dolu
olmali -- agir, yorucu, kasvetli bir hava KESINLIKLE sezilmemeli. Sanki bir arkadasina heyecanla ilginc
bir sey anlatiyormussun gibi bir enerji tasimali, akademik bir sunum gibi degil.

Cevabini SADECE Turkce olarak ver, baska hicbir dilde yazma.
Cikti formati TAM OLARAK soyle olsun, baska hicbir sey ekleme:
1: <konu>
2: <konu>
3: <konu>
4: <konu>
5: <konu>
"""
    raw_options = call_llm_chain(trend_prompt, TREND_MODEL, prefer_gemini_first=True)
    options = parse_topic_options(raw_options)

    if len(options) < 3:
        print("   [!] Konu secenekleri yetersiz/bozuk gorunuyor, Gemini ile tekrar deneniyor...")
        retry_raw = safe_gemini_generate(trend_prompt)
        retry_options = parse_topic_options(retry_raw)
        if len(retry_options) > len(options):
            options = retry_options

    if not options:
        print("   [!] Hicbir konu secenegi uretilemedi, yedek konu kullanilacak.")
        return "Why is Google Free?"

    append_topics_to_history(options)

    print("\n   >> Asagidaki konulardan birini secin:\n")
    for i, opt in enumerate(options, start=1):
        print(f"      {i}) {opt}")

    chosen = None
    while chosen is None:
        raw_input_val = input(f"\n   Secim (1-{len(options)}): ").strip()
        if raw_input_val.isdigit() and 1 <= int(raw_input_val) <= len(options):
            chosen = options[int(raw_input_val) - 1]
        else:
            print(f"   [!] Gecersiz secim, 1 ile {len(options)} arasinda bir sayi girin.")

    print(f"   >> Secilen konu: '{chosen}'")
    return chosen

# GUNCELLEME 53: FALLBACK_TERM burada, ust seviyede tanimlaniyor - sanitize_keyword()
# fonksiyonu hem yeni uretimde hem de eski bir oturuma devam ederken cagrilabildigi icin
# sadece "yeni uretim" dalinda tanimli kalirsa oturuma devam ederken NameError olurdu.
FALLBACK_TERM = "abstract dark background with subtle motion"

# GUNCELLEME 53: Oturum kontrolu - yarim kalmis bir video var mi diye bakar.
resumed_session = None
_prev_session = load_session()
if _prev_session:
    _prev_topic = _prev_session.get("topic", "?")
    _prev_keywords = _prev_session.get("keywords", [])
    _done_count = 0
    for _i in range(len(_prev_keywords)):
        for _ext in ("jpg", "jpeg", "png", "mp4", "mov"):
            if os.path.exists(os.path.join(MANUAL_IMAGES_DIR, f"scene_{_i+1}.{_ext}")):
                _done_count += 1
                break
    print(f"\n   >> Yarim kalmis bir oturum bulundu: '{_prev_topic}' ({_done_count}/{len(_prev_keywords)} sahne gorseli hazir)")
    _resume_choice = input("   Bu oturuma devam edilsin mi? (E/H): ").strip().lower()
    if _resume_choice == "e":
        resumed_session = _prev_session
    else:
        print("   >> Yeni oturum baslatiliyor, eski oturum dosyasi silinecek.")
        delete_session()

if resumed_session:
    current_topic = resumed_session["topic"]
    clean_script = resumed_session["script"]
    segments = resumed_session["segments"]
    keywords = resumed_session["keywords"]
    animations = resumed_session.get("animations", ["" for _ in segments])
    audio_path = resumed_session["audio_path"]
    real_audio_duration = resumed_session.get("real_audio_duration")
    print(f"   >> Oturum yuklendi: '{current_topic}' ({len(segments)} sahne)")
else:
    current_topic = fetch_viral_topic()

    # GUNCELLEME 38: Mazlum Kiper/Morgan Freeman referansi kaldirildi, yeni
    # somut ton talimati eklendi -- merakli/sorgulayici ama kasvetli olmayan bir ses.
    print("\n[1/5] DeepSeek Senarist Devrede...")
    lang_instruction = "Turkce" if TARGET_LANGUAGE == "tr" else "English"
    script_prompt = f"""
Sen {BRAND_CONFIG['project_name']} kanalinin "{BRAND_CONFIG['series_name']}" serisinin bas senaristisin.
Anlatim tarzin: "Merakli Kasif" sesi -- zeki ve meraklandirici bir arkadas/gazeteci tonu, Kurzgesagt
tarzi kanallar gibi. Ciddi konulari durustce isle ama izleyiciyi "vay be, bilmiyordum" hissiyle birak,
"her sey berbat" hissiyle degil. Siirsel/dramatik metaforlardan ("ruhumuz soluyor", "teninin icinde
surgun" gibi asiri duygusal ifadelerden) kacinir; somut, gercekci gozlemlere ve merak uyandiran
sorulara dayanir. Dilin herkesin rahatca anlayacagi, gunluk konusma diline yakin, sicak bir sohbet
tonunda olmali -- akademik veya mesafeli degil. ESPIRI YAPMA -- hicbir sakacı yorum, esprili benzetme
veya "komik olmaya calisan" ifade kullanma. Ton merakli ve sicak olsun ama TAMAMEN ciddi/bilgilendirici
kal, mizah katma denemesi HIC OLMASIN.
Konu: "{current_topic}"

KURALLAR:
1. ILK CUMLE KURALI (ONEMLI): Izleyiciyi ekrana kilitleyecek psikolojik bir kanca (hook) olmali
   ({BRAND_CONFIG['intro_hook_type']}). "Dusunsene:", "Simdi bir dusun:", "Hic dusundun mu:" gibi
   KALIPLASMIS, tekrar eden acilis ifadelerini KESINLIKLE KULLANMA -- bunlar klise ve sikici. Bunun
   yerine, HER SEFERINDE FARKLI bir teknik kullan:
   (a) Canli bir mikro-sahne ile basla (kisa, somut bir an tarif et)
   (b) Sasirtici bir istatistigi dogrudan, sohbet diliyle actikla
   (c) Izleyiciye dogrudan, cesur bir soru sor (klise "dusundun mu" degil, daha spesifik/carpici)
   (d) Beklenmedik/cok bilinen bir inanca meydan okuyan cesur bir iddiayla ac
2. Anlatim {lang_instruction} dilinde TAM OLARAK 90-100 kelime olmalidir.
3. BENZETME KURALI (ONEMLI): Soyut veya teknik bir kavramdan bahsederken (ornek: "hipokampal
   haritalama", "algoritma", "bulut depolama") MUTLAKA canli, gundelik bir benzetmeyle somutlastir --
   ornek: "hipokampus kuculuyor" DEGIL, "beynindeki dahili GPS, kullanilmayan bir kas gibi kuculuyor"
   gibi. Ayni cumle icinde, terimin ne anlama geldigini de dogal bir sekilde aciklayarak ver -- izleyici
   bu terimleri bilmiyor olabilir, teknik bir kelime kullanirsan hemen ardindan sade bir aciklama ekle.
4. KAPANIS CUMLESI KURALI (ONEMLI): "Sence bunu fark ettin mi?", "Ne dusunuyorsun?" gibi jenerik/
   siradan sorulardan KESINLIKLE kacin. Bunun yerine, konuya OZEL, asagidaki 5 yaklasimdan RUH olarak
   esinlenen ama HARFİYEN KOPYALAMAYAN, her seferinde FARKLI bir kapanis uret:
   (a) Ifsa tarzi tatmin edici bir kapanis cumlesi (soru degil, "ve iste bu yuzden..." gibi)
   (b) Ileriye donuk merak tuzagi (baska gunluk bir seye isaret eden bir ipucu)
   (c) Oyunbaz dogrudan hitap (izleyiciyi eglenceli sekilde "tuzaga dusurme")
   (d) Retorik gerceklesme ani (cevap istemeyen bir "aha" cumlesi)
   (e) Meraki odullendiren davet (bir sonraki kesfe cagri, baski yapmadan)
   Kapanis MUTLAKA bu spesifik konunun icerigiyle dogrudan ilgili olmali, jenerik/herhangi bir videoya
   yapistirilabilecek bir cumle OLMAMALI.
5. NITELIK VE DERINLIK KURALI (ONEMLI, GUNCELLEME 53): Metin yuzeysel/genel-gecer cumlelerden olusmamali --
   somut sayilar, gercek mekanizmalar ("bu NASIL calisir", "bunun NEDENI ne") ve arastirilmis hissi veren
   spesifik detaylar icermeli. Herkesin zaten bildigi bir seyi farkli kelimelerle tekrar etmek YETERSIZ --
   izleyiciye "bunu bilmiyordum" dedirtecek somut bir bilgi veya mekanizma sun. Bu kural mizahtan (yukaridaki
   ESPIRI YAPMA kuralindan) tamamen ayridir -- ciddi ve sicak kal ama asla YUZEYSEL olma.
6. Sadece ve sadece belgesel seslendirme metnini ver. Hicbir baslik veya aciklama ekleme.
"""
    doc_script = call_llm_chain(script_prompt, SCRIPT_MODEL, prefer_gemini_first=False)

    def is_bad_script(text):
        if not text:
            return True
        if len(text.split()) < 20:
            return True
        lowered = text.lower()
        if "user safety" in lowered or "i cannot" in lowered or "i'm sorry" in lowered or "as an ai" in lowered:
            return True
        return False

    if is_bad_script(doc_script):
        print("   [!] Senaryo yaniti bozuk/eksik gorunuyor, Gemini ile tekrar deneniyor...")
        retry_script = safe_gemini_generate(script_prompt)
        if retry_script and not is_bad_script(retry_script):
            doc_script = retry_script
        else:
            print("   [!] Tekrar deneme de basarisiz oldu, yedek metin kullanilacak.")

    clean_script = re.sub(r'[*#=_~]', '', doc_script or "Internet dunyasinin arkasindaki bilinmeyen gercekler.")
    clean_script = re.sub(r'\s+', ' ', clean_script).strip()
    print(f"\n--- URETILEN SENARYO ---\n{clean_script}\n-----------------------")

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
    raw_seo = call_llm_chain(seo_prompt, SEO_MODEL, prefer_gemini_first=True)

    if raw_seo:
        print(f"   >> SEO Metinleri Uretildi!")
        try:
            with open("youtube_metadata.txt", "w", encoding="utf-8") as f:
                f.write(raw_seo)
        except Exception as e:
            print(f"   [!] SEO dosyasi kaydedilemedi: {e}")

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

    print("\n[2/5] Seslendirme Uretimi...")
    audio_path = "voiceover_tr.mp3" if TARGET_LANGUAGE == "tr" else "voiceover_en.mp3"
    voice_name = "tr-TR-AhmetNeural" if TARGET_LANGUAGE == "tr" else "en-US-GuyNeural"

    async def generate_voiceover():
        communicate = edge_tts.Communicate(clean_script, voice_name)
        await communicate.save(audio_path)

    whisper_segments_raw = []
    real_audio_duration = None
    if RENDER_MODE:
        try:
            asyncio.run(generate_voiceover())
            print(f"   >> Seslendirme uretildi: {audio_path}")
            from moviepy import AudioFileClip as _ProbeClip
            real_audio_duration = _ProbeClip(audio_path).duration
        except Exception as e:
            print(f"   [!] Seslendirme Hatasi: {e}")

        print("   >> Whisper ile konusma zaman damgalari cikariliyor...")
        try:
            whisper_model = whisper.load_model("base")
            whisper_lang = "tr" if TARGET_LANGUAGE == "tr" else "en"
            result = whisper_model.transcribe(audio_path, language=whisper_lang)
            whisper_segments_raw = result.get("segments", [])
            print(f"   >> Whisper {len(whisper_segments_raw)} dogal konusma segmenti buldu.")

            heard_text = " ".join(seg["text"].strip() for seg in whisper_segments_raw)
            def _normalize_words(text):
                return re.sub(r'[^\w\s]', '', text.lower()).split()
            original_words = _normalize_words(clean_script)
            heard_words = _normalize_words(heard_text)
            matcher = difflib.SequenceMatcher(None, original_words, heard_words)
            mismatches = []
            for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                if tag != "equal":
                    orig_part = " ".join(original_words[i1:i2])
                    heard_part = " ".join(heard_words[j1:j2])
                    if orig_part or heard_part:
                        mismatches.append((orig_part, heard_part))
            if mismatches:
                print("   [?] Telaffuz/tanima farki bulundu, kontrol etmek isteyebilirsin:")
                for orig_part, heard_part in mismatches[:8]:
                    print(f"      Yazilan: '{orig_part}'  ->  Whisper'in duydugu: '{heard_part}'")
            else:
                print("   >> Telaffuz kontrolu: fark bulunamadi, seslendirme senaryoyla tutarli.")
        except Exception as e:
            print(f"   [!] Whisper Hatasi: {e}")
    else:
        print(f"   [DRY-RUN] Seslendirme ve Whisper hizlica atlandi.")

    MAX_SEGMENT_LEN = 8.0  # GUNCELLEME 47: 6.0 -> 8.0, hem maliyet dusurmek (daha az sahne) hem efektlere nefes alani vermek icin

    def build_segments(whisper_segments, full_script, audio_duration=None):
        segments = []
        if whisper_segments:
            for seg in whisper_segments:
                start, end, text = seg["start"], seg["end"], seg["text"].strip()
                dur = end - start
                if dur <= 0 or not text:
                    continue
                if dur <= MAX_SEGMENT_LEN:
                    segments.append({"start": start, "end": end, "text": text})
                else:
                    n = max(1, int(dur // MAX_SEGMENT_LEN) + 1)
                    step = dur / n
                    for i in range(n):
                        segments.append({"start": start + i * step, "end": start + (i + 1) * step, "text": text})
        else:
            sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', full_script) if s.strip()]
            if not sentences:
                sentences = [full_script]
            total = audio_duration if audio_duration else len(sentences) * 3.0
            per_sentence = total / len(sentences)
            t = 0.0
            for s in sentences:
                segments.append({"start": t, "end": t + per_sentence, "text": s})
                t += per_sentence
        return segments

    segments = build_segments(whisper_segments_raw, clean_script, real_audio_duration)
    if not segments:
        segments = [{"start": 0.0, "end": 3.0, "text": clean_script}]

    def close_segment_gaps(segs, total_duration):
        if not segs:
            return segs
        for i in range(len(segs) - 1):
            if segs[i]["end"] < segs[i + 1]["start"]:
                segs[i]["end"] = segs[i + 1]["start"]
        if total_duration:
            segs[-1]["end"] = max(segs[-1]["end"], total_duration)
        return segs

    segments = close_segment_gaps(segments, real_audio_duration)

    print(f"\n[1.5/5] Claude Sonnet Gorsel Yonetmen Devrede... ({len(segments)} segment icin)")
    segment_list_text = "\n".join([f"{i+1}: {seg['text']}" for i, seg in enumerate(segments)])

    kw_prompt = f"""
Act as a Hollywood Visual Director for {BRAND_CONFIG['project_name']}.

CONTENT SAFETY RULE (STRICT): Never suggest scenes involving smoking, cigarettes, vaping, alcohol,
drugs, nudity, or sexual content. Keep every visual concept family-friendly and brand-safe (SFW).

REPRESENTATION RULE (STRICT): When a scene depicts a person, rotate across different ethnic and
geographic backgrounds across the full set of scenes (e.g. Asian, Black, Middle Eastern, Latin American,
European) - no single background should dominate. NEVER default to poverty imagery for any specific
ethnicity or region, and never default to wealth/luxury imagery for any specific ethnicity or region,
unless that specific narration line is explicitly about economic inequality. Depict all people, regardless
of background, in a natural but visually appealing, camera-friendly way (not exaggerated, not unflattering).

FORMAT RULE (STRICT): Each visual term must describe ONE concrete, searchable visual SUBJECT
(a specific object, person, place, or action -- something a stock-footage site could literally search for).
NEVER include style, lighting, quality, or mood words (examples of what NOT to output: "cinematic",
"moody lighting", "8k documentary", "teal and orange color grading"). Style is handled separately.

NO READABLE TEXT RULE (STRICT): Never describe a scene that requires visible, readable on-screen text,
UI screens, receipts, app interfaces, signage with legible words, or any close-up of text. This
EXPLICITLY includes map apps, navigation screens, GPS interfaces, turn-by-turn directions, and any
screen showing streets/routes -- these render as garbled gibberish just as often as written text. AI
image generators reliably render such text/UI as garbled gibberish. Instead, describe the same idea
through composition, objects, gestures, or environment without any legible text or map UI in frame
(example: instead of "phone screen showing checkout total", use "hand holding phone near shopping bags";
instead of "phone showing map navigation screen", use "person standing at a street corner looking up
from their phone, uncertain which way to go").

ANATOMICAL/SCIENTIFIC SUBJECT RULE (STRICT): When a line references an internal body process (brain,
hippocampus, nervous system, hormones, etc.), do NOT default to a plastic/toy anatomical model prop --
these render as obviously fake and cheap-looking. Prefer a real medical imaging context instead: an MRI
or brain scan image on a lightbox or monitor, a doctor/researcher reviewing scan images, or a person
having a scan taken. This reads as far more credible and less "prop-like".

OCCUPATION/ROLE SUBJECT RULE (STRICT): When a line references a specific occupation or role (taxi
driver, courier, cashier, etc.), the visual term MUST include enough scene context for the role to be
unambiguous -- not just a person in vague attire. Ground the person in their actual working environment
and action (example: not "person in uniform near car", but "taxi driver behind the wheel inside a black
cab" -- include the vehicle interior, the specific task, or the tool/setting that makes the occupation
instantly readable).

DYNAMIC ACTION RULE (STRICT): AI image models frequently render full-body dynamic action (running,
jumping, mid-stride motion, complex sports movement) with anatomically awkward results. For journey or
movement metaphors, prefer a medium or close shot of a person in a natural, near-static pose that still
implies movement or a path ahead (example: instead of "person running along a marked trail", use
"person standing at a trailhead looking down a winding path" or "person's feet on a running trail,
close-up"). Avoid full-body mid-action shots as the primary subject.

DIGITAL MEDIA SUBJECT RULE (STRICT): When a line is about music/video streaming, algorithmic content
curation, or digital playlists, do NOT default to vintage/analog imagery (vinyl records, turntables,
cassette tapes) unless the line is explicitly about physical/analog media -- this misrepresents a
digital-era topic. Instead use modern digital listening imagery: a person wearing headphones near a
laptop or phone with a softly blurred, out-of-focus glow from the screen (no readable text or UI), a
cozy "digital listening room" mood, or a person relaxed with headphones and a phone in hand.

VISUAL LANGUAGE RULE (STRICT): This is a present-day, real-world documentary channel -- NOT science
fiction. NEVER describe futuristic, sci-fi, cyberpunk, space-age, holographic, neon-glowing-tunnel,
robotic, or "advanced technology" imagery, even for abstract concepts like "control", "transformation",
or "measurement". Ground every abstract idea in ordinary present-day settings and objects: real offices,
homes, streets, hands, faces, everyday devices (a normal smartphone, not a glowing futuristic one),
weather, nature, or plain human gesture and expression. If a concept is abstract, translate it into a
grounded real-world metaphor (example: instead of "a futuristic energy shield representing protection",
use "a person sheltering under a coat in the rain"; instead of "a glowing holographic data stream", use
"a person quietly reading documents at a desk").

SPECIFICITY RULE (STRICT): Read each narration line carefully and identify the single most specific
concrete noun or action it explicitly names (example: "kulaklik", "algoritma", "banka hesabi", "trafik").
Ground your visual term in THAT specific word, not a generic paraphrase. If the line names or implies a
specific technology (algorithm, app, data, cloud, connectivity), do NOT default to "person holding
smartphone" every time -- vary the concrete technology object across the scene set depending on what
fits the specific line: laptop screen, desktop monitor, tablet, television, smartwatch, router, server
room lights, headphones, car dashboard screen, etc.

VARIETY RULE (STRICT): Identify the CORE ANCHOR OBJECT/DEVICE of this topic (the thing the whole video
is about -- e.g. a smartwatch, a phone, a delivery package). This anchor object may be the MAIN VISUAL
FOCUS in AT MOST 2 scenes across the entire numbered set below -- this cap applies regardless of how the
action or angle differs (e.g. "person glancing at smartwatch", "smartwatch screen close-up", "person
staring at smartwatch", and "person removing smartwatch" all count as THE SAME anchor-object subject,
not four different ones -- do not exploit differing verbs/angles to sneak past the cap). For every scene
beyond that 2-scene budget, shift the visual focus AWAY from the anchor object itself and onto its
CONSEQUENCES, CONTEXT, or the person's broader experience instead (e.g. tired legs, sitting alone,
walking through an environment, a quiet room) -- the object can still be present in frame incidentally,
but must not be the main subject. Also vary COMPOSITION TYPE across scenes: extreme close-up on a
relevant object or detail, wide establishing environmental shot, an object being used naturally in the
scene, a reflection (in a window, screen, or mirror), a silhouette, or a from-behind shot. This keeps
the video visually varied while staying on-topic and grounded in each line's specific wording.

Below are {len(segments)} numbered narration lines from the script, in order. For EACH numbered line,
output ONE concrete visual search term (aim for exactly 5 words in English) that matches EXACTLY what
that specific line is talking about -- not a generic or loosely related idea. Apply the SPECIFICITY and
VARIETY rules above across the full set of terms you output.

Additionally, for EACH line, suggest ONE short camera/motion direction in Turkish (3-6 words) describing
how this specific image could be subtly animated if turned into a short video clip (examples: "yavas
sagdan sola kaydirma", "hafif ileri zoom", "sabit, hafif nefes efekti", "yukaridan asagiya yavas
kaydirma"). Keep this concrete to the image's own content, not a generic instruction repeated everywhere.

Output EXACTLY {len(segments)} lines, same numbering, in this format (term and motion direction
separated by " | "):
1: <visual term> | <camera/motion direction in Turkish>
2: <visual term> | <camera/motion direction in Turkish>
...

Narration lines:
{segment_list_text}
"""
    def parse_keyword_animation_lines(raw):
        terms = []
        animations = []
        if raw:
            for line in raw.split("\n"):
                line = line.strip()
                m = re.match(r'^\d+[:.\)]\s*(.+)$', line)
                if m:
                    rest = m.group(1).strip()
                    if "|" in rest:
                        term_part, anim_part = rest.split("|", 1)
                        terms.append(term_part.strip())
                        animations.append(anim_part.strip())
                    else:
                        terms.append(rest.strip())
                        animations.append("")
        return terms, animations

    raw_keywords = call_llm_chain(kw_prompt, VISUAL_MODEL, prefer_gemini_first=False)
    keywords, animations = parse_keyword_animation_lines(raw_keywords)

    if len(keywords) < max(1, len(segments) // 2):
        print("   [!] Gorsel yonetmen yaniti yetersiz/bozuk gorunuyor, Gemini ile tekrar deneniyor...")
        retry_raw = safe_gemini_generate(kw_prompt)
        retry_keywords, retry_animations = parse_keyword_animation_lines(retry_raw)
        if len(retry_keywords) > len(keywords):
            keywords = retry_keywords
            animations = retry_animations

    FALLBACK_TERM = "abstract dark background with subtle motion"
    FALLBACK_ANIMATION = "sabit, hafif nefes efekti"
    while len(keywords) < len(segments):
        keywords.append(FALLBACK_TERM)
    while len(animations) < len(segments):
        animations.append(FALLBACK_ANIMATION)
    keywords = keywords[:len(segments)]
    animations = animations[:len(segments)]

    # GUNCELLEME 53: Arama terimini 5 kelimeye zorla (fazlaysa kirpilir, azsa oldugu gibi kalir).
    def enforce_word_limit(term, limit=5):
        words = term.split()
        if len(words) > limit:
            return " ".join(words[:limit])
        return term

    keywords = [enforce_word_limit(k) for k in keywords]
    print(f"   >> {len(keywords)} Gorsel Terim ve Hareket Yonergesi Olusturuldu (segment sayisiyla eslesti).")

BANNED_KEYWORDS = ["cigarette", "smoking", "smoke break", "vape", "vaping", "alcohol", "beer", "wine",
                   "whiskey", "drug", "drugs", "nude", "naked", "sexy", "sexual"]
STYLE_ONLY_TERMS = ["cinematic", "moody", "moody lighting", "8k", "8k documentary", "8k documentary style",
                     "documentary", "documentary style", "color grading", "color grade", "dark cinematic",
                     "lighting", "teal and orange", "high contrast", "chiaroscuro", "film grain",
                     "depth of field", "wide shot", "close-up", "close up"]

def sanitize_keyword(raw_prompt):
    lowered = raw_prompt.lower().strip()
    if any(banned in lowered for banned in BANNED_KEYWORDS):
        print(f"   [!] Guvenlik filtresi: '{raw_prompt[:30]}...' terimi yasakli icerik iceriyor, degistirildi.")
        return FALLBACK_TERM
    if lowered in STYLE_ONLY_TERMS:
        print(f"   [!] Stil filtresi: '{raw_prompt[:30]}...' somut bir konu degil, atlanip yedek terimle degistirildi.")
        return FALLBACK_TERM
    return raw_prompt

def optimize_prompt(raw_prompt):
    safe_prompt = sanitize_keyword(raw_prompt)
    return f"{safe_prompt}, {BRAND_CONFIG['visual_style']}"

# GUNCELLEME 52: CHECKPOINT A - uretime baslamadan once, tum sahnelerin arama
# terimini VE tam Leonardo prompt'unu gosterir. Boylece Leonardo hic cagrilmadan,
# hic kredi harcanmadan once terimleri gozden gecirip istediginizi elle
# degistirebilirsiniz (kendi Google/Flickr aramanizi yapmak icin de kullanabilirsiniz).
if MANUAL_REVIEW:
    print("\n   >> CHECKPOINT A - Uretime baslamadan once terimleri gozden gecirin:\n")

    # GUNCELLEME 53: Ayni liste artik "scene_prompts.txt" dosyasina da yaziliyor -
    # terminalden Ctrl+C ile kopyalamaya calismak programi durdurdugu icin (Windows'ta
    # Ctrl+C = kesme sinyali), bu dosyayi Notepad'de acip oradan kopyalamak guvenli.
    try:
        with open("scene_prompts.txt", "w", encoding="utf-8") as f:
            f.write(f"KONU: {current_topic}\n\n")
            for idx, kw_item in enumerate(keywords, start=1):
                full_prompt = optimize_prompt(kw_item)
                anim = animations[idx - 1] if idx - 1 < len(animations) else ""
                f.write(f"Sahne {idx}\n")
                f.write(f"Arama terimi     : {kw_item}\n")
                f.write(f"Flow prompt      : {full_prompt}\n")
                f.write(f"Hareket yonergesi: {anim}\n\n")
        print("   >> Tum sahne prompt'lari 'scene_prompts.txt' dosyasina da yazildi (Notepad'den kopyalayin).")
    except Exception as e:
        print(f"   [!] scene_prompts.txt yazilamadi: {e}")

    for idx, kw_item in enumerate(keywords, start=1):
        full_prompt = optimize_prompt(kw_item)
        anim = animations[idx - 1] if idx - 1 < len(animations) else ""
        print(f"      {idx}) Arama terimi: {kw_item}")
        print(f"         Tam prompt   : {full_prompt}")
        print(f"         Hareket      : {anim}\n")

    if not resumed_session:
        while True:
            edit_input = input("   Degistirmek istediginiz sahne no (bitirdiyseniz sadece Enter): ").strip()
            if not edit_input:
                break
            if edit_input.isdigit() and 1 <= int(edit_input) <= len(keywords):
                edit_idx = int(edit_input) - 1
                new_term = input(f"   Sahne {edit_idx+1} icin yeni arama terimi: ").strip()
                if new_term:
                    keywords[edit_idx] = enforce_word_limit(new_term)
                    print(f"   >> Sahne {edit_idx+1} guncellendi: {keywords[edit_idx]}\n")
            else:
                print("   [!] Gecersiz sahne numarasi, tekrar deneyin.")
    else:
        print("   >> Onceki oturumdan devam edildigi icin duzenleme adimi atlandi.\n")

# GUNCELLEME 53: Checkpoint A tamamlaninca oturum diske kaydedilir - PC kapanip
# acilsa bile senaryo/segment/terim/hareket bilgisi kaybolmaz.
session_data = {
    "topic": current_topic,
    "script": clean_script,
    "segments": segments,
    "keywords": keywords,
    "animations": animations,
    "audio_path": audio_path,
    "real_audio_duration": real_audio_duration,
    "created": datetime.now().isoformat()
}
save_session(session_data)

print(f"\n[1.8/5] Dinamik SFX Katmani Yapilandiriliyor...")
sfx_map = []
for idx, seg in enumerate(segments):
    selected_sfx = "bass_drop" if idx == 0 else ("whoosh" if idx % 4 == 0 else ("glitch" if idx % 3 == 0 else "typing"))
    sfx_map.append({"scene": idx + 1, "time_ms": int(seg["start"] * 1000), "type": selected_sfx})

print(f"   >> {len(sfx_map)} Adet Dinamik SFX Zaman Kodu Uretildi.")

print(f"\n[3/5] Gorsel Eslestirme & Brand Watermark Overlay... ({len(segments)} sahne)")
used_urls = set()

ABSTRACT_MARKERS = ["freedom", "cost", "price", "control", "power", "trust", "identity", "value",
                    "algorithm", "society", "economy", "choice", "privacy", "consciousness",
                    "future", "belief", "truth", "reality", "illusion", "sacrifice", "burden",
                    "hidden", "invisible", "silence", "fear", "isolation"]

def pick_pexels_quality(video_files):
    if not video_files:
        return None
    hd_files = [f for f in video_files if f.get("quality") == "hd" and f.get("width", 0) >= 1280]
    if hd_files:
        hd_files.sort(key=lambda f: f.get("width", 0))
        return hd_files[-1].get("link")
    hd_any = [f for f in video_files if f.get("quality") == "hd"]
    if hd_any:
        return hd_any[0].get("link")
    return video_files[0].get("link")

leonardo_success_count = 0
pollinations_fallback_count = 0

LEONARDO_NEGATIVE_PROMPT = ("teal and orange, cinematic split toning, orange skin tone, cyan shadows, "
                             "smooth skin, airbrushed, 3D render, CGI, glossy, "
                             "stock photo, staged pose, generic advertising photography, "
                             "celebrity likeness, resembling a specific real famous person, "
                             "sad expression, distressed, gloomy mood, miserable, exhausted tired look, "
                             "melancholic, negative emotion, crying, suffering, "
                             "dull flat desaturated colors, washed out, muted colors, drab, grey tones, "
                             "deformed face, asymmetrical eyes, blurry face, distorted features, "
                             "warped face, mangled hands, extra fingers, malformed limbs, "
                             "close-up of hands, extreme close-up hands, hand close-up")

def generate_leonardo_image(prompt_text):
    global leonardo_success_count
    if not leonardo_api_key:
        return None
    headers = {
        "accept": "application/json",
        "authorization": f"Bearer {leonardo_api_key}",
        "content-type": "application/json"
    }
    data = {
        "modelId": LEONARDO_PHOENIX_MODEL_ID,
        "prompt": prompt_text[:1500],
        "num_images": 1,
        "width": 1472,
        "height": 832,
        "contrast": 3,
        "alchemy": True,
        "negative_prompt": LEONARDO_NEGATIVE_PROMPT
    }
    try:
        res = requests.post("https://cloud.leonardo.ai/api/rest/v1/generations", headers=headers, json=data, timeout=20)
        if res.status_code != 200:
            print(f"   [!] Leonardo HTTP {res.status_code}: {res.text[:200]}")
            return None
        generation_id = res.json().get("sdGenerationJob", {}).get("generationId")
        if not generation_id:
            return None
        for _ in range(20):
            time.sleep(2)
            check = requests.get(
                f"https://cloud.leonardo.ai/api/rest/v1/generations/{generation_id}",
                headers=headers, timeout=15
            )
            if check.status_code == 200:
                gen_data = check.json().get("generations_by_pk", {})
                if gen_data.get("status") == "COMPLETE":
                    images = gen_data.get("generated_images", [])
                    if images:
                        leonardo_success_count += 1
                        return images[0].get("url")
                elif gen_data.get("status") == "FAILED":
                    return None
        return None
    except Exception as e:
        print(f"   [!] Leonardo Baglanti Hatasi: {e}")
        return None

# GUNCELLEME 48: STOK VIDEO HIBRIT SISTEMI - belirli konularda (harita, kalabalik,
# arac, doga, sehir, kurumsal mekan vb.) AI yerine gercek stok video kullaniyoruz.
# Bu hem maliyeti dusurur hem de AI'nin zayif oldugu alanlarda (okunabilir ekran/harita)
# daha guvenilir sonuc verir. Ham 'kw' kullanilir - visual_style (Leonardo stil
# talimatlari) stok aramasina eklenmez, stok motorlari bunu anlamaz.
STOCK_TRIGGER_KEYWORDS = [
    "map", "navigation", "gps", "screen", "interface", "dashboard display", "receipt",
    "sign", "app interface", "crowd", "group of people", "laboratory", "research facility",
    "airport", "economy", "financial market", "stock chart", "cars driving", "car ", " car,",
    "ship", "boat", "plane", "airplane", "ocean", "sea", "forest", "city skyline", "skyline",
    "buildings", "urban", "crosswalk", "pedestrian crossing", "street", "traffic", "clouds",
    "sky", "aerial view", "weather", "rain", "night street",
]

def matches_stock_trigger(term):
    lowered = f" {term.lower()} "
    return any(k in lowered for k in STOCK_TRIGGER_KEYWORDS)

stock_video_count = 0

def _thumbnail_brightness(url):
    # GUNCELLEME 50: Aday stok videolarin kucuk onizleme gorsellerini indirip ortalama
    # parlakligini olcuyoruz - karanlik/uyumsuz klip secme riskini azaltmak icin.
    try:
        r = requests.get(url, timeout=8)
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(r.content)).convert("L").resize((64, 36))
        return float(np.array(img).mean())
    except Exception:
        return 0.0

def _pick_brightest_candidate(candidates):
    # candidates: list of (video_link, thumbnail_url) tuples
    if not candidates:
        return None
    scored = []
    for link, thumb in candidates[:10]:
        brightness = _thumbnail_brightness(thumb) if thumb else 0.0
        scored.append((brightness, link))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]

def search_pexels_video(query, min_duration):
    if not pexels_key:
        return None
    headers = {"Authorization": pexels_key}
    params = {"query": query, "orientation": "landscape", "size": "large", "per_page": 15}
    try:
        res = requests.get("https://api.pexels.com/videos/search", headers=headers, params=params, timeout=15)
        if res.status_code != 200:
            return None
        candidates = []
        for v in res.json().get("videos", []):
            if v.get("duration", 0) >= min_duration:
                link = pick_pexels_quality(v.get("video_files", []))
                if link:
                    candidates.append((link, v.get("image")))
        return _pick_brightest_candidate(candidates)
    except Exception as e:
        print(f"   [!] Pexels video arama hatasi: {e}")
        return None

def search_pixabay_video(query, min_duration):
    if not pixabay_key:
        return None
    params = {"key": pixabay_key, "q": query, "video_type": "film", "orientation": "horizontal", "per_page": 15}
    try:
        res = requests.get("https://pixabay.com/api/videos/", params=params, timeout=15)
        if res.status_code != 200:
            return None
        candidates = []
        for h in res.json().get("hits", []):
            if h.get("duration", 0) >= min_duration:
                videos = h.get("videos", {})
                best = videos.get("large") or videos.get("medium") or videos.get("small")
                if best and best.get("width", 0) >= 1280:
                    candidates.append((best.get("url"), best.get("thumbnail")))
        return _pick_brightest_candidate(candidates)
    except Exception as e:
        print(f"   [!] Pixabay video arama hatasi: {e}")
        return None

def fetch_media(kw, index, target_duration=5.0):
    opt_kw = optimize_prompt(kw)

    for ext in ("jpg", "jpeg", "png"):
        manual_path = os.path.join(MANUAL_IMAGES_DIR, f"scene_{index+1}.{ext}")
        if os.path.exists(manual_path):
            return ("image_manual", manual_path)

    # GUNCELLEME 53: Manuel klasorde video da aranir (Flow'da hareketlendirdiginiz ya da
    # kendi buldugunuz stok klip icin) - resimden sonra, oncelik sirasi gorselde kalir.
    for ext in ("mp4", "mov"):
        manual_video_path = os.path.join(MANUAL_IMAGES_DIR, f"scene_{index+1}.{ext}")
        if os.path.exists(manual_video_path):
            return ("video_manual", manual_video_path)

    if matches_stock_trigger(kw):
        min_duration = target_duration + 1
        stock_url = search_pexels_video(kw, min_duration)
        if not stock_url:
            stock_url = search_pixabay_video(kw, min_duration)
        if stock_url:
            global stock_video_count
            stock_video_count += 1
            return ("video_stock", stock_url)

    leonardo_url = generate_leonardo_image(opt_kw)
    if leonardo_url:
        return ("image_leonardo", leonardo_url)

    global pollinations_fallback_count
    pollinations_fallback_count += 1
    print(f"   [!] Leonardo basarisiz/kredi tukendi, Pollinations'a gecildi (Sahne {index+1})")
    seed = random.randint(1, 999999)
    url = f"https://image.pollinations.ai/prompt/{requests.utils.quote(opt_kw)}?width=1920&height=1080&nologo=true&seed={seed}"
    return ("image_ai", url)

os.makedirs("downloaded_videos", exist_ok=True)

def _is_valid_image(filename):
    try:
        from PIL import Image
        with Image.open(filename) as img:
            img.verify()
        return True
    except Exception:
        return False

def download_media(url, filename, retries=3):
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=40, stream=True)
            with open(filename, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
            if filename.lower().endswith((".jpg", ".jpeg", ".png")):
                if not _is_valid_image(filename):
                    raise ValueError("Indirilen dosya gecerli bir gorsel degil (bozuk/eksik)")
            return filename
        except Exception as e:
            if attempt < retries - 1:
                print(f"   [!] Indirme sorunu, tekrar deneniyor ({filename}): {e}")
                time.sleep(2)
            else:
                print(f"   [!] Indirme Hatasi ({filename}): {e}")
    return None

CROSSFADE_COMPENSATION = 0.4

scene_files = []
segment_count = len(segments)
for i, (kw, seg) in enumerate(zip(keywords, segments)):
    target_duration = max(0.5, seg["end"] - seg["start"])
    if i == segment_count - 1 and segment_count > 1:
        target_duration += (segment_count - 1) * CROSSFADE_COMPENSATION
    m_type, m_src = fetch_media(kw, i, target_duration)
    current_sfx = sfx_map[i]['type'] if i < len(sfx_map) else "whoosh"
    print(f"   >> Sahne [{i+1}/{len(segments)}] ({target_duration:.1f}sn) Kaynak -> [{m_type}] | Prompt -> '{kw[:25]}...' | SFX -> [{current_sfx}] | Watermark -> [{BRAND_CONFIG['watermark_text']}]")

    if RENDER_MODE:
        if m_type in ("image_manual", "video_manual"):
            scene_files.append((m_type, m_src, target_duration))
        else:
            ext = "mp4" if m_type.startswith("video") else "jpg"
            local_path = f"downloaded_videos/scene_{i+1}.{ext}"
            result = download_media(m_src, local_path)
            if result:
                scene_files.append((m_type, result, target_duration))

def _jamendo_search(tags_param, instrumental=True):
    try:
        url = (f"https://api.jamendo.com/v3.0/tracks/?client_id={jamendo_client_id}"
               f"&format=json&limit=10&order=popularity_total&audioformat=mp31")
        if tags_param:
            url += f"&tags={requests.utils.quote(tags_param)}&fuzzytags=1"
        if instrumental:
            url += "&instrumental=1"
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            return res.json().get("results", [])
    except Exception as e:
        print(f"   [!] Jamendo istegi basarisiz: {e}")
    return []

def fetch_background_music(mood):
    if not jamendo_client_id:
        return None
    tags_csv = mood.replace(" ", ",")
    attempts = [tags_csv, tags_csv.split(",")[0], None]
    for attempt_tags in attempts:
        results = _jamendo_search(attempt_tags)
        if results:
            track = random.choice(results)
            audio_url = track.get("audio")
            if audio_url:
                return download_media(audio_url, "bg_music.mp3")
    return None

# GUNCELLEME 52: CHECKPOINT B - tum sahneler indirildikten sonra, montaja
# gecmeden once durup goz atmaniza izin verir. "downloaded_videos" klasorune
# bakip begenmediginiz sahnenin numarasini girerseniz SADECE o sahne (isterseniz
# yeni bir terimle) yeniden uretilir - 9 gorselin hepsi degil.
if MANUAL_REVIEW and scene_files:
    print(f"\n   >> CHECKPOINT B - Tum sahneler uretildi. 'downloaded_videos' klasorunu kontrol edin.")
    while True:
        redo_input = input(f"   Yeniden uretmek istediginiz sahne no (1-{len(scene_files)}, bitirdiyseniz sadece Enter): ").strip()
        if not redo_input:
            break
        if redo_input.isdigit() and 1 <= int(redo_input) <= len(scene_files):
            redo_idx = int(redo_input) - 1
            new_term = input(f"   Sahne {redo_idx+1} icin yeni terim (bos birakirsan ayni terimle tekrar dener): ").strip()
            term_to_use = new_term if new_term else keywords[redo_idx]
            old_m_type, old_path, redo_duration = scene_files[redo_idx]
            print(f"   >> Sahne {redo_idx+1} yeniden uretiliyor ('{term_to_use}')...")
            try:
                new_m_type, new_m_src = fetch_media(term_to_use, redo_idx, redo_duration)
                if new_m_type in ("image_manual", "video_manual"):
                    scene_files[redo_idx] = (new_m_type, new_m_src, redo_duration)
                    keywords[redo_idx] = term_to_use
                    print(f"   >> Sahne {redo_idx+1} guncellendi -> [{new_m_type}]")
                else:
                    ext = "mp4" if new_m_type.startswith("video") else "jpg"
                    local_path = f"downloaded_videos/scene_{redo_idx+1}.{ext}"
                    result = download_media(new_m_src, local_path)
                    if result:
                        scene_files[redo_idx] = (new_m_type, result, redo_duration)
                        keywords[redo_idx] = term_to_use
                        print(f"   >> Sahne {redo_idx+1} guncellendi -> [{new_m_type}]")
                    else:
                        print(f"   [!] Indirme basarisiz, eski sahne korunuyor.")
                # GUNCELLEME 53: Checkpoint B'deki degisiklik de oturuma yansitilir.
                session_data["keywords"] = keywords
                save_session(session_data)
            except Exception as e:
                print(f"   [!] Yeniden uretim hatasi, eski sahne korunuyor: {e}")
        else:
            print("   [!] Gecersiz sahne numarasi, tekrar deneyin.")

print("\n[4/5] Montaj Katmani...")
if RENDER_MODE:
    try:
        from moviepy import VideoFileClip, ImageClip, CompositeVideoClip, concatenate_videoclips, AudioFileClip, CompositeAudioClip, concatenate_audioclips, afx, vfx

        narration = AudioFileClip(audio_path)
        total_duration = narration.duration
        print(f"   >> Toplam seslendirme suresi: {total_duration:.1f}sn | {len(scene_files)} sahne (degisken sure, konusma ritmine gore)")

        CROSSFADE = 0.4
        MOVEMENT_CYCLE = ["static", "zoom", "shake"]  # duruş anindaki hareket (sirayla donguleniyor)
        ENTRY_EXIT_CYCLE = ["slide", "zoom", "fade"]  # GUNCELLEME 47: giris/cikis TARZI da sirayla degisiyor - artik hep yandan kaymiyor

        # GUNCELLEME 42: Vinyet (kenar karartma) + film grain (dokusal gren) +
        # ekranda yavas, duzensiz bir yorungede gezinen 2-3 belli belirsiz karartma
        # lekesi (referans videodaki gibi) - hepsi tek bir zaman-bazli fonksiyonda
        # birlesiyor. Maske/kernel bir kere hesaplanip tekrar kullaniliyor (performans icin).
        def _build_vignette_mask(w=1920, h=1080, strength=0.35):
            yy, xx = np.mgrid[0:h, 0:w]
            cx, cy = w / 2, h / 2
            max_dist = (cx ** 2 + cy ** 2) ** 0.5
            dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / max_dist
            mask = 1.0 - strength * np.clip(dist, 0, 1) ** 2
            return mask.astype("float32")

        VIGNETTE_MASK = _build_vignette_mask()

        def _build_blob_kernel(size=220, max_alpha=0.45):
            yy, xx = np.mgrid[0:size, 0:size]
            cx, cy = size / 2, size / 2
            dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / (size / 2)
            alpha = max_alpha * np.clip(1 - dist, 0, 1) ** 2
            return alpha.astype("float32")

        BLOB_KERNEL = _build_blob_kernel()
        BLOB_SIZE = BLOB_KERNEL.shape[0]

        def _make_drift_particles(n=3, w=1920, h=1080):
            particles = []
            for _ in range(n):
                particles.append({
                    "cx": random.uniform(0.2, 0.8) * w,
                    "cy": random.uniform(0.2, 0.8) * h,
                    "amp_x": random.uniform(80, 220),
                    "amp_y": random.uniform(60, 160),
                    "freq_x": random.uniform(0.04, 0.11),
                    "freq_y": random.uniform(0.03, 0.09),
                    "phase_x": random.uniform(0, 6.28),
                    "phase_y": random.uniform(0, 6.28),
                    "pulse_freq": random.uniform(0.05, 0.15),
                    "pulse_phase": random.uniform(0, 6.28),
                })
            return particles

        def _apply_drift_particles(f, t, particles):
            h, w = f.shape[:2]
            half = BLOB_SIZE // 2
            for p in particles:
                px = p["cx"] + p["amp_x"] * math.sin(2 * math.pi * p["freq_x"] * t + p["phase_x"])
                py = p["cy"] + p["amp_y"] * math.cos(2 * math.pi * p["freq_y"] * t + p["phase_y"])
                pulse = 0.4 + 0.6 * (0.5 + 0.5 * math.sin(2 * math.pi * p["pulse_freq"] * t + p["pulse_phase"]))
                x0, y0 = int(px - half), int(py - half)
                x1, y1 = x0 + BLOB_SIZE, y0 + BLOB_SIZE
                sx0, sy0 = max(0, x0), max(0, y0)
                sx1, sy1 = min(w, x1), min(h, y1)
                if sx0 >= sx1 or sy0 >= sy1:
                    continue
                kx0, ky0 = sx0 - x0, sy0 - y0
                kx1, ky1 = kx0 + (sx1 - sx0), ky0 + (sy1 - sy0)
                alpha = BLOB_KERNEL[ky0:ky1, kx0:kx1] * pulse
                region = f[sy0:sy1, sx0:sx1]
                f[sy0:sy1, sx0:sx1] = region * (1 - alpha[..., None])
            return f

        def _apply_stock_color_correction(f):
            # GUNCELLEME 49: Stok video, kendi orijinal (genelde flat/koyu) renk
            # ayarlariyla geliyor - vinyet/grain tek basina bunu duzeltmiyor. Burada
            # parlaklik + doygunluk + sicak ton kaymasi ile bizim Ektar-vivid paletimize
            # dogru zorluyoruz. Sadece stok klipler icin kullanilir, Leonardo gorselleri
            # zaten dogru renkte geldigi icin bu adimdan gecmiyor.
            f = f * 1.15 + 10  # parlaklik lift
            mean = f.mean(axis=2, keepdims=True)
            f = mean + (f - mean) * 1.25  # doygunluk artisi
            f[..., 0] *= 1.05  # kirmizi kanal - sicak ton
            f[..., 2] *= 0.95  # mavi kanal - sicakliga karsi denge
            return f

        def _make_post_process(particles, grain_strength=8, stock_correction=False, apply_effects=True):
            # GUNCELLEME 52: apply_effects=False iken vinyet/grain/karartma-lekesi
            # paketi UYGULANMAZ - sadece stock_correction (renk duzeltmesi) kalir.
            # Video (stok) sahneler zaten kendi hareketini tasidigi icin bu "efekt"
            # paketine (ozellikle karartma lekelerine) ihtiyaci yok, sadece durgun
            # AI gorsellerinde kullanilmali.
            # GUNCELLEME 43: KRITIK BUG DUZELTMESI - get_frame(t) bazen 1920x1080
            # disinda bir boyut donduruyordu (crossfade/resize etkilesimi), bu da
            # VIGNETTE_MASK ile carpimda "broadcast" hatasi ve sahnelerin sessizce
            # dusmesine (video-ses hizalama kaymasina) sebep oluyordu. Artik frame
            # her zaman 1920x1080'e zorlaniyor (ortadan kirpma/doldurma ile).
            def _force_frame_size(frame, target_h=1080, target_w=1920):
                fh, fw = frame.shape[:2]
                if fh == target_h and fw == target_w:
                    return frame
                canvas = np.zeros((target_h, target_w, 3), dtype=frame.dtype)
                copy_h, copy_w = min(fh, target_h), min(fw, target_w)
                sy0 = max(0, (fh - copy_h) // 2)
                sx0 = max(0, (fw - copy_w) // 2)
                dy0 = max(0, (target_h - copy_h) // 2)
                dx0 = max(0, (target_w - copy_w) // 2)
                canvas[dy0:dy0 + copy_h, dx0:dx0 + copy_w] = frame[sy0:sy0 + copy_h, sx0:sx0 + copy_w]
                return canvas

            def _post_process(get_frame, t):
                frame = _force_frame_size(get_frame(t))
                f = frame.astype("float32")
                if stock_correction:
                    f = _apply_stock_color_correction(f)
                if apply_effects:
                    f *= VIGNETTE_MASK[..., None]
                    f = _apply_drift_particles(f, t, particles)
                    noise = np.random.normal(0, grain_strength, f.shape).astype("float32")
                    f += noise
                return np.clip(f, 0, 255).astype("uint8")
            return _post_process

        def _ease_out(p):
            return 1 - (1 - p) ** 3

        def _ease_in(p):
            return p ** 3

        def make_ken_burns_clip(path, target_duration, scene_index=0):
            # GUNCELLEME 47: Giris/cikis artik TEK tip (hep yandan kayma) degil, sirayla
            # 3 farkli tarz arasinda donguleniyor: "slide" (yandan kayma, yavaslatilmis),
            # "zoom" (kuculmusten buyuyerek beliren/buyuyerek kaybolan gecis, yanal hareket
            # yok), "fade" (hic hareket yok, gecis sadece crossfade ile - montaj asamasinda
            # crossfade SADECE bu tarzda uygulaniyor, digerlerinde cift-gecis karmasasi
            # olmasin diye crossfade kapali kaliyor).
            # Duruş anindaki hareket (movement_mode) ayri bir dongu: %50 sabit, %30 hafif
            # zoom, %20 hafif el-kamerasi sarsintisi.
            base = ImageClip(path).with_duration(target_duration).resized(height=1350)
            w = base.w
            h = base.h
            x_center = (1920 - w) / 2
            y_center = (1080 - h) / 2

            dur = max(target_duration, 0.1)
            entry_exit_style = ENTRY_EXIT_CYCLE[scene_index % len(ENTRY_EXIT_CYCLE)]

            # Yavaslatilmis sure: eskiden max 1.0sn sabitti, simdi max 1.6sn - daha
            # hissedilir, daha az "aceleci" bir gecis.
            entry_dur = min(1.6, dur * 0.35)
            exit_dur = min(1.6, dur * 0.35)
            if entry_dur + exit_dur >= dur:
                entry_dur = dur * 0.3
                exit_dur = dur * 0.3
            hold_start = entry_dur
            hold_end = dur - exit_dur
            hold_dur = max(0.0, hold_end - hold_start)

            enter_side = random.choice(["left", "right"])
            exit_side = random.choice(["left", "right"])
            x_enter_start = -w if enter_side == "left" else 1920
            x_exit_end = -w if exit_side == "left" else 1920

            movement_mode = MOVEMENT_CYCLE[scene_index % len(MOVEMENT_CYCLE)]
            hold_zoom_end_scale = 1.08  # eskiden 1.03 - fark edilmiyordu
            shake_amp = 2.0
            shake_freq_x = random.uniform(1.3, 2.1)
            shake_freq_y = random.uniform(1.1, 1.9)
            shake_phase_x = random.uniform(0, 6.28)
            shake_phase_y = random.uniform(0, 6.28)

            def pos(t):
                if entry_exit_style == "slide":
                    if t < entry_dur:
                        progress = _ease_out(t / entry_dur) if entry_dur > 0 else 1
                        x = x_enter_start + (x_center - x_enter_start) * progress
                    elif t < hold_end:
                        x = x_center
                    else:
                        progress = _ease_in((t - hold_end) / exit_dur) if exit_dur > 0 else 1
                        x = x_center + (x_exit_end - x_center) * progress
                else:
                    x = x_center
                if movement_mode == "shake" and entry_exit_style != "zoom":
                    shake_x = shake_amp * math.sin(2 * math.pi * shake_freq_x * t + shake_phase_x)
                    shake_y = shake_amp * math.cos(2 * math.pi * shake_freq_y * t + shake_phase_y)
                    return (x + shake_x, y_center + shake_y)
                return (x, y_center)

            def scale(t):
                if entry_exit_style == "zoom":
                    if t < entry_dur:
                        progress = _ease_out(t / entry_dur) if entry_dur > 0 else 1
                        return 0.82 + (1.0 - 0.82) * progress
                    elif t < hold_end:
                        s = 1.0
                    else:
                        progress = _ease_in((t - hold_end) / exit_dur) if exit_dur > 0 else 1
                        return 1.0 + (1.18 - 1.0) * progress
                else:
                    s = 1.0
                if movement_mode == "zoom" and hold_dur > 0 and hold_start <= t < hold_end:
                    progress = (t - hold_start) / hold_dur
                    return s + (hold_zoom_end_scale - s) * progress if entry_exit_style != "zoom" else s
                return s

            moving = base.resized(scale).with_position(pos)
            composed = CompositeVideoClip([moving], size=(1920, 1080)).with_duration(target_duration)
            particles = _make_drift_particles()
            composed = composed.transform(_make_post_process(particles))
            # GUNCELLEME 45: .transform() sonrasi klibin boyut metadatasi guvenilmez
            # kalabiliyor - CrossFadeIn/Out bu yuzden yanlis boyutlu maske olusturup
            # sahnenin sessizce dusmesine (broadcast hatasi) sebep oluyordu. Veriyi
            # degistirmeden (zaten 1920x1080) sadece boyut bilgisini acikca sabitliyoruz.
            composed = composed.resized(new_size=(1920, 1080))
            return composed, entry_exit_style

        clips = []
        last_good_path = None
        for scene_idx, (m_type, path, target_duration) in enumerate(scene_files):
            try:
                if m_type.startswith("video"):
                    # GUNCELLEME 48: Stok video zaten kendi hareketini tasidigi icin
                    # pozisyon kaymasi/zoom/sarsinti UYGULANMIYOR - sadece marka
                    # tutarliligi icin ayni vinyet+grain+karartma-lekesi paketi
                    # bindiriliyor, gecis "fade" (crossfade) ile yapiliyor.
                    clip = VideoFileClip(path).without_audio()
                    if clip.duration < target_duration:
                        loops_needed = int(target_duration // clip.duration) + 1
                        clip = concatenate_videoclips([clip] * loops_needed)
                    start_offset = max(0.0, (clip.duration - target_duration) / 2)
                    start_offset = min(start_offset, max(0.0, clip.duration - target_duration))
                    clip = clip.subclipped(start_offset, start_offset + target_duration).resized(new_size=(1920, 1080))
                    clip = CompositeVideoClip([clip.with_position("center")], size=(1920, 1080)).with_duration(target_duration)
                    particles = _make_drift_particles()
                    # GUNCELLEME 53: video_manual (Flow'da hareketlendirilen ya da elle
                    # secilen klip) zaten marka rengine uygun kabul edilir - stok renk
                    # duzeltmesi almaz, ama sahneler arasi tutarlilik icin vinyet/grain
                    # paketini alir. video_stock ise tam tersi (renk duzeltmesi alir,
                    # kendi hareketi zaten var diye vinyet/grain almaz).
                    is_stock = (m_type == "video_stock")
                    clip = clip.transform(_make_post_process(particles, stock_correction=is_stock, apply_effects=not is_stock))
                    clip = clip.resized(new_size=(1920, 1080))
                    entry_exit_style = "fade"
                else:
                    clip, entry_exit_style = make_ken_burns_clip(path, target_duration, scene_idx)

                # GUNCELLEME 47: Crossfade artik SADECE "fade" tarzi sahnelerde uygulaniyor.
                # "slide" ve "zoom" tarzlari kendi gecislerini zaten tasiyor - ikisini
                # ust uste bindirmek "cikiyor ama hala orada" karmasasina sebep oluyordu.
                if entry_exit_style == "fade":
                    this_fade = CROSSFADE * random.uniform(0.75, 1.25)
                    clip = clip.with_effects([vfx.CrossFadeIn(this_fade), vfx.CrossFadeOut(this_fade)])
                clips.append(clip)
                last_good_path = path
            except Exception as e:
                print(f"   [!] Sahne isleme hatasi ({path}): {e}")
                if last_good_path:
                    print(f"      >> Onceki gecerli gorsel bu sahne icin tekrar kullaniliyor (sure kaybi olmasin diye).")
                    try:
                        fallback_clip, fallback_style = make_ken_burns_clip(last_good_path, target_duration, scene_idx)
                        if fallback_style == "fade":
                            fallback_clip = fallback_clip.with_effects([vfx.CrossFadeIn(CROSSFADE), vfx.CrossFadeOut(CROSSFADE)])
                        clips.append(fallback_clip)
                    except Exception as e2:
                        print(f"      [!] Yedek sahne de basarisiz oldu: {e2}")

        if clips:
            print("   >> Arka plan muzigi araniyor (Jamendo)...")
            bg_music_path = fetch_background_music(BRAND_CONFIG.get("music_mood", "cinematic ambient"))
            final_audio = narration
            if bg_music_path:
                try:
                    bg = AudioFileClip(bg_music_path)
                    if bg.duration < total_duration:
                        loops_needed = int(total_duration // bg.duration) + 1
                        bg = concatenate_audioclips([bg] * loops_needed)
                    bg = bg.subclipped(0, total_duration).with_effects([afx.MultiplyVolume(0.08)])
                    final_audio = CompositeAudioClip([bg, narration])
                    print("   >> Arka plan muzigi eklendi (dusuk sesle).")
                except Exception as e:
                    print(f"   [!] Muzik karistirma hatasi, sadece seslendirme kullanilacak: {e}")
            else:
                print("   [!] Uygun muzik bulunamadi, sadece seslendirme kullanilacak.")

            final_video = concatenate_videoclips(clips, method="compose", padding=-CROSSFADE)
            final_video = final_video.with_audio(final_audio).with_duration(total_duration)
            final_video.write_videofile("final_documentary.mp4", fps=24, codec="libx264", audio_codec="aac", ffmpeg_params=["-pix_fmt", "yuv420p"])
            print("   >> final_documentary.mp4 basariyla olusturuldu!")

            # GUNCELLEME 53: Video basariyla olusturuldu - bu oturuma ait manuel
            # gorseller/videolar SILINMEZ, konu adiyla bir arsiv alt klasorune tasinir.
            # Boylece bir sonraki calistirmada "scene_1.jpg" gibi eski bir dosyayla
            # karismaz, ama istersen arsivden geri de alabilirsin.
            try:
                archive_slug = slugify_topic(current_topic)
                archive_dir = os.path.join(MANUAL_IMAGES_DIR, "arsiv", f"{archive_slug}_{datetime.now().strftime('%Y%m%d_%H%M')}")
                moved_any = False
                for fname in os.listdir(MANUAL_IMAGES_DIR):
                    fpath = os.path.join(MANUAL_IMAGES_DIR, fname)
                    if os.path.isfile(fpath) and fname.lower().startswith("scene_"):
                        os.makedirs(archive_dir, exist_ok=True)
                        shutil.move(fpath, os.path.join(archive_dir, fname))
                        moved_any = True
                if moved_any:
                    print(f"   >> Kullanilan manuel gorseller/videolar '{archive_dir}' klasorune arsivlendi.")
            except Exception as e:
                print(f"   [!] Manuel gorsel arsivleme hatasi: {e}")

            delete_session()
            print("   >> Oturum tamamlandi, session.json temizlendi.")
        else:
            print("   [!] Hicbir sahne islenemedi, video olusturulamadi.")
    except Exception as e:
        print(f"   [!] Montaj Hatasi: {e}")
else:
    print(f"   [DRY-RUN] Guncelleme 25 (Whisper Segment-Bazli Senkronizasyon) Basariyla Dogrulandi!")

print("\n[5/5] Maliyet Ozeti ve GitHub Yedekleme...")

estimated_cost = leonardo_success_count * 0.015
print(f"   >> Leonardo ile uretilen gorsel: {leonardo_success_count} (~${estimated_cost:.3f})")
print(f"   >> Stok video kullanilan (ucretsiz) sahne: {stock_video_count}")
if pollinations_fallback_count > 0:
    print(f"   >> Pollinations'a dusen (ucretsiz) gorsel: {pollinations_fallback_count}")
print(f"   >> Bu video icin tahmini toplam maliyet: ~${estimated_cost:.3f}")

_or_final_info = get_openrouter_credit_info()
if _or_final_info and _or_baseline_usage is not None:
    used_this_run = _or_final_info.get("usage", 0) - _or_baseline_usage
    _rem_final = _or_final_info.get("limit_remaining")
    print(f"   >> OpenRouter bu calismada kullanilan: ${used_this_run:.4f}")
    if _rem_final is not None:
        print(f"   >> OpenRouter kalan kredi: ${_rem_final:.4f}")

try:
    commit_msg = f"Auto-update: {current_topic[:60]}"
    subprocess.run(["git", "add", "app.py", ".gitignore"], check=True, capture_output=True, text=True)
    commit_result = subprocess.run(["git", "commit", "-m", commit_msg], capture_output=True, text=True)
    push_result = subprocess.run(["git", "push", "origin", "main"], capture_output=True, text=True)
    if push_result.returncode == 0:
        print("   >> GitHub'a otomatik yedeklendi.")
    else:
        print(f"   [!] GitHub push basarisiz (elle kontrol et): {push_result.stderr[:200]}")
except Exception as e:
    print(f"   [!] GitHub otomasyon hatasi (elle git push yapabilirsin): {e}")