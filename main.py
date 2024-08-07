import requests
from solend import get_supply_apr
from kamino import get_apy
import time
import logging
import json
import telebot
import threading
from datetime import datetime, timedelta
import pytz
from logging_config import setup_logging

# Load configuration
with open('config.json', 'r') as config_file:
    config = json.load(config_file)

TELEGRAM_BOT_TOKEN = config['TELEGRAM_BOT_TOKEN']
TELEGRAM_CHAT_ID = config['TELEGRAM_CHAT_ID']
CHECK_INTERVAL = config.get('CHECK_INTERVAL', 300)
LOG_LEVEL = config.get('LOG_LEVEL', 'INFO').upper()
MIN_APY_THRESHOLD = config.get('MIN_APY_THRESHOLD', 6.0)
PERCENTAGE_CHANGE = config.get('PERCENTAGE_CHANGE', 5.0)
LOG_FILE = config.get('LOG_FILE', 'bot.log')
AUTHORIZED_USERS = config.get('AUTHORIZED_USERS', [])

# Setup logging
setup_logging(LOG_FILE, LOG_LEVEL)

# Initialize bot
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, parse_mode='Markdown')

last_fetched_data = {
    'solend_data': None,
    'kamino_data': None,
    'timestamp': None
}

user_last_request_time = {}

moscow_tz = pytz.timezone('Europe/Moscow')


def send_telegram_message(message):
    try:
        bot.send_message(TELEGRAM_CHAT_ID, message)
        logging.info("Message sent successfully.")
    except Exception as e:
        logging.error(f"Failed to send message: {e}")


def get_kamino_data_with_retries(retries=3, delay=5):
    for attempt in range(retries):
        try:
            usdt_apy, usdc_apy = get_apy()
            logging.info(f"Data fetched successfully from Kamino on attempt {attempt + 1}.")
            return {'USDT': float(usdt_apy.strip('%')), 'USDC': float(usdc_apy.strip('%'))}
        except Exception as e:
            logging.error(f"Attempt {attempt + 1} failed: {e}")
            if attempt < retries - 1:
                time.sleep(delay)
    logging.error("All retry attempts failed for Kamino.")
    return None


def fetch_and_send_data():
    try:
        solend_data = get_supply_apr()
        solend_data = {token: float(apr) for token, apr in solend_data.items()}

        kamino_data = get_kamino_data_with_retries()
        kamino_message = "Failed to fetch Kamino data." if kamino_data is None else "\n".join(
            [f'`{token} Supply APY: {apy:.3f}%`' for token, apy in kamino_data.items()]
        )

        last_fetched_data['solend_data'] = solend_data
        last_fetched_data['kamino_data'] = kamino_data
        last_fetched_data['timestamp'] = datetime.now(moscow_tz)

        if should_send_notification(solend_data, kamino_data):
            message = f"Solend Data:\n{format_solend_message(solend_data)}\n\nKamino Data:\n{kamino_message}\n\nLast updated: `{last_fetched_data['timestamp'].strftime('%Y-%m-%d %H:%M:%S %Z%z')}`"
            send_telegram_message(message)

        if has_significant_change(last_fetched_data['solend_data'], solend_data) or has_significant_change(
                last_fetched_data['kamino_data'], kamino_data):
            message = f"ALERT: Significant APY change detected!\n\nSolend Data:\n{format_solend_message(solend_data)}\n\nKamino Data:\n{kamino_message}\n\nLast updated: `{last_fetched_data['timestamp'].strftime('%Y-%m-%d %H:%M:%S %Z%z')}`"
            send_telegram_message(message)

    except Exception as e:
        logging.error(f"Failed to fetch and send data: {e}")


def format_solend_message(data):
    return "\n".join([f'`{token}: {apr:.3f}%`' for token, apr in data.items()])


def should_send_notification(solend_data, kamino_data):
    return any(apr <= MIN_APY_THRESHOLD for apr in solend_data.values()) or (
            kamino_data and any(apy <= MIN_APY_THRESHOLD for apy in kamino_data.values())
    )


