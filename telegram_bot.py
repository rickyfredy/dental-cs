"""Telegram bot channel listener.

Thin channel layer that renders inline keyboard menus and handles
callback queries. All business logic lives in the harness module.
"""

import asyncio
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import settings
from harness import process_message, reset_session, HarnessResult

logger = logging.getLogger(__name__)


def _build_keyboard(keyboard_data: list[list[dict]] | None) -> InlineKeyboardMarkup | None:
    """Convert harness keyboard data to Telegram InlineKeyboardMarkup."""
    if not keyboard_data:
        return None

    rows = []
    for row in keyboard_data:
        buttons = [
            InlineKeyboardButton(text=btn["label"], callback_data=btn["data"])
            for btn in row
        ]
        rows.append(buttons)
    return InlineKeyboardMarkup(rows)


async def _send_result(update: Update, result: HarnessResult) -> None:
    """Send a HarnessResult as a Telegram message with optional keyboard."""
    reply_markup = _build_keyboard(result.keyboard)
    await update.message.reply_text(result.text, reply_markup=reply_markup)


async def _edit_to_result(query, result: HarnessResult) -> None:
    """Edit the current message to show a new HarnessResult (for callbacks)."""
    reply_markup = _build_keyboard(result.keyboard)
    await query.edit_message_text(result.text, reply_markup=reply_markup)


async def _start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start — reset session and greet the user."""
    result = reset_session(update.message.chat_id)
    await _send_result(update, result)


async def _handle_message(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Forward incoming text messages to the harness and reply."""
    if not update.message or not update.message.text:
        return

    user_id = update.message.chat_id
    user_text = update.message.text

    result = await asyncio.to_thread(process_message, user_id, user_text, None)
    await _send_result(update, result)


async def _handle_callback(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline button presses."""
    query = update.callback_query
    if not query:
        return

    await query.answer()

    user_id = query.message.chat_id
    callback_data = query.data

    # Remove the existing keyboard to prevent stale button presses
    result = await asyncio.to_thread(process_message, user_id, None, callback_data)
    await _edit_to_result(query, result)


def run_bot() -> None:
    """Start the Telegram bot with long polling."""
    if not settings.telegram_bot_token:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set in .env")

    app = Application.builder().token(settings.telegram_bot_token).build()

    # Commands
    app.add_handler(CommandHandler("start", _start))

    # Callback query handler (inline button presses)
    app.add_handler(CallbackQueryHandler(_handle_callback))

    # Catch-all text handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_message))

    logger.info("Starting Telegram bot — polling for messages…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)