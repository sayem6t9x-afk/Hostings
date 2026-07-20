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
from flask import Flask
import threading

# --- Setup Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Bot Token & App URL ---
BOT_TOKEN = '8465423862:AAHkZn88S_jr1aZpBZXzJb_EUxLSXscPZzo'
bot = telebot.TeleBot(BOT_TOKEN)

# Render-এর লিংকটি আমরা পরে Environment Variable দিয়ে সেট করবো
APP_URL = os.environ.get("APP_URL", "https://replace-this-with-your-url.com") 

# --- Web Server (Flask) Setup ---
app = Flask(__name__)
email_cache = {} # মেইলের HTML ক্যাশ করে রাখার জন্য

@app.route('/')
def home():
    return "Bot Server is Running!"

@app.route('/mail/<int:chat_id>/<int:idx>')
def view_mail(chat_id, idx):
    try:
        html_content = email_cache.get(chat_id, [])[idx]
        return html_content
    except IndexError:
        return "<h3>Error: Email not found or session expired. Please refresh the bot.</h3>"

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
            client_id TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()
user_states = {}

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

# --- Main Menu ---
@bot.message_handler(commands=['start', 'menu'])
def send_welcome(message):
    show_main_menu(message.chat.id)

def show_main_menu(chat_id):
    markup = types.InlineKeyboardMarkup(row_width=1)
    btn_zoho = types.InlineKeyboardButton("🏢 Zoho Mail Reader", callback_data="provider_zoho")
    btn_hotmail = types.InlineKeyboardButton("🔥 Hotmail Reader (API)", callback_data="provider_hotmail")
    markup.add(btn_zoho, btn_hotmail)
    
    bot.send_message(
        chat_id, 
        "👋 **Welcome to Pro Mail Reader Bot!**\n\nদয়া করে আপনার ইমেইল প্রোভাইডার সিলেক্ট করুন:", 
        parse_mode="Markdown",
        reply_markup=markup
    )

# --- Callback Handlers ---
@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    chat_id = call.message.chat.id
    
    if call.data.startswith("provider_"):
        provider = call.data.split("_")[1]
        user_states[chat_id] = provider
        
        if provider == 'zoho':
            bot.edit_message_text(chat_id=chat_id, message_id=call.message.message_id, text="Zoho Mail 🏢 - আপনার ডিটেইলস দিন:\n`email@zoho.com|AppPassword`", parse_mode="Markdown")
            bot.register_next_step_handler(call.message, process_zoho)
            
        elif provider == 'hotmail':
            bot.edit_message_text(chat_id=chat_id, message_id=call.message.message_id, text="Hotmail 🔥 - আপনার ডিটেইলস দিন:\n`email|password|refresh_token|client_id`", parse_mode="Markdown")
            bot.register_next_step_handler(call.message, process_hotmail)

    elif call.data == "action_refresh":
        bot.answer_callback_query(call.id, "Refreshing Inbox...")
        fetch_and_send_emails(chat_id, edit_message_id=call.message.message_id)
        
    elif call.data == "action_new_email":
        show_main_menu(chat_id)
            
    elif call.data == "action_menu":
        bot.delete_message(chat_id=chat_id, message_id=call.message.message_id)
        show_main_menu(chat_id)

# --- Process Credentials ---
def process_zoho(message):
    chat_id = message.chat.id
    text = message.text.strip()
    try:
        email_address, app_password = text.split('|')
        conn = sqlite3.connect('mail_bot.db')
        cursor = conn.cursor()
        cursor.execute("REPLACE INTO users (user_id, email, password, provider, refresh_token, client_id) VALUES (?, ?, ?, ?, ?, ?)", (chat_id, email_address.strip(), app_password.strip(), 'zoho', None, None))
        conn.commit()
        conn.close()
        bot.send_message(chat_id, "✅ **লগইন সফল!**", parse_mode="Markdown")
        fetch_and_send_emails(chat_id)
    except Exception:
        bot.send_message(chat_id, "❌ **ভুল ফরম্যাট!** আবার চেষ্টা করুন।")
        bot.register_next_step_handler(message, process_zoho)

