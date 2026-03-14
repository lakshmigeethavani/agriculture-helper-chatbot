from flask import Flask, request, jsonify, render_template_string, g, session
from flask_cors import CORS
from difflib import SequenceMatcher, get_close_matches
import re, sqlite3, datetime, requests, os
from deep_translator import GoogleTranslator
from dotenv import load_dotenv

# ---------- CONFIG ----------
DATABASE = "chat_logs.db"

# Load secrets from .env
load_dotenv()
OPENWEATHERMAP_API_KEY = os.getenv("OPENWEATHERMAP_API_KEY")
SECRET_KEY = os.getenv("SECRET_KEY")
# ----------------------------

app = Flask(__name__)
CORS(app)
app.secret_key = SECRET_KEY

# -----------------------------
# Knowledge Base
# -----------------------------
KB = {
    "crops": {
        "rice": {"season": "Kharif", "soil": "Clayey", "water": "High",
                 "diseases": ["rice blast", "brown spot"], "fertilizer": "NPK"},
        "wheat": {"season": "Rabi", "soil": "Loamy", "water": "Moderate",
                  "diseases": ["wheat rust", "loose smut"], "fertilizer": "DAP"},
        "cotton": {"season": "Kharif", "soil": "Black soil", "water": "Moderate",
                   "diseases": ["whitefly infestation", "bacterial blight"], "fertilizer": "NPK + Micronutrients"}
    },
    "pests_diseases": {
        "rice blast": {"crop": "rice",
                       "symptoms": ["brown or gray spots on leaves", "diamond-shaped lesions", "neck rot"],
                       "causes": "Fungus (Magnaporthe oryzae)",
                       "organic": ["Remove infected stubble", "Improve drainage", "Apply Trichoderma biocontrol"],
                       "chemical": ["Apply fungicides containing tricyclazole or azoxystrobin"],
                       "preventive": ["Use resistant varieties", "Avoid dense planting", "Rotate crops"]},
        "whitefly infestation": {"crop": "cotton",
                                 "symptoms": ["white insects on leaves", "honeydew secretion", "sooty mold"],
                                 "causes": "Insect (Whitefly)",
                                 "organic": ["Neem oil spray (2-3%)", "Yellow sticky traps", "Encourage predators (ladybird beetles)"],
                                 "chemical": ["Imidacloprid or other IRAC recommended insecticides (use rotation)"],
                                 "preventive": ["Rotate insecticides", "Maintain field hygiene", "Regular monitoring with traps"]},
        "wheat rust": {"crop": "wheat",
                       "symptoms": ["orange-brown pustules", "yellowing leaves", "reduced grain filling"],
                       "causes": "Fungus (Puccinia spp.)",
                       "organic": ["Remove volunteer wheat", "Use resistant varieties"],
                       "chemical": ["Apply systemic fungicides (propiconazole, tebuconazole)"],
                       "preventive": ["Grow resistant varieties", "Timely fungicide sprays"]}
    },
    "market_prices": {"rice": "₹2500/quintal", "wheat": "₹2200/quintal", "cotton": "₹5500/quintal"},
    "schemes": {"rice": ["Pradhan Mantri Fasal Bima Yojana", "Soil Health Card Scheme"],
                "wheat": ["PMFBY", "Rashtriya Krishi Vikas Yojana"],
                "cotton": ["Cotton Development Board Subsidy", "PMFBY"]},
    "faq": {"how to plant rice": "Rice is usually planted in puddled fields during Kharif season.",
            "what is npk": "NPK stands for Nitrogen, Phosphorus, and Potassium — key nutrients for crops."}
}

# -----------------------------
# Utilities
# -----------------------------
def translate_text(text, source, target):
    if not text or source == target:
        return text
    try:
        return GoogleTranslator(source=source, target=target).translate(text)
    except Exception:
        return text

def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
    return db

