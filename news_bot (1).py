"""
ai-news-summarizer-bot
-----------------------
Watches a news RSS feed (crypto/web3 by default, but works with any feed),
summarizes each new article with Google's free Gemini API, and posts the
summary to a Telegram channel or chat.

How it works:
1. Every CHECK_INTERVAL_SECONDS seconds, the script reads the RSS feed.
2. For each article it hasn't posted before (tracked in seen_articles.json),
   it sends the title + description to Gemini and asks for a short summary.
3. It sends the summary, plus a link to the original article, to Telegram.

Setup before running:
    1. Create a Telegram bot via @BotFather -> get a BOT_TOKEN.
    2. Get your CHAT_ID the same way as in price-tracker-bot
       (message the bot, then open https://api.telegram.org/bot<TOKEN>/getUpdates).
    3. Get a free Gemini API key at https://aistudio.google.com/apikey
    4. Fill in BOT_TOKEN, CHAT_ID and GEMINI_API_KEY below (or set them as
       environment variables: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, GEMINI_API_KEY).

Run:
    python news_bot.py
The script runs in an endless loop. Stop it with Ctrl+C.

Note: posting directly to X/Twitter requires a paid developer API plan,
so this bot posts to Telegram, which is free. A Telegram channel can be
linked and cross-posted to Twitter manually, or the send_to_telegram()
function below can be adapted to another destination if needed.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

import feedparser
import requests

# ======================= CONFIG =======================
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "PASTE_YOUR_BOT_TOKEN_HERE")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "PASTE_YOUR_CHAT_ID_HERE")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "PASTE_YOUR_GEMINI_API_KEY_HERE")

# Any RSS feed works here. This default is CoinDesk's crypto news feed.
RSS_FEED_URL = "https://www.coindesk.com/arc/outboundfeeds/rss/"

GEMINI_MODEL = "gemini-flash-latest"          # free-tier Gemini model
MAX_ARTICLES_PER_CHECK = 3                 # don't flood the channel on first run
CHECK_INTERVAL_SECONDS = 900               # how often to check the feed (15 min)

SEEN_FILE = "seen_articles.json"           # local file tracking already-posted articles
# ========================================================

GEMINI_URL_TEMPLATE = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
)
TELEGRAM_SEND_MESSAGE_URL = "https://api.telegram.org/bot{token}/sendMessage"


def load_seen_ids() -> set:
    """Loads the set of article links that have already been posted."""
    if not os.path.exists(SEEN_FILE):
        return set()
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except (json.JSONDecodeError, OSError):
        return set()


def save_seen_ids(seen_ids: set) -> None:
    """Saves the set of posted article links, keeping only the most recent 500."""
    trimmed = list(seen_ids)[-500:]
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(trimmed, f)


def fetch_latest_articles() -> list[dict]:
    """Downloads and parses the RSS feed, returning a list of {title, link, summary}."""
    feed = feedparser.parse(RSS_FEED_URL)

    if feed.bozo and not feed.entries:
        raise ValueError(f"Could not parse the RSS feed: {feed.bozo_exception}")

    articles = []
    for entry in feed.entries:
        articles.append(
            {
                "title": entry.get("title", "").strip(),
                "link": entry.get("link", "").strip(),
                "summary": entry.get("summary", "").strip(),
            }
        )
    return articles


def summarize_with_gemini(title: str, summary: str) -> str:
    """Asks Gemini for a 2-3 sentence summary of the article. Returns the original
    summary as a fallback if the request fails."""
    prompt = (
        "Summarize this news article in 2-3 short, punchy sentences for a Telegram "
        "audience interested in crypto and web3. No preamble, just the summary.\n\n"
        f"Title: {title}\n\nDescription: {summary}"
    )

    url = GEMINI_URL_TEMPLATE.format(model=GEMINI_MODEL, key=GEMINI_API_KEY)
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    try:
        response = requests.post(url, json=payload, timeout=20)
        response.raise_for_status()
        data = response.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (requests.RequestException, KeyError, IndexError) as e:
        print(f"Gemini summarization failed, falling back to the raw description: {e}", file=sys.stderr)
        return summary[:300]


def send_to_telegram(title: str, summary: str, link: str) -> bool:
    """Sends a formatted summary to Telegram. Returns True on success."""
    text = f"<b>{title}</b>\n\n{summary}\n\n{link}"
    url = TELEGRAM_SEND_MESSAGE_URL.format(token=BOT_TOKEN)
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": False}

    try:
        response = requests.post(url, json=payload, timeout=10)
    except requests.RequestException as e:
        print(f"Could not reach Telegram: {e}", file=sys.stderr)
        return False

    if response.status_code != 200:
        print(f"Telegram returned an error {response.status_code}: {response.text}", file=sys.stderr)
        return False

    print(f"Posted to Telegram: {title}")
    return True


def check_feed_once() -> None:
    """One check: fetch the feed, summarize and post any new articles."""
    seen_ids = load_seen_ids()
    articles = fetch_latest_articles()

    new_articles = [a for a in articles if a["link"] and a["link"] not in seen_ids]
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {len(articles)} articles in feed, {len(new_articles)} new.")

    # Only post a handful at a time, oldest first, so the channel isn't flooded.
    for article in new_articles[-MAX_ARTICLES_PER_CHECK:]:
        summary = summarize_with_gemini(article["title"], article["summary"])
        send_to_telegram(article["title"], summary, article["link"])
        seen_ids.add(article["link"])
        time.sleep(5)  # small delay between Telegram messages

    save_seen_ids(seen_ids)


def config_is_valid() -> bool:
    """Checks that all required config values were filled in before starting."""
    placeholders = ("PASTE_YOUR_BOT_TOKEN_HERE", "PASTE_YOUR_CHAT_ID_HERE", "PASTE_YOUR_GEMINI_API_KEY_HERE")
    if BOT_TOKEN in placeholders or CHAT_ID in placeholders or GEMINI_API_KEY in placeholders:
        print(
            "Set BOT_TOKEN, CHAT_ID and GEMINI_API_KEY at the top of this file before running.",
            file=sys.stderr,
        )
        return False
    return True


def main() -> None:
    if not config_is_valid():
        sys.exit(1)

    print(f"Watching {RSS_FEED_URL} every {CHECK_INTERVAL_SECONDS} sec. Press Ctrl+C to stop.")

    while True:
        try:
            check_feed_once()
        except requests.RequestException as e:
            print(f"Network error while fetching the feed: {e}", file=sys.stderr)
        except ValueError as e:
            print(f"Feed error: {e}", file=sys.stderr)

        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
