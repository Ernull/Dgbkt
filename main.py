"""
🚀 Nexus Extractor - PRO TELEGRAM BOT & WEB GATEWAY (REDIS INTEGRATED)
"""
import os, json, asyncio, logging, secrets, uuid, random, urllib.parse, re, time, io, urllib3
from datetime import datetime
from aiohttp import web
from bs4 import BeautifulSoup
import redis.asyncio as aioredis
import telebot
from telebot import types

# غیرفعال کردن هشدارهای SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ================= Configuration =================
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
admin_ids_env = os.environ.get("ADMIN_IDS", "123456789")
ADMIN_IDS = [int(x.strip()) for x in admin_ids_env.split(",") if x.strip().isdigit()]

WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://your-domain.com").rstrip('/')
PORT = int(os.environ.get("PORT", "8080"))
REDIS_URL = os.environ.get("REDIS_URL", "redis://default:DWYgsUKwUJTjTMQpasIEHBYVPEMMhoPt@altaria.proxy.rlwy.net:22748")

APP_SECRET_HEADER = "JetApp-Secure-Client"

db = aioredis.from_url(REDIS_URL, decode_responses=True)
bot = telebot.TeleBot(TOKEN)

logging.basicConfig(format='%(asctime)s - %(message)s', level=logging.INFO)

# متغیرهای موقت محلی برای ربات تلگرام
user_data = {}
user_proxies = {}

# ================= Helper Functions =================
def generate_pkce():
    verifier = secrets.token_urlsafe(64)
    hashed = hashlib.sha256(verifier.encode('ascii')).digest()
    challenge = base64.urlsafe_b64encode(hashed).decode('ascii').rstrip('=')
    return verifier, challenge

def format_proxy(p_str):
    p_str = p_str.strip()
    if not p_str:
        return None
    if not re.match(r'^(http|https|socks4|socks5)://', p_str):
        p_str = f"http://{p_str}"
    return p_str

def get_main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    btn_clean = types.KeyboardButton("📦 دریافت حساب‌های بدون سفارش")
    btn_proxy = types.KeyboardButton("🌐 تنظیم پروکسی")
    markup.add(btn_clean, btn_proxy)
    return markup

def send_final_file(chat_id, status_message, logs, extracted_data=None):
    bot.send_message(chat_id, status_message)
    report_text = "\n".join(logs)
    if extracted_data:
        report_text += "\n\n====== [ اطلاعات خروجی مشابه سیستم bg1 ] ======\n"
        report_text += json.dumps(extracted_data, ensure_ascii=False, indent=4)
        
    file_obj = io.BytesIO(report_text.encode('utf-8'))
    file_obj.name = f"Nexus_Report_{int(time.time())}.txt"
    bot.send_document(chat_id, file_obj, caption="📄 فایل لاگ و دیتای نشست")