def init_db():
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS logs
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT,
                  user_message TEXT, intent TEXT, crop TEXT, bot_reply TEXT)''')
    conn.commit()
    conn.close()

@app.teardown_appcontext
def close_connection(exc):
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()

def log_interaction(user_message, intent, crop, bot_reply):
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("INSERT INTO logs (timestamp, user_message, intent, crop, bot_reply) VALUES (?, ?, ?, ?, ?)",
                  (datetime.datetime.utcnow().isoformat(), user_message, intent, crop, bot_reply))
        conn.commit()
    except Exception:
        pass

def normalize(s: str) -> str:
    return re.sub(r'[^a-z0-9 ]', '', s.lower())

def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()

def find_crop(name):
    if not name:
        return None
    keys = list(KB['crops'].keys())
    m = get_close_matches(name.lower(), keys, n=1, cutoff=0.6)
    return m[0] if m else None

def match_pest_disease(msg, crop=None):
    t = normalize(msg)
    results = []
    for name, info in KB['pests_diseases'].items():
        if crop and info["crop"] != crop:
            continue
        best = 0.0
        for s in info["symptoms"]:
            best = max(best, similarity(normalize(s), t))
        if best > 0.25:
            results.append((best, name, info))
    results.sort(key=lambda x: x[0], reverse=True)
    return results[:3]

def detect_intent(text):
    t = text.lower()
    if re.search(r"(hi|hello|namaste|hey)", t):
        return "greeting"
    if any(w in t for w in ["fertilizer", "fertiliser", "npk", "manure"]):
        return "ask_fertilizer"
    if any(w in t for w in ["weather", "rain", "forecast", "temperature"]):
        return "ask_weather"
    if any(w in t for w in ["scheme", "yojana", "subsidy"]):
        return "ask_scheme"
    if any(w in t for w in ["price", "market", "mandi", "rate"]):
        return "ask_market"
    if any(w in t for w in ["season", "kharif", "rabi", "planting"]):
        return "ask_season"
    for q in KB["faq"].keys():
        if q in t:
            return "ask_faq"
    if find_crop(t):
        return "ask_crop_info"
    return "unknown"

# -----------------------------
# Weather
# -----------------------------
def get_weather_for_city(city_name):
    url = f"http://api.openweathermap.org/data/2.5/weather?q={city_name}&units=metric&appid={OPENWEATHERMAP_API_KEY}"
    try:
        r = requests.get(url, timeout=6)
        data = r.json()
        if r.status_code != 200:
            return {"error": data.get("message", "API error")}
        return {"city": data.get("name"), "temp": data["main"]["temp"], "weather": data["weather"][0]["description"]}
    except Exception as e:
        return {"error": str(e)}


# -----------------------------
# Frontend HTML + JS embedded directly
# -----------------------------
@app.route("/")
def index():
    return render_template_string(""" 
<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<title>AgriChatBot</title>
<style>
body { font-family: Arial; background:#f0fbff; margin:20px;}
#chat { border:1px solid #cfecec; padding:12px; height:420px; overflow-y:auto; background:#fff; }
.user { color:#045a8d; margin:6px 0; }
.bot  { color:#00695c; margin:6px 0; white-space:pre-wrap; }
.controls { margin:10px 0; }
.btn { padding:8px 12px; margin-left:6px; background-color:#0288d1; color:white; border:none; border-radius:6px; cursor:pointer; }
.btn:hover { background-color:#20c997; color:black; }
input[type="text"], select { padding:6px; border-radius:6px; border:1px solid #ddd; }
</style>
</head>
<body>
<h2>🌱 AgriChatBot</h2>
<div class="controls">
Language: <select id="lang">
<option value="en">English</option>
<option value="hi">Hindi</option>
<option value="te">Telugu</option>
</select>
<button id="startVoice" class="btn">🎤 Start Voice</button>
<button id="stopVoice" class="btn">⏹ Stop Voice</button>
<button id="playReply" class="btn">🔊 Play last reply</button>
<button id="saveLog" class="btn">💾 Save conversation</button>
</div>
<div id="chat"></div>
<div style="margin-top:10px;">
<input id="msg" type="text" placeholder="Type your message" style="width:60%">
<input id="city" type="text" placeholder="(Optional) city" style="width:25%">
<button id="sendBtn" class="btn">Send</button>
</div>
<script>
let lastReply="";
function addMessage(text,cls){ const d=document.createElement('div'); d.className=cls; d.textContent=text; document.getElementById('chat').appendChild(d); document.getElementById('chat').scrollTop=document.getElementById('chat').scrollHeight;}
document.getElementById('sendBtn').addEventListener('click',sendMsg);
document.getElementById('msg').addEventListener('keydown',(e)=>{if(e.key==='Enter') sendMsg();});
async function sendMsg(){
const msg=document.getElementById('msg').value.trim();
if(!msg) return;
const lang=document.getElementById('lang').value;
const city=document.getElementById('city').value.trim();
addMessage('You: '+msg,'user');
document.getElementById('msg').value='';
const res=await fetch('/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:msg,city:city,lang:lang})});
const data=await res.json();
lastReply=data.reply;
addMessage('Bot: '+data.reply,'bot');
}
async function speakText(text){
if(!text) return;
const lang=document.getElementById('lang').value;
const ut=new SpeechSynthesisUtterance(text);
ut.lang=(lang==='hi')?'hi-IN':(lang==='te')?'te-IN':'en-IN';
const voices=window.speechSynthesis.getVoices();
let candidate=null;
for(let v of voices){
if(!v.lang) continue;
if(v.lang.startsWith(ut.lang)){
const name=v.name.toLowerCase();
if(name.includes('female')||name.includes('google')||name.includes('amy')||name.includes('alexa')||name.includes('samantha')||name.includes('meera')||name.includes('hema')||name.includes('anjali')){candidate=v; break;}
if(!candidate) candidate=v;
}
}
if(candidate) ut.voice=candidate;
window.speechSynthesis.speak(ut);
}
document.getElementById('playReply').addEventListener('click',()=>{speakText(lastReply);});
document.getElementById('saveLog').addEventListener('click',async()=>{const res=await fetch('/export_logs'); const data=await res.json(); alert('Exported logs count: '+data.count);});
let recognition=null;
if('webkitSpeechRecognition' in window || 'SpeechRecognition' in window){
const Rec=window.SpeechRecognition||window.webkitSpeechRecognition;
recognition=new Rec();
recognition.continuous=false;
recognition.interimResults=false;
recognition.onresult=function(event){ const text=event.results[0][0].transcript; document.getElementById('msg').value=text; sendMsg(); };
recognition.onerror=function(e){console.log('Speech error',e);};
} else { document.getElementById('startVoice').disabled=true; document.getElementById('stopVoice').disabled=true; }
document.getElementById('startVoice').addEventListener('click',()=>{ if(!recognition){ alert('Voice recognition not supported in this browser. Use Chrome.'); return;} const lang=document.getElementById('lang').value; recognition.lang=(lang==='hi')?'hi-IN':(lang==='te')?'te-IN':'en-IN'; recognition.start(); });
document.getElementById('stopVoice').addEventListener('click',()=>{if(recognition) recognition.stop();});
window.speechSynthesis.onvoiceschanged=function(){console.log('voices loaded');};
</script>
</body>
</html>
""")

# -----------------------------
# Chat API
# -----------------------------
@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json(force=True)
    user_msg = data.get("message","") or ""
    city = data.get("city","") or ""
    lang = data.get("lang","en")
    user_msg_en = translate_text(user_msg, lang, "en")

    detected_crop = None
    for k in KB["crops"].keys():
        if k in user_msg_en.lower():
            detected_crop=k
            session["last_crop"]=k
            break
    crop = detected_crop or session.get("last_crop")

    intent = detect_intent(user_msg_en)
    if intent=="unknown" and not city and re.fullmatch(r"[A-Za-z ]{2,40}",user_msg_en.strip()):
        city=user_msg_en.strip()

    if intent=="greeting":
        reply_en=handle_greeting()
    elif intent=="ask_crop_info":
        reply_en=handle_crop_info(crop)
    elif intent=="ask_fertilizer":
        reply_en=handle_ask_fertilizer(crop)
    elif intent=="ask_market":
        reply_en=handle_market_price(crop)
    elif intent=="ask_scheme":
        reply_en=handle_gov_schemes(crop)
    elif intent=="ask_season":
        reply_en=handle_ask_season(crop)
    elif intent=="ask_weather":
        chosen_city = city or (lambda t:(re.search(r'in ([A-Za-z ]+)',t.lower()) and re.search(r'in ([A-Za-z ]+)',t.lower()).group(1).strip()) or None)(user_msg_en)
        if not chosen_city:
            reply_en="Please provide a city name for weather (e.g., 'weather in Pune')."
        else:
            w=get_weather_for_city(chosen_city)
            reply_en=("Weather error: "+w["error"]) if "error" in w else f"Weather in {w['city']}: {w['temp']}°C, {w['weather']}"
    elif intent=="ask_faq":
        reply_en=handle_faq(user_msg_en)
    else:
        pd=match_pest_disease(user_msg_en,crop)
        if pd:
            lines=[]
            for score,name,info in pd:
                lines.append(f"Possible issue: {name.title()} (Confidence {round(score*100)}%)\\nCauses: {info['causes']}\\nSymptoms: {', '.join(info['symptoms'])}\\nOrganic: {', '.join(info['organic'])}\\nChemical: {', '.join(info['chemical'])}\\nPreventive: {', '.join(info['preventive'])}")
            reply_en="\\n\\n".join(lines)
        else:
            if city:
                w=get_weather_for_city(city)
                reply_en=("Weather error: "+w["error"]) if "error" in w else f"Weather in {w['city']}: {w['temp']}°C, {w['weather']}"
            else:
                reply_en="Sorry, I didn’t get that. Ask about crop info, fertilizer, pests, weather, market price, schemes, or FAQs."

    reply_translated = translate_text(reply_en,"en",lang)
    log_interaction(user_msg,intent,crop,reply_translated)
    return jsonify({"intent":intent,"crop":crop,"reply":reply_translated})

# -----------------------------
# Export logs
# -----------------------------
@app.route("/export_logs")
def export_logs():
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM logs")
    count=c.fetchone()[0]
    conn.close()
    return jsonify({"count":count})

# -----------------------------
# START APP
# -----------------------------
if __name__=="__main__":
    init_db()
    app.run(debug=True)