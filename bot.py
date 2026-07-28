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
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bulk_accounts (
                owner_id INTEGER,
                email TEXT PRIMARY KEY,
                password TEXT,
                provider TEXT,
                refresh_token TEXT,
                client_id TEXT
            )
        ''')
        # 🔴 NEW: Table for tracking purchase history
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS purchase_history (
                owner_id INTEGER,
                email TEXT,
                order_id TEXT,
                purchased_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
    try: bot.delete_message(chat_id, message_id)
    except: pass

# --- Background Task ---
def auto_cleanup_task():
    while True:
        try:
            time.sleep(600)
            with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM users")
                cursor.execute("DELETE FROM email_cache")
                conn.commit()
                
            for c_id, m_id in list(active_menu_messages.items()): safe_delete(c_id, m_id)
            for c_id, m_id in list(active_mail_messages.items()): safe_delete(c_id, m_id)
                
            active_mail_messages.clear()
            active_menu_messages.clear()
        except Exception as e:
            logging.error(f"Cleanup error: {e}")

# --- HTML Cleaner & Extractor ---
def clean_html_tags(raw_html):
    if not raw_html: return "No Content"
    text = html.unescape(raw_html)
    text = re.sub(r'<(style|script)[^>]*>[\s\S]*?</\1>', '', text, flags=re.IGNORECASE)
    clean_text = re.sub(r'<[^>]+>', ' ', text)
    clean_text = re.sub(r'\s+', ' ', clean_text).strip()
    return clean_text

def get_html_body(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/html": return part.get_payload(decode=True).decode(errors='ignore')
        for part in msg.walk():
            if part.get_content_type() == "text/plain": return part.get_payload(decode=True).decode(errors='ignore')
    else:
        return msg.get_payload(decode=True).decode(errors='ignore')
    return "No HTML Content Found."

def detect_otp_type(subject, content):
    combined_text = (subject + " " + content).lower()
    if "facebook" in combined_text or "fb" in combined_text:
        code_match = re.search(r'\b\d{6,8}\b', combined_text)
        return "📘 FACEBOOK OTP", (code_match.group(0) if code_match else "Not Found")
    return None, None

# --- Main Menu / Start ---
@bot.message_handler(commands=['start', 'menu'])
def send_welcome(message):
    try: bot.delete_message(message.chat.id, message.message_id)
    except: pass
    show_main_instruction(message.chat.id)

def show_main_instruction(chat_id, message_id=None):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("🛒 Buy Gmail", callback_data="action_buy_gmail"),
        types.InlineKeyboardButton("📊 Check Stock", callback_data="action_check_stock")
    )
    markup.add(
        types.InlineKeyboardButton("📁 My Bulk Accounts", callback_data="action_bulk_list"),
        types.InlineKeyboardButton("📜 Buy History", callback_data="action_buy_history") # 🔴 New Button
    )
    markup.add(
        types.InlineKeyboardButton("🔄 Refresh Inbox", callback_data="action_refresh_direct"),
        types.InlineKeyboardButton("⚙️ Settings (API)", callback_data="action_settings")
    )
    
    instruction_text = (
        "🤖 **Auto Secure FB Mail & OTP Reader Bot**\n\n"
        "**🔥 SECURE BULK MODE ACTIVE!**\n"
        "1. Send a `.txt` file (It stays Private to you).\n"
        "2. Click **📁 My Bulk Accounts** to pick an email.\n"
        "*(Bot auto-deletes account from list ONLY if OTP is found!)*\n\n"
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
        except Exception: pass
            
    sent_msg = bot.send_message(chat_id, instruction_text, parse_mode="Markdown", reply_markup=markup)
    active_menu_messages[chat_id] = sent_msg.message_id
    threading.Timer(600, lambda c=chat_id, m=sent_msg.message_id: safe_delete(c, m)).start()

# --- Callback Handlers ---
@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    
    if call.data == "action_menu":
        safe_delete(chat_id, message_id)
        if chat_id in active_mail_messages: safe_delete(chat_id, active_mail_messages.pop(chat_id))
        if chat_id in active_menu_messages: safe_delete(chat_id, active_menu_messages.pop(chat_id))
        show_main_instruction(chat_id)
        return

    active_menu_messages[chat_id] = message_id

    # --- BUY HISTORY VIEWER ---
    if call.data == "action_buy_history":
        with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
            cursor = conn.cursor()
            # Fetch last 15 purchases for THIS specific user
            cursor.execute("SELECT email, order_id, purchased_at FROM purchase_history WHERE owner_id=? ORDER BY purchased_at DESC LIMIT 15", (chat_id,))
            rows = cursor.fetchall()
            
        if not rows:
            bot.answer_callback_query(call.id, "⚠️ Your purchase history is empty.", show_alert=True)
            return
            
        history_text = "📜 **Your Last Purchased Gmails**\n━━━━━━━━━━━━━━━━━━━\n\n"
        for idx, (eml, ord_id, date_str) in enumerate(rows, 1):
            history_text += f"**{idx}.** `{eml}|{ord_id}`\n"
            
        history_text += "\n*(Use this list to re-check OTPs if needed)*"
        
        markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
        bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=history_text, parse_mode="Markdown", reply_markup=markup)

    # --- BULK LIST SELECTOR ---
    elif call.data == "action_bulk_list":
        with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT rowid, email FROM bulk_accounts WHERE owner_id=? LIMIT 10", (chat_id,))
            rows = cursor.fetchall()
            cursor.execute("SELECT COUNT(*) FROM bulk_accounts WHERE owner_id=?", (chat_id,))
            total = cursor.fetchone()[0]
            
        if not rows:
            bot.answer_callback_query(call.id, "⚠️ Your Bulk List is empty! Upload a .txt file first.", show_alert=True)
            return
            
        list_text = f"📁 **Your Private Bulk Accounts ({total} remaining)**\n\n👇 Click an email below to fetch OTP:"
        markup = types.InlineKeyboardMarkup(row_width=1)
        
        for r_id, eml in rows: markup.add(types.InlineKeyboardButton(eml, callback_data=f"bf_{r_id}"))
            
        markup.row(types.InlineKeyboardButton("🔄 Refresh List", callback_data="action_bulk_list"), types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
        bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=list_text, parse_mode="Markdown", reply_markup=markup)

    # --- FETCH SPECIFIC BULK ACCOUNT ---
    elif call.data.startswith("bf_"):
        row_id = call.data.split("_")[1]
        with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT email, password, provider, refresh_token, client_id FROM bulk_accounts WHERE rowid=? AND owner_id=?", (row_id, chat_id))
            row = cursor.fetchone()
            
        if row:
            eml, pwd, prov, ref, cli = row
            with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT user_id FROM users WHERE user_id=?", (chat_id,))
                if cursor.fetchone(): cursor.execute("UPDATE users SET email=?, password=?, provider=?, refresh_token=?, client_id=? WHERE user_id=?", (eml, pwd, prov, ref, cli, chat_id))
                else: cursor.execute("INSERT INTO users (user_id, email, password, provider, refresh_token, client_id) VALUES (?, ?, ?, ?, ?, ?)", (chat_id, eml, pwd, prov, ref, cli))
                conn.commit()
                
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"⏳ **Working...**\nChecking `{eml}`", parse_mode="Markdown")
            fetch_and_send_emails(chat_id, edit_message_id=message_id, bulk_email_to_delete=eml)
        else:
            bot.answer_callback_query(call.id, "⚠️ Account not found! It may have been processed.", show_alert=True)
            handle_query(types.CallbackQuery(call.id, call.from_user, call.data, call.chat_instance, call.message, data="action_bulk_list"))

    elif call.data == "action_settings":
        current_api = get_user_api_key(chat_id)
        api_status = "✅ Set" if current_api else "❌ Not Set"
        settings_text = f"⚙️ **Bot Settings**\n\n🔑 **yshshopmails API Key:** {api_status}\n\nIf you haven't set your API key yet, click the button below to add it."
        markup = types.InlineKeyboardMarkup(row_width=1).add(types.InlineKeyboardButton("🔑 Set / Update API Key", callback_data="action_set_api"), types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
        bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=settings_text, parse_mode="Markdown", reply_markup=markup)

    elif call.data == "action_set_api":
        msg = bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="👇 **Please send your yshshopmails API Key now:**", parse_mode="Markdown")
        bot.register_next_step_handler(call.message, process_api_key_step, msg.message_id)

    elif call.data == "action_check_stock":
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="⏳ **Working... Fetching Data**", parse_mode="Markdown")
            stock_url = "https://yshshopmails.com/v1/stock"
            stock_resp = requests.get(stock_url, params={"service": "facebook"}).json()
            stock_count = stock_resp.get("stock", "Error")
            price = stock_resp.get("price", "Error")
            
            balance = "⚠️ API Key not set"
            api_key = get_user_api_key(chat_id)
            if api_key:
                bal_resp = requests.get("https://yshshopmails.com/v1/api/user", headers={"api_key": api_key}).json()
                if "balance" in bal_resp: balance = f"${bal_resp['balance']}"
                else: balance = "❌ Invalid API Key"

            with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
                local_stock = conn.cursor().execute("SELECT COUNT(*) FROM bulk_accounts WHERE owner_id=?", (chat_id,)).fetchone()[0]

            dashboard_text = f"📊 **Live Dashboard**\n━━━━━━━━━━━━━━━━━━━\n📦 **FB Gmail Stock:** `{stock_count}` pcs\n💰 **Price:** `${price}`\n💳 **Your Balance:** `{balance}`\n━━━━━━━━━━━━━━━━━━━\n📁 **Your Local TXT Stock:** `{local_stock}` accounts."
            
            markup = types.InlineKeyboardMarkup()
            markup.row(types.InlineKeyboardButton("🔄 Refresh", callback_data="action_check_stock"), types.InlineKeyboardButton("🛒 Buy Now", callback_data="action_buy_gmail"))
            markup.row(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=dashboard_text, parse_mode="Markdown", reply_markup=markup)
        except Exception as e:
            markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"❌ **Error:** {e}", parse_mode="Markdown", reply_markup=markup)

    elif call.data == "action_buy_gmail":
        if not get_user_api_key(chat_id): return bot.answer_callback_query(call.id, "⚠️ Set API Key in Settings first!", show_alert=True)
        markup = types.InlineKeyboardMarkup(row_width=2).add(types.InlineKeyboardButton("✅ Buy Now", callback_data="confirm_buy_gmail"), types.InlineKeyboardButton("🏠 Cancel", callback_data="action_menu"))
        bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="🛒 **Confirm Purchase**\n\nBuy 1 Facebook Gmail?", parse_mode="Markdown", reply_markup=markup)

    elif call.data == "confirm_buy_gmail":
        api_key = get_user_api_key(chat_id)
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="⏳ **Working... Purchasing Gmail**", parse_mode="Markdown")
            resp = requests.get(f"https://yshshopmails.com/v1/api/create-order.php?key={api_key}&service=facebook").json()
            if "mail" in resp and "order_id" in resp:
                eml, ord_id = resp["mail"], resp["order_id"]
                
                with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
                    cursor = conn.cursor()
                    # 🔴 1. Save to active user session
                    cursor.execute("SELECT user_id FROM users WHERE user_id=?", (chat_id,))
                    if cursor.fetchone(): cursor.execute("UPDATE users SET email=?, password=?, provider=?, refresh_token=NULL, client_id=NULL WHERE user_id=?", (eml, ord_id, 'gmail', chat_id))
                    else: cursor.execute("INSERT INTO users (user_id, email, password, provider) VALUES (?, ?, ?, ?)", (chat_id, eml, ord_id, 'gmail'))
                    
                    # 🔴 2. SAVE TO PURCHASE HISTORY
                    cursor.execute("INSERT INTO purchase_history (owner_id, email, order_id) VALUES (?, ?, ?)", (chat_id, eml, ord_id))
                    conn.commit()
                
                bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"🎉 **Purchased!**\n📧 `{eml}`\n⏳ *Working... Fetching OTP*", parse_mode="Markdown")
                time.sleep(2)
                fetch_and_send_emails(chat_id, edit_message_id=message_id)
            else:
                markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
                bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"❌ **Failed:** `{resp}`", parse_mode="Markdown", reply_markup=markup)
        except Exception as e:
            markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"❌ **Error:** {e}", parse_mode="Markdown", reply_markup=markup)

    elif call.data == "action_refresh" or call.data == "action_refresh_direct":
        bot.answer_callback_query(call.id, "Refreshing Inbox...")
        bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="⏳ **Working... Refreshing Inbox**", parse_mode="Markdown")
        with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
            user_eml = conn.cursor().execute("SELECT email FROM users WHERE user_id=?", (chat_id,)).fetchone()
            bulk_eml = conn.cursor().execute("SELECT email FROM bulk_accounts WHERE email=? AND owner_id=?", (user_eml[0] if user_eml else "", chat_id)).fetchone()
        
        fetch_and_send_emails(chat_id, edit_message_id=message_id, bulk_email_to_delete=bulk_eml[0] if bulk_eml else None)
        
    elif call.data.startswith("view_mail_"):
        idx = int(call.data.split("_")[2])
        send_full_mail_to_chat(chat_id, idx)
        bot.answer_callback_query(call.id)

def process_api_key_step(message, edit_msg_id):
    chat_id, api_key = message.chat.id, message.text.strip()
    try: bot.delete_message(chat_id, message.message_id)
    except: pass
    set_user_api_key(chat_id, api_key)
    markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
    try: bot.edit_message_text(chat_id=chat_id, message_id=edit_msg_id, text="✅ **API Key Saved!**", parse_mode="Markdown", reply_markup=markup)
    except: pass

# --- BULK UPLOAD HANDLER ---
@bot.message_handler(content_types=['document'])
def handle_document(message):
    chat_id = message.chat.id
    if not message.document.file_name.endswith('.txt'): return bot.send_message(chat_id, "⚠️ Please upload a `.txt` file.")
    try:
        msg = bot.send_message(chat_id, "⏳ **Working... Storing Accounts Safely**", parse_mode="Markdown")
        file_info = bot.get_file(message.document.file_id)
        lines = bot.download_file(file_info.file_path).decode('utf-8').strip().split('\n')
        
        success_count = 0
        with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
            cursor = conn.cursor()
            for line in lines:
                line = line.strip()
                if not line or '|' not in line: continue
                parts = [p.strip() for p in line.split('|')]
                eml = parts[0]
                prov = 'gmail' if 'gmail' in eml.lower() else 'zoho' if 'zoho' in eml.lower() else 'yandex' if 'yandex' in eml.lower() else 'hotmail' if len(parts) >= 4 else 'zoho'
                
                if len(parts) == 2:
                    cursor.execute("INSERT OR REPLACE INTO bulk_accounts (owner_id, email, password, provider, refresh_token, client_id) VALUES (?, ?, ?, ?, NULL, NULL)", (chat_id, eml, parts[1], prov))
                    success_count += 1
                elif len(parts) >= 4:
                    cursor.execute("INSERT OR REPLACE INTO bulk_accounts (owner_id, email, password, provider, refresh_token, client_id) VALUES (?, ?, ?, ?, ?, ?)", (chat_id, eml, parts[1], prov, parts[2], parts[3]))
                    success_count += 1
            conn.commit()
            
        markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
        bot.edit_message_text(chat_id=chat_id, message_id=msg.message_id, text=f"✅ **Bulk Upload Complete!**\n\n🔒 Added `{success_count}` accounts to your Private List.", parse_mode="Markdown", reply_markup=markup)
    except Exception as e:
        bot.send_message(chat_id, f"❌ Error: {e}")

# --- GLOBAL TEXT LISTENER ---
@bot.message_handler(func=lambda message: message.text and not message.text.startswith('/'))
def process_text_messages(message):
    chat_id, text = message.chat.id, message.text.strip()
    try: bot.delete_message(chat_id, message.message_id)
    except: pass

    if chat_id in active_menu_messages: safe_delete(chat_id, active_menu_messages.pop(chat_id))

    if '|' in text:
        try:
            parts = [p.strip() for p in text.split('|')]
            eml = parts[0]
            prov = 'gmail' if 'gmail' in eml.lower() else 'zoho' if 'zoho' in eml.lower() else 'yandex' if 'yandex' in eml.lower() else 'hotmail' if len(parts)>=4 else 'zoho'
            
            with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
                cursor = conn.cursor()
                if len(parts) == 2:
                    cursor.execute("SELECT user_id FROM users WHERE user_id=?", (chat_id,))
                    if cursor.fetchone(): cursor.execute("UPDATE users SET email=?, password=?, provider=?, refresh_token=NULL, client_id=NULL WHERE user_id=?", (eml, parts[1], prov, chat_id))
                    else: cursor.execute("INSERT INTO users (user_id, email, password, provider) VALUES (?, ?, ?, ?)", (chat_id, eml, parts[1], prov))
                elif len(parts) >= 4:
                    cursor.execute("SELECT user_id FROM users WHERE user_id=?", (chat_id,))
                    if cursor.fetchone(): cursor.execute("UPDATE users SET email=?, password=?, provider=?, refresh_token=?, client_id=? WHERE user_id=?", (eml, parts[1], prov, parts[2], parts[3], chat_id))
                    else: cursor.execute("INSERT INTO users (user_id, email, password, provider, refresh_token, client_id) VALUES (?, ?, ?, ?, ?, ?)", (chat_id, eml, parts[1], prov, parts[2], parts[3]))
                conn.commit()

            msg = bot.send_message(chat_id, f"⏳ **Working...**\nChecking `{eml}`", parse_mode="Markdown")
            fetch_and_send_emails(chat_id, edit_message_id=msg.message_id)
        except Exception:
            err = bot.send_message(chat_id, "❌ **Invalid Format!**", parse_mode="Markdown")
            threading.Timer(5.0, lambda c=chat_id, m=err.message_id: safe_delete(c, m)).start()

    elif '@' in text and '.' in text:
        with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT password, provider, refresh_token, client_id FROM bulk_accounts WHERE email=? AND owner_id=?", (text, chat_id))
            row = cursor.fetchone()
            
            if row:
                pwd, prov, ref, cli = row
                cursor.execute("SELECT user_id FROM users WHERE user_id=?", (chat_id,))
                if cursor.fetchone(): cursor.execute("UPDATE users SET email=?, password=?, provider=?, refresh_token=?, client_id=? WHERE user_id=?", (text, pwd, prov, ref, cli, chat_id))
                else: cursor.execute("INSERT INTO users (user_id, email, password, provider, refresh_token, client_id) VALUES (?, ?, ?, ?, ?, ?)", (chat_id, text, pwd, prov, ref, cli))
                conn.commit()
                
                msg = bot.send_message(chat_id, f"⏳ **Working...**\nChecking `{text}`", parse_mode="Markdown")
                fetch_and_send_emails(chat_id, edit_message_id=msg.message_id, bulk_email_to_delete=text)
            else:
                err = bot.send_message(chat_id, f"❌ **`{text}` not found in your Private DB!**", parse_mode="Markdown")
                threading.Timer(4.0, lambda c=chat_id, m=err.message_id: safe_delete(c, m)).start()

# --- Send Full Email ---
def send_full_mail_to_chat(chat_id, idx):
    if chat_id in active_mail_messages: safe_delete(chat_id, active_mail_messages.pop(chat_id))

    with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT subject, sender, full_content FROM email_cache WHERE user_id=? AND idx=?", (chat_id, idx))
        row = cursor.fetchone()
        cursor.execute("SELECT provider FROM users WHERE user_id=?", (chat_id,))
        user_row = cursor.fetchone()
        provider = user_row[0] if user_row else 'unknown'
    
    if not row: return
        
    subject, sender, full_content = row
    safe_body = clean_html_tags(full_content).replace('*', '').replace('_', '').replace('`', '').replace('[', '').replace(']', '')
    
    logo_url = "https://cdn-icons-png.flaticon.com/512/732/732200.png"
    if provider == 'gmail': logo_url = "https://upload.wikimedia.org/wikipedia/commons/thumb/7/7e/Gmail_icon_%282020%29.svg/512px-Gmail_icon_%282020%29.svg.png"
    elif provider == 'hotmail': logo_url = "https://i.ibb.co.com/x8LVnqMr/image-removebg-preview.png"
    elif provider == 'zoho': logo_url = "https://upload.wikimedia.org/wikipedia/commons/thumb/3/36/Zoho_Corporation_logo.svg/512px-Zoho_Corporation_logo.svg.png"
    elif provider == 'yandex': logo_url = "https://upload.wikimedia.org/wikipedia/commons/thumb/8/80/Yandex_Mail_icon.svg/512px-Yandex_Mail_icon.svg.png"
    
    message_text = f"📬 **Email Details (FB Only)**\n\n👤 **From:** `{sender}`\n📌 **Subject:** `{subject}`\n━━━━━━━━━━━━━━━━━━━\n\n{safe_body[:3000]}\n\n⚠️ *Auto-deletes in 10 mins.*"
    
    try:
        sent_msg = bot.send_photo(chat_id, logo_url, caption=message_text, parse_mode="Markdown")
        if sent_msg: active_mail_messages[chat_id] = sent_msg.message_id
    except:
        sent_msg = bot.send_message(chat_id, message_text, parse_mode="Markdown", disable_web_page_preview=True)
        if sent_msg: active_mail_messages[chat_id] = sent_msg.message_id
    threading.Timer(600, lambda c=chat_id, m=sent_msg.message_id: safe_delete(c, m)).start()

# --- Fetch Emails ---
def fetch_and_send_emails(chat_id, edit_message_id=None, bulk_email_to_delete=None):
    with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT email, password, provider, refresh_token, client_id FROM users WHERE user_id=?", (chat_id,))
        result = cursor.fetchone()

    def _create_markup(emails_cached, is_bulk):
        m = types.InlineKeyboardMarkup()
        if emails_cached: m.row(types.InlineKeyboardButton("📖 Read Mail", callback_data="view_mail_0"))
        if is_bulk: m.row(types.InlineKeyboardButton("🔄 Refresh", callback_data="action_refresh"), types.InlineKeyboardButton("➡️ Next Account", callback_data="action_bulk_list"))
        else: m.row(types.InlineKeyboardButton("🔄 Refresh", callback_data="action_refresh"))
        m.row(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
        return m

    if not result: return show_main_instruction(chat_id, message_id=edit_message_id)

    email_address, password, provider, refresh_token, client_id = result
    target_eml_lower = email_address.lower().strip()
    
    response_text = ""
    cached_emails = []
    otp_found = False
    
    try:
        if provider == 'gmail':
            api_key = get_user_api_key(chat_id) or "6804564184237369dmp0dUFS0G4xAHQy"
            try:
                data = requests.get(f"https://yshshopmails.com/v1/api/check-otp.php?key={api_key}&id={password}").json()
                if "otp" in data and data["otp"]:
                    otp_found = True
                    otp_code = data["otp"]
                    subject = f"Facebook OTP: {otp_code}"
                    cached_emails.append((subject, "API@yshshopmails", f"Facebook OTP Code: {otp_code}"))
                    response_text = f"📨 **Inbox ({email_address}) [API]:**\n\n🔹 **[📘 FACEBOOK OTP]** Code: `{otp_code}`\n📌 **Subject:** {subject}\n━━━━━━━━━━━━━━━━━━━\n"
                elif "error" in data: response_text = f"❌ **API Error:** {data['error']}"
                else: response_text = f"📭 **Inbox ({email_address})**\nNo Facebook OTP found yet."
            except: response_text = "❌ **API Connection Error.**"

        elif provider in ['zoho', 'yandex']:
            login_email = email_address
            if '+' in login_email and '@' in login_email: login_email = f"{login_email.split('+')[0]}@{login_email.split('@')[1]}"
            
            imap_server = 'imap.zoho.com' if provider == 'zoho' else 'imap.yandex.com'
            try:
                mail = imaplib.IMAP4_SSL(imap_server)
                mail.login(login_email, password)
                mail.select("inbox")
                status, messages = mail.search(None, "ALL")
                email_ids = messages[0].split()

                if not email_ids: response_text = f"📭 **Inbox ({email_address})** is empty."
                else:
                    response_text = f"📨 **Inbox ({email_address}):**\n\n"
                    fb_found = False
                    for e_id in reversed(email_ids[-15:]):
                        status, msg_data = mail.fetch(e_id, "(RFC822)")
                        for response_part in msg_data:
                            if isinstance(response_part, tuple):
                                msg = email.message_from_bytes(response_part[1])
                                
                                to_header = msg.get("To", "")
                                if to_header:
                                    to_hdr_decoded = decode_header(to_header)[0]
                                    to_str = to_hdr_decoded[0]
                                    if isinstance(to_str, bytes): to_str = to_str.decode(to_hdr_decoded[1] if to_hdr_decoded[1] else 'utf-8', errors='ignore')
                                    if target_eml_lower not in to_str.lower(): continue 

                                raw_html = get_html_body(msg)
                                subject, encoding = decode_header(msg["Subject"])[0]
                                if isinstance(subject, bytes): subject = subject.decode(encoding if encoding else "utf-8", errors="ignore")
                                from_ = msg.get("From", "Unknown")
                                
                                lbl, code = detect_otp_type(subject, clean_html_tags(raw_html))
                                if lbl: 
                                    cached_emails.append((subject, from_, raw_html))
                                    response_text += f"🔹 **[{lbl}]** Code: `{code}`\n📌 **Subject:** {subject}\n━━━━━━━━━━━━━━━━━━━\n"
                                    fb_found = True
                                    otp_found = True
                                    break
                        if fb_found: break
                    if not fb_found: response_text = f"📭 **Inbox ({email_address})**\nNo Facebook OTP found for this specific alias."
                mail.logout()
            except imaplib.IMAP4.error: response_text = "❌ **IMAP Login Failed!** Check your App Password."

        elif provider == 'hotmail':
            url = "https://api-tools.yshshopmails.shop/api/v1/public/outlook/read_inbox"
            response = requests.post(url, json={"data": f"{email_address}|{password}|{refresh_token}|{client_id}"}, headers={'Content-Type': 'application/json'})
            if response.status_code == 200 and response.json().get("success"):
                emails = response.json().get("data", [])
                if not emails: response_text = f"📭 **Inbox ({email_address})** is empty."
                else:
                    response_text = f"📨 **Inbox ({email_address}):**\n\n"
                    fb_found = False
                    for msg in emails[:10]:
                        msg_to = str(msg.get("to", "")).lower()
                        if msg_to and target_eml_lower not in msg_to: continue
                            
                        raw_body, subject, from_sender = msg.get("message", "No Content"), msg.get("subject", "No Subject"), msg.get("from", "Outlook User")
                        lbl, code = detect_otp_type(subject, clean_html_tags(raw_body))
                        if lbl:
                            cached_emails.append((subject, from_sender, raw_body))
                            response_text += f"🔹 **[{lbl}]** Code: `{code}`\n📌 **Subject:** {subject}\n━━━━━━━━━━━━━━━━━━━\n"
                            fb_found = True
                            otp_found = True
                            break
                    if not fb_found: response_text = f"📭 **Inbox ({email_address})**\nNo Facebook OTP found."
            else: response_text = "❌ **API Error:** Could not load Hotmail data."

        if otp_found and bulk_email_to_delete:
            with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM bulk_accounts WHERE email=? AND owner_id=?", (bulk_email_to_delete, chat_id))
                conn.commit()
            response_text += f"\n✅ *Account processed & removed from your list.*"
        elif not otp_found and bulk_email_to_delete:
            response_text += f"\nℹ️ *Account kept in your list (No OTP found).*"

        if cached_emails:
            with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM email_cache WHERE user_id=?", (chat_id,))
                for idx, (sub, snd, html_content) in enumerate(cached_emails): cursor.execute("INSERT INTO email_cache (user_id, idx, subject, sender, full_content) VALUES (?, ?, ?, ?, ?)", (chat_id, idx, sub, snd, html_content))
                conn.commit()

        response_text += f"\n🕒 *Last Refresh:* {datetime.now().strftime('%I:%M:%S %p')}"
        markup = _create_markup(bool(cached_emails), bool(bulk_email_to_delete))

        if edit_message_id:
            try: 
                bot.edit_message_text(chat_id=chat_id, message_id=edit_message_id, text=response_text, parse_mode="Markdown", reply_markup=markup)
                active_menu_messages[chat_id] = edit_message_id
            except: pass
        else:
            sent_msg = bot.send_message(chat_id, response_text, parse_mode="Markdown", reply_markup=markup)
            active_menu_messages[chat_id] = sent_msg.message_id
            threading.Timer(600, lambda c=chat_id, m=sent_msg.message_id: safe_delete(c, m)).start()

    except Exception as e:
        markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
        if edit_message_id:
            try: bot.edit_message_text(chat_id=chat_id, message_id=edit_message_id, text=f"⚠️ Error processing data: {e}", parse_mode="Markdown", reply_markup=markup)
            except: pass
        else:
            err = bot.send_message(chat_id, f"⚠️ Error processing data: {e}", reply_markup=markup)
            threading.Timer(60, lambda c=chat_id, m=err.message_id: safe_delete(c, m)).start()

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    cleanup_thread = threading.Thread(target=auto_cleanup_task, daemon=True)
    cleanup_thread.start()
    
    bot.remove_webhook()
    logging.info("Bot Started with Buy History Feature!")
    
    while True:
        try: bot.infinity_polling(skip_pending=True, interval=1, timeout=20)
        except Exception: time.sleep(5)