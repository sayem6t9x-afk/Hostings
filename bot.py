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

# --- Flask Server to Bind Port for Render Web Service ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Mail Bot is Running Successfully!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
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
            active_mail_messages.clear()
            active_menu_messages.clear()
            logging.info("Auto-cleanup executed: Database and active views cleared after 10 minutes.")
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

# --- Smart OTP / Service Detector ---
def detect_otp_type(subject, content):
    combined_text = (subject + " " + content).lower()
    
    service_name = "🔑 GENERAL OTP"
    if "facebook" in combined_text or "fb" in combined_text:
        service_name = "📘 FACEBOOK OTP (FB-OTP)"
    elif "instagram" in combined_text or "ig" in combined_text:
        service_name = "📸 INSTAGRAM OTP (IG-OTP)"
    elif "google" in combined_text or "gmail" in combined_text:
        service_name = "🌐 GOOGLE OTP"
    elif "whatsapp" in combined_text:
        service_name = "💚 WHATSAPP OTP"
    elif "telegram" in combined_text:
        service_name = "✈️ TELEGRAM OTP"
    elif "twitter" in combined_text or "x.com" in combined_text:
        service_name = "🐦 TWITTER / X OTP"
    elif "discord" in combined_text:
        service_name = "🎮 DISCORD OTP"
    elif "microsoft" in combined_text or "outlook" in combined_text:
        service_name = "🪟 MICROSOFT OTP"
    elif "netflix" in combined_text:
        service_name = "🍿 NETFLIX OTP"

    code_match = re.search(r'\b\d{4,8}\b', combined_text)
    extracted_code = code_match.group(0) if code_match else "Not Found"
    
    return service_name, extracted_code

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
        types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"),
        types.InlineKeyboardButton("❓ Help & Format", callback_data="action_help"),
        types.InlineKeyboardButton("ℹ️ About Bot", callback_data="action_about"),
        types.InlineKeyboardButton("🔄 Refresh Inbox", callback_data="action_refresh_direct")
    )
    
    instruction_text = (
        "🤖 **Auto Secure Mail & OTP Reader Bot**\n\n"
        "Send your mail credentials directly in chat to load your inbox:\n\n"
        "🏢 **For Zoho:** `email@zohomail.com|AppPassword`\n"
        "🔥 **For Hotmail:** `email|password|refresh_token|client_id`\n\n"
        "⚠️ *All data and opened emails automatically delete after 10 minutes for safety.*"
    )
    
    logo_url = "https://i.ibb.co.com/x8LVnqMr/image-removebg-preview.png"

    if message_id:
        try:
            bot.edit_message_media(
                chat_id=chat_id,
                message_id=message_id,
                media=types.InputMediaPhoto(logo_url, caption=instruction_text, parse_mode="Markdown"),
                reply_markup=markup
            )
            active_menu_messages[chat_id] = message_id
            bot.register_next_step_handler_by_chat_id(chat_id, process_auto_credentials)
            return
        except Exception:
            pass
            
    sent_msg = bot.send_photo(chat_id, logo_url, caption=instruction_text, parse_mode="Markdown", reply_markup=markup)
    active_menu_messages[chat_id] = sent_msg.message_id
    bot.register_next_step_handler_by_chat_id(chat_id, process_auto_credentials)