def check_purchase_history(session, dk_token, logs):
    """استخراج تاریخچه خریدها از طریق API دیجی‌کالاجت"""
    try:
        logs.append("▶️ در حال بررسی سوابق خرید (ارتباط با دیجی‌کالاجت)...")
        sa_headers = {
            "x-web-client": "desktop", 
            "x-web-client-id": "web", 
            "x-web-optimize-response": "1",
            "Referer": "https://www.digikala.com/",
            "Authorization": f"Bearer {dk_token}" if not dk_token.startswith("Bearer") else dk_token,
            "User-Agent": session.headers.get("User-Agent", "Mozilla/5.0")
        }
        res_sa = session.get("https://api.digikala.com/super-app/v1/sso/jet/", 
                             params={"redirect_url": "/?utm_source=digikala-superweb"}, 
                             headers=sa_headers, allow_redirects=False, timeout=15)
        sa_token = None
        if 'Location' in res_sa.headers:
            match = re.search(r'sa_token=([^&]+)', res_sa.headers['Location'])
            if match: sa_token = match.group(1)
        if not sa_token:
            res_sa_redirect = session.get("https://api.digikala.com/super-app/v1/sso/jet/", 
                                          params={"redirect_url": "/?utm_source=digikala-superweb"},
                                          headers=sa_headers, allow_redirects=True, timeout=15)
            parsed = urllib.parse.urlparse(res_sa_redirect.url)
            qs = urllib.parse.parse_qs(parsed.query)
            if 'sa_token' in qs: sa_token = qs['sa_token'][0]
        if not sa_token:
            logs.append("❌ خطا در دریافت توکن SA_TOKEN")
            return 0

        jet_headers = {
            'authority': 'api.digikalajet.ir', 
            'accept': 'application/json, text/plain, */*',
            'app-id': '470de285-a905-462b-b967-53ce8eced716', 
            'client': 'mobile',
            'clientid': 'FINGERPRINTV2-' + str(uuid.uuid4().hex), 
            'clientos': 'Android',
            'content-type': 'application/json', 
            'origin': 'https://www.digikalajet.com',
            'user-agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 Chrome/137.0.0.0 Mobile'
        }
        res_jet = session.post("https://api.digikalajet.ir/super-app-sso/",
                               headers=jet_headers, json={"dsa_token": sa_token}, timeout=15)
        if res_jet.status_code != 200:
            logs.append(f"❌ خطا در لاگین جت | Status: {res_jet.status_code}")
            return 0
            
        jet_token = res_jet.json().get('data', {}).get('token')
        if not jet_token:
            logs.append("❌ توکن نهایی جت در ریسپانس یافت نشد.")
            return 0

        jet_headers['Authorization'] = jet_token
        jet_headers['session'] = f"{uuid.uuid4()}-V3*{int(time.time())}"
        res_orders = session.get("https://api.digikalajet.ir/order-shipments/?ch=jj", headers=jet_headers, timeout=15)
        if res_orders.status_code == 200:
            res_json = res_orders.json()
            if res_json.get("status") == 200:
                data_obj = res_json.get("data", {})
                pager_total = data_obj.get("pager", {}).get("total_items", 0)
                orders_obj = data_obj.get("orders", {})
                ongoing = len(orders_obj.get("ongoing", []) or [])
                accomplished = len(orders_obj.get("accomplished", []) or [])
                total_orders = max(pager_total, ongoing + accomplished)
                logs.append(f"✅ سوابق خرید استخراج شد. مجموع خریدها: {total_orders}")
                return total_orders
        return 0
    except Exception as e:
        logs.append(f"❌ خطای چک کردن خرید: {str(e)}")
        return 0

# ================= Telegram Handlers =================
@bot.message_handler(commands=['start', 'help', 'admin'])
def send_welcome(message):
    if message.from_user.id not in ADMIN_IDS: return
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔗 دریافت لینک‌های ساخته شده (با تعداد)", callback_data="adm_export_links"))
    markup.add(types.InlineKeyboardButton("📦 دریافت لینک‌های دارای سابقه خرید", callback_data="adm_export_ordered"))
    markup.add(types.InlineKeyboardButton("🌐 تنظیم پروکسی", callback_data="adm_set_proxy"))
    bot.send_message(message.chat.id, "⚙️ **پنل مدیریت مرکزی Nexus:**\n\nلطفاً یکی از گزینه‌های زیر را انتخاب کنید:", reply_markup=markup, parse_mode="Markdown")
    bot.send_message(message.chat.id, "محیط آماده به کار است. برای ورود شماره موبایل بفرستید:", reply_markup=get_main_keyboard())

