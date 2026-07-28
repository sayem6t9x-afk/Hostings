elif call.data == "action_buy_menu":
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("🛍️ Buy Hotmail (New)", callback_data="buy_hotmailnew"),
            types.InlineKeyboardButton("🔙 Back", callback_data="action_menu")
        )
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="🛒 **Account Purchase Menu**\n\nSelect which type of account you want to buy:", parse_mode="Markdown", reply_markup=markup)
        except Exception:
            pass

    elif call.data.startswith("buy_"):
        service_type = call.data.split("_")[1] # Example: 'hotmailnew'
        
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="⏳ **Placing order... Please wait.**", parse_mode="Markdown")
            
            # Step 1: Hit Pre-Order API
            pre_order_url = f"https://yshshopmails.com/v2/api/pre-order.php?key={YSH_API_KEY}"
            resp1 = requests.get(pre_order_url).json()
            
            if resp1.get("status") == "success" and "url" in resp1:
                order_url = resp1["url"]
                
                bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="✅ **Pre-order successful!**\nFetching account credentials from server...", parse_mode="Markdown")
                
                # Step 2: Hit Final Order API
                resp2 = requests.get(order_url).json()
                
                if resp2.get("status") == "success" and "mail" in resp2:
                    mail_data = resp2["mail"]
                    order_id = resp2.get("order_id", "N/A")
                    
                    # Format: Email | Password | Token (| ClientID optional)
                    parts = mail_data.split('|')
                    email_address = parts[0]
                    password = parts[1] if len(parts) > 1 else ""
                    refresh_token = parts[2] if len(parts) > 2 else ""
                    client_id = parts[3] if len(parts) > 3 else "" # Jodi API theke 4ta part na dey, tahole faka thakbe
                    
                    # Auto Save to Database & Set as Active Listener
                    with sqlite3.connect('mail_bot.db', check_same_thread=False) as conn:
                        cursor = conn.cursor()
                        cursor.execute("SELECT user_id FROM users WHERE user_id=?", (chat_id,))
                        
                        if cursor.fetchone():
                            cursor.execute("UPDATE users SET email=?, password=?, provider=?, refresh_token=?, client_id=? WHERE user_id=?", 
                                           (email_address, password, 'hotmail', refresh_token, client_id, chat_id))
                        else:
                            cursor.execute("INSERT INTO users (user_id, email, password, provider, refresh_token, client_id) VALUES (?, ?, ?, ?, ?, ?)", 
                                           (chat_id, email_address, password, 'hotmail', refresh_token, client_id))
                        conn.commit()

                    success_msg = (
                        f"🎉 **Account Purchased & Logged In!**\n\n"
                        f"📧 **Email:** `{email_address}`\n"
                        f"🔑 **Password:** `{password}`\n"
                        f"🆔 **Order ID:** `{order_id}`\n\n"
                        f"🤖 *Bot is now automatically checking for Facebook OTPs...*"
                    )
                    
                    try:
                        bot.delete_message(chat_id, message_id)
                    except:
                        pass
                        
                    msg = bot.send_message(chat_id, success_msg, parse_mode="Markdown")
                    active_menu_messages[chat_id] = msg.message_id
                    
                    # Sathe sathe inbox theke FB OTP fetch kora shuru korbe
                    time.sleep(1)
                    fetch_and_send_emails(chat_id)
                    
                else:
                    bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"❌ **Order Failed!**\nAPI Error: `{resp2}`", parse_mode="Markdown")
            
            else:
                bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"❌ **Pre-order Failed!**\nAPI Error: `{resp1}`", parse_mode="Markdown")
                
        except Exception as e:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"❌ **API Connection Error:** {e}", parse_mode="Markdown")