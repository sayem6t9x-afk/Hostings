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
        # NEW TABLE FOR BULK UPLOAD FEATURE
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bulk_accounts (
                email TEXT PRIMARY KEY,
                password TEXT,
                provider TEXT,
                refresh_token TEXT,
                client_id TEXT
            )
        ''')
        conn.commit()

init_db()

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
                # Clean active session but preserve API keys and Bulk Accounts
                cursor.execute("DELETE FROM users")
                cursor.execute("DELETE FROM email_cache")
                conn.commit()
                
            for c_id, m_id in list(active_menu_messages.items()):
                safe_delete(c_id, m_id)
            for c_id, m_id in list(active_mail_messages.items()):
                safe_delete(c_id, m_id)
                
            active_mail_messages.clear()
            active_menu_messages.clear()
        except Exception as e:
            logging.error(f"Cleanup error: {e}")

# --- HTML Cleaner & Extractor (CSS FIX APPLIED) ---
def clean_html_tags(raw_html):
    if not raw_html:
        return "No Content"
    text = html.unescape(raw_html)
    # Fix: Remove <style> and <script> tags COMPLETELY so CSS doesn't show up
    text = re.sub(r'<(style|script)[^>]*>[\s\S]*?</\1>', '', text, flags=re.IGNORECASE)
    # Remove remaining HTML tags
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
        types.InlineKeyboardButton("📊 Check Stock & Balance", callback_data="action_check_stock")
    )
    markup.add(
        types.InlineKeyboardButton("🔄 Refresh Inbox", callback_data="action_refresh_direct"),
        types.InlineKeyboardButton("⚙️ Settings (API)", callback_data="action_settings")
    )
    
    instruction_text = (
        "🤖 **Auto Secure FB Mail & OTP Reader Bot**\n\n"
        "**🔥 NEW: BULK UPLOAD MODE!**\n"
        "1. Send a `.txt` file containing your credentials.\n"
        "2. Then just send an *Email Address* here to fetch its OTP instantly.\n"
        "*(Bot will automatically remove it from the list after fetch)*\n\n"
        "**Manual Input Format:**\n"
        "🏢 **Zoho/Yandex:** `email|AppPassword`\n"
        "🔴 **Gmail:** `email@gmail.com|OrderID`\n"
        "🔥 **Hotmail:** `email|password|token|client_id`"
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

# --- Callback Handlers (Same as before) ---
@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    active_menu_messages[chat_id] = message_id
    
    if call.data == "action_menu":
        show_main_instruction(chat_id, message_id=message_id)

    elif call.data == "action_settings":
        current_api = get_user_api_key(chat_id)
        api_status = "✅ Set" if current_api else "❌ Not Set"
        
        settings_text = (
            "⚙️ **Bot Settings**\n\n"
            f"🔑 **yshshopmails API Key:** {api_status}\n\n"
            "If you haven't set your API key yet, click the button below to add it."
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
        msg = bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="👇 **Please send your yshshopmails API Key now:**", parse_mode="Markdown")
        bot.register_next_step_handler(call.message, process_api_key_step, msg.message_id)

    elif call.data == "action_check_stock":
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="⏳ **Fetching Live Stock & Balance...**", parse_mode="Markdown")
            stock_url = "https://yshshopmails.com/v1/stock"
            stock_params = {"service": "facebook"}
            stock_resp = requests.get(stock_url, params=stock_params).json()
            
            stock_count = stock_resp.get("stock", "Error")
            price = stock_resp.get("price", "Error")
            
            balance = "⚠️ API Key not set"
            api_key = get_user_api_key(chat_id)
            
            if api_key:
                bal_url = "https://yshshopmails.com/v1/api/user"
                bal_headers = {"api_key": api_key}
                bal_resp = requests.get(bal_url, headers=bal_headers).json()
                if "balance" in bal_resp:
                    balance = f"${bal_resp['balance']}"
                else:
                    balance = "❌ Invalid API Key"

            # Check local Bulk Accounts in DB
            with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM bulk_accounts")
                local_stock = cursor.fetchone()[0]

            dashboard_text = (
                "📊 **Live Stock & Balance Dashboard**\n"
                "━━━━━━━━━━━━━━━━━━━\n\n"
                f"📦 **Facebook Gmail Stock:** `{stock_count}` pcs\n"
                f"💰 **Price per account:** `${price}`\n"
                f"💳 **Your Balance:** `{balance}`\n\n"
                "━━━━━━━━━━━━━━━━━━━\n"
                f"📁 **Your Local TXT Stock:** `{local_stock}` accounts ready for Quick Fetch."
            )
            
            markup = types.InlineKeyboardMarkup()
            markup.row(
                types.InlineKeyboardButton("🔄 Refresh Stock", callback_data="action_check_stock"),
                types.InlineKeyboardButton("🛒 Buy Now", callback_data="action_buy_gmail")
            )
            markup.row(types.InlineKeyboardButton("🔙 Back to Menu", callback_data="action_menu"))
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=dashboard_text, parse_mode="Markdown", reply_markup=markup)
        except Exception as e:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"❌ **Error fetching data:** {e}", parse_mode="Markdown")

    elif call.data == "action_buy_gmail":
        api_key = get_user_api_key(chat_id)
        if not api_key:
            bot.answer_callback_query(call.id, "⚠️ Please set your API Key in Settings first!", show_alert=True)
            return
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("✅ Yes, Buy Now", callback_data="confirm_buy_gmail"),
            types.InlineKeyboardButton("❌ Cancel", callback_data="action_menu")
        )
        bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="🛒 **Confirm Purchase**\n\nAre you sure you want to buy 1 Facebook Gmail account?", parse_mode="Markdown", reply_markup=markup)

    elif call.data == "confirm_buy_gmail":
        api_key = get_user_api_key(chat_id)
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="⏳ **Purchasing Gmail API Order...**", parse_mode="Markdown")
            order_url = f"https://yshshopmails.com/v1/api/create-order.php?key={api_key}&service=facebook"
            resp = requests.get(order_url).json()
            
            if "mail" in resp and "order_id" in resp:
                email_address, order_id = resp["mail"], resp["order_id"]
                with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT user_id FROM users WHERE user_id=?", (chat_id,))
                    if cursor.fetchone():
                        cursor.execute("UPDATE users SET email=?, password=?, provider=?, refresh_token=NULL, client_id=NULL WHERE user_id=?", (email_address, order_id, 'gmail', chat_id))
                    else:
                        cursor.execute("INSERT INTO users (user_id, email, password, provider) VALUES (?, ?, ?, ?)", (chat_id, email_address, order_id, 'gmail'))
                    conn.commit()

                try:
                    bot.delete_message(chat_id, message_id)
                except: pass
                    
                msg = bot.send_message(chat_id, f"🎉 **Gmail Purchased Successfully!**\n\n📧 **Email:** `{email_address}`\n\n🤖 *Fetching Facebook OTP...*", parse_mode="Markdown")
                active_menu_messages[chat_id] = msg.message_id
                time.sleep(2)
                fetch_and_send_emails(chat_id)
            else:
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("🔙 Back to Menu", callback_data="action_menu"))
                bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"❌ **Failed to buy!**\nAPI Response: `{resp}`", parse_mode="Markdown", reply_markup=markup)
        except Exception as e:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"❌ **Error:** {e}", parse_mode="Markdown")

    elif call.data == "action_refresh" or call.data == "action_refresh_direct":
        bot.answer_callback_query(call.id, "Refreshing Inbox...")
        fetch_and_send_emails(chat_id, edit_message_id=message_id)
        
    elif call.data.startswith("view_mail_"):
        idx = int(call.data.split("_")[2])
        send_full_mail_to_chat(chat_id, idx)
        bot.answer_callback_query(call.id)

def process_api_key_step(message, edit_msg_id):
    chat_id, api_key = message.chat.id, message.text.strip()
    try: bot.delete_message(chat_id, message.message_id)
    except: pass
    set_user_api_key(chat_id, api_key)
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔙 Back to Menu", callback_data="action_menu"))
    try: bot.edit_message_text(chat_id=chat_id, message_id=edit_msg_id, text="✅ **API Key Saved!**", parse_mode="Markdown", reply_markup=markup)
    except: pass

# --- BULK UPLOAD HANDLER ---
@bot.message_handler(content_types=['document'])
def handle_document(message):
    chat_id = message.chat.id
    if not message.document.file_name.endswith('.txt'):
        bot.send_message(chat_id, "⚠️ Please upload a `.txt` file.")
        return
        
    try:
        msg = bot.send_message(chat_id, "⏳ **Reading File & Storing Accounts...**", parse_mode="Markdown")
        
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        lines = downloaded_file.decode('utf-8').strip().split('\n')
        
        success_count = 0
        with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
            cursor = conn.cursor()
            for line in lines:
                line = line.strip()
                if not line or '|' not in line: continue
                
                parts = [p.strip() for p in line.split('|')]
                email_address = parts[0]
                
                provider = 'unknown'
                if "gmail" in email_address.lower(): provider = 'gmail'
                elif "zoho" in email_address.lower(): provider = 'zoho'
                elif "yandex" in email_address.lower(): provider = 'yandex'
                elif len(parts) >= 4: provider = 'hotmail'
                else: provider = 'zoho' # fallback
                
                if len(parts) == 2:
                    cursor.execute("INSERT OR REPLACE INTO bulk_accounts (email, password, provider, refresh_token, client_id) VALUES (?, ?, ?, NULL, NULL)", (email_address, parts[1], provider))
                    success_count += 1
                elif len(parts) >= 4:
                    cursor.execute("INSERT OR REPLACE INTO bulk_accounts (email, password, provider, refresh_token, client_id) VALUES (?, ?, ?, ?, ?)", (email_address, parts[1], provider, parts[2], parts[3]))
                    success_count += 1
            conn.commit()
            
        bot.edit_message_text(chat_id=chat_id, message_id=msg.message_id, text=f"✅ **Bulk Upload Complete!**\n\nAdded `{success_count}` accounts to database.\n\n👉 Now just send an email address here (e.g., `test@hotmail.com`) to instantly fetch its OTP!", parse_mode="Markdown")
        
    except Exception as e:
        bot.send_message(chat_id, f"❌ Error processing file: {e}")

# --- GLOBAL TEXT LISTENER (Manual Full + Quick Fetch) ---
@bot.message_handler(func=lambda message: message.text and not message.text.startswith('/'))
def process_text_messages(message):
    chat_id = message.chat.id
    text = message.text.strip()
    
    try:
        bot.delete_message(chat_id, message.message_id)
    except:
        pass

    # 1. MANUAL FULL INPUT (Format: email|password|...)
    if '|' in text:
        try:
            parts = [p.strip() for p in text.split('|')]
            email_address = parts[0]
            
            provider = 'unknown'
            if "gmail" in email_address.lower(): provider = 'gmail'
            elif "zoho" in email_address.lower(): provider = 'zoho'
            elif "yandex" in email_address.lower(): provider = 'yandex'
            
            with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
                cursor = conn.cursor()
                if len(parts) == 2:
                    if provider == 'unknown': provider = 'zoho' # default
                    cursor.execute("SELECT user_id FROM users WHERE user_id=?", (chat_id,))
                    if cursor.fetchone():
                        cursor.execute("UPDATE users SET email=?, password=?, provider=?, refresh_token=NULL, client_id=NULL WHERE user_id=?", (email_address, parts[1], provider, chat_id))
                    else:
                        cursor.execute("INSERT INTO users (user_id, email, password, provider) VALUES (?, ?, ?, ?)", (chat_id, email_address, parts[1], provider))
                    
                    msg = bot.send_message(chat_id, f"✅ **{provider.capitalize()} Detected!**\nConnecting to Inbox...", parse_mode="Markdown")
                    
                elif len(parts) >= 4:
                    provider = 'hotmail'
                    cursor.execute("SELECT user_id FROM users WHERE user_id=?", (chat_id,))
                    if cursor.fetchone():
                        cursor.execute("UPDATE users SET email=?, password=?, provider=?, refresh_token=?, client_id=? WHERE user_id=?", (email_address, parts[1], provider, parts[2], parts[3], chat_id))
                    else:
                        cursor.execute("INSERT INTO users (user_id, email, password, provider, refresh_token, client_id) VALUES (?, ?, ?, ?, ?, ?)", (chat_id, email_address, parts[1], provider, parts[2], parts[3]))
                    
                    msg = bot.send_message(chat_id, "✅ **Hotmail Detected!**\nConnecting to Inbox...", parse_mode="Markdown")
                else:
                    raise ValueError("Format Error")
                conn.commit()

            if chat_id in active_menu_messages:
                try: bot.delete_message(chat_id, active_menu_messages[chat_id])
                except: pass
            
            fetch_and_send_emails(chat_id)
            threading.Timer(3.0, lambda c=chat_id, m=msg.message_id: safe_delete(c, m)).start()
        except Exception:
            err = bot.send_message(chat_id, "❌ **Invalid Format!**", parse_mode="Markdown")
            threading.Timer(5.0, lambda c=chat_id, m=err.message_id: safe_delete(c, m)).start()

    # 2. QUICK FETCH (Just an Email Address)
    elif '@' in text and '.' in text:
        with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT password, provider, refresh_token, client_id FROM bulk_accounts WHERE email=?", (text,))
            row = cursor.fetchone()
            
            if row:
                password, provider, refresh_token, client_id = row
                
                # Make it active user
                cursor.execute("SELECT user_id FROM users WHERE user_id=?", (chat_id,))
                if cursor.fetchone():
                    cursor.execute("UPDATE users SET email=?, password=?, provider=?, refresh_token=?, client_id=? WHERE user_id=?", (text, password, provider, refresh_token, client_id, chat_id))
                else:
                    cursor.execute("INSERT INTO users (user_id, email, password, provider, refresh_token, client_id) VALUES (?, ?, ?, ?, ?, ?)", (chat_id, text, password, provider, refresh_token, client_id))
                
                # BURN AFTER FETCH (Remove from bulk list)
                cursor.execute("DELETE FROM bulk_accounts WHERE email=?", (text,))
                conn.commit()
                
                msg = bot.send_message(chat_id, f"✅ **Account Loaded from Bulk List!**\n`{text}` has been removed from DB.\nFetching OTP...", parse_mode="Markdown")
                
                if chat_id in active_menu_messages:
                    try: bot.delete_message(chat_id, active_menu_messages[chat_id])
                    except: pass
                    
                fetch_and_send_emails(chat_id)
                threading.Timer(4.0, lambda c=chat_id, m=msg.message_id: safe_delete(c, m)).start()
            else:
                err = bot.send_message(chat_id, f"❌ **Email `{text}` not found in Bulk Database!**\nPlease upload a .txt file first.", parse_mode="Markdown")
                threading.Timer(5.0, lambda c=chat_id, m=err.message_id: safe_delete(c, m)).start()

# --- Send Full Email ---
def send_full_mail_to_chat(chat_id, idx):
    if chat_id in active_mail_messages:
        try: bot.delete_message(chat_id, active_mail_messages[chat_id])
        except: pass

    with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT subject, sender, full_content FROM email_cache WHERE user_id=? AND idx=?", (chat_id, idx))
        row = cursor.fetchone()
        
        cursor.execute("SELECT provider FROM users WHERE user_id=?", (chat_id,))
        user_row = cursor.fetchone()
        provider = user_row[0] if user_row else 'unknown'
    
    if not row:
        return
        
    subject, sender, full_content = row
    clean_body = clean_html_tags(full_content)
    safe_body = clean_body.replace('*', '').replace('_', '').replace('`', '').replace('[', '').replace(']', '')
    
    if provider == 'gmail': logo_url = "https://upload.wikimedia.org/wikipedia/commons/thumb/7/7e/Gmail_icon_%282020%29.svg/512px-Gmail_icon_%282020%29.svg.png"
    elif provider == 'hotmail': logo_url = "https://i.ibb.co.com/x8LVnqMr/image-removebg-preview.png"
    elif provider == 'zoho': logo_url = "https://upload.wikimedia.org/wikipedia/commons/thumb/3/36/Zoho_Corporation_logo.svg/512px-Zoho_Corporation_logo.svg.png"
    elif provider == 'yandex': logo_url = "https://upload.wikimedia.org/wikipedia/commons/thumb/8/80/Yandex_Mail_icon.svg/512px-Yandex_Mail_icon.svg.png"
    else: logo_url = "https://cdn-icons-png.flaticon.com/512/732/732200.png"
    
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
            api_key = get_user_api_key(chat_id) or "6804564184237369dmp0dUFS0G4xAHQy"
            order_id = password
            api_url = f"https://yshshopmails.com/v1/api/check-otp.php?key={api_key}&id={order_id}"
            
            try:
                data = requests.get(api_url).json()
                if "otp" in data and data["otp"]:
                    otp_code = data["otp"]
                    subject = f"Facebook OTP: {otp_code}"
                    cached_emails.append((subject, "API@yshshopmails", f"Facebook OTP Code: {otp_code}"))
                    response_text = f"📨 **Inbox ({email_address}) [API]:**\n\n🔹 **[📘 FACEBOOK OTP]** Code: `{otp_code}`\n📌 **Subject:** {subject}\n━━━━━━━━━━━━━━━━━━━\n"
                elif "error" in data:
                    response_text = f"❌ **API Error:** {data['error']}"
                else:
                    response_text = f"📭 **Inbox ({email_address})** No Facebook OTP found yet."
            except:
                response_text = "❌ **API Connection Error.**"

        elif provider in ['zoho', 'yandex']:
            login_email = email_address
            if '+' in login_email and '@' in login_email:
                base_name, domain = login_email.split('@', 1)
                login_email = f"{base_name.split('+')[0]}@{domain}"

            imap_server = 'imap.zoho.com' if provider == 'zoho' else 'imap.yandex.com'

            try:
                mail = imaplib.IMAP4_SSL(imap_server)
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
                        if fb_found: break
                    if not fb_found:
                        response_text = f"📭 **Inbox ({email_address})** No Facebook OTP found."
                mail.logout()
            except imaplib.IMAP4.error:
                response_text = "❌ **IMAP Login Failed!** Check your App Password."

        elif provider == 'hotmail':
            url = "https://api-tools.yshshopmails.shop/api/v1/public/outlook/read_inbox"
            response = requests.post(url, json={"data": f"{email_address}|{password}|{refresh_token}|{client_id}"}, headers={'Content-Type': 'application/json'})
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
                    cursor.execute("INSERT INTO email_cache (user_id, idx, subject, sender, full_content) VALUES (?, ?, ?, ?, ?)", (chat_id, idx, sub, snd, html_content))
                conn.commit()

        current_time = datetime.now().strftime("%I:%M:%S %p")
        response_text += f"\n🕒 *Last Refresh:* {current_time}"
        
        markup = types.InlineKeyboardMarkup()
        if cached_emails:
            markup.row(types.InlineKeyboardButton("📖 Read Mail", callback_data="view_mail_0"))
        markup.row(types.InlineKeyboardButton("🔄 Refresh", callback_data="action_refresh"), types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))

        if edit_message_id:
            try: bot.edit_message_text(chat_id=chat_id, message_id=edit_message_id, text=response_text, parse_mode="Markdown", reply_markup=markup)
            except: pass
        else:
            sent_msg = bot.send_message(chat_id, response_text, parse_mode="Markdown", reply_markup=markup)
            active_menu_messages[chat_id] = sent_msg.message_id
            threading.Timer(600, lambda c=chat_id, m=sent_msg.message_id: safe_delete(c, m)).start()

    except Exception as e:
        error_msg = "⚠️ Error processing data."
        if edit_message_id:
            try: bot.edit_message_text(chat_id=chat_id, message_id=edit_message_id, text=error_msg, parse_mode="Markdown")
            except: pass
        else:
            err = bot.send_message(chat_id, error_msg)
            threading.Timer(60, lambda c=chat_id, m=err.message_id: safe_delete(c, m)).start()

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    cleanup_thread = threading.Thread(target=auto_cleanup_task, daemon=True)
    cleanup_thread.start()
    
    bot.remove_webhook()
    logging.info("Bot Started with Bulk Upload & Yandex!")
    
    while True:
        try: bot.infinity_polling(skip_pending=True, interval=1, timeout=20)
        except Exception as e: time.sleep(5)