@bot.callback_query_handler(func=lambda call: call.data.startswith("adm_"))
def admin_callback(call):
    if call.from_user.id not in ADMIN_IDS: return
    chat_id = call.message.chat.id
    
    if call.data == "adm_export_links":
        records = asyncio.run(db.hgetall("jet:bulk_accounts"))
        if not records:
            bot.send_message(chat_id, "❌ هیچ لینکی در سیستم موجود نیست.")
            return
        text_content = f"🔗 لیست کل لینک‌های استخراج شده (تعداد: {len(records)}):\n\n"
        for phone, val in records.items():
            data = json.loads(val)
            link = f"{WEBHOOK_URL}/auth/{data['token']}"
            text_content += f"📱 شماره: {phone}\n🔗 لینک: {link}\n──────────────────\n"
        file_bytes = io.BytesIO(text_content.encode('utf-8'))
        file_bytes.name = f"Nexus_All_Links_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        bot.send_document(chat_id, file_bytes, caption=f"✅ لیست {len(records)} لینک ورود آماده شد.")

    elif call.data == "adm_export_ordered":
        ordered_records = asyncio.run(db.hgetall("jet:ordered_accounts"))
        if not ordered_records:
            bot.send_message(chat_id, "❌ هیچ اکانت دارای سفارشی یافت نشد.")
            return
        text_content = f"📦 لیست اکانت‌های دارای سابقه خرید (تعداد: {len(ordered_records)}):\n\n"
        for phone, val in ordered_records.items():
            data = json.loads(val)
            token = data.get("token", "")
            link = f"{WEBHOOK_URL}/auth/{token}" if token else "لینک ناموجود"
            text_content += f"📱 شماره: {phone}\n🔗 لینک: {link}\n──────────────────\n"
        file_bytes = io.BytesIO(text_content.encode('utf-8'))
        file_bytes.name = f"Nexus_Ordered_Links_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        bot.send_document(chat_id, file_bytes, caption=f"✅ لیست {len(ordered_records)} اکانت دارای سفارش آماده شد.")

    elif call.data == "adm_set_proxy":
        bot.send_message(chat_id, "🌐 لطفاً پروکسی‌های خود را ارسال کنید (هر خط یک پروکسی یا فایل txt):")
        bot.register_next_step_handler(call.message, process_proxy_list)

@bot.message_handler(func=lambda m: m.text == "📦 دریافت حساب‌های بدون سفارش")
def get_clean_accounts(message):
    chat_id = message.chat.id
    file_path = "clean_accounts.txt"
    if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
        with open(file_path, "rb") as f:
            bot.send_document(chat_id, f, caption="📦 لیست حساب‌های خامی که سفارش نداشته‌اند.")
    else:
        bot.send_message(chat_id, "❌ هنوز هیچ حساب کاربری بدون سفارشی ثبت نشده است.", reply_markup=get_main_keyboard())

@bot.message_handler(func=lambda m: m.text == "🌐 تنظیم پروکسی" or m.text == "/proxy")
def setup_proxy(message):
    msg = ("🌐 **تنظیمات پروکسی:**\n\n"
           "لطفاً پروکسی‌های خود را بفرستید. (پیام متنی یا آپلود فایل .txt)")
    bot.reply_to(message, msg, parse_mode="Markdown")
    bot.register_next_step_handler(message, process_proxy_list)

@bot.message_handler(content_types=['document'])
def handle_docs(message):
    if message.document and message.document.file_name.endswith('.txt'):
        process_proxy_list(message)

def process_proxy_list(message):
    chat_id = message.chat.id
    proxies_list = []
    if message.document:
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        content = downloaded_file.decode('utf-8').splitlines()
        for line in content:
            p = format_proxy(line)
            if p: proxies_list.append(p)
    elif message.text:
        for line in message.text.splitlines():
            p = format_proxy(line)
            if p: proxies_list.append(p)
            
    if proxies_list:
        user_proxies[chat_id] = proxies_list
        bot.send_message(chat_id, f"✅ تعداد {len(proxies_list)} پروکسی تنظیم شد.\n\nاکنون شماره موبایل را بفرستید.", reply_markup=get_main_keyboard())
    else:
        bot.send_message(chat_id, "❌ پروکسی معتبری یافت نشد!", reply_markup=get_main_keyboard())

