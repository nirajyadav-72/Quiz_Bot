import os
import sqlite3
import json
import logging
import asyncio
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    filters, ContextTypes, ConversationHandler, CallbackQueryHandler, PollAnswerHandler
)

# Enable Logging
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID")) if os.getenv("OWNER_ID") else None

DB_FILE = "quiz_bot.db"

def escape_markdown(text):
    """Escape special characters for Telegram Markdown"""
    if not text:
        return text
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`']
    for char in special_chars:
        text = text.replace(char, f'\\{char}')
    return text

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS quizzes (
            quiz_id INTEGER PRIMARY KEY AUTOINCREMENT,
            creator_id INTEGER,
            title TEXT,
            description TEXT,
            timer INTEGER DEFAULT 30
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quiz_id INTEGER,
            question_text TEXT,
            options TEXT,
            correct_answer TEXT,
            explanation TEXT,
            pre_message TEXT,
            FOREIGN KEY(quiz_id) REFERENCES quizzes(quiz_id)
        )
    """)
    conn.commit()
    conn.close()

init_db()

# Global dictionary for active group games memory
GROUP_GAMES = {}

(TITLE, DESCRIPTION, QUESTIONS, TIMER) = range(4)

async def new_quiz_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Let's create a new quiz. First, send me the title of your quiz (e.g., 'Aptitude Test' or '10 questions about bears').",
        reply_markup=ReplyKeyboardRemove()
    )
    # store builder + creator id so callback handlers can verify permissions
    context.user_data["quiz_build"] = {"title": "", "description": "", "questions": []}
    context.user_data["quiz_build_creator_id"] = update.message.from_user.id
    return TITLE

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if args and len(args) > 0 and args[0].startswith("quiz_"):
        quiz_id = args[0].split("_")[1]
        chat_id = update.effective_chat.id
        
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT title, description, timer FROM quizzes WHERE quiz_id = ?", (quiz_id,))
        quiz_data = cursor.fetchone()
        cursor.execute("SELECT COUNT(*) FROM questions WHERE quiz_id = ?", (quiz_id,))
        total_q = cursor.fetchone()
        conn.close()
        
        if not quiz_data:
            await update.message.reply_text("❌ Quiz data not found.")
            return

        title, desc, timer = quiz_data
        time_disp = f"{timer} sec" if timer < 60 else f"{timer // 60} min"
        
        init_text = (
            f"🏁 **Quiz Setup Ready!**\n\n"
            f"📚 **Title:** {escape_markdown(title)}\n"
            f"ℹ️ **Description:** {escape_markdown(desc) if desc else 'No description'}\n"
            f"🙋‍♂️ **Questions:** {total_q[0]}\n"
            f"⏱ **Time per question:** {time_disp}\n\n"
            "⚠️ *Khelne ke liye kam se kam 2 users ka join karna zaroori hai!*"
        )
        
        keyboard = [[InlineKeyboardButton("Join Quiz ➕", callback_data=f"join_{quiz_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(init_text, reply_markup=reply_markup, parse_mode="Markdown")
        return

    welcome_text = (
        "👋 **Welcome to Laado Quiz Bot!**\n\n"
        "🚀 /newquiz - Naya Quiz banana shuru karein\n"
        "❌ /cancel - Active creation flow cancel karein"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")

async def receive_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["quiz_build"]["title"] = update.message.text
    await update.message.reply_text("Good. Now send me a description of your quiz. This is optional, you can /skip this step.")
    return DESCRIPTION

async def receive_desc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text
    context.user_data["quiz_build"]["description"] = "" if text.lower() == "/skip" else text
    await update.message.reply_text(
        f"Good. Your quiz '{context.user_data['quiz_build']['title']}' now has 0 questions. If you made a mistake, send /undo.\n\n"
        "💡 **Sawal jodne ke liye:**\nClick on 📎 (Attachment) -> Select **Poll**.\n"
        "Enable **Quiz Mode**, add 2-7 options, pick the correct one, and tap Create.\n\n"
        "Send /done when finished adding questions.",
        reply_markup=ReplyKeyboardRemove()
    )
    return QUESTIONS

async def receive_poll(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    poll = update.message.poll
    if poll.type != "quiz":
        await update.message.reply_text("❌ Kripya Quiz mode wala poll hi send karein:")
        return QUESTIONS
    if len(poll.options) > 7:
        await update.message.reply_text("❌ Maximum 7 options allowed. Re-send poll:")
        return QUESTIONS

    opts = [o.text for o in poll.options]
    q_data = {
        "text": poll.question, "options": opts, "correct": opts[poll.correct_option_id],
        "explanation": poll.explanation if poll.explanation else "", "pre_message": ""
    }
    context.user_data["quiz_build"]["questions"].append(q_data)
    
    await update.message.reply_text(
        f"✅ Question added! Your quiz now has {len(context.user_data['quiz_build']['questions'])} question(s).\n\n"
        "Send next question or /done to finish."
    )
    return QUESTIONS

async def handle_undo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    quiz = context.user_data.get("quiz_build")
    if quiz and quiz["questions"]:
        quiz["questions"].pop()
        await update.message.reply_text(f"↩️ Last question removed! Quiz now has {len(quiz['questions'])} question(s).\n\nSend next question or /done.")
    else:
        await update.message.reply_text("❌ No questions to remove!")
    return QUESTIONS

async def finish_quiz_creation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    quiz = context.user_data.get("quiz_build", {})
    if not quiz or not quiz.get("questions"):
        await update.message.reply_text("❌ Error: Quiz must have at least 1 question!")
        return QUESTIONS
    
    await update.message.reply_text(
        "⏱️ **Please set a time limit for questions:**\n\n"
        "Type any of these: 15, 30, 40, 60\n\n"
        "Example: Type '30' for 30 seconds per question",
        reply_markup=ReplyKeyboardRemove()
    )
    return TIMER

async def handle_timer_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    logging.info("handle_timer_text called; text=%s user=%s", text, update.message.from_user.id if update.message and update.message.from_user else None)
    time_map = {"15": 15, "30": 30, "40": 40, "60": 60}
    
    if text not in time_map:
        await update.message.reply_text("❌ Invalid time. Please enter: 15, 30, 40, or 60")
        return TIMER
    
    t_sec = time_map[text]
    quiz = context.user_data.get("quiz_build", {})
    
    if not quiz or not quiz.get("title"):
        await update.message.reply_text("❌ Error: Quiz data missing. Please start over with /newquiz")
        return ConversationHandler.END
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO quizzes (creator_id, title, description, timer) VALUES (?, ?, ?, ?)", (update.message.from_user.id, quiz["title"], quiz["description"], t_sec))
    qid = cursor.lastrowid
    for q in quiz["questions"]:
        cursor.execute("INSERT INTO questions (quiz_id, question_text, options, correct_answer, explanation, pre_message) VALUES (?, ?, ?, ?, ?, ?)", 
                       (qid, q["text"], json.dumps(q["options"]), q["correct"], q["explanation"], q["pre_message"]))
    conn.commit()
    conn.close()
    
    # Clear user_data after successful save
    context.user_data.pop("quiz_build", None)
    context.user_data.pop("quiz_build_creator_id", None)
    
    # Send confirmation message
    await update.message.reply_text("✅ Timer set! Creating your quiz summary...")
    logging.info("Timer set for quiz_id=%s by user=%s", qid, update.message.from_user.id)
    await show_summary_panel_text(update, context, qid)
    return ConversationHandler.END

async def show_summary_panel(query, context, quiz_id):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT title, timer FROM quizzes WHERE quiz_id = ?", (quiz_id,))
        quiz_data = cursor.fetchone()
        
        if not quiz_data:
            logging.error(f"Quiz {quiz_id} not found in database!")
            await query.message.reply_text("❌ Error: Quiz data could not be retrieved.")
            conn.close()
            return
        
        title, timer = quiz_data
        cursor.execute("SELECT COUNT(*) FROM questions WHERE quiz_id = ?", (quiz_id,))
        total_q = cursor.fetchone()
        conn.close()

        time_display = f"{timer} sec" if timer < 60 else f"{timer // 60} min"
        bot_username = context.bot.username
        
        # Escape markdown special characters
        escaped_title = escape_markdown(title)
        
        # Don't use parse_mode for URLs - build without markdown for the link
        summary_text = (
            "👍 Quiz created.\n\n"
            "🏁 Here's your quiz:\n"
            f"📚 {escaped_title}\n"
            f"🙋‍♂️ {total_q[0]} question(s) · ⏱ Time: {time_display}\n\n"
            f"🔗 External sharing link:\n"
            f"https://t.me/{bot_username}?start=quiz_{quiz_id}"
        )
        
        inline_keyboard = [
            [InlineKeyboardButton("🏁 Start this quiz", callback_data=f"start_{quiz_id}")],
            [InlineKeyboardButton("👥 Start quiz in group", url=f"https://t.me/{bot_username}?startgroup=quiz_{quiz_id}")],
            [InlineKeyboardButton("📢 Share quiz", url=f"https://t.me/share/url?url=https://t.me/{bot_username}?start=quiz_{quiz_id}")],
            [InlineKeyboardButton("⚙️ Edit quiz", callback_data=f"edit_{quiz_id}"), InlineKeyboardButton("📊 Quiz status", callback_data=f"status_{quiz_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(inline_keyboard)
        await query.message.reply_text(summary_text, reply_markup=reply_markup)
    except Exception as e:
        logging.error(f"Error in show_summary_panel: {e}")
        await query.message.reply_text(f"❌ Error: {str(e)}")

async def show_summary_panel_text(update, context, quiz_id):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT title, timer FROM quizzes WHERE quiz_id = ?", (quiz_id,))
        quiz_data = cursor.fetchone()
        
        if not quiz_data:
            logging.error(f"Quiz {quiz_id} not found in database!")
            await update.message.reply_text("❌ Error: Quiz data could not be retrieved.")
            conn.close()
            return
        
        title, timer = quiz_data
        cursor.execute("SELECT COUNT(*) FROM questions WHERE quiz_id = ?", (quiz_id,))
        total_q = cursor.fetchone()
        conn.close()

        time_display = f"{timer} sec" if timer < 60 else f"{timer // 60} min"
        bot_username = context.bot.username
        
        # Escape markdown special characters
        escaped_title = escape_markdown(title)
        
        # Don't use parse_mode for URLs - build without markdown for the link
        summary_text = (
            "👍 Quiz created.\n\n"
            "🏁 Here's your quiz:\n"
            f"📚 {escaped_title}\n"
            f"🙋‍♂️ {total_q[0]} question(s) · ⏱ Time: {time_display}\n\n"
            f"🔗 External sharing link:\n"
            f"https://t.me/{bot_username}?start=quiz_{quiz_id}"
        )
        
        inline_keyboard = [
            [InlineKeyboardButton("🏁 Start this quiz", callback_data=f"start_{quiz_id}")],
            [InlineKeyboardButton("👥 Start quiz in group", url=f"https://t.me/{bot_username}?startgroup=quiz_{quiz_id}")],
            [InlineKeyboardButton("📢 Share quiz", url=f"https://t.me/share/url?url=https://t.me/{bot_username}?start=quiz_{quiz_id}")],
            [InlineKeyboardButton("⚙️ Edit quiz", callback_data=f"edit_{quiz_id}"), InlineKeyboardButton("📊 Quiz status", callback_data=f"status_{quiz_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(inline_keyboard)
        await update.message.reply_text(summary_text, reply_markup=reply_markup)
    except Exception as e:
        logging.error(f"Error in show_summary_panel_text: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")

async def edit_quiz_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    quiz_id = query.data.split("_")[1]
    
    keyboard = [
        [InlineKeyboardButton("📝 Edit title", callback_data=f"edtitle_{quiz_id}")],
        [InlineKeyboardButton("ℹ️ Edit description", callback_data=f"eddesc_{quiz_id}")],
        [InlineKeyboardButton("❓ Edit question", callback_data=f"edquest_{quiz_id}")],
        [InlineKeyboardButton("⏱ Edit timer settings", callback_data=f"edtime_{quiz_id}")],
        [InlineKeyboardButton("Back 🔙", callback_data=f"backto_{quiz_id}")]
    ]
    await query.edit_message_text(
        text="⚙️ **Edit Quiz Menu**\n\nAap is quiz ka kya badalna chahte hain? Niche se chunyein:",
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
    )

async def back_to_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    quiz_id = query.data.split("_")[1]
    await query.message.delete()
    await show_summary_panel(query, context, quiz_id)

async def handle_group_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    user_name = query.from_user.username if query.from_user.username else query.from_user.first_name
    quiz_id = query.data.split("_")[1]
    
    if chat_id not in GROUP_GAMES:
        GROUP_GAMES[chat_id] = {
            "quiz_id": quiz_id, 
            "joined_users": {}, 
            "current_q": 0, 
            "scores": {}, 
            "poll_map": {}, 
            "start_time": None,
            "user_answers": {},  # Track all answers per user
            "question_start_times": {}  # Track when each question started
        }
        
    game = GROUP_GAMES[chat_id]
    game["joined_users"][user_id] = f"@{user_name}" if query.from_user.username else user_name
    game["scores"][user_id] = {"score": 0, "total_time": 0.0}
    game["user_answers"][user_id] = {}  # Store answers for this user
    
    joined_count = len(game["joined_users"])
    names_list = ", ".join(game["joined_users"].values())
    
    keyboard = [[InlineKeyboardButton("Join Quiz ➕", callback_data=f"join_{quiz_id}")]]
    if joined_count >= 2:
        keyboard.append([InlineKeyboardButton("Start Quiz 🚀", callback_data=f"run_{quiz_id}")])
        
    await query.edit_message_text(
        text=f"🏁 **Quiz Setup Active**\n\nJoined Users ({joined_count}): {names_list}\n\n*Minimum 2 users criteria met. You can start the quiz now.*",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    await query.answer(text="Aapne quiz successfully join kar li!")

async def launch_group_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    game = GROUP_GAMES.get(chat_id)
    
    if not game or len(game["joined_users"]) < 2:
        await query.answer("❌ Error: Minimum 2 users zaroori hain!", show_alert=True)
        return
        
    await query.answer()
    await query.message.reply_text("🔥 Get ready! Quiz shuru ho rahi hai...")
    await asyncio.sleep(2)
    
    game["current_q"] = 0
    await send_next_group_poll(chat_id, context)

async def send_next_group_poll(chat_id, context):
    game = GROUP_GAMES[chat_id]
    qid = game["quiz_id"]
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT timer FROM quizzes WHERE quiz_id = ?", (qid,))
    timer = cursor.fetchone()
    cursor.execute("SELECT question_text, options, correct_answer, pre_message, explanation FROM questions WHERE quiz_id = ?", (qid,))
    questions = cursor.fetchall()
    conn.close()
    
    if game["current_q"] >= len(questions):
        await compile_group_leaderboard(chat_id, context)
        return

    q = questions[game["current_q"]]
    q_text, options_json, correct_ans, pre_msg, explanation = q
    options = json.loads(options_json)
    correct_idx = options.index(correct_ans)
    
    if pre_msg:
        await context.bot.send_message(chat_id=chat_id, text=f"📢 Context: {pre_msg}")
        await asyncio.sleep(1)

    # Record question start time
    game["question_start_times"][game["current_q"]] = datetime.now()
    
    game["start_time"] = datetime.now()
    poll_msg = await context.bot.send_poll(
        chat_id=chat_id, question=f"❓ Q ({game['current_q'] + 1}/{len(questions)}): {q_text}",
        options=options, type="quiz", correct_option_id=correct_idx,
        explanation=explanation if explanation else None, is_anonymous=False
    )
    
    # Store poll info with correct answer
    game["poll_map"][poll_msg.poll.id] = {
        "correct_idx": correct_idx, 
        "chat_id": chat_id,
        "correct_answer": correct_ans,
        "question_index": game["current_q"]
    }
    
    logging.info(f"Poll {poll_msg.poll.id} sent for question {game['current_q']} in chat {chat_id}")
    
    await asyncio.sleep(timer[0])
    game["current_q"] += 1
    await send_next_group_poll(chat_id, context)

async def track_poll_answers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Track poll answers in real-time"""
    ans = update.poll_answer
    pid = ans.poll_id
    uid = ans.user.id
    
    logging.info(f"Poll answer received: poll_id={pid}, user_id={uid}, options={ans.option_ids}")
    
    found = False
    for cid, game in list(GROUP_GAMES.items()):
        if pid in game["poll_map"]:
            found = True
            poll_info = game["poll_map"][pid]
            correct_idx = poll_info["correct_idx"]
            question_idx = poll_info["question_index"]
            
            logging.info(f"Found poll in chat {cid}, checking user {uid}")
            logging.info(f"Joined users: {list(game['joined_users'].keys())}")
            
            # Initialize if user hasn't answered before (they must be in joined_users)
            if uid not in game["user_answers"]:
                if uid in game["joined_users"]:
                    game["user_answers"][uid] = {}
                    logging.info(f"Initialized user_answers for user {uid}")
                else:
                    logging.warning(f"User {uid} not in joined_users, skipping")
                    continue
            
            # Store the user's answer for this question
            game["user_answers"][uid][question_idx] = {
                "selected": ans.option_ids,
                "correct_idx": correct_idx,
                "timestamp": datetime.now()
            }
            
            logging.info(f"✅ Stored answer for user {uid}: {ans.option_ids} (correct: {correct_idx})")
    
    if not found:
        logging.warning(f"Poll {pid} not found in any active game")

async def compile_group_leaderboard(chat_id, context):
    """Calculate final leaderboard based on tracked answers"""
    game = GROUP_GAMES.get(chat_id)
    if not game: 
        return
    
    # Get quiz questions to calculate correct answers
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT question_text, options, correct_answer FROM questions WHERE quiz_id = ?", (game["quiz_id"],))
    questions = cursor.fetchall()
    conn.close()
    
    # Build correct answers list
    correct_answers = {}
    for idx, (q_text, options_json, correct_ans) in enumerate(questions):
        options = json.loads(options_json)
        correct_idx = options.index(correct_ans)
        correct_answers[idx] = correct_idx
    
    logging.info(f"Total questions: {len(questions)}, Correct answers: {correct_answers}")
    
    # Calculate scores based on tracked answers
    final_scores = {}
    for uid, user_answers in game["user_answers"].items():
        score = 0
        total_time = 0.0
        
        logging.info(f"User {uid}: {len(user_answers)} answers recorded")
        
        # Only count users who actually answered questions
        if not user_answers:
            logging.info(f"User {uid} has no answers, skipping from leaderboard")
            continue
            
        for question_idx, answer_data in user_answers.items():
            # Fix: Extract the selected index properly
            selected_idx = answer_data["selected"][0] if answer_data["selected"] else -1
            correct_idx = correct_answers.get(question_idx, -1)
            
            logging.info(f"User {uid}: Q{question_idx} - Selected {selected_idx}, Correct {correct_idx}")
            
            if selected_idx == correct_idx:
                score += 1
                # Calculate time taken for this question
                start_time = game["question_start_times"].get(question_idx, answer_data["timestamp"])
                if isinstance(start_time, datetime):
                    elapsed = (answer_data["timestamp"] - start_time).total_seconds()
                    total_time += elapsed
        
        final_scores[uid] = {"score": score, "total_time": total_time}
        logging.info(f"User {uid} final: {score} correct, {total_time} sec total")
    
    logging.info(f"Final scores for chat {chat_id}: {final_scores}")
    
    # Sort by score desc, then total_time asc - ONLY INCLUDE USERS WITH SCORES
    sorted_scores = sorted(final_scores.items(), key=lambda item: (-item[1]["score"], item[1]["total_time"]))[:20]
    board = "🏆 FINAL QUIZ LEADERBOARD (Top 20) 🏆\n\n"
    
    if not sorted_scores:
        board += "❌ कोई भी user successfully participate नहीं कर सका। 🤷‍♂️\n"
        board += f"Total users joined: {len(game['joined_users'])}\n"
        board += f"Users with answers: {len(game['user_answers'])}"
        kb = []
    else:
        total_q = len(questions)
        for idx, (uid, meta) in enumerate(sorted_scores, 1):
            user_obj = game["joined_users"].get(uid, "User")
            score = meta["score"]
            total_time = round(meta["total_time"], 2)
            
            if idx == 1: medal = "🥇"
            elif idx == 2: medal = "🥈"
            elif idx == 3: medal = "🥉"
            else: medal = f"{idx}."
                
            board += f"{medal} {user_obj} — ⭐ {score}/{total_q} Sahi (⏱ {total_time} sec)\n"
            
        share_text = f"Maine Laado Quiz Bot me participate kiya aur top players me rank banayi! 🔥"
        kb = [[InlineKeyboardButton("📢 Share Score / Results", url=f"https://t.me/share/url?url={share_text}")]]
        
    await context.bot.send_message(chat_id=chat_id, text=board, reply_markup=InlineKeyboardMarkup(kb) if kb else None)
    GROUP_GAMES.pop(chat_id, None)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❌ Setup cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

def main():
    if not BOT_TOKEN: return
    app = Application.builder().token(BOT_TOKEN).build()
    
    new_quiz_handler = ConversationHandler(
        entry_points=[CommandHandler("newquiz", new_quiz_start)],
        states={
            TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_title)],
            DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_desc), CommandHandler("skip", receive_desc)],
            QUESTIONS: [CommandHandler("undo", handle_undo), CommandHandler("done", finish_quiz_creation), MessageHandler(filters.POLL, receive_poll)],
            TIMER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_timer_text)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(new_quiz_handler)

    app.add_handler(CallbackQueryHandler(handle_group_join, pattern="^join_"))
    app.add_handler(CallbackQueryHandler(launch_group_quiz, pattern="^run_"))
    app.add_handler(CallbackQueryHandler(edit_quiz_menu, pattern="^edit_"))
    app.add_handler(CallbackQueryHandler(back_to_summary, pattern="^backto_"))
    app.add_handler(PollAnswerHandler(track_poll_answers))
    
    print("🚀 Advanced Telegram Quiz-Bot UI Active...")
    app.run_polling()

if __name__ == "__main__":
    main()