def process_hotmail(message):
    chat_id = message.chat.id
    text = message.text.strip()
    try:
        email_address, password, refresh_token, client_id = text.split('|')
        conn = sqlite3.connect('mail_bot.db')
        cursor = conn.cursor()
        cursor.execute("REPLACE INTO users (user_id, email, password, provider, refresh_token, client_id) VALUES (?, ?, ?, ?, ?, ?)", (chat_id, email_address.strip(), password.strip(), 'hotmail', refresh_token.strip(), client_id.strip()))
        conn.commit()
        conn.close()
        bot.send_message(chat_id, "✅ **সেটআপ সম্পন্ন!**", parse_mode="Markdown")
        fetch_and_send_emails(chat_id)
    except Exception:
        bot.send_message(chat_id, "❌ **ভুল ফরম্যাট!** আবার চেষ্টা করুন।")
        bot.register_next_step_handler(message, process_hotmail)

# --- Fetch Emails ---
def fetch_and_send_emails(chat_id, edit_message_id=None):
    conn = sqlite3.connect('mail_bot.db')
    cursor = conn.cursor()
    cursor.execute("SELECT email, password, provider, refresh_token, client_id FROM users WHERE user_id=?", (chat_id,))
    result = cursor.fetchone()
    conn.close()

    if not result:
        show_main_menu(chat_id)
        return

    email_address, password, provider, refresh_token, client_id = result
    response_text = ""
    email_cache[chat_id] = [] # Reset cache
    
    try:
        # ================= ZOHO IMAP LOGIC =================
        if provider == 'zoho':
            mail = imaplib.IMAP4_SSL('imap.zoho.com')
            mail.login(email_address, password)
            mail.select("inbox")
            status, messages = mail.search(None, "ALL")
            email_ids = messages[0].split()

            if not email_ids:
                response_text = f"📭 **{email_address}** এর ইনবক্স ফাঁকা।"
            else:
                response_text = f"📨 **সর্বশেষ ইমেইল ({email_address}):**\n\n"
                for e_id in reversed(email_ids[-3:]):
                    status, msg_data = mail.fetch(e_id, "(RFC822)")
                    for response_part in msg_data:
                        if isinstance(response_part, tuple):
                            msg = email.message_from_bytes(response_part[1])
                            raw_html = get_html_body(msg)
                            email_cache[chat_id].append(raw_html)
                            
                            subject, encoding = decode_header(msg["Subject"])[0]
                            if isinstance(subject, bytes):
                                subject = subject.decode(encoding if encoding else "utf-8", errors="ignore")
                            from_ = msg.get("From")
                            response_text += f"🔹 **From:** {from_}\n📌 **Subject:** {subject}\n━━━━━━━━━━━━━━━━━━━\n"
            mail.logout()

        # ================= HOTMAIL YSHShop API LOGIC =================
        elif provider == 'hotmail':
            url = "https://api-tools.yshshopmails.shop/api/v1/public/outlook/read_inbox"
            payload = {"data": f"{email_address}|{password}|{refresh_token}|{client_id}"}
            headers = {'Content-Type': 'application/json'}
            
            response = requests.post(url, json=payload, headers=headers)
            if response.status_code == 200 and response.json().get("success"):
                emails = response.json().get("data", [])
                if not emails:
                    response_text = f"📭 **{email_address}** এর ইনবক্স ফাঁকা।"
                else:
                    response_text = f"📨 **আপনার ইনবক্স ({email_address}):**\n\n"
                    for msg in emails[:3]:
                        email_cache[chat_id].append(msg.get("message", "No Content"))
                        subject = msg.get("subject", "No Subject")
                        clean_body = clean_html_tags(msg.get("message", ""))[:150] + "..."
                        response_text += f"📌 **Subject:** {subject}\n📝 **Message:** {clean_body}\n━━━━━━━━━━━━━━━━━━━\n"
            else:
                response_text = "❌ **API Error:** ডেটা লোড করা যায়নি।"

        current_time = datetime.now().strftime("%I:%M:%S %p")
        response_text += f"\n🕒 *সর্বশেষ রিফ্রেশ:* {current_time}"
        
        # Setup Web App Buttons
        markup = types.InlineKeyboardMarkup()
        html_buttons = []
        for i in range(len(email_cache[chat_id])):
            web_app_url = f"{APP_URL}/mail/{chat_id}/{i}"
            # WebAppInfo ব্যবহার করে বাটন তৈরি
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
        bot.send_message(chat_id, "⚠️ ডেটা রিড করতে সমস্যা হচ্ছে।")

# --- Run Flask and Telebot Together ---
if __name__ == "__main__":
    # Start Flask server in a separate thread
    server_thread = threading.Thread(target=run_server)
    server_thread.start()
    
    logging.info("Bot and Web Server are starting...")
    bot.infinity_polling()