def has_significant_change(cached_data, new_data):
    try:
        if cached_data is None:
            return False
        for token, new_apy in new_data.items():
            if token in cached_data:
                old_apy = cached_data[token]
                if abs((new_apy - old_apy) / old_apy * 100) >= PERCENTAGE_CHANGE:
                    return True
        return False
    except Exception as e:
        logging.error(f"Error in has_significant_change: {e}")
        return False


def is_authorized(user_id):
    return user_id in AUTHORIZED_USERS


def is_request_too_frequent(user_id):
    now = datetime.now()
    if user_id in user_last_request_time:
        last_request_time = user_last_request_time[user_id]
        if (now - last_request_time) < timedelta(seconds=2):
            return True
    user_last_request_time[user_id] = now
    return False


@bot.message_handler(commands=['start'])
def start(message):
    try:
        logging.info(
            f"User {message.from_user.username} {message.from_user.first_name} {message.from_user.last_name} ({message.from_user.id}) issued /start command")

        markup = telebot.types.ReplyKeyboardMarkup(row_width=2)
        apy_btn = telebot.types.KeyboardButton('/apy')
        set_threshold_btn = telebot.types.KeyboardButton('/set_threshold')
        set_percentage_change_btn = telebot.types.KeyboardButton('/set_percentage_change')
        show_config_btn = telebot.types.KeyboardButton('/show_config')
        markup.add(apy_btn, set_threshold_btn, set_percentage_change_btn, show_config_btn)

        bot.reply_to(message,
                     'Use the buttons below to interact with the bot.',
                     reply_markup=markup)
    except Exception as e:
        logging.error(f"Error in start: {e}")


@bot.message_handler(commands=['apy'])
def apy(message):
    if is_request_too_frequent(message.from_user.id):
        return

    try:
        logging.info(
            f"User {message.from_user.username} {message.from_user.first_name} {message.from_user.last_name} ({message.from_user.id}) issued /apy command")
        if last_fetched_data['timestamp'] is None:
            bot.reply_to(message, "No data available yet. Please wait for the first fetch.")
        else:
            solend_message = format_solend_message(last_fetched_data['solend_data'])
            kamino_message = "\n".join(
                [f'`{token}: {apy:.3f}%`' for token, apy in last_fetched_data['kamino_data'].items()])
            timestamp = last_fetched_data['timestamp'].strftime('%Y-%m-%d %H:%M:%S %Z%z')
            message_text = f'Solend Data:\n{solend_message}\n\nKamino Data:\n{kamino_message}\n\nLast updated: `{timestamp}`'
            bot.reply_to(message, message_text)
    except Exception as e:
        bot.reply_to(message, f"Failed to fetch data: {e}")
        logging.error(f"Failed to fetch data: {e}")


@bot.message_handler(commands=['set_threshold'])
def set_threshold(message):
    if is_request_too_frequent(message.from_user.id):
        return

    try:
        logging.info(
            f"User {message.from_user.username} {message.from_user.first_name} {message.from_user.last_name} ({message.from_user.id}) issued /set_threshold command")
        if is_authorized(message.from_user.id):
            msg = bot.reply_to(message,
                               f'Current APY threshold is `{MIN_APY_THRESHOLD}`. Please enter new APY threshold:')
            bot.register_next_step_handler(msg, process_threshold_step)
        else:
            logging.warning(
                f"Unauthorized user {message.from_user.username} ({message.from_user.id}) tried to set threshold")
            bot.reply_to(message, 'You are not authorized to change settings.')
    except Exception as e:
        logging.error(f"Error in set_threshold: {e}")


def process_threshold_step(message):
    global MIN_APY_THRESHOLD
    try:
        logging.info(
            f"User {message.from_user.username} {message.from_user.first_name} {message.from_user.last_name} ({message.from_user.id}) issued new threshold: {message.text}")
        if is_authorized(message.from_user.id):
            new_threshold = float(message.text)
            MIN_APY_THRESHOLD = new_threshold
            bot.reply_to(message, f'APY threshold set to `{MIN_APY_THRESHOLD}`')
        else:
            logging.warning(
                f"Unauthorized user {message.from_user.username} ({message.from_user.id}) tried to set threshold")
            bot.reply_to(message, 'You are not authorized to change settings.')
    except ValueError:
        bot.reply_to(message, 'Invalid value. Please enter a valid number.')
    except Exception as e:
        bot.reply_to(message, f"Failed to process threshold: {e}")
        logging.error(f"Failed to process threshold: {e}")


