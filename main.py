import os
import json
import logging
import asyncio
import requests
import pandas as pd
import pytz
from datetime import datetime as dt, date, time as dtime
from bs4 import BeautifulSoup
from telegram import Bot
import matplotlib.pyplot as plt
import random

# ---------------- CONFIGURATION ----------------
ZONE = pytz.timezone("Asia/Kolkata")
CHECK_INTERVAL = (55, 65)  # Random delay range in seconds
TEST_MODE = True  # ✅ Set True for after-hours testing
RUN_ON_WEEKENDS = True  # ✅ Allow weekend execution if True

# Logging setup
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Load clients from environment variable
CLIENTS = json.loads(os.getenv("CLIENTS_JSON", "{}"))

# Load public holidays from environment variable (format: ["2025-01-26", "2025-08-15"])
HOLIDAYS = json.loads(os.getenv("HOLIDAYS_JSON", "[]"))
HOLIDAYS = [date.fromisoformat(d) for d in HOLIDAYS]

# ---------------- FUNCTIONS ----------------
def is_market_hours():
    now = dt.now(tz=ZONE).time()
    return dtime(9, 15) <= now <= dtime(15, 15)

def is_weekend():
    today = dt.now(tz=ZONE).weekday()  # Monday=0, Sunday=6
    return today >= 5

def is_holiday():
    today = dt.now(tz=ZONE).date()
    return today in HOLIDAYS

async def send_to_telegram(bot: Bot, chat_id: str, stock_code: str):
    try:
        img_name = f"{stock_code}.png"
        plt.figure(figsize=(4, 2))
        plt.text(0.5, 0.5, f"TradingView Chart\n{stock_code}", fontsize=12, ha="center")
        plt.axis("off")
        plt.savefig(img_name)
        plt.close()

        with open(img_name, "rb") as photo:
            await bot.send_photo(chat_id=chat_id, photo=photo, caption=f"TradingView: {stock_code}")
        logging.info(f"Sent {stock_code} to Telegram for chat {chat_id}")
        os.remove(img_name)
    except Exception as e:
        logging.error(f"Error sending {stock_code}: {e}")

def get_stocks(scanner_url: str, payload: str):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "text/html,application/xhtml+xml",
        "Referer": "https://chartink.com/",
    }
    try:
        with requests.Session() as s:
            s.headers.update(headers)
            r = s.get(scanner_url)
            soup = BeautifulSoup(r.text, "html.parser")
            csrf_tag = soup.select_one("[name='csrf-token']")
            if not csrf_tag:
                logging.warning("CSRF token not found.")
                return pd.DataFrame()

            csrf = csrf_tag["content"]
            s.headers["x-csrf-token"] = csrf

            r = s.post("https://chartink.com/screener/process", data={"scan_clause": payload})
            json_data = r.json()

            if "data" not in json_data or not json_data["data"]:
                logging.warning("No data returned from Chartink.")
                return pd.DataFrame()

            df = pd.DataFrame(json_data["data"])
            if "close" not in df.columns or "nsecode" not in df.columns:
                logging.warning(f"Required columns missing: {df.columns.tolist()}")
                return pd.DataFrame()

            df.sort_values(by=["close"], inplace=True)
            df.drop("sr", axis=1, errors="ignore", inplace=True)
            df.reset_index(drop=True, inplace=True)

            logging.info(f"Number of stocks found: {len(df)}")
            return df.head(10)
    except Exception as e:
        logging.error(f"Error fetching stocks: {e}")
        return pd.DataFrame()

async def main():
    while True:
        today = dt.now(tz=ZONE).date()

        # Check conditions
        if is_holiday():
            logging.info("Today is a public holiday. Skipping execution.")
        elif is_weekend() and not RUN_ON_WEEKENDS:
            logging.info("Weekend detected and RUN_ON_WEEKENDS=False. Skipping execution.")
        elif is_market_hours() or TEST_MODE:
            for client_name, client_data in CLIENTS.items():
                expiry_date = date.fromisoformat(client_data["EXPIRY"])
                if today > expiry_date:
                    logging.warning(f"Client {client_name} subscription expired.")
                    continue

                bot = Bot(token=client_data["BOT_TOKEN"])
                chat_id = client_data["CHAT_ID"]
                scanner_url = client_data["SCANNER_URL"]
                payload = client_data["PAYLOAD"]

                stocks = get_stocks(scanner_url, payload)
                if not stocks.empty and "nsecode" in stocks.columns:
                    for stock in stocks["nsecode"].tolist():
                        await send_to_telegram(bot, chat_id, stock)
                else:
                    logging.info(f"No valid stock data found for client {client_name}.")
        else:
            logging.info("Outside market hours. Waiting...")

        delay = random.uniform(*CHECK_INTERVAL)
        logging.info(f"Waiting for {int(delay)} seconds...")
        await asyncio.sleep(delay)

if __name__ == "__main__":
    asyncio.run(main())