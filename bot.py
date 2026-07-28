import telebot
from telebot import types
import imaplib
import poplib
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
        conn.commit()

init_db()

def safe_delete(chat_id, message_id):
    try:
        bot.delete_message(chat_id, message_id)
    except:
        pass

# --- Background Task to Clean Data Every 10 Minutes ---
def auto_cleanup_task():
    while True:
        try:
            time.sleep(600) # 10 Minutes
            with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM users")
                cursor.execute("DELETE FROM email_cache")
                conn.commit()
                
            # Force delete any remaining tracked messages from Telegram
            for c_id, m_id in list(active_menu_messages.items()):
                safe_delete(c_id, m_id)
            for c_id, m_id in list(active_mail_messages.items()):
                safe_delete(c_id, m_id)
                
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

# --- STRICT Facebook OTP Detector ---
def detect_otp_type(subject, content):
    combined_text = (subject + " " + content).lower()
    
    # Return ONLY Facebook OTPs. Ignore everything else.
    if "facebook" in combined_text or "fb" in combined_text:
        service_name = "📘 FACEBOOK OTP"
        code_match = re.search(r'\b\d{6,8}\b', combined_text) # FB OTPs are usually 6-8 digits
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
        types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"),
        types.InlineKeyboardButton("❓ Help & Format", callback_data="action_help"),
        types.InlineKeyboardButton("ℹ️ About Bot", callback_data="action_about"),
        types.InlineKeyboardButton("🔄 Refresh Inbox", callback_data="action_refresh_direct")
    )
    
    instruction_text = (
        "🤖 **Auto Secure FB Mail & OTP Reader Bot**\n\n"
        "Send your mail credentials directly in chat AT ANY TIME:\n\n"
        "🏢 **For Zoho / Alias:** `email@zohomail.com|AppPassword`\n"
        "🔴 **For Gmail:** `email@gmail.com|AppPassword`\n"
        "🔥 **For Hotmail:** `email|password|token|client_id`\n\n"
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
        bot.answer_callback_query(call.id, "Welcome to Main Menu")
        show_main_instruction(chat_id, message_id=message_id)

    elif call.data == "action_help":
        bot.answer_callback_query(call.id, "Help Guide")
        help_text = (
            "📖 **Bot Usage Guide:**\n\n"
            "1. Just send your credentials in the format anytime.\n"
            "2. Bot always stays awake and listens for login info.\n"
            "3. It strictly filters out junk and shows only FB OTP.\n"
            "4. Auto-deletes everything after 10 minutes."
        )
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔙 Back to Menu", callback_data="action_menu"))
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=help_text, parse_mode="Markdown", reply_markup=markup)
        except Exception:
            pass

    elif call.data == "action_about":
        bot.answer_callback_query(call.id, "About Bot")
        about_text = (
            "ℹ️ **About Secure Mail Bot:**\n\n"
            "• Always-On Credentials Listener\n"
            "• Strict Facebook-Only OTP Fetching\n"
            "• Built-in Auto-Deletion Security\n"
            "• Supports Gmail POP3 & Zoho Aliases"
        )
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔙 Back to Menu", callback_data="action_menu"))
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=about_text, parse_mode="Markdown", reply_markup=markup)
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
        try:
            bot.delete_message(chat_id, message_id)
        except:
            pass
        show_main_instruction(chat_id)

# --- GLOBAL LISTENER: ALWAYS WAITING FOR CREDENTIALS ---
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

            msg = bot.send_message(chat_id, f"✅ **{provider.capitalize()} Mail Detected!**\nConnecting to Inbox...", parse_mode="Markdown")
            
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
        err_msg = bot.send_message(chat_id, "❌ **Invalid Format!**\nSend Zoho/Gmail: `email|AppPassword`\nSend Hotmail: `email|password|token|client_id`", parse_mode="Markdown")
        threading.Timer(5.0, lambda c=chat_id, m=err_msg.message_id: safe_delete(c, m)).start()