@bot.message_handler(commands=['set_percentage_change'])
def set_percentage_change(message):
    if is_request_too_frequent(message.from_user.id):
        return

    try:
        logging.info(
            f"User {message.from_user.username} {message.from_user.first_name} {message.from_user.last_name} ({message.from_user.id}) issued /set_percentage_change command")
        if is_authorized(message.from_user.id):
            msg = bot.reply_to(message,
                               f'Current percentage change threshold is `{PERCENTAGE_CHANGE}`. Please enter new percentage change threshold:')
            bot.register_next_step_handler(msg, process_percentage_change_step)
        else:
            logging.warning(
                f"Unauthorized user {message.from_user.username} ({message.from_user.id}) tried to set percentage change threshold")
            bot.reply_to(message, 'You are not authorized to change settings.')
    except Exception as e:
        logging.error(f"Error in set_percentage_change: {e}")


def process_percentage_change_step(message):
    global PERCENTAGE_CHANGE
    try:
        logging.info(
            f"User {message.from_user.username} {message.from_user.first_name} {message.from_user.last_name} ({message.from_user.id}) issued new percentage change threshold: {message.text}")
        if is_authorized(message.from_user.id):
            new_threshold = float(message.text)
            PERCENTAGE_CHANGE = new_threshold
            bot.reply_to(message, f'Percentage change threshold set to `{PERCENTAGE_CHANGE}`')
        else:
            logging.warning(
                f"Unauthorized user {message.from_user.username} ({message.from_user.id}) tried to set percentage change threshold")
            bot.reply_to(message, 'You are not authorized to change settings.')
    except ValueError:
        bot.reply_to(message, 'Invalid value. Please enter a valid number.')
    except Exception as e:
        bot.reply_to(message, f"Failed to process percentage change threshold: {e}")
        logging.error(f"Failed to process percentage change threshold: {e}")


@bot.message_handler(commands=['show_config'])
def show_config(message):
    if is_request_too_frequent(message.from_user.id):
        return

    try:
        logging.info(
            f"User {message.from_user.username} {message.from_user.first_name} {message.from_user.last_name} ({message.from_user.id}) issued /show_config command")
        if is_authorized(message.from_user.id):
            authorized_usernames = [f"{bot.get_chat(user_id).username} ({user_id})" for user_id in AUTHORIZED_USERS]
            config_message = (
                f"```bash\nAuthorized Users: {', '.join(authorized_usernames)}\n"
                f"MIN_APY_THRESHOLD: {MIN_APY_THRESHOLD}\n"
                f"PERCENTAGE_CHANGE: {PERCENTAGE_CHANGE}\n"
                f"CHECK_INTERVAL: {CHECK_INTERVAL} seconds```"
            )
            bot.reply_to(message, config_message)
        else:
            logging.warning(
                f"Unauthorized user {message.from_user.username} ({message.from_user.id}) tried to view configuration")
            bot.reply_to(message, 'You are not authorized to view the configuration.')
    except Exception as e:
        logging.error(f"Error in show_config: {e}")


def scheduled_fetch_and_send():
    while True:
        fetch_and_send_data()
        time.sleep(CHECK_INTERVAL)


def run_bot():
    while True:
        try:
            bot.polling(none_stop=True)
        except Exception as e:
            logging.critical(f"Bot polling failed: {e}", exc_info=True)
            time.sleep(60)


def main():
    threading.Thread(target=run_bot).start()
    scheduled_fetch_and_send()


if __name__ == "__main__":
    while True:
        try:
            main()
        except Exception as e:
            logging.critical(f"Main loop exception: {e}", exc_info=True)
            time.sleep(60)
