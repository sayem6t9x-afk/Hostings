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
from flask import Flask, send_from_directory
import threading
import time

# --- Setup Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Bot Token & App URL ---
BOT_TOKEN = '8465423862:AAHkZn88S_jr1aZpBZXzJb_EUxLSXscPZzo'
bot = telebot.TeleBot(BOT_TOKEN)

APP_URL = os.environ.get("APP_URL", "https://hostings-gtvu.onrender.com")  

# --- Web Server (Flask) Setup ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot Server is Running!"

# Routes to serve specific logos for Zoho and Hotmail
@app.route('/zoho_logo.png')
def serve_zoho_logo():
    return send_from_directory('.', 'zoho_logo.png')

@app.route('/hotmail_logo.png')
def serve_hotmail_logo():
    return send_from_directory('.', 'hotmail_logo.png')

@app.route('/mail/<int:chat_id>/<int:idx>')
def view_mail(chat_id, idx):
    try:
        conn = sqlite3.connect('mail_bot.db', check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("SELECT html_content FROM email_cache WHERE user_id=? AND idx=?", (chat_id, idx))
        row = cursor.fetchone()
        
        # Get provider to display respective logo
        cursor.execute("SELECT provider FROM users WHERE user_id=?", (chat_id,))
        user_row = cursor.fetchone()
        conn.close()
        
        provider = user_row[0] if user_row else 'zoho'
        logo_url = "/zoho_logo.png" if provider == 'zoho' else "/hotmail_logo.png"
        
        if row and row[0]:
            styled_content = f"""
            <html>
            <head>
                <style>
                    body {{ background-color: #121212; color: #e0e0e0; font-family: Arial, sans-serif; padding: 20px; }}
                    .header {{ text-align: center; margin-bottom: 20px; padding-bottom: 10px; border-bottom: 1px solid #333; }}
                    .header img {{ width: 60px; height: 60px; object-fit: contain; }}
                </style>
            </head>
            <body>
                <div class="header">
                    <img src="{logo_url}" alt="Logo">
                </div>
                {row[0]}
            </body>
            </html>
            """
            return styled_content
        else:
            return "<h3>⚠️ Mail session expired or not found. Please go back to the bot, click 'Refresh', and try opening the mail again.</h3>"
    except Exception as e:
        return f"<h3>Error loading email: {str(e)}</h3>"

def run_server():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

# --- Database Setup ---
def init_db():
    conn = sqlite3.connect('mail_bot.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            email TEXT,
            password TEXT,
            provider TEXT,
            refresh_token TEXT,
            client_id TEXT,
            language TEXT DEFAULT 'en',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS email_cache (
            user_id INTEGER,
            idx INTEGER,
            html_content TEXT,
            PRIMARY KEY (user_id, idx)
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# --- Background Task to Clean Data Every 10 Minutes ---
def auto_cleanup_task():
    while True:
        try:
            time.sleep(600) # 10 Minutes
            conn = sqlite3.connect('mail_bot.db', check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM users")
            cursor.execute("DELETE FROM email_cache")
            conn.commit()
            conn.close()
            logging.info("Auto-cleanup executed: Database cleared after 10 minutes.")
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

def get_user_lang(chat_id):
    conn = sqlite3.connect('mail_bot.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT language FROM users WHERE user_id=?", (chat_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row and row[0] else 'en'

# --- Main Menu / Start ---
@bot.message_handler(commands=['start', 'menu'])
def send_welcome(message):
    try:
        bot.delete_message(message.chat.id, message.message_id)
    except:
        pass
    
    chat_id = message.chat.id
    lang = get_user_lang(chat_id)
    
    if not lang or lang == 'en' and not has_user_record(chat_id):
        show_language_menu(chat_id)
    else:
        show_main_instruction(chat_id)

def has_user_record(chat_id):
    conn = sqlite3.connect('mail_bot.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE user_id=?", (chat_id,))
    row = cursor.fetchone()
    conn.close()
    return row is not None

def show_language_menu(chat_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"),
        types.InlineKeyboardButton("🇮🇳 हिन्दी", callback_data="lang_hi"),
        types.InlineKeyboardButton("🇰🇭 ភាសាខ្មែរ", callback_data="lang_km"),
        types.InlineKeyboardButton("🇧🇩 বাংলা", callback_data="lang_bn")
    )
    bot.send_message(
        chat_id,
        "🌐 **Please select your language:**",
        parse_mode="Markdown",
        reply_markup=markup
    )

def show_main_instruction(chat_id):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🌐 Change Language", callback_data="action_changelang"))
    
    instruction_text = (
        "🤖 **Auto Mail Reader Bot Ready!**\n\n"
        "Just send your mail credentials directly in chat:\n\n"
        "🏢 **For Zoho:** `email@zohomail.com|AppPassword`\n"
        "🔥 **For Hotmail:** `email|password|refresh_token|client_id`\n\n"
        "The bot will automatically detect the provider and fetch your inbox!"
    )
    bot.send_message(chat_id, instruction_text, parse_mode="Markdown", reply_markup=markup)
    bot.register_next_step_handler_by_chat_id(chat_id, process_auto_credentials)

# --- Callback Handlers ---
@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    chat_id = call.message.chat.id
    
    if call.data.startswith("lang_"):
        lang = call.data.split("_")[1]
        conn = sqlite3.connect('mail_bot.db', check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET language=? WHERE user_id=?", (lang, chat_id))
        if cursor.rowcount == 0:
            cursor.execute("INSERT INTO users (user_id, language) VALUES (?, ?)", (chat_id, lang))
        conn.commit()
        conn.close()
        
        bot.answer_callback_query(call.id, "Language saved successfully!")
        try:
            bot.delete_message(chat_id, call.message.message_id)
        except:
            pass
        show_main_instruction(chat_id)

    elif call.data == "action_changelang":
        try:
            bot.delete_message(chat_id, call.message.message_id)
        except:
            pass
        show_language_menu(chat_id)

    elif call.data == "action_refresh":
        bot.answer_callback_query(call.id, "Refreshing Inbox...")
        fetch_and_send_emails(chat_id, edit_message_id=call.message.message_id)
        
    elif call.data == "action_new_email":
        try:
            bot.delete_message(chat_id, call.message.message_id)
        except:
            pass
        show_main_instruction(chat_id)
            
    elif call.data == "action_menu":
        try:
            bot.delete_message(chat_id, call.message.message_id)
        except:
            pass
        show_main_instruction(chat_id)

# --- Auto Detect and Process Credentials ---
def process_auto_credentials(message):
    chat_id = message.chat.id
    text = message.text.strip()
    
    try:
        bot.delete_message(chat_id, message.message_id)
    except:
        pass

    try:
        parts = [p.strip() for p in text.split('|')]
        
        if len(parts) == 2 or ("zoho" in parts[0].lower()):
            email_address, app_password = parts[0], parts[1]
            conn = sqlite3.connect('mail_bot.db', check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET email=?, password=?, provider=?, refresh_token=NULL, client_id=NULL WHERE user_id=?", (email_address, app_password, 'zoho', chat_id))
            if cursor.rowcount == 0:
                cursor.execute("INSERT INTO users (user_id, email, password, provider) VALUES (?, ?, ?, ?)", (chat_id, email_address, app_password, 'zoho'))
            conn.commit()
            conn.close()

            msg = bot.send_message(chat_id, "✅ **Zoho Mail Detected & Login Successful!**", parse_mode="Markdown")
            fetch_and_send_emails(chat_id)
            threading.Timer(3.0, lambda: safe_delete(chat_id, msg.message_id)).start()

        elif len(parts) >= 4:
            email_address, password, refresh_token, client_id = parts[0], parts[1], parts[2], parts[3]
            conn = sqlite3.connect('mail_bot.db', check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET email=?, password=?, provider=?, refresh_token=?, client_id=? WHERE user_id=?", (email_address, password, 'hotmail', refresh_token, client_id, chat_id))
            if cursor.rowcount == 0:
                cursor.execute("INSERT INTO users (user_id, email, password, provider, refresh_token, client_id) VALUES (?, ?, ?, ?, ?, ?)", (chat_id, email_address, password, 'hotmail', refresh_token, client_id))
            conn.commit()
            conn.close()

            msg = bot.send_message(chat_id, "✅ **Hotmail API Detected & Setup Completed!**", parse_mode="Markdown")
            fetch_and_send_emails(chat_id)
            threading.Timer(3.0, lambda: safe_delete(chat_id, msg.message_id)).start()
        else:
            raise ValueError("Unknown format")

        bot.register_next_step_handler_by_chat_id(chat_id, process_auto_credentials)

    except Exception:
        err_msg = bot.send_message(chat_id, "❌ **Invalid Format!**\nSend Zoho: `email@zohomail.com|AppPassword`\nSend Hotmail: `email|password|refresh_token|client_id`", parse_mode="Markdown")
        bot.register_next_step_handler_by_chat_id(chat_id, process_auto_credentials)
        threading.Timer(5.0, lambda: safe_delete(chat_id, err_msg.message_id)).start()

def safe_delete(chat_id, message_id):
    try:
        bot.delete_message(chat_id, message_id)
    except:
        pass

# --- Fetch Emails ---
def fetch_and_send_emails(chat_id, edit_message_id=None):
    conn = sqlite3.connect('mail_bot.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT email, password, provider, refresh_token, client_id FROM users WHERE user_id=?", (chat_id,))
    result = cursor.fetchone()
    conn.close()

    if not result:
        show_main_instruction(chat_id)
        return

    email_address, password, provider, refresh_token, client_id = result
    response_text = ""
    fetched_htmls = []
    
    try:
        # ================= ZOHO IMAP LOGIC =================
        if provider == 'zoho':
            mail = imaplib.IMAP4_SSL('imap.zoho.com')
            mail.login(email_address, password)
            mail.select("inbox")
            status, messages = mail.search(None, "ALL")
            email_ids = messages[0].split()

            if not email_ids:
                response_text = f"📭 **{email_address}** Inbox is empty."
            else:
                response_text = f"📨 **Latest Emails ({email_address}):**\n\n"
                for e_id in reversed(email_ids[-3:]):
                    status, msg_data = mail.fetch(e_id, "(RFC822)")
                    for response_part in msg_data:
                        if isinstance(response_part, tuple):
                            msg = email.message_from_bytes(response_part[1])
                            raw_html = get_html_body(msg)
                            fetched_htmls.append(raw_html)
                            
                            subject, encoding = decode_header(msg["Subject"])[0]
                            if isinstance(subject, bytes):
                                subject = subject.decode(encoding if encoding else "utf-8", errors="ignore")
                            from_ = msg.get("From")
                            response_text += f"🔹 **From:** {from_}\n📌 **Subject:** {subject}\n━━━━━━━━━━━━━━━━━━━\n"
            mail.logout()

        # ================= HOTMAIL API LOGIC =================
        elif provider == 'hotmail':
            url = "https://api-tools.yshshopmails.shop/api/v1/public/outlook/read_inbox"
            payload = {"data": f"{email_address}|{password}|{refresh_token}|{client_id}"}
            headers = {'Content-Type': 'application/json'}
            
            response = requests.post(url, json=payload, headers=headers)
            if response.status_code == 200 and response.json().get("success"):
                emails = response.json().get("data", [])
                if not emails:
                    response_text = f"📭 **{email_address}** Inbox is empty."
                else:
                    response_text = f"📨 **Inbox ({email_address}):**\n\n"
                    for msg in emails[:3]:
                        fetched_htmls.append(msg.get("message", "No Content"))
                        subject = msg.get("subject", "No Subject")
                        clean_body = clean_html_tags(msg.get("message", ""))[:150] + "..."
                        response_text += f"📌 **Subject:** {subject}\n📝 **Message:** {clean_body}\n━━━━━━━━━━━━━━━━━━━\n"
            else:
                response_text = "❌ **API Error:** Could not load data."

        # Save HTMLs to Database Cache
        conn = sqlite3.connect('mail_bot.db', check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM email_cache WHERE user_id=?", (chat_id,))
        for idx, html_content in enumerate(fetched_htmls):
            cursor.execute("INSERT INTO email_cache (user_id, idx, html_content) VALUES (?, ?, ?)", (chat_id, idx, html_content))
        conn.commit()
        conn.close()

        current_time = datetime.now().strftime("%I:%M:%S %p")
        response_text += f"\n🕒 *Last Refresh:* {current_time}"
        
        # Setup Web App Buttons
        markup = types.InlineKeyboardMarkup()
        html_buttons = []
        for i in range(len(fetched_htmls)):
            web_app_url = f"{APP_URL}/mail/{chat_id}/{i}"
            html_buttons.append(types.InlineKeyboardButton(f"📱 Full Mail {i+1}", web_app=types.WebAppInfo(url=web_app_url)))
        
        for i in range(0, len(html_buttons), 2):
            markup.row(*html_buttons[i:i+2])

        markup.row(types.InlineKeyboardButton("🔄 Refresh", callback_data="action_refresh"), types.InlineKeyboardButton("➕ New Email", callback_data="action_new_email"))
        markup.row(types.InlineKeyboardButton("🔙 Back to Menu", callback_data="action_menu"))

        if edit_message_id:
            bot.edit_message_text(chat_id=chat_id, message_id=edit_message_id, text=response_text, parse_mode="Markdown", reply_markup=markup)
        else:
            bot.send_message(chat_id, response_text, parse_mode="Markdown", reply_markup=markup)

    except Exception as e:
        bot.send_message(chat_id, "⚠️ Error reading data.")

# --- Run Flask, Telegram Bot and Background Cleanup Together ---
if __name__ == "__main__":
    server_thread = threading.Thread(target=run_server)
    server_thread.start()
    
    cleanup_thread = threading.Thread(target=auto_cleanup_task, daemon=True)
    cleanup_thread.start()
    
    logging.info("Bot, Web Server and Auto-Cleanup are starting...")
    bot.infinity_polling()