# --- Send Full Email Content Directly to Chat ---
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
    
    if not row:
        err_msg = bot.send_message(chat_id, "⚠️ Mail session expired. Please refresh the inbox.")
        threading.Timer(60, lambda c=chat_id, m=err_msg.message_id: safe_delete(c, m)).start()
        return
        
    subject, sender, full_content = row
    logo_url = "https://i.ibb.co.com/x8LVnqMr/image-removebg-preview.png"
    
    clean_body = clean_html_tags(full_content)
    otp_label, otp_code = detect_otp_type(subject, clean_body)
    
    message_text = (
        f"📬 **Email Details (FB Only)**\n\n"
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
            threading.Timer(600, lambda c=chat_id, m=sent_msg.message_id: safe_delete(c, m)).start()
    except Exception:
        sent_msg = bot.send_message(chat_id, message_text, parse_mode="Markdown")
        if sent_msg:
            active_mail_messages[chat_id] = sent_msg.message_id
            threading.Timer(600, lambda c=chat_id, m=sent_msg.message_id: safe_delete(c, m)).start()

# --- Fetch Emails (Facebook Filtered & Limited to 1) ---
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
        if provider in ['zoho', 'gmail']:
            imap_server = 'imap.gmail.com' if provider == 'gmail' else 'imap.zoho.com'
            
            login_email = email_address
            if '+' in login_email and '@' in login_email:
                base_name, domain = login_email.split('@', 1)
                base_name = base_name.split('+')[0]
                login_email = f"{base_name}@{domain}"

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
                    
                    # Scan last 10 emails to find the latest FB OTP
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
                                
                                if lbl: # Only grab if it is Facebook
                                    cached_emails.append((subject, from_, raw_html))
                                    response_text += f"🔹 **[{lbl}]** Code: `{code}`\n📌 **Subject:** {subject}\n━━━━━━━━━━━━━━━━━━━\n"
                                    fb_found = True
                                    break # Got 1 FB email, stop searching
                        if fb_found:
                            break
                            
                    if not fb_found:
                        response_text = f"📭 **Inbox ({email_address})** No Facebook OTP found."
                mail.logout()
                
            except imaplib.IMAP4.error as e:
                # POP3 FALLBACK
                if provider == 'gmail':
                    logging.warning(f"IMAP failed for {email_address}, trying POP3 fallback...")
                    try:
                        pop_server = 'pop.gmail.com'
                        mail_pop = poplib.POP3_SSL(pop_server)
                        mail_pop.user(login_email)
                        mail_pop.pass_(password)

                        num_messages, total_size = mail_pop.stat()
                        
                        if num_messages == 0:
                            response_text = f"📭 **Inbox ({email_address})** is empty (via POP3)."
                        else:
                            response_text = f"📨 **Inbox ({email_address}) [POP3]:**\n\n"
                            fb_found = False
                            start_msg = max(1, num_messages - 9)
                            
                            for i in range(num_messages, start_msg - 1, -1):
                                response, lines, octets = mail_pop.retr(i)
                                raw_email = b"\n".join(lines)
                                msg = email.message_from_bytes(raw_email)
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
                                    break # Got 1 FB email, stop
                                    
                            if not fb_found:
                                response_text = f"📭 **Inbox ({email_address})** No Facebook OTP found (via POP3)."
                        mail_pop.quit()
                    except Exception as pop_e:
                        response_text = "❌ **Login Failed!** App Password is wrong or IP blocked."
                        logging.error(f"POP3 Error for {email_address}: {pop_e}")
                else:
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
                            break # Got 1 FB email
                            
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
        
        # Only show Read button if we found an email
        if cached_emails:
            markup.row(types.InlineKeyboardButton("📖 Read Mail", callback_data="view_mail_0"))

        markup.row(types.InlineKeyboardButton("🔄 Refresh", callback_data="action_refresh"), types.InlineKeyboardButton("➕ New Email", callback_data="action_new_email"))
        markup.row(types.InlineKeyboardButton("🏠 Main Menu", callback_data="action_menu"))

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
    
    logging.info("Bot, Flask Server, and Auto-Cleanup are starting...")
    while True:
        try:
            bot.infinity_polling(skip_pending=True, interval=1, timeout=20)
        except Exception as e:
            logging.error(f"Polling error occurred: {e}")
            time.sleep(5)