# ================= Phone & OTP Handlers =================
@bot.message_handler(func=lambda m: m.text and m.text.startswith("09") and len(m.text.strip()) == 11)
def process_phone_step(message):
    phone = message.text.strip()
    chat_id = message.chat.id
    
    bot.send_message(chat_id, f"⏳ در حال برقراری ارتباط برای شماره {phone}...")
    session = requests.Session()
    logs = [f"====== [ شروع عملیات برای: {phone} ] ======"]
    
    proxies = user_proxies.get(chat_id, [])
    if proxies:
        proxy_url = random.choice(proxies)
        session.proxies = {"http": proxy_url, "https": proxy_url}
        logs.append(f"🌐 Proxy in use: {proxy_url}")
    else:
        logs.append("🌐 Proxy: No Proxy (Direct Connection)")

    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'fa,en-US;q=0.7,en;q=0.3'
    })

    verifier, challenge = generate_pkce()
    nonce = secrets.token_urlsafe(16)
    state = secrets.token_urlsafe(16)
    redirect_uri = "https://www.digikala.com/sso-redirect/?back-url=%2Fprofile%2F"

    auth_url = (
        "https://auth.digikala.com/realms/dk-group/protocol/openid-connect/auth"
        f"?client_id=digikala-web-public&redirect_uri={urllib.parse.quote(redirect_uri)}"
        f"&response_mode=fragment&response_type=code&scope=openid&nonce={nonce}&state={state}"
        f"&code_challenge={challenge}&code_challenge_method=S256"
    )

    try:
        session.headers.update({"Referer": "https://www.digikala.com/"})
        res = session.get(auth_url, timeout=15)
        soup = BeautifulSoup(res.text, 'html.parser')
        form = soup.find('form')
        
        if not form:
            send_final_file(chat_id, "❌ خطا: فرم ورود یافت نشد.", logs)
            return

        action_url = form.get('action').replace('&amp;', '&')
        session.headers.update({"Referer": res.url})
        res = session.post(action_url, data={"username": phone, "rememberMe": "on"}, timeout=15)
        soup = BeautifulSoup(res.text, 'html.parser')

        # سوییچ هوشمند رمزدار / بدون رمز
        if soup.find('input', {'name': 'password'}) or soup.find('input', {'type': 'password'}):
            otp_exec_input = soup.find('input', {'id': 'otp-auth-execution'})
            if otp_exec_input and otp_exec_input.has_attr('value'):
                exec_val = otp_exec_input['value']
                form_login = soup.find('form', {'id': 'dk-form-login'})
                form_action = form_login.get('action') if form_login else action_url
                if form_action:
                    form_action = form_action.replace('&amp;', '&')
                    session.headers.update({"Referer": res.url})
                    payload = {"authenticationExecution": exec_val}
                    res = session.post(form_action, data=payload, timeout=15)
                    soup = BeautifulSoup(res.text, 'html.parser')
        
        form_otp = soup.find('form')
        if not form_otp or not soup.find('input', {'name': 'code'}): 
            send_final_file(chat_id, "❌ خطا: فرم دریافت پیامک بارگذاری نشد.", logs)
            return
            
        otp_action_url = form_otp.get('action').replace('&amp;', '&')
        session.headers.update({"Referer": res.url})
        
        user_data[chat_id] = {
            'phone': phone,
            'session': session,
            'otp_action_url': otp_action_url,
            'verifier': verifier,
            'redirect_uri': redirect_uri,
            'logs': logs
        }
        
        msg_reply = bot.send_message(chat_id, "✅ پیامک ارسال شد!\n\nلطفاً کد تایید ۵ یا ۶ رقمی را بفرستید:")
        bot.register_next_step_handler(msg_reply, process_code_step)

    except Exception as e:
        send_final_file(chat_id, f"❌ خطای ارتباطی: {str(e)}", logs)

