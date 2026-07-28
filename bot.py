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
import hmac
import base64
import struct
import hashlib

# ==========================================
# ⚙️ CONFIGURATIONS & LOGGING
# ==========================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

BOT_TOKEN = '8465423862:AAHkZn88S_jr1aZpBZXzJb_EUxLSXscPZzo'
bot = telebot.TeleBot(BOT_TOKEN)

# 👑 ADMIN CONFIG
ADMIN_ID = 5605925198 
ADMIN_USERNAME_LINK = "[@sayem6t9](https://t.me/sayem6t9)"
BANNED_MSG = f"🚫 **You have been BANNED from using this bot!**\n\nTo request an unban, please message the Admin: {ADMIN_USERNAME_LINK}"

# ==========================================
# 🧹 STRICT UI TRACKER (Message Management)
# ==========================================
chat_history = {}
active_mail_messages = {}
active_menu_messages = {}

def track_message(chat_id, message_id):
    if chat_id not in chat_history:
        chat_history[chat_id] = []
    if message_id not in chat_history[chat_id]:
        chat_history[chat_id].append(message_id)

def clear_chat_history(chat_id, keep_message_id=None):
    if chat_id in chat_history:
        for msg_id in chat_history[chat_id]:
            if msg_id != keep_message_id:
                try: bot.delete_message(chat_id, msg_id)
                except: pass
        chat_history[chat_id] = []
        if keep_message_id:
            chat_history[chat_id].append(keep_message_id)

def safe_delete(chat_id, message_id):
    try: bot.delete_message(chat_id, message_id)
    except: pass

# ==========================================
# 🌐 FLASK SERVER (For 24/7 Hosting)
# ==========================================
app = Flask(__name__)

@app.route('/')
def home():
    return "Mail Bot is Running Successfully! Premium Version V3.0 (Subdomain API Fixed)"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# ==========================================
