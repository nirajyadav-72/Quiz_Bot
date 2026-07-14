# QuizBot Bug Fixes

## Bug #1: Line ~335-346 - Incorrect args handling

**BEFORE:**
```python
if args and len(args) > 0 and args.startswith("quiz_"):  # ❌ args is a LIST
    quiz_id = args.split("_")  # ❌ Returns a list, causes type error
```

**AFTER:**
```python
if args and len(args) > 0 and args[0].startswith("quiz_"):  # ✅ Check first element
    quiz_id = int(args[0].replace("quiz_", ""))  # ✅ Extract and convert to int
```

---

## Bug #2: Line ~1531 - Timer extraction

**BEFORE (messy but works):**
```python
raw_timer = timer_data[0] if (timer_data and isinstance(timer_data, tuple)) else 30
```

**AFTER (cleaner):**
```python
timer = timer_data[0] if timer_data else 30
```

---

## Bug #3: Line ~1093 - Missing handler

**In show_question_detail_panel():**
Remove or add handler for `replaceq_` pattern.

**ADD THIS to main():**
```python
app.add_handler(CallbackQueryHandler(handle_replace_question, pattern="^replaceq_"))
```

**OR Remove the button from line ~1093:**
```python
# [InlineKeyboardButton("Replace Question", callback_data=f"replaceq_{quiz_id}_{q_id}")],  # Remove this line
```

---

## Bug #4: Potential - Missing str() conversion in parse

**Line ~352 in handle_ready_click:**
```python
if not old_game.get("quiz_started") or old_game.get("setup_message_id") != message_id:
```

Consider ensuring types match:
```python
if not old_game.get("quiz_started") or str(old_game.get("setup_message_id")) != str(message_id):
```