def process_code_step(message):
    code = message.text.strip()
    chat_id = message.chat.id
    
    if chat_id not in user_data:
        bot.send_message(chat_id, "⚠️ نشست منقضی شد. لطفا شماره موبایل را مجدداً بفرستید.", reply_markup=get_main_keyboard())
        return
        
    udata = user_data[chat_id]
    session = udata['session']
    logs = udata['logs']
    phone = udata['phone']
    
    bot.send_message(chat_id, "⏳ در حال بررسی کد و چک کردن سوابق خرید...")
    try:
        session.headers.update({"Referer": "https://auth.digikala.com/"})
        res = session.post(udata['otp_action_url'], data={"code": code}, allow_redirects=False, timeout=15)
        auth_code = None
        if 'Location' in res.headers:
            cmatch = re.search(r'code=([^&]+)', res.headers['Location'])
            if cmatch: auth_code = cmatch.group(1)
        if not auth_code:
            cmatch = re.search(r'code=([^&]+)', res.text)
            if cmatch: auth_code = cmatch.group(1)

        if not auth_code:
            send_final_file(chat_id, "❌ کد تایید نامعتبر است.", logs)
            return

        token_data = {
            "code": auth_code,
            "grant_type": "authorization_code",
            "client_id": "digikala-web-public",
            "redirect_uri": udata['redirect_uri'],
            "code_verifier": udata['verifier']
        }
        res_token = session.post("https://auth.digikala.com/realms/dk-group/protocol/openid-connect/token", data=token_data, timeout=15)

        if res_token.status_code == 200:
            tokens = res_token.json()
            access_token = tokens.get('access_token')
            refresh_token = tokens.get('refresh_token')
            
            session.cookies.set("Digikala:User:Token:v2", access_token, domain=".api.digikala.com", path="/", secure=True)
            if refresh_token:
                session.cookies.set("Digikala:User:Refresh:Token:v2", refresh_token, domain=".api.digikala.com", path="/v1/user/", secure=True)

            total_orders = check_purchase_history(session, access_token, logs)
            
            extension_cookies = []
            for cookie in session.cookies:
                extension_cookies.append({
                    "domain": cookie.domain,
                    "hostOnly": not cookie.domain.startswith('.'),
                    "httpOnly": 'HttpOnly' in cookie._rest if cookie._rest else False,
                    "name": cookie.name,
                    "path": cookie.path,
                    "sameSite": "lax",
                    "secure": cookie.secure or True,
                    "session": cookie.expires is None,
                    "storeId": "0",
                    "value": cookie.value,
                    "expirationDate": int(time.time()) + (30 * 24 * 60 * 60)
                })

            final_json = {"cookies": extension_cookies, "origins": []}
            session_token = secrets.token_urlsafe(14)
            
            # ذخیره نشست در ردیس برای استفاده اپلیکیشن
            asyncio.run(db.setex(f"jet_session:{session_token}", 30 * 24 * 3600, json.dumps(final_json, ensure_ascii=False)))
            
            record = {"phone": phone, "token": session_token, "name": "کاربر دیجی‌کالا"}
            
            if total_orders > 0:
                asyncio.run(db.hset("jet:ordered_accounts", phone, json.dumps(record, ensure_ascii=False)))
                order_status_text = f"✅ تعداد خریدهای اکانت: {total_orders} (ذخیره در بخش خریدداران)"
            else:
                asyncio.run(db.hset("jet:bulk_accounts", phone, json.dumps(record, ensure_ascii=False)))
                order_status_text = "⚠️ این اکانت خام است و هیچ سفارشی نداشته (ذخیره در لینک‌های عادی)."
                try:
                    with open("clean_accounts.txt", "a", encoding="utf-8") as f:
                        f.write(f"{json.dumps({'phone': phone, 'data': final_json}, ensure_ascii=False)}\n")
                except:
                    pass

            payload = {"phone": phone, "name": "کاربر دیجی‌کالا", "orders_count": total_orders, "data": final_json}
            final_message = f"✅ ورود موفقیت‌آمیز بود!\n\n🛍 {order_status_text}\n\nفایل دیتای کاربر در حال ارسال است..."
            send_final_file(chat_id, final_message, logs, payload)
        else:
            send_final_file(chat_id, "❌ خطا در دریافت توکن نهایی.", logs)
    except Exception as e:
        send_final_file(chat_id, f"❌ خطای سیستم: {str(e)}", logs)
    finally:
        if chat_id in user_data:
            del user_data[chat_id]

# ================= Secure Gateway Web Route =================
async def web_secure_gateway(request: web.Request):
    token_key = request.match_info.get("token")
    session_data_str = await db.get(f"jet_session:{token_key}")

    if not session_data_str:
        return web.Response(text="پیوند منقضی یا نامعتبر است.", status=404, content_type="text/plain;charset=utf-8")

    user_agent = request.headers.get("User-Agent", "")
    app_header = request.headers.get("X-Client-App", "")

    if app_header == APP_SECRET_HEADER or "JetAppClient" in user_agent:
        return web.json_response({"status": "success", "session": json.loads(session_data_str)})

    html_blocked = '<html dir="rtl" lang="fa"><meta charset="UTF-8"><body style="font-family:Tahoma;text-align:center;margin-top:50px;"><h3>دسترسی مسدود است</h3><p>لینک فقط در اپلیکیشن باز می‌شود.</p></body></html>'
    return web.Response(text=html_blocked, content_type="text/html")

# ================= Background Main Runner =================
async def main():
    web_app = web.Application()
    web_app.router.add_get("/auth/{token}", web_secure_gateway)
    
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    
    print("🤖 Bot and Web Gateway are running simultaneously...")
    bot.infinity_polling()

if __name__ == "__main__":
    asyncio.run(main())