# --- Callback Handlers ---
@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    active_menu_messages[chat_id] = message_id
    
    if call.data == "action_menu":
        bot.answer_callback_query(call.id, "Welcome to Main Menu")
        show_main_instruction(chat_id, message_id=message_id)

    elif call.data == "action_help":
        bot.answer_callback_query(call.id, "Help Guide")
        help_text = (
            "📖 **Bot Usage Guide:**\n\n"
            "1. Send your credentials in the exact specified format.\n"
            "2. Click on any `📖 Read Mail` button to check full content and extracted OTP.\n"
            "3. Opening a new mail will instantly remove the previous one.\n"
            "4. Everything wipes out automatically every 10 minutes."
        )
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔙 Back to Menu", callback_data="action_menu"))
        logo_url = "https://i.ibb.co.com/x8LVnqMr/image-removebg-preview.png"
        try:
            bot.edit_message_media(
                chat_id=chat_id,
                message_id=message_id,
                media=types.InputMediaPhoto(logo_url, caption=help_text, parse_mode="Markdown"),
                reply_markup=markup
            )
        except Exception:
            pass

    elif call.data == "action_about":
        bot.answer_callback_query(call.id, "About Bot")
        about_text = (
            "ℹ️ **About Secure Mail Bot:**\n\n"
            "• Direct Chat Telegram Email & OTP Reader\n"
            "• Smart Auto-Detection for FB, IG, Google & More\n"
            "• Built-in Auto-Deletion Security"
        )
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔙 Back to Menu", callback_data="action_menu"))
        logo_url = "https://i.ibb.co.com/x8LVnqMr/image-removebg-preview.png"
        try:
            bot.edit_message_media(
                chat_id=chat_id,
                message_id=message_id,
                media=types.InputMediaPhoto(logo_url, caption=about_text, parse_mode="Markdown"),
                reply_markup=markup
            )
        except Exception:
            pass

    elif call.data == "action_refresh" or call.data == "action_refresh_direct":
        bot.answer_callback_query(call.id, "Refreshing Inbox...")
        fetch_and_send_emails(chat_id, edit_message_id=message_id)
        
    elif call.data.startswith("view_mail_"):
        idx = int(call.data.split("_")[2])
        send_full_mail_to_chat(chat_id, idx)
        bot.answer_callback_query(call.id)
        
    elif call.data == "action_new_email":
        show_main_instruction(chat_id, message_id=message_id)

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
            cursor.execute("SELECT user_id FROM users WHERE user_id=?", (chat_id,))
            if cursor.fetchone():
                cursor.execute("UPDATE users SET email=?, password=?, provider=?, refresh_token=NULL, client_id=NULL WHERE user_id=?", (email_address, app_password, 'zoho', chat_id))
            else:
                cursor.execute("INSERT INTO users (user_id, email, password, provider) VALUES (?, ?, ?, ?)", (chat_id, email_address, app_password, 'zoho'))
            conn.commit()
            conn.close()

            msg = bot.send_message(chat_id, "✅ **Zoho Mail Detected & Login Successful!**", parse_mode="Markdown")
            
            if chat_id in active_menu_messages:
                try:
                    bot.delete_message(chat_id, active_menu_messages[chat_id])
                except:
                    pass
                    
            fetch_and_send_emails(chat_id)
            threading.Timer(3.0, lambda: safe_delete(chat_id, msg.message_id)).start()

        elif len(parts) >= 4:
            email_address, password, refresh_token, client_id = parts[0], parts[1], parts[2], parts[3]
            conn = sqlite3.connect('mail_bot.db', check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM users WHERE user_id=?", (chat_id,))
            if cursor.fetchone():
                cursor.execute("UPDATE users SET email=?, password=?, provider=?, refresh_token=?, client_id=? WHERE user_id=?", (email_address, password, 'hotmail', refresh_token, client_id, chat_id))
            else:
                cursor.execute("INSERT INTO users (user_id, email, password, provider, refresh_token, client_id) VALUES (?, ?, ?, ?, ?, ?)", (chat_id, email_address, password, 'hotmail', refresh_token, client_id))
            conn.commit()
            conn.close()

            msg = bot.send_message(chat_id, "✅ **Hotmail API Detected & Setup Completed!**", parse_mode="Markdown")
            
            if chat_id in active_menu_messages:
                try:
                    bot.delete_message(chat_id, active_menu_messages[chat_id])
                except:
                    pass

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

# --- Send Full Email Content and Extracted OTP Directly to Chat ---
def send_full_mail_to_chat(chat_id, idx):
    if chat_id in active_mail_messages:
        try:
            bot.delete_message(chat_id, active_mail_messages[chat_id])
        except:
            pass

    conn = sqlite3.connect('mail_bot.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT subject, sender, full_content FROM email_cache WHERE user_id=? AND idx=?", (chat_id, idx))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        bot.send_message(chat_id, "⚠️ Mail session expired. Please refresh the inbox.")
        return
        
    subject, sender, full_content = row
    logo_url = "https://i.ibb.co.com/x8LVnqMr/image-removebg-preview.png"
    
    clean_body = clean_html_tags(full_content)
    otp_label, otp_code = detect_otp_type(subject, clean_body)
    
    message_text = (
        f"📬 **Email Details #{idx + 1}**\n\n"
        f"🏷️ **Detected Type:** `{otp_label}`\n"
        f"🔑 **Extracted Code:** `{otp_code}`\n\n"
        f"👤 **From:** {sender}\n"
        f"📌 **Subject:** {subject}\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"{clean_body[:3000]}\n\n"
        f"⚠️ *This message will automatically delete after 10 minutes.*"
    )
    
    try:
        sent_msg = bot.send_photo(chat_id, logo_url, caption=message_text, parse_mode="Markdown")
        if sent_msg:
            active_mail_messages[chat_id] = sent_msg.message_id
            threading.Timer(600, lambda: safe_delete(chat_id, sent_msg.message_id)).start()
    except Exception:
        sent_msg = bot.send_message(chat_id, message_text, parse_mode="Markdown")
        if sent_msg:
            active_mail_messages[chat_id] = sent_msg.message_id
            threading.Timer(600, lambda: safe_delete(chat_id, sent_msg.message_id)).start()

# --- Fetch Emails ---
def fetch_and_send_emails(chat_id, edit_message_id=None):
    conn = sqlite3.connect('mail_bot.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT email, password, provider, refresh_token, client_id FROM users WHERE user_id=?", (chat_id,))
    result = cursor.fetchone()
    conn.close()

    if not result:
        show_main_instruction(chat_id, message_id=edit_message_id)
        return

    email_address, password, provider, refresh_token, client_id = result
    response_text = ""
    cached_emails = []
    
    try:
        if provider == 'zoho':
            mail = imaplib.IMAP4_SSL('imap.zoho.com')
            mail.login(email_address, password)
            mail.select("inbox")
            status, messages = mail.search(None, "ALL")
            email_ids = messages[0].split()

            if not email_ids:
                response_text = f"📭 **{email_address}** Inbox is empty."
            else:
                response_text = f"📨 **Latest Inbox ({email_address}):**\n\n"
                for e_id in reversed(email_ids[-3:]):
                    status, msg_data = mail.fetch(e_id, "(RFC822)")
                    for response_part in msg_data:
                        if isinstance(response_part, tuple):
                            msg = email.message_from_bytes(response_part[1])
                            raw_html = get_html_body(msg)
                            
                            subject, encoding = decode_header(msg["Subject"])[0]
                            if isinstance(subject, bytes):
                                subject = subject.decode(encoding if encoding else "utf-8", errors="ignore")
                            from_ = msg.get("From", "Unknown")
                            
                            cached_emails.append((subject, from_, raw_html))
                            clean_b = clean_html_tags(raw_html)
                            lbl, code = detect_otp_type(subject, clean_b)
                            response_text += f"🔹 **[{lbl}]** Code: `{code}`\n📌 **Subject:** {subject}\n━━━━━━━━━━━━━━━━━━━\n"
            mail.logout()

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
                        raw_body = msg.get("message", "No Content")
                        subject = msg.get("subject", "No Subject")
                        from_sender = msg.get("from", "Outlook User")
                        
                        cached_emails.append((subject, from_sender, raw_body))
                        clean_body = clean_html_tags(raw_body)
                        lbl, code = detect_otp_type(subject, clean_body)
                        response_text += f"🔹 **[{lbl}]** Code: `{code}`\n📌 **Subject:** {subject}\n━━━━━━━━━━━━━━━━━━━\n"
            else:
                response_text = "❌ **API Error:** Could not load data."

        conn = sqlite3.connect('mail_bot.db', check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM email_cache WHERE user_id=?", (chat_id,))
        for idx, (sub, snd, html_content) in enumerate(cached_emails):
            cursor.execute("INSERT INTO email_cache (user_id, idx, subject, sender, full_content) VALUES (?, ?, ?, ?, ?)", 
                           (chat_id, idx, sub, snd, html_content))
        conn.commit()
        conn.close()

        current_time = datetime.now().strftime("%I:%M:%S %p")
        response_text += f"\n🕒 *Last Refresh:* {current_time}"
        
        markup = types.InlineKeyboardMarkup()
        mail_buttons = []
        for i in range(len(cached_emails)):
            mail_buttons.append(types.InlineKeyboardButton(f"📖 Read Mail {i+1}", callback_data=f"view_mail_{i}"))
        
        for i in range(0, len(mail_buttons), 2):
            markup.row(*mail_buttons[i:i+2])

        markup.row(types.InlineKeyboardButton("🔄 Refresh", callback_data="action_refresh"), types.InlineKeyboardButton("➕ New Email", callback_data="action_new_email"))
        markup.row(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))

        logo_url = "https://i.ibb.co.com/x8LVnqMr/image-removebg-preview.png"

        if edit_message_id:
            try:
                bot.edit_message_media(
                    chat_id=chat_id,
                    message_id=edit_message_id,
                    media=types.InputMediaPhoto(logo_url, caption=response_text, parse_mode="Markdown"),
                    reply_markup=markup
                )
                active_menu_messages[chat_id] = edit_message_id
            except Exception:
                try:
                    bot.edit_message_text(chat_id=chat_id, message_id=edit_message_id, text=response_text, parse_mode="Markdown", reply_markup=markup)
                    active_menu_messages[chat_id] = edit_message_id
                except Exception:
                    sent_msg = bot.send_photo(chat_id, logo_url, caption=response_text, parse_mode="Markdown", reply_markup=markup)
                    active_menu_messages[chat_id] = sent_msg.message_id
        else:
            sent_msg = bot.send_photo(chat_id, logo_url, caption=response_text, parse_mode="Markdown", reply_markup=markup)
            active_menu_messages[chat_id] = sent_msg.message_id

    except Exception as e:
        if edit_message_id:
            try:
                bot.edit_message_text(chat_id=chat_id, message_id=edit_message_id, text="⚠️ Error reading data or session expired.", parse_mode="Markdown")
            except Exception:
                bot.send_message(chat_id, "⚠️ Error reading data.")
        else:
            bot.send_message(chat_id, "⚠️ Error reading data.")

# --- Run Flask Server, Background Cleanup, and Telegram Bot Together ---
if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    cleanup_thread = threading.Thread(target=auto_cleanup_task, daemon=True)
    cleanup_thread.start()
    
    bot.remove_webhook()
    
    logging.info("Bot, Flask Server, and Auto-Cleanup are starting...")
    while True:
        try:
            bot.infinity_polling(skip_pending=True, interval=1, timeout=20)
        except Exception as e:
            logging.error(f"Polling error occurred: {e}")
            time.sleep(5)