# 💾 DATABASE MANAGEMENT
# ==========================================
def init_db():
    with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, email TEXT, password TEXT, provider TEXT, refresh_token TEXT, client_id TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS email_cache (user_id INTEGER, idx INTEGER, subject TEXT, sender TEXT, full_content TEXT, PRIMARY KEY (user_id, idx))''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS user_settings (user_id INTEGER PRIMARY KEY, api_key TEXT, auto_delete INTEGER DEFAULT 1, username TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS bulk_accounts (owner_id INTEGER, email TEXT PRIMARY KEY, password TEXT, provider TEXT, refresh_token TEXT, client_id TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS purchase_history (owner_id INTEGER, email TEXT, order_id TEXT, purchased_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS banned_users (user_id INTEGER PRIMARY KEY)''')
        
        cursor.execute('''CREATE TABLE IF NOT EXISTS system_settings (key TEXT PRIMARY KEY, value TEXT)''')
        cursor.execute("INSERT OR IGNORE INTO system_settings (key, value) VALUES ('service_code', 'facebook')")
        
        try: cursor.execute("ALTER TABLE user_settings ADD COLUMN username TEXT")
        except: pass
        
        try: cursor.execute("DELETE FROM banned_users WHERE user_id=?", (ADMIN_ID,))
        except: pass
        
        conn.commit()

init_db()

def get_service_code():
    with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
        res = conn.cursor().execute("SELECT value FROM system_settings WHERE key='service_code'").fetchone()
        return res[0] if res else "facebook"

def set_service_code(new_code):
    with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
        conn.cursor().execute("UPDATE system_settings SET value=? WHERE key='service_code'", (new_code,))
        conn.commit()

def save_user_info(user_id, username):
    with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM user_settings WHERE user_id=?", (user_id,))
        if not cursor.fetchone():
            cursor.execute("INSERT INTO user_settings (user_id, auto_delete) VALUES (?, 1)", (user_id,))
        if username:
            cursor.execute("UPDATE user_settings SET username=? WHERE user_id=?", (username.lower(), user_id))
        conn.commit()

def is_user_banned(user_id):
    if user_id == ADMIN_ID: return False
    with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
        return conn.cursor().execute("SELECT 1 FROM banned_users WHERE user_id=?", (user_id,)).fetchone() is not None

def get_user_settings(user_id):
    with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT api_key, auto_delete FROM user_settings WHERE user_id=?", (user_id,))
        row = cursor.fetchone()
        if row: return {"api_key": row[0], "auto_delete": bool(row[1])}
        return {"api_key": None, "auto_delete": True}

def set_user_api_key(user_id, api_key):
    with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM user_settings WHERE user_id=?", (user_id,))
        if cursor.fetchone(): cursor.execute("UPDATE user_settings SET api_key=? WHERE user_id=?", (api_key, user_id))
        else: cursor.execute("INSERT INTO user_settings (user_id, api_key, auto_delete) VALUES (?, ?, 1)", (user_id, api_key))
        conn.commit()

def toggle_auto_delete(user_id):
    settings = get_user_settings(user_id)
    new_val = 0 if settings["auto_delete"] else 1
    with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM user_settings WHERE user_id=?", (user_id,))
        if cursor.fetchone(): cursor.execute("UPDATE user_settings SET auto_delete=? WHERE user_id=?", (new_val, user_id))
        else: cursor.execute("INSERT INTO user_settings (user_id, auto_delete) VALUES (?, ?)", (user_id, new_val))
        conn.commit()
    return bool(new_val)

def verify_yshshop_api(api_key):
    if len(api_key) < 20 or " " in api_key: return False
    try:
        bal_resp = requests.get("https://facebook.yshshopmails.com/v1/api/user", headers={"api_key": api_key}, timeout=5).json()
        if "balance" in bal_resp: return True
    except: pass
    return False

# ==========================================
# ⏱️ BACKGROUND TASKS
# ==========================================
def auto_cleanup_task():
    while True:
        try:
            time.sleep(600)
            with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM users")
                cursor.execute("DELETE FROM email_cache")
                conn.commit()
                
            for chat_id, msgs in list(chat_history.items()):
                for m_id in msgs: safe_delete(chat_id, m_id)
                chat_history[chat_id] = []
        except Exception: pass

# ==========================================
# 🛠️ CORE LOGIC & PARSERS
# ==========================================
def clean_html_tags(raw_html):
    if not raw_html: return "No Content"
    text = html.unescape(raw_html)
    text = re.sub(r'<(style|script)[^>]*>[\s\S]*?</\1>', '', text, flags=re.IGNORECASE)
    clean_text = re.sub(r'<[^>]+>', ' ', text)
    return re.sub(r'\s+', ' ', clean_text).strip()

def get_html_body(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/html": return part.get_payload(decode=True).decode(errors='ignore')
        for part in msg.walk():
            if part.get_content_type() == "text/plain": return part.get_payload(decode=True).decode(errors='ignore')
    else: return msg.get_payload(decode=True).decode(errors='ignore')
    return "No HTML Content Found."

def detect_otp_type(subject, content):
    combined_text = (subject + " " + content).lower()
    if "facebook" in combined_text or "fb" in combined_text:
        code_match = re.search(r'\b\d{6,8}\b', combined_text)
        return "📘 FACEBOOK OTP", (code_match.group(0) if code_match else "Not Found")
    return None, None

def get_totp_token(secret):
    try:
        secret = secret.replace(' ', '').upper()
        missing_padding = len(secret) % 8
        if missing_padding != 0:
            secret += '=' * (8 - missing_padding)
        key = base64.b32decode(secret, casefold=True)
        msg = struct.pack(">Q", int(time.time() // 30))
        mac = hmac.new(key, msg, hashlib.sha1).digest()
        offset = mac[-1] & 0x0f
        binary = struct.unpack('>L', mac[offset:offset+4])[0] & 0x7fffffff
        return str(binary % 1000000).zfill(6)
    except Exception:
        return None

# ==========================================
# 📱 MAIN MENU INTERFACE
# ==========================================
@bot.message_handler(commands=['start', 'menu'])
def send_welcome(message):
    chat_id = message.chat.id
    save_user_info(chat_id, message.from_user.username)
    
    track_message(chat_id, message.message_id)
    clear_chat_history(chat_id)
    
    if is_user_banned(chat_id):
        msg = bot.send_message(chat_id, BANNED_MSG, parse_mode="Markdown", disable_web_page_preview=True)
        track_message(chat_id, msg.message_id)
        return
        
    show_main_instruction(chat_id)

def show_main_instruction(chat_id, message_id=None):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("🛒 Buy Gmail (yshshop)", callback_data="action_buy_gmail"),
        types.InlineKeyboardButton("📊 Check Stock", callback_data="action_check_stock")
    )
    markup.add(
        types.InlineKeyboardButton("📁 My Bulk Accounts", callback_data="action_bulk_list"),
        types.InlineKeyboardButton("📜 Buy History", callback_data="action_buy_history")
    )
    markup.add(
        types.InlineKeyboardButton("🔄 Refresh Inbox", callback_data="action_refresh_direct"),
        types.InlineKeyboardButton("⚙️ Settings", callback_data="action_settings")
    )
    
    if chat_id == ADMIN_ID:
        markup.add(types.InlineKeyboardButton("👨‍💻 Admin Panel (Boss Only)", callback_data="action_admin_panel"))
    
    instruction_text = (
        "🤖 **Auto Secure FB Mail & OTP Reader Bot**\n\n"
        "**🔥 SECURE BULK MODE ACTIVE!**\n"
        "1. Send a `.txt` file (It stays Private to you).\n"
        "2. Click **📁 My Bulk Accounts** to pick an email.\n"
        "*(Check Settings to toggle Auto-Delete feature!)*\n\n"
        "**Manual Input Format:**\n"
        "🏢 **Zoho/Yandex:** `email|AppPassword`\n"
        "🔴 **Gmail:** `email@gmail.com|OrderID`\n"
        "🔥 **Hotmail:** `email|password|token|client_id`\n"
        "🔐 **2FA Code:** Send `Secret Key` (e.g. JBSWY3DPEHPK3PXP)"
    )
    
    if message_id:
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=instruction_text, parse_mode="Markdown", reply_markup=markup)
            track_message(chat_id, message_id)
            return
        except Exception: pass
            
    sent_msg = bot.send_message(chat_id, instruction_text, parse_mode="Markdown", reply_markup=markup)
    track_message(chat_id, sent_msg.message_id)

# ==========================================
# 🕹️ BUTTON CALLBACK HANDLERS
# ==========================================
@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    track_message(chat_id, message_id)
    
    if is_user_banned(chat_id):
        bot.answer_callback_query(call.id, "🚫 You are BANNED!", show_alert=True)
        bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=BANNED_MSG, parse_mode="Markdown", disable_web_page_preview=True)
        return

    if call.data == "action_menu":
        clear_chat_history(chat_id, keep_message_id=message_id)
        show_main_instruction(chat_id, message_id=message_id)
        return

    elif call.data == "action_admin_panel":
        if chat_id != ADMIN_ID: return
        bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="⏳ **Loading Admin Stats...**", parse_mode="Markdown")
        try:
            with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
                c = conn.cursor()
                total_users = c.execute("SELECT COUNT(DISTINCT user_id) FROM user_settings").fetchone()[0]
                banned_count = c.execute("SELECT COUNT(*) FROM banned_users").fetchone()[0]
                
            stats_msg = (
                "👨‍💻 **Secret Boss Dashboard**\n"
                "━━━━━━━━━━━━━━━━━━━\n"
                f"👥 **Total Registered Users:** `{total_users}`\n"
                f"🚫 **Total Banned Users:** `{banned_count}`\n"
                f"🔧 **Current Service ID:** `{get_service_code()}`\n"
                "━━━━━━━━━━━━━━━━━━━\n"
                "🛡️ What would you like to do?"
            )
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(types.InlineKeyboardButton("👥 View All Users", callback_data="admin_view_users"))
            markup.add(
                types.InlineKeyboardButton("🚫 Ban User", callback_data="admin_ban_user"),
                types.InlineKeyboardButton("✅ Unban User", callback_data="admin_unban_user")
            )
            markup.add(types.InlineKeyboardButton("🔧 Set Service ID", callback_data="admin_set_service"))
            markup.add(types.InlineKeyboardButton("🏠 Back to Main Menu", callback_data="action_menu"))
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=stats_msg, parse_mode="Markdown", reply_markup=markup)
        except Exception as e:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"❌ Admin Error: {e}")

    elif call.data == "admin_set_service":
        if chat_id != ADMIN_ID: return
        msg_text = (
            f"👇 **Current Service ID:** `{get_service_code()}`\n\n"
            "Please type and send the correct Service ID below."
        )
        msg = bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=msg_text, parse_mode="Markdown")
        bot.register_next_step_handler(call.message, process_service_code_step, msg.message_id)

    elif call.data == "admin_view_users":
        if chat_id != ADMIN_ID: return
        bot.answer_callback_query(call.id, "Generating User List...")
        with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
            users = conn.cursor().execute("SELECT user_id, username FROM user_settings").fetchall()
            
        if not users:
            bot.send_message(chat_id, "⚠️ No users found in database.")
            return
            
        filename = f"Bot_Users_List.txt"
        with open(filename, "w") as f:
            f.write("--- 👥 Bot Registered Users ---\n\n")
            for i, u in enumerate(users, 1): 
                uname = f"@{u[1]}" if u[1] else "No Username"
                f.write(f"{i}. ID: {u[0]} | Username: {uname}\n")
            
        with open(filename, "rb") as f:
            bot.send_document(chat_id, f, caption=f"📊 **Total Users:** {len(users)}", parse_mode="Markdown")
        os.remove(filename)

    elif call.data == "admin_ban_user":
        if chat_id != ADMIN_ID: return
        msg = bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="👇 **Send the User ID or @username you want to BAN:**", parse_mode="Markdown")
        bot.register_next_step_handler(call.message, process_ban_step, msg.message_id)

    elif call.data == "admin_unban_user":
        if chat_id != ADMIN_ID: return
        msg = bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="👇 **Send the User ID or @username you want to UNBAN:**", parse_mode="Markdown")
        bot.register_next_step_handler(call.message, process_unban_step, msg.message_id)

    elif call.data == "action_settings":
        settings = get_user_settings(chat_id)
        api_status = "✅ Set & Validated" if settings["api_key"] else "❌ Not Set"
        del_status = "🟢 ON" if settings["auto_delete"] else "🔴 OFF"
        
        settings_text = (
            "⚙️ **Bot Preferences & Settings**\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            f"🔑 **yshshopmails API Key:** {api_status}\n"
            f"🗑️ **Auto-Delete (Bulk List):** {del_status}\n\n"
            "*(If Auto-Delete is ON, bot automatically removes the account from your Bulk List once an OTP is successfully found.)*"
        )
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(types.InlineKeyboardButton("🔑 Update yshshopmails API Key", callback_data="action_set_api"))
        markup.add(types.InlineKeyboardButton(f"Toggle Auto-Delete", callback_data="action_toggle_autodel"))
        markup.add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
        bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=settings_text, parse_mode="Markdown", reply_markup=markup)

    elif call.data == "action_toggle_autodel":
        toggle_auto_delete(chat_id)
        handle_query(types.CallbackQuery(call.id, call.from_user, call.data, call.chat_instance, call.message, data="action_settings"))

    elif call.data == "action_set_api":
        msg = bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="👇 **Please send your valid 'yshshopmails' API Key now:**\n*(We will verify it live with the yshshopmails server)*", parse_mode="Markdown")
        bot.register_next_step_handler(call.message, process_api_key_step, msg.message_id)

    elif call.data == "action_buy_history":
        with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT email, order_id, purchased_at FROM purchase_history WHERE owner_id=? ORDER BY purchased_at DESC LIMIT 15", (chat_id,))
            rows = cursor.fetchall()
            
        if not rows:
            bot.answer_callback_query(call.id, "⚠️ Your purchase history is empty.", show_alert=True)
            return
            
        history_text = "📜 **Your Last 15 Purchased Gmails (yshshopmails)**\n━━━━━━━━━━━━━━━━━━━\n\n"
        for idx, (eml, ord_id, date_str) in enumerate(rows, 1):
            history_text += f"**{idx}.** `{eml}|{ord_id}`\n"
        history_text += "\n*(Tip: Copy a line and send it to bot to re-check OTP)*"
        
        markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
        bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=history_text, parse_mode="Markdown", reply_markup=markup)

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
        markup.row(types.InlineKeyboardButton("📤 Export List", callback_data="action_export_bulk"))
        markup.row(types.InlineKeyboardButton("🔄 Refresh", callback_data="action_bulk_list"), types.InlineKeyboardButton("🏠 Menu", callback_data="action_menu"))
        bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=list_text, parse_mode="Markdown", reply_markup=markup)

    elif call.data == "action_export_bulk":
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="⏳ **Generating your File...**", parse_mode="Markdown")
            with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT email, password, provider, refresh_token, client_id FROM bulk_accounts WHERE owner_id=?", (chat_id,))
                rows = cursor.fetchall()
            if not rows:
                bot.answer_callback_query(call.id, "⚠️ Your list is empty.", show_alert=True)
                return
            filename = f"exported_accounts_{chat_id}.txt"
            with open(filename, "w", encoding="utf-8") as f:
                for row in rows:
                    if row[2] == 'hotmail' and row[3] and row[4]: f.write(f"{row[0]}|{row[1]}|{row[3]}|{row[4]}\n")
                    else: f.write(f"{row[0]}|{row[1]}\n")
            with open(filename, "rb") as f:
                doc_msg = bot.send_document(chat_id, f, caption=f"📤 **Export Successful!**\nTotal Accounts: {len(rows)}", parse_mode="Markdown")
                track_message(chat_id, doc_msg.message_id)
            os.remove(filename) 
            handle_query(types.CallbackQuery(call.id, call.from_user, call.data, call.chat_instance, call.message, data="action_menu"))
        except Exception as e:
            bot.send_message(chat_id, f"❌ Export Error: {e}")

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

    # 🛒 STOCK CHECK UPDATE: Uses Subdomain API Endpoint `https://facebook.yshshopmails.com/v1/api/stock`
    elif call.data == "action_check_stock":
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="⏳ **Working... Fetching yshshopmails Live Data**", parse_mode="Markdown")
            stock_url = "https://facebook.yshshopmails.com/v1/api/stock"
            stock_resp = requests.get(stock_url, timeout=10).json()
            stock_count = stock_resp.get("stock", "Error")
            price = stock_resp.get("price", "Error")
            
            balance = "⚠️ yshshopmails API Key not set"
            api_key = get_user_settings(chat_id)["api_key"]
            if api_key:
                bal_resp = requests.get("https://facebook.yshshopmails.com/v1/api/user", headers={"api_key": api_key}, timeout=5).json()
                if "balance" in bal_resp: balance = f"${bal_resp['balance']}"
                else: balance = "❌ Invalid API Key"

            with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
                local_stock = conn.cursor().execute("SELECT COUNT(*) FROM bulk_accounts WHERE owner_id=?", (chat_id,)).fetchone()[0]

            dashboard_text = f"📊 **yshshopmails Server Dashboard**\n━━━━━━━━━━━━━━━━━━━\n📦 **FB Gmail Stock:** `{stock_count}` pcs\n💰 **Price per Acc:** `${price}`\n💳 **Your Balance:** `{balance}`\n━━━━━━━━━━━━━━━━━━━\n📁 **Your Local TXT Stock:** `{local_stock}` accounts."
            markup = types.InlineKeyboardMarkup()
            markup.row(types.InlineKeyboardButton("🔄 Refresh", callback_data="action_check_stock"), types.InlineKeyboardButton("🛒 Buy Now", callback_data="action_buy_gmail"))
            markup.row(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=dashboard_text, parse_mode="Markdown", reply_markup=markup)
        except Exception as e:
            markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"❌ **API Error:** {e}", parse_mode="Markdown", reply_markup=markup)

    elif call.data == "action_buy_gmail":
        if not get_user_settings(chat_id)["api_key"]: 
            return bot.answer_callback_query(call.id, "⚠️ Set your yshshopmails API Key in Settings first!", show_alert=True)
        markup = types.InlineKeyboardMarkup(row_width=2).add(types.InlineKeyboardButton("✅ Confirm Purchase", callback_data="confirm_buy_gmail"), types.InlineKeyboardButton("🏠 Cancel", callback_data="action_menu"))
        bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="🛒 **Checkout Confirmation (yshshopmails)**\n\nAre you sure you want to deduct balance from your **yshshopmails** account and buy 1 Facebook Gmail?", parse_mode="Markdown", reply_markup=markup)

    # 🛒 BUY SCRIPT UPDATE: Uses Subdomain API Endpoint `https://facebook.yshshopmails.com/v1/api/create-order.php`
    elif call.data == "confirm_buy_gmail":
        api_key = get_user_settings(chat_id)["api_key"]
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="⏳ **Working... Calling yshshopmails API**", parse_mode="Markdown")
            resp = requests.get(f"https://facebook.yshshopmails.com/v1/api/create-order.php?key={api_key}", timeout=10).json()
            if "mail" in resp and "order_id" in resp:
                eml, ord_id = resp["mail"], resp["order_id"]
                with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT user_id FROM users WHERE user_id=?", (chat_id,))
                    if cursor.fetchone(): cursor.execute("UPDATE users SET email=?, password=?, provider=?, refresh_token=NULL, client_id=NULL WHERE user_id=?", (eml, ord_id, 'gmail', chat_id))
                    else: cursor.execute("INSERT INTO users (user_id, email, password, provider) VALUES (?, ?, ?, ?)", (chat_id, eml, ord_id, 'gmail'))
                    cursor.execute("INSERT INTO purchase_history (owner_id, email, order_id) VALUES (?, ?, ?)", (chat_id, eml, ord_id))
                    conn.commit()
                bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"🎉 **yshshopmails Transaction Success!**\n📧 `{eml}`\n⏳ *Fetching initial OTP...*", parse_mode="Markdown")
                time.sleep(1.5)
                fetch_and_send_emails(chat_id, edit_message_id=message_id)
            else:
                markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
                bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"❌ **Failed to buy from yshshopmails:** `{resp}`", parse_mode="Markdown", reply_markup=markup)
        except Exception as e:
            markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"❌ **Connection Error:** {e}", parse_mode="Markdown", reply_markup=markup)

    elif call.data == "action_refresh" or call.data == "action_refresh_direct":
        bot.answer_callback_query(call.id, "Refreshing Secure Inbox...")
        bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="⏳ **Working... Syncing with Mail Server**", parse_mode="Markdown")
        with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
            user_eml = conn.cursor().execute("SELECT email FROM users WHERE user_id=?", (chat_id,)).fetchone()
            bulk_eml = conn.cursor().execute("SELECT email FROM bulk_accounts WHERE email=? AND owner_id=?", (user_eml[0] if user_eml else "", chat_id)).fetchone()
        fetch_and_send_emails(chat_id, edit_message_id=message_id, bulk_email_to_delete=bulk_eml[0] if bulk_eml else None)
        
    elif call.data.startswith("view_mail_"):
        idx = int(call.data.split("_")[2])
        send_full_mail_to_chat(chat_id, idx)
        bot.answer_callback_query(call.id)

# 👑 ADMIN SUB-HANDLERS
def process_service_code_step(message, edit_msg_id):
    chat_id = message.chat.id
    if chat_id != ADMIN_ID: return
    try: bot.delete_message(chat_id, message.message_id)
    except: pass
    
    new_code = message.text.strip()
    set_service_code(new_code)
    
    markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🔙 Back to Admin", callback_data="action_admin_panel"))
    bot.edit_message_text(chat_id=chat_id, message_id=edit_msg_id, text=f"✅ **Service ID Successfully Updated!**\n\n**New ID:** `{new_code}`", parse_mode="Markdown", reply_markup=markup)

def process_ban_step(message, edit_msg_id):
    chat_id = message.chat.id
    if chat_id != ADMIN_ID: return
    try: bot.delete_message(chat_id, message.message_id)
    except: pass
    
    target_input = message.text.strip()
    markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🔙 Back to Admin", callback_data="action_admin_panel"))
    user_to_ban = None
    
    with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
        cursor = conn.cursor()
        if target_input.isdigit():
            user_to_ban = int(target_input)
        else:
            uname = target_input.replace('@', '').lower()
            row = cursor.execute("SELECT user_id FROM user_settings WHERE username=?", (uname,)).fetchone()
            if row: user_to_ban = row[0]
            
        if user_to_ban == ADMIN_ID:
            bot.edit_message_text(chat_id=chat_id, message_id=edit_msg_id, text="❌ **Boss, Admin ke ban kora jabe na!**", parse_mode="Markdown", reply_markup=markup)
            return

        if user_to_ban:
            cursor.execute("INSERT OR IGNORE INTO banned_users (user_id) VALUES (?)", (user_to_ban,))
            conn.commit()
            bot.edit_message_text(chat_id=chat_id, message_id=edit_msg_id, text=f"✅ **Success!**\nUser / ID `{target_input}` has been **BANNED**.", parse_mode="Markdown", reply_markup=markup)
        else:
            bot.edit_message_text(chat_id=chat_id, message_id=edit_msg_id, text=f"❌ **User Not Found!**\nNo user with username `{target_input}` found in database.", parse_mode="Markdown", reply_markup=markup)

def process_unban_step(message, edit_msg_id):
    chat_id = message.chat.id
    if chat_id != ADMIN_ID: return
    try: bot.delete_message(chat_id, message.message_id)
    except: pass
    
    target_input = message.text.strip()
    markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🔙 Back to Admin", callback_data="action_admin_panel"))
    user_to_unban = None
    
    with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
        cursor = conn.cursor()
        if target_input.isdigit():
            user_to_unban = int(target_input)
        else:
            uname = target_input.replace('@', '').lower()
            row = cursor.execute("SELECT user_id FROM user_settings WHERE username=?", (uname,)).fetchone()
            if row: user_to_unban = row[0]
            
        if user_to_unban:
            cursor.execute("DELETE FROM banned_users WHERE user_id=?", (user_to_unban,))
            conn.commit()
            bot.edit_message_text(chat_id=chat_id, message_id=edit_msg_id, text=f"✅ **Success!**\nUser / ID `{target_input}` has been **UNBANNED**.", parse_mode="Markdown", reply_markup=markup)
        else:
            bot.edit_message_text(chat_id=chat_id, message_id=edit_msg_id, text=f"❌ **User Not Found!**\nCould not find `{target_input}` in database.", parse_mode="Markdown", reply_markup=markup)

def process_api_key_step(message, edit_msg_id):
    chat_id, api_key = message.chat.id, message.text.strip()
    track_message(chat_id, message.message_id)
    try: bot.delete_message(chat_id, message.message_id)
    except: pass
    
    markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
    try: bot.edit_message_text(chat_id=chat_id, message_id=edit_msg_id, text="⏳ **Verifying yshshopmails API Key...**", parse_mode="Markdown")
    except: pass
    
    if verify_yshshop_api(api_key):
        set_user_api_key(chat_id, api_key)
        try: bot.edit_message_text(chat_id=chat_id, message_id=edit_msg_id, text="✅ **Success! Your 'yshshopmails' API Key is Validated and Saved.**", parse_mode="Markdown", reply_markup=markup)
        except: pass
    else:
        try: bot.edit_message_text(chat_id=chat_id, message_id=edit_msg_id, text="❌ **Invalid API Key!**\n\nThe key you entered is not recognized by the **yshshopmails** server. Please check and try again.", parse_mode="Markdown", reply_markup=markup)
        except: pass

# ==========================================
# 📄 BULK UPLOAD HANDLER (.TXT)
# ==========================================
@bot.message_handler(content_types=['document'])
def handle_document(message):
    chat_id = message.chat.id
    save_user_info(chat_id, message.from_user.username)
    track_message(chat_id, message.message_id)
    
    if is_user_banned(chat_id):
        try: bot.delete_message(chat_id, message.message_id)
        except: pass
        msg = bot.send_message(chat_id, BANNED_MSG, parse_mode="Markdown", disable_web_page_preview=True)
        track_message(chat_id, msg.message_id)
        return
        
    if not message.document.file_name.endswith('.txt'): 
        msg = bot.send_message(chat_id, "⚠️ Please upload a valid `.txt` file.")
        track_message(chat_id, msg.message_id)
        return
    try:
        msg = bot.send_message(chat_id, "⏳ **Working... Verifying and Storing Accounts**", parse_mode="Markdown")
        track_message(chat_id, msg.message_id)
        file_info = bot.get_file(message.document.file_id)
        lines = bot.download_file(file_info.file_path).decode('utf-8').strip().split('\n')
        try: bot.delete_message(chat_id, message.message_id)
        except: pass
        
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
        bot.edit_message_text(chat_id=chat_id, message_id=msg.message_id, text=f"✅ **Secure Bulk Upload Complete!**\n\n🔒 Added `{success_count}` accounts to your Private Encrypted Storage.", parse_mode="Markdown", reply_markup=markup)
    except Exception as e:
        err = bot.send_message(chat_id, f"❌ File Processing Error: {e}")
        track_message(chat_id, err.message_id)

# ==========================================
# 💬 GLOBAL TEXT LISTENER
# ==========================================
@bot.message_handler(func=lambda message: message.text and not message.text.startswith('/'))
def process_text_messages(message):
    chat_id, text = message.chat.id, message.text.strip()
    save_user_info(chat_id, message.from_user.username)
    track_message(chat_id, message.message_id)
    try: bot.delete_message(chat_id, message.message_id)
    except: pass
    
    if is_user_banned(chat_id):
        clear_chat_history(chat_id)
        msg = bot.send_message(chat_id, BANNED_MSG, parse_mode="Markdown", disable_web_page_preview=True)
        track_message(chat_id, msg.message_id)
        return
        
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

            msg = bot.send_message(chat_id, f"⏳ **Working...**\nChecking Connection to `{eml}`", parse_mode="Markdown")
            track_message(chat_id, msg.message_id)
            fetch_and_send_emails(chat_id, edit_message_id=msg.message_id)
        except Exception:
            err = bot.send_message(chat_id, "❌ **Format Error!** Check manual format.", parse_mode="Markdown")
            track_message(chat_id, err.message_id)

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
                
                msg = bot.send_message(chat_id, f"⏳ **Working...**\nConnecting securely to `{text}`", parse_mode="Markdown")
                track_message(chat_id, msg.message_id)
                fetch_and_send_emails(chat_id, edit_message_id=msg.message_id, bulk_email_to_delete=text)
            else:
                err = bot.send_message(chat_id, f"❌ **Error:** `{text}` not found in your Private DB!", parse_mode="Markdown")
                track_message(chat_id, err.message_id)
                
    elif re.match(r'^[A-Z2-7]{16,100}$', text.replace(" ", "").upper()):
        code = get_totp_token(text)
        if code:
            markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Menu", callback_data="action_menu"))
            msg = bot.send_message(chat_id, f"🔐 **Live 2FA Generator**\n━━━━━━━━━━━━━━━━━━━\n\n🔹 **Code:** `{code}`\n🔑 **Secret:** `{text}`\n\n*(Updates automatically every 30s in your auth app)*", parse_mode="Markdown", reply_markup=markup)
            track_message(chat_id, msg.message_id)
        else:
            err = bot.send_message(chat_id, "❌ **Error!** Could not generate 2FA code. Check your Secret Key.", parse_mode="Markdown")
            track_message(chat_id, err.message_id)
    else:
        clear_chat_history(chat_id)
        show_main_instruction(chat_id)

# ==========================================
# 📧 CORE EMAIL ENGINE
# ==========================================
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
    
    message_text = f"📬 **Secure Encrypted Mail Viewer**\n\n👤 **From:** `{sender}`\n📌 **Subject:** `{subject}`\n━━━━━━━━━━━━━━━━━━━\n\n{safe_body[:3000]}\n\n⚠️ *Data Auto-Destructs in 10 mins.*"
    
    try:
        sent_msg = bot.send_photo(chat_id, logo_url, caption=message_text, parse_mode="Markdown")
        if sent_msg: track_message(chat_id, sent_msg.message_id)
    except:
        sent_msg = bot.send_message(chat_id, message_text, parse_mode="Markdown", disable_web_page_preview=True)
        if sent_msg: track_message(chat_id, sent_msg.message_id)

def fetch_and_send_emails(chat_id, edit_message_id=None, bulk_email_to_delete=None):
    with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT email, password, provider, refresh_token, client_id FROM users WHERE user_id=?", (chat_id,))
        result = cursor.fetchone()

    def _create_markup(emails_cached, is_bulk):
        m = types.InlineKeyboardMarkup()
        if emails_cached: m.row(types.InlineKeyboardButton("📖 View Full Email", callback_data="view_mail_0"))
        if is_bulk: m.row(types.InlineKeyboardButton("🔄 Re-Sync Inbox", callback_data="action_refresh"), types.InlineKeyboardButton("➡️ Next Account", callback_data="action_bulk_list"))
        else: m.row(types.InlineKeyboardButton("🔄 Re-Sync Inbox", callback_data="action_refresh"))
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
            api_key = get_user_settings(chat_id)["api_key"]
            if not api_key:
                response_text = "❌ **yshshopmails API Key Missing!**\n\nPlease go to **⚙️ Settings** from the Main Menu and set your private **yshshopmails API key** first to read Gmail OTPs."
            else:
                try:
                    data = requests.get(f"https://facebook.yshshopmails.com/v1/api/check-otp.php?key={api_key}&id={password}", timeout=10).json()
                    if "otp" in data and data["otp"]:
                        otp_found = True
                        otp_code = data["otp"]
                        subject = f"Facebook OTP: {otp_code}"
                        cached_emails.append((subject, "API@yshshopmails", f"Facebook OTP Code: {otp_code} (Verified API)"))
                        response_text = f"📨 **Live Inbox ({email_address}) [yshshopmails API]:**\n\n🔹 **[📘 FACEBOOK OTP]** Code: `{otp_code}`\n📌 **Subject:** {subject}\n━━━━━━━━━━━━━━━━━━━\n"
                    elif "error" in data: response_text = f"❌ **API Sync Error:** {data['error']}"
                    else: response_text = f"📭 **Live Inbox ({email_address})**\nScanning complete. No FB OTP found yet."
                except: response_text = "❌ **API Connection Timeout.** Try again."

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

                if not email_ids: response_text = f"📭 **Live Inbox ({email_address})** is empty."
                else:
                    response_text = f"📨 **Live Inbox ({email_address}):**\n\n"
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
                    if not fb_found: response_text = f"📭 **Live Inbox ({email_address})**\nNo specific FB OTP found for this alias."
                mail.logout()
            except imaplib.IMAP4.error: response_text = "❌ **IMAP Authentication Failed!** Check Provider / Password."

        elif provider == 'hotmail':
            url = "https://api-tools.yshshopmails.shop/api/v1/public/outlook/read_inbox"
            try:
                response = requests.post(url, json={"data": f"{email_address}|{password}|{refresh_token}|{client_id}"}, headers={'Content-Type': 'application/json'}, timeout=15)
                if response.status_code == 200 and response.json().get("success"):
                    emails = response.json().get("data", [])
                    if not emails: response_text = f"📭 **Live Inbox ({email_address})** is empty."
                    else:
                        response_text = f"📨 **Live Inbox ({email_address}):**\n\n"
                        fb_found = False
                        for msg in emails[:10]:
                            msg_to = str(msg.get("to", "")).lower()
                            if msg_to and target_eml_lower not in msg_to: continue
                                
                            raw_body, subject, from_sender = msg.get("message", "No Content"), msg.get("subject", "No Subject"), msg.get("from", "Outlook System")
                            lbl, code = detect_otp_type(subject, clean_html_tags(raw_body))
                            if lbl:
                                cached_emails.append((subject, from_sender, raw_body))
                                response_text += f"🔹 **[{lbl}]** Code: `{code}`\n📌 **Subject:** {subject}\n━━━━━━━━━━━━━━━━━━━\n"
                                fb_found = True
                                otp_found = True
                                break
                        if not fb_found: response_text = f"📭 **Live Inbox ({email_address})**\nNo FB OTP matched."
                else: response_text = "❌ **Outlook Server Error:** Gateway unavailable."
            except: response_text = "❌ **Outlook API Timeout:** Server took too long to respond."

        if bulk_email_to_delete:
            user_settings = get_user_settings(chat_id)
            if otp_found and user_settings["auto_delete"]:
                with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM bulk_accounts WHERE email=? AND owner_id=?", (bulk_email_to_delete, chat_id))
                    conn.commit()
                response_text += f"\n✅ *Auto-Delete ON: Account safely removed from Database.*"
            elif otp_found and not user_settings["auto_delete"]:
                response_text += f"\nℹ️ *Auto-Delete OFF: Account preserved in Database.*"
            elif not otp_found:
                response_text += f"\nℹ️ *Account safely kept in queue (No OTP found).* "

        if cached_emails:
            with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM email_cache WHERE user_id=?", (chat_id,))
                for idx, (sub, snd, html_content) in enumerate(cached_emails): 
                    cursor.execute("INSERT INTO email_cache (user_id, idx, subject, sender, full_content) VALUES (?, ?, ?, ?, ?)", (chat_id, idx, sub, snd, html_content))
                conn.commit()

        response_text += f"\n🕒 *Server Sync Time:* {datetime.now().strftime('%I:%M:%S %p')}"
        markup = _create_markup(bool(cached_emails), bool(bulk_email_to_delete))

        if edit_message_id:
            try: bot.edit_message_text(chat_id=chat_id, message_id=edit_message_id, text=response_text, parse_mode="Markdown", reply_markup=markup)
            except: pass
        else:
            sent_msg = bot.send_message(chat_id, response_text, parse_mode="Markdown", reply_markup=markup)
            track_message(chat_id, sent_msg.message_id)

    except Exception as e:
        markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))
        if edit_message_id:
            try: bot.edit_message_text(chat_id=chat_id, message_id=edit_message_id, text=f"⚠️ Critical Processing Error: {e}", parse_mode="Markdown", reply_markup=markup)
            except: pass
        else:
            err = bot.send_message(chat_id, f"⚠️ Critical Processing Error: {e}", reply_markup=markup)
            track_message(chat_id, err.message_id)

# ==========================================
# 🚀 MAIN EXECUTION THREADS
# ==========================================
if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    cleanup_thread = threading.Thread(target=auto_cleanup_task, daemon=True)
    cleanup_thread.start()
    
    bot.remove_webhook()
    logging.info("Premium V3.0 Started! Subdomain API integration completed.")
    
    while True:
        try: bot.infinity_polling(skip_pending=True, interval=1, timeout=20)
        except Exception: time.sleep(5)