import telebot
from telebot import types
import imaplib
import email
from email.header import decode_header
import sqlite3
import logging
import requests
from datetime import datetime
import re
import html
import os
import threading
import time
from flask import Flask

# --- Setup Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Bot Token ---
BOT_TOKEN = '8465423862:AAHkZn88S_jr1aZpBZXzJb_EUxLSXscPZzo'
bot = telebot.TeleBot(BOT_TOKEN)

# To track active messages for clean removal
active_mail_messages = {}
active_menu_messages = {}

# --- Flask Server for Render ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Mail Bot is Running Successfully!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# --- Database Setup ---
def init_db():
    with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                email TEXT,
                password TEXT,
                provider TEXT,
                refresh_token TEXT,
                client_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS email_cache (
                user_id INTEGER,
                idx INTEGER,
                subject TEXT,
                sender TEXT,
                full_content TEXT,
                PRIMARY KEY (user_id, idx)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id INTEGER PRIMARY KEY,
                api_key TEXT
            )
        ''')
        conn.commit()

init_db()

# --- Database Helpers for API Key ---
def get_user_api_key(user_id):
    with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT api_key FROM user_settings WHERE user_id=?", (user_id,))
        row = cursor.fetchone()
        return row[0] if row else None

def set_user_api_key(user_id, api_key):
    with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM user_settings WHERE user_id=?", (user_id,))
        if cursor.fetchone():
            cursor.execute("UPDATE user_settings SET api_key=? WHERE user_id=?", (api_key, user_id))
        else:
            cursor.execute("INSERT INTO user_settings (user_id, api_key) VALUES (?, ?)", (user_id, api_key))
        conn.commit()

def safe_delete(chat_id, message_id):
    try:
        bot.delete_message(chat_id, message_id)
    except:
        pass

# --- Background Task ---
def auto_cleanup_task():
    while True:
        try:
            time.sleep(600) # 10 Minutes
            with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM users")
                cursor.execute("DELETE FROM email_cache")
                conn.commit()
                
            for c_id, m_id in list(active_menu_messages.items()):
                safe_delete(c_id, m_id)
            for c_id, m_id in list(active_mail_messages.items()):
                safe_delete(c_id, m_id)
                
            active_mail_messages.clear()
            active_menu_messages.clear()
            logging.info("Auto-cleanup executed (API keys preserved).")
        except Exception as e:
            logging.error(f"Cleanup error: {e}")

# --- HTML Cleaner & Extractor ---
def clean_html_tags(raw_html):
    if not raw_html:
        return "No Content"
    text = html.unescape(raw_html)
    clean_text = re.sub(r'<[^>]+>', ' ', text)
    clean_text = re.sub(r'\s+', ' ', clean_text).strip()
    return clean_text

def get_html_body(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                return part.get_payload(decode=True).decode(errors='ignore')
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                return part.get_payload(decode=True).decode(errors='ignore')
    else:
        return msg.get_payload(decode=True).decode(errors='ignore')
    return "No HTML Content Found."

def detect_otp_type(subject, content):
    combined_text = (subject + " " + content).lower()
    if "facebook" in combined_text or "fb" in combined_text:
        service_name = "📘 FACEBOOK OTP"
        code_match = re.search(r'\b\d{6,8}\b', combined_text)
        extracted_code = code_match.group(0) if code_match else "Not Found"
        return service_name, extracted_code
    return None, None

# --- Main Menu / Start ---
@bot.message_handler(commands=['start', 'menu'])
def send_welcome(message):
    try:
        bot.delete_message(message.chat.id, message.message_id)
    except:
        pass
    show_main_instruction(message.chat.id)

def show_main_instruction(chat_id, message_id=None):
    if chat_id in active_menu_messages:
        try:
            bot.delete_message(chat_id, active_menu_messages[chat_id])
        except:
            pass

    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("🛒 Buy Gmail", callback_data="action_buy_gmail"),
        types.InlineKeyboardButton("📊 Check Stock", callback_data="action_check_stock")
    )
    markup.add(
        types.InlineKeyboardButton("🔄 Refresh Inbox", callback_data="action_refresh_direct"),
        types.InlineKeyboardButton("⚙️ Settings (API)", callback_data="action_settings")
    )
    
    instruction_text = (
        "🤖 **Auto Secure FB Mail & OTP Reader Bot**\n\n"
        "Click a button below to Buy Gmail or Check Stock.\n"
        "*(Make sure your API key is configured in Settings first)*\n\n"
        "**Manual Input Format:**\n"
        "🏢 **Zoho:** `email@zohomail.com|AppPassword`\n"
        "🔴 **Gmail:** `email@gmail.com|OrderID`\n"
        "🔥 **Hotmail:** `email|password|token|client_id`\n\n"
        "⚠️ *Bot will ONLY fetch the latest Facebook OTP.*"
    )
    
    if message_id:
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=instruction_text, parse_mode="Markdown", reply_markup=markup)
            active_menu_messages[chat_id] = message_id
            return
        except Exception:
            pass
            
    sent_msg = bot.send_message(chat_id, instruction_text, parse_mode="Markdown", reply_markup=markup)
    active_menu_messages[chat_id] = sent_msg.message_id
    threading.Timer(600, lambda c=chat_id, m=sent_msg.message_id: safe_delete(c, m)).start()

# --- Callback Handlers ---
@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    active_menu_messages[chat_id] = message_id
    
    if call.data == "action_menu":
        show_main_instruction(chat_id, message_id=message_id)

    # --- SETTINGS LOGIC ---
    elif call.data == "action_settings":
        current_api = get_user_api_key(chat_id)
        api_status = "✅ Set" if current_api else "❌ Not Set"
        
        settings_text = (
            "⚙️ **Bot Settings**\n\n"
            f"🔑 **yshshopmails API Key:** {api_status}\n\n"
            "If you haven't set your API key yet, click the button below to add it. This key is securely stored."
        )
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton("🔑 Set / Update API Key", callback_data="action_set_api"),
            types.InlineKeyboardButton("🔙 Back to Menu", callback_data="action_menu")
        )
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=settings_text, parse_mode="Markdown", reply_markup=markup)
        except Exception:
            pass

    elif call.data == "action_set_api":
        msg = bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="👇 **Please send your yshshopmails API Key now:**\n\n*(Your message will be automatically deleted for security)*", parse_mode="Markdown")
        bot.register_next_step_handler(call.message, process_api_key_step, msg.message_id)

    # --- BUY GMAIL CONFIRMATION LOGIC ---
    elif call.data == "action_buy_gmail":
        api_key = get_user_api_key(chat_id)
        if not api_key:
            bot.answer_callback_query(call.id, "⚠️ Please set your API Key in Settings first!", show_alert=True)
            return
            
        confirm_text = (
            "🛒 **Confirm Purchase**\n\n"
            "Are you sure you want to buy 1 Facebook Gmail account?\n"
            "Your balance will be deducted from your yshshopmails account."
        )
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("✅ Yes, Buy Now", callback_data="confirm_buy_gmail"),
            types.InlineKeyboardButton("❌ Cancel", callback_data="action_menu")
        )
        bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=confirm_text, parse_mode="Markdown", reply_markup=markup)

    # --- EXECUTE BUY GMAIL ---
    elif call.data == "confirm_buy_gmail":
        api_key = get_user_api_key(chat_id)
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="⏳ **Purchasing Gmail API Order... Please wait.**", parse_mode="Markdown")
            
            order_url = f"https://yshshopmails.com/v1/api/create-order.php?key={api_key}&service=facebook"
            resp = requests.get(order_url).json()
            
            if "mail" in resp and "order_id" in resp:
                email_address = resp["mail"]
                order_id = resp["order_id"]
                
                with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT user_id FROM users WHERE user_id=?", (chat_id,))
                    if cursor.fetchone():
                        cursor.execute("UPDATE users SET email=?, password=?, provider=?, refresh_token=NULL, client_id=NULL WHERE user_id=?", 
                                       (email_address, order_id, 'gmail', chat_id))
                    else:
                        cursor.execute("INSERT INTO users (user_id, email, password, provider) VALUES (?, ?, ?, ?)", 
                                       (chat_id, email_address, order_id, 'gmail'))
                    conn.commit()

                success_msg = (
                    f"🎉 **Gmail Purchased Successfully!**\n\n"
                    f"📧 **Email:** `{email_address}`\n"
                    f"🆔 **Order ID:** `{order_id}`\n\n"
                    f"🤖 *Bot is now automatically fetching Facebook OTP...*"
                )
                
                try:
                    bot.delete_message(chat_id, message_id)
                except:
                    pass
                    
                msg = bot.send_message(chat_id, success_msg, parse_mode="Markdown")
                active_menu_messages[chat_id] = msg.message_id
                
                time.sleep(2)
                fetch_and_send_emails(chat_id)
                
            elif "error" in resp:
                if "no mail" in resp["error"].lower():
                    err_txt = "❌ **Out of Stock!**\n\nThere are currently no Facebook Gmails available on yshshopmails. Please try again later."
                else:
                    err_txt = f"❌ **Failed to buy Gmail!**\nError: `{resp['error']}`"
                
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("🔙 Back to Menu", callback_data="action_menu"))
                bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=err_txt, parse_mode="Markdown", reply_markup=markup)
            else:
                bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"❌ **Unknown API Response:** `{resp}`", parse_mode="Markdown")
                
        except Exception as e:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"❌ **API Connection Error:** {e}", parse_mode="Markdown")

    elif call.data == "action_check_stock":
        bot.answer_callback_query(call.id, "⚠️ Stock API link pending from user!", show_alert=True)

    elif call.data == "action_refresh" or call.data == "action_refresh_direct":
        bot.answer_callback_query(call.id, "Refreshing Inbox...")
        fetch_and_send_emails(chat_id, edit_message_id=message_id)
        
    elif call.data.startswith("view_mail_"):
        idx = int(call.data.split("_")[2])
        send_full_mail_to_chat(chat_id, idx)
        bot.answer_callback_query(call.id)

def process_api_key_step(message, edit_msg_id):
    chat_id = message.chat.id
    api_key = message.text.strip()
    
    try:
        bot.delete_message(chat_id, message.message_id)
    except:
        pass

    set_user_api_key(chat_id, api_key)
    
    success_text = "✅ **API Key Saved Successfully!**\n\nYou can now use the 'Buy Gmail' feature."
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔙 Back to Menu", callback_data="action_menu"))
    
    try:
        bot.edit_message_text(chat_id=chat_id, message_id=edit_msg_id, text=success_text, parse_mode="Markdown", reply_markup=markup)
    except:
        sent_msg = bot.send_message(chat_id, success_text, parse_mode="Markdown", reply_markup=markup)
        active_menu_messages[chat_id] = sent_msg.message_id

# --- GLOBAL LISTENER ---
@bot.message_handler(func=lambda message: '|' in message.text)
def process_auto_credentials(message):
    chat_id = message.chat.id
    text = message.text.strip()
    
    try:
        bot.delete_message(chat_id, message.message_id)
    except:
        pass

    try:
        parts = [p.strip() for p in text.split('|')]
        
        if len(parts) == 2:
            email_address, app_password = parts[0], parts[1]
            provider = 'zoho'
            if "gmail" in email_address.lower():
                provider = 'gmail'
            elif "zoho" in email_address.lower():
                provider = 'zoho'
            
            with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT user_id FROM users WHERE user_id=?", (chat_id,))
                if cursor.fetchone():
                    cursor.execute("UPDATE users SET email=?, password=?, provider=?, refresh_token=NULL, client_id=NULL WHERE user_id=?", (email_address, app_password, provider, chat_id))
                else:
                    cursor.execute("INSERT INTO users (user_id, email, password, provider) VALUES (?, ?, ?, ?)", (chat_id, email_address, app_password, provider))
                conn.commit()

            msg_type = "API" if provider == 'gmail' else "Mail"
            msg = bot.send_message(chat_id, f"✅ **{provider.capitalize()} {msg_type} Detected!**\nConnecting to Inbox...", parse_mode="Markdown")
            
            if chat_id in active_menu_messages:
                try:
                    bot.delete_message(chat_id, active_menu_messages[chat_id])
                except:
                    pass
                    
            fetch_and_send_emails(chat_id)
            threading.Timer(3.0, lambda c=chat_id, m=msg.message_id: safe_delete(c, m)).start()

        elif len(parts) >= 4:
            email_address, password, refresh_token, client_id = parts[0], parts[1], parts[2], parts[3]
            
            with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT user_id FROM users WHERE user_id=?", (chat_id,))
                if cursor.fetchone():
                    cursor.execute("UPDATE users SET email=?, password=?, provider=?, refresh_token=?, client_id=? WHERE user_id=?", (email_address, password, 'hotmail', refresh_token, client_id, chat_id))
                else:
                    cursor.execute("INSERT INTO users (user_id, email, password, provider, refresh_token, client_id) VALUES (?, ?, ?, ?, ?, ?)", (chat_id, email_address, password, 'hotmail', refresh_token, client_id))
                conn.commit()

            msg = bot.send_message(chat_id, "✅ **Hotmail Detected!**\nConnecting to Inbox...", parse_mode="Markdown")
            
            if chat_id in active_menu_messages:
                try:
                    bot.delete_message(chat_id, active_menu_messages[chat_id])
                except:
                    pass

            fetch_and_send_emails(chat_id)
            threading.Timer(3.0, lambda c=chat_id, m=msg.message_id: safe_delete(c, m)).start()
        else:
            raise ValueError("Unknown format")

    except Exception:
        err_msg = bot.send_message(chat_id, "❌ **Invalid Format!**\nSend Zoho: `email|AppPassword`\nSend Gmail: `email|OrderID`\nSend Hotmail: `email|password|token|client_id`", parse_mode="Markdown")
        threading.Timer(5.0, lambda c=chat_id, m=err_msg.message_id: safe_delete(c, m)).start()

# --- Send Full Email (FIXED LOGO & FORMATTING) ---
def send_full_mail_to_chat(chat_id, idx):
    if chat_id in active_mail_messages:
        try:
            bot.delete_message(chat_id, active_mail_messages[chat_id])
        except:
            pass

    with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT subject, sender, full_content FROM email_cache WHERE user_id=? AND idx=?", (chat_id, idx))
        row = cursor.fetchone()
        
        # Get provider for dynamic logo
        cursor.execute("SELECT provider FROM users WHERE user_id=?", (chat_id,))
        user_row = cursor.fetchone()
        provider = user_row[0] if user_row else 'unknown'
    
    if not row:
        err_msg = bot.send_message(chat_id, "⚠️ Mail session expired. Please refresh the inbox.")
        threading.Timer(60, lambda c=chat_id, m=err_msg.message_id: safe_delete(c, m)).start()
        return
        
    subject, sender, full_content = row
    clean_body = clean_html_tags(full_content)
    
    # 🔴 FIX FOR TELEGRAM MARKDOWN ERROR 🔴
    # Remove characters from the body that trick Telegram into thinking it's broken Markdown
    safe_body = clean_body.replace('*', '').replace('_', '').replace('`', '').replace('[', '').replace(']', '')
    
    # 🔴 FIX FOR DYNAMIC LOGO 🔴
    if provider == 'gmail':
        logo_url = "https://upload.wikimedia.org/wikipedia/commons/thumb/7/7e/Gmail_icon_%282020%29.svg/512px-Gmail_icon_%282020%29.svg.png"
    elif provider == 'hotmail':
        logo_url = "https://i.ibb.co.com/x8LVnqMr/image-removebg-preview.png"
    elif provider == 'zoho':
        logo_url = "https://upload.wikimedia.org/wikipedia/commons/thumb/3/36/Zoho_Corporation_logo.svg/512px-Zoho_Corporation_logo.svg.png"
    else:
        logo_url = "https://cdn-icons-png.flaticon.com/512/732/732200.png" # generic mail icon
    
    message_text = (
        f"📬 **Email Details (FB Only)**\n\n"
        f"👤 **From:** `{sender}`\n"
        f"📌 **Subject:** `{subject}`\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"{safe_body[:3000]}\n\n"
        f"⚠️ *This message will automatically delete after 10 minutes.*"
    )
    
    try:
        sent_msg = bot.send_photo(chat_id, logo_url, caption=message_text, parse_mode="Markdown")
        if sent_msg:
            active_mail_messages[chat_id] = sent_msg.message_id
            threading.Timer(600, lambda c=chat_id, m=sent_msg.message_id: safe_delete(c, m)).start()
    except Exception as e:
        logging.error(f"Photo send error: {e}")
        # Fail-safe: Send text only if image load fails or text has weird parsing
        sent_msg = bot.send_message(chat_id, message_text, parse_mode="Markdown", disable_web_page_preview=True)
        if sent_msg:
            active_mail_messages[chat_id] = sent_msg.message_id
            threading.Timer(600, lambda c=chat_id, m=sent_msg.message_id: safe_delete(c, m)).start()

# --- Fetch Emails ---
def fetch_and_send_emails(chat_id, edit_message_id=None):
    with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT email, password, provider, refresh_token, client_id FROM users WHERE user_id=?", (chat_id,))
        result = cursor.fetchone()

    if not result:
        show_main_instruction(chat_id, message_id=edit_message_id)
        return

    email_address, password, provider, refresh_token, client_id = result
    response_text = ""
    cached_emails = []
    
    try:
        if provider == 'gmail':
            api_key = get_user_api_key(chat_id)
            if not api_key:
                api_key = "6804564184237369dmp0dUFS0G4xAHQy" # Fallback
                
            order_id = password
            api_url = f"https://yshshopmails.com/v1/api/check-otp.php?key={api_key}&id={order_id}"
            
            try:
                resp = requests.get(api_url)
                data = resp.json()
                
                if "otp" in data and data["otp"]:
                    otp_code = data["otp"]
                    subject = f"Facebook OTP: {otp_code}"
                    from_sender = "API@yshshopmails"
                    raw_html = f"Facebook OTP Code: {otp_code} (Verified and Fetched via Direct API)"
                    
                    cached_emails.append((subject, from_sender, raw_html))
                    response_text = f"📨 **Inbox ({email_address}) [API]:**\n\n"
                    response_text += f"🔹 **[📘 FACEBOOK OTP]** Code: `{otp_code}`\n📌 **Subject:** {subject}\n━━━━━━━━━━━━━━━━━━━\n"
                elif "error" in data:
                    response_text = f"❌ **API Error:** {data['error']}"
                else:
                    response_text = f"📭 **Inbox ({email_address})** No Facebook OTP found yet."
            except Exception as e:
                response_text = "❌ **API Connection Error:** Could not connect to API server."
                logging.error(f"Gmail API Error: {e}")

        elif provider == 'zoho':
            login_email = email_address
            if '+' in login_email and '@' in login_email:
                base_name, domain = login_email.split('@', 1)
                base_name = base_name.split('+')[0]
                login_email = f"{base_name}@{domain}"

            try:
                mail = imaplib.IMAP4_SSL('imap.zoho.com')
                mail.login(login_email, password)
                mail.select("inbox")
                status, messages = mail.search(None, "ALL")
                email_ids = messages[0].split()

                if not email_ids:
                    response_text = f"📭 **Inbox ({email_address})** is empty."
                else:
                    response_text = f"📨 **Inbox ({email_address}):**\n\n"
                    fb_found = False
                    for e_id in reversed(email_ids[-10:]):
                        status, msg_data = mail.fetch(e_id, "(RFC822)")
                        for response_part in msg_data:
                            if isinstance(response_part, tuple):
                                msg = email.message_from_bytes(response_part[1])
                                raw_html = get_html_body(msg)
                                subject, encoding = decode_header(msg["Subject"])[0]
                                if isinstance(subject, bytes):
                                    subject = subject.decode(encoding if encoding else "utf-8", errors="ignore")
                                from_ = msg.get("From", "Unknown")
                                
                                clean_b = clean_html_tags(raw_html)
                                lbl, code = detect_otp_type(subject, clean_b)
                                
                                if lbl: 
                                    cached_emails.append((subject, from_, raw_html))
                                    response_text += f"🔹 **[{lbl}]** Code: `{code}`\n📌 **Subject:** {subject}\n━━━━━━━━━━━━━━━━━━━\n"
                                    fb_found = True
                                    break
                        if fb_found:
                            break
                    if not fb_found:
                        response_text = f"📭 **Inbox ({email_address})** No Facebook OTP found."
                mail.logout()
            except imaplib.IMAP4.error as e:
                response_text = "❌ **IMAP Login Failed!** Check your App Password."

        elif provider == 'hotmail':
            url = "https://api-tools.yshshopmails.shop/api/v1/public/outlook/read_inbox"
            payload = {"data": f"{email_address}|{password}|{refresh_token}|{client_id}"}
            headers = {'Content-Type': 'application/json'}
            
            response = requests.post(url, json=payload, headers=headers)
            if response.status_code == 200 and response.json().get("success"):
                emails = response.json().get("data", [])
                if not emails:
                    response_text = f"📭 **Inbox ({email_address})** is empty."
                else:
                    response_text = f"📨 **Inbox ({email_address}):**\n\n"
                    fb_found = False
                    for msg in emails[:10]:
                        raw_body = msg.get("message", "No Content")
                        subject = msg.get("subject", "No Subject")
                        from_sender = msg.get("from", "Outlook User")
                        
                        clean_body = clean_html_tags(raw_body)
                        lbl, code = detect_otp_type(subject, clean_body)
                        
                        if lbl:
                            cached_emails.append((subject, from_sender, raw_body))
                            response_text += f"🔹 **[{lbl}]** Code: `{code}`\n📌 **Subject:** {subject}\n━━━━━━━━━━━━━━━━━━━\n"
                            fb_found = True
                            break
                    if not fb_found:
                        response_text = f"📭 **Inbox ({email_address})** No Facebook OTP found."
            else:
                response_text = "❌ **API Error:** Could not load Hotmail data."

        # Cache Update
        if cached_emails:
            with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM email_cache WHERE user_id=?", (chat_id,))
                for idx, (sub, snd, html_content) in enumerate(cached_emails):
                    cursor.execute("INSERT INTO email_cache (user_id, idx, subject, sender, full_content) VALUES (?, ?, ?, ?, ?)", 
                                   (chat_id, idx, sub, snd, html_content))
                conn.commit()

        current_time = datetime.now().strftime("%I:%M:%S %p")
        response_text += f"\n🕒 *Last Refresh:* {current_time}"
        
        markup = types.InlineKeyboardMarkup()
        if cached_emails:
            markup.row(types.InlineKeyboardButton("📖 Read Mail", callback_data="view_mail_0"))
        markup.row(types.InlineKeyboardButton("🔄 Refresh", callback_data="action_refresh"), types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))

        if edit_message_id:
            try:
                bot.edit_message_text(chat_id=chat_id, message_id=edit_message_id, text=response_text, parse_mode="Markdown", reply_markup=markup)
                active_menu_messages[chat_id] = edit_message_id
            except Exception:
                sent_msg = bot.send_message(chat_id, response_text, parse_mode="Markdown", reply_markup=markup)
                active_menu_messages[chat_id] = sent_msg.message_id
                threading.Timer(600, lambda c=chat_id, m=sent_msg.message_id: safe_delete(c, m)).start()
        else:
            sent_msg = bot.send_message(chat_id, response_text, parse_mode="Markdown", reply_markup=markup)
            active_menu_messages[chat_id] = sent_msg.message_id
            threading.Timer(600, lambda c=chat_id, m=sent_msg.message_id: safe_delete(c, m)).start()

    except Exception as e:
        error_msg = "⚠️ Error processing data or session expired."
        logging.error(f"Fetch Error: {e}")
        if edit_message_id:
            try:
                bot.edit_message_text(chat_id=chat_id, message_id=edit_message_id, text=error_msg, parse_mode="Markdown")
            except Exception:
                err_msg = bot.send_message(chat_id, error_msg)
                threading.Timer(60, lambda c=chat_id, m=err_msg.message_id: safe_delete(c, m)).start()
        else:
            err_msg = bot.send_message(chat_id, error_msg)
            threading.Timer(60, lambda c=chat_id, m=err_msg.message_id: safe_delete(c, m)).start()

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    cleanup_thread = threading.Thread(target=auto_cleanup_task, daemon=True)
    cleanup_thread.start()
    
    bot.remove_webhook()
    logging.info("Bot Started with Dynamic Logos & Error Filter!")
    
    while True:
        try:
            bot.infinity_polling(skip_pending=True, interval=1, timeout=20)
        except Exception as e:
            logging.error(f"Polling error: {e}")
            time.sleep(5)