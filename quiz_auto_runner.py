"""
🔥 QUIZ AUTO-RUNNER SYSTEM
Automatically starts all quizzes from database in support group on scheduled intervals
"""

import sqlite3
import asyncio
import logging
import json
import random
from datetime import datetime, timedelta
from telegram import InlineKeyboardMarkup, InlineKeyboardButton

DB_FILE = "quiz_bot.db"

class QuizAutoRunner:
    """Manages automatic quiz execution in support group"""
    
    def __init__(self, context, support_group_id, GROUP_GAMES):
        self.context = context
        self.support_group_id = support_group_id
        self.GROUP_GAMES = GROUP_GAMES
        self.run_interval = 300  # 5 minutes between quiz starts (configurable)
        self.current_running_quiz = None
        self.logger = logging.getLogger(__name__)
        
    async def get_all_quizzes(self):
        """Fetch all quizzes from database"""
        try:
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT q.quiz_id, q.creator_id, q.title, q.description, q.timer, q.negative_value, COUNT(qu.id) as q_count
                FROM quizzes q
                LEFT JOIN questions qu ON q.quiz_id = qu.quiz_id
                GROUP BY q.quiz_id
                ORDER BY RANDOM()
            """)
            
            quizzes = cursor.fetchall()
            conn.close()
            return quizzes
        except Exception as e:
            self.logger.error(f"❌ Error fetching quizzes: {e}")
            return []
    
    async def get_quiz_questions(self, quiz_id):
        """Get all questions for a quiz"""
        try:
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT id, question_text, options, correct_answer, explanation, pre_message 
                FROM questions 
                WHERE quiz_id = ?
                ORDER BY id
            """, (quiz_id,))
            
            questions = cursor.fetchall()
            conn.close()
            return questions
        except Exception as e:
            self.logger.error(f"❌ Error fetching questions: {e}")
            return []
    
    async def announce_quiz_start(self, quiz_id, title, description, question_count, timer, negative_value):
        """Announce quiz in support group"""
        try:
            from quizbot import escape_markdown, format_time
            
            if not self.support_group_id:
                self.logger.warning("⚠️ Support group ID not configured")
                return False
            
            time_display = f"{timer}s" if timer < 60 else f"{timer // 60}m"
            escaped_title = escape_markdown(title) if title else "Untitled"
            escaped_desc = escape_markdown(description) if description else "No description"
            neg_display = "❌ Disabled" if negative_value == 0.0 else f"📉 -{negative_value} per wrong"
            
            announce_text = (
                f"🎮 *LIVE QUIZ STARTING NOW!*\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"📌 *Title:* {escaped_title}\n"
                f"📄 *Description:* {escaped_desc}\n"
                f"❓ *Questions:* {question_count}\n"
                f"⏱️ *Timer:* {time_display} per question\n"
                f"📉 *Negative Marking:* {neg_display}\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"👇 *Click 'I am ready!' to participate!*\n"
                f"⏳ *Minimum 2 players needed to start*"
            )
            
            # Ready button
            keyboard = [[{
                "text": "I am ready!  (0)",
                "callback_data": f"ready_{quiz_id}",
                "style": "success"
            }]]
            
            announce_msg = await self.context.bot.send_message(
                chat_id=self.support_group_id,
                text=announce_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
            
            # Initialize game in GROUP_GAMES
            self.GROUP_GAMES[self.support_group_id] = {
                "quiz_id": quiz_id,
                "joined_users": {},
                "current_q": 0,
                "scores": {},
                "poll_map": {},
                "start_time": None,
                "user_answers": {},
                "question_start_times": {},
                "ready_users": set(),
                "quiz_started": False,
                "poll_message_ids": {},
                "setup_message_id": announce_msg.message_id,
                "setup_panel_text": announce_text,
                "is_private": False,
                "quiz_paused": False,
                "consecutive_no_answers": 0,
                "auto_run": True  # Mark as auto-run quiz
            }
            
            self.current_running_quiz = quiz_id
            self.logger.info(f"✅ Quiz {quiz_id} announced in support group")
            return True
            
        except Exception as e:
            self.logger.error(f"❌ Error announcing quiz: {e}")
            return False
    
    async def wait_for_players(self, timeout=30):
        """Wait for minimum 2 players to join (or timeout)"""
        try:
            game = self.GROUP_GAMES.get(self.support_group_id)
            if not game:
                return False
            
            start_time = datetime.now()
            while (datetime.now() - start_time).seconds < timeout:
                ready_count = len(game.get("ready_users", set()))
                
                if ready_count >= 2:
                    self.logger.info(f"✅ {ready_count} players ready! Starting quiz...")
                    return True
                
                await asyncio.sleep(2)
            
            # Timeout - show message
            ready_count = len(game.get("ready_users", set()))
            if ready_count < 2:
                await self.context.bot.send_message(
                    chat_id=self.support_group_id,
                    text=f"⏳ Only {ready_count} player ready. Quiz starting anyway..."
                )
                return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"❌ Error waiting for players: {e}")
            return False
    
    async def auto_run_quiz(self, quiz_id, title, description, question_count, timer, negative_value):
        """Automatically run a complete quiz"""
        try:
            # Step 1: Announce quiz
            announced = await self.announce_quiz_start(
                quiz_id, title, description, question_count, timer, negative_value
            )
            
            if not announced:
                return False
            
            # Step 2: Wait for players to join (30 seconds)
            await asyncio.sleep(3)
            players_joined = await self.wait_for_players(timeout=30)
            
            if not players_joined:
                await self.context.bot.send_message(
                    chat_id=self.support_group_id,
                    text="❌ Not enough players. Quiz cancelled."
                )
                self.GROUP_GAMES.pop(self.support_group_id, None)
                return False
            
            # Step 3: Start the quiz
            game = self.GROUP_GAMES.get(self.support_group_id)
            if game:
                game["quiz_started"] = True
                
                # Remove the ready button
                try:
                    keyboard = []
                    await self.context.bot.edit_message_reply_markup(
                        chat_id=self.support_group_id,
                        message_id=game.get("setup_message_id"),
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                except Exception:
                    pass
                
                # Send countdown
                for count in ["🎲 Quiz starting in 3...", "2️⃣", "1️⃣", "🚀 GO!"]:
                    countdown_msg = await self.context.bot.send_message(
                        chat_id=self.support_group_id, 
                        text=count
                    )
                    await asyncio.sleep(1)
                    try:
                        await self.context.bot.delete_message(
                            chat_id=self.support_group_id, 
                            message_id=countdown_msg.message_id
                        )
                    except Exception:
                        pass
                
                # Import the quiz sending function
                from quizbot import send_next_group_poll
                
                # 🔥 FIX: Create task instead of await - THIS IS THE KEY FIX!
                asyncio.create_task(send_next_group_poll(self.support_group_id, self.context))
                
            self.logger.info(f"✅ Quiz {quiz_id} auto-run started")
            return True
            
        except Exception as e:
            self.logger.error(f"❌ Error auto-running quiz: {e}")
            self.GROUP_GAMES.pop(self.support_group_id, None)
            return False
    
    async def start_auto_run_loop(self):
        """Start infinite loop that automatically runs quizzes"""
        self.logger.info("🚀 Starting Quiz Auto-Runner Loop...")
        
        while True:
            try:
                # Check if a quiz is already running
                game = self.GROUP_GAMES.get(self.support_group_id)
                
                if game and game.get("quiz_started"):
                    # Quiz still running, wait before checking again
                    await asyncio.sleep(10)
                    continue
                
                # Get all available quizzes
                quizzes = await self.get_all_quizzes()
                
                if not quizzes:
                    self.logger.warning("⚠️ No quizzes found in database")
                    await asyncio.sleep(self.run_interval)
                    continue
                
                # Pick a random quiz
                quiz_id, creator_id, title, description, timer, negative_value, q_count = random.choice(quizzes)
                
                if q_count == 0:
                    self.logger.warning(f"⚠️ Quiz {quiz_id} has no questions, skipping")
                    await asyncio.sleep(10)
                    continue
                
                self.logger.info(f"🎮 Starting auto-run for Quiz: {title} (ID: {quiz_id})")
                
                # Run the quiz
                await self.auto_run_quiz(quiz_id, title, description, q_count, timer, negative_value)
                
                # Wait before next quiz
                await asyncio.sleep(self.run_interval)
                
            except Exception as e:
                self.logger.error(f"❌ Error in auto-run loop: {e}")
                await asyncio.sleep(60)
    
    async def get_runner_status(self):
        """Get current auto-runner status"""
        game = self.GROUP_GAMES.get(self.support_group_id)
        quizzes = await self.get_all_quizzes()
        
        return {
            "total_quizzes": len(quizzes),
            "current_running": self.current_running_quiz,
            "group_configured": bool(self.support_group_id),
            "quiz_active": game.get("quiz_started") if game else False,
            "players_joined": len(game.get("joined_users", {})) if game else 0,
            "timestamp": datetime.now().isoformat()
        }


async def init_auto_runner(context, support_group_id, GROUP_GAMES):
    """Initialize and start the auto-runner system"""
    runner = QuizAutoRunner(context, support_group_id, GROUP_GAMES)
    asyncio.create_task(runner.start_auto_run_loop())
    return runner
