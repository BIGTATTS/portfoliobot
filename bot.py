import os
import asyncio
import requests
import yfinance as yf
import anthropic
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], timeout=45.0)

_CIK_CACHE = {}

def get_price(ticker: str):
    data = yf.Ticker(ticker).history(period="1d")
    if data.empty:
        return None
    return round(data['Close'].iloc[-1], 2)

def get_pct_change(ticker: str):
    data = yf.Ticker(ticker).history(period="2d")
    if len(data) < 2:
        return None
    prev_close = data['Close'].iloc[-2]
    last_close = data['Close'].iloc[-1]
    return round((last_close - prev_close) / prev_close * 100, 2)

def get_news(ticker: str, limit: int = 3):
    try:
        items = yf.Ticker(ticker).news or []
    except Exception:
        return []
    headlines = []
    for item in items[:limit]:
        content = item.get("content", item)
        title = content.get("title") or item.get("title")
        if title:
            headlines.append(title)
    return headlines

def get_cik(ticker: str):
    global _CIK_CACHE
    if not _CIK_CACHE:
        try:
            resp = requests.get(
                "https://www.sec.gov/files/company_tickers.json",
                headers={"User-Agent": "portfolio-bot contact@example.com"},
                timeout=10,
            )
            data = resp.json()
            for entry in data.values():
                _CIK_CACHE[entry["ticker"].upper()] = str(entry["cik_str"]).zfill(10)
        except Exception:
            return None
    return _CIK_CACHE.get(ticker.upper())

def get_recent_filings(ticker: str, limit: int = 2):
    cik = get_cik(ticker)
    if not cik:
        return []
    try:
        resp = requests.get(
            f"https://data.sec.gov/submissions/CIK{cik}.json",
            headers={"User-Agent": "portfolio-bot contact@example.com"},
            timeout=10,
        )
        recent = resp.json().get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        filings = []
        for i in range(min(limit, len(forms))):
            filings.append({"form": forms[i], "date": dates[i]})
        return filings
    except Exception:
        return []

def gather_ticker_data(ticker: str) -> str:
    price = get_price(ticker)
    if price is None:
        return f"{ticker}: no data found\n"
    pct = get_pct_change(ticker)
    filings = get_recent_filings(ticker)
    news = get_news(ticker)

    block = f"--- {ticker} ---\n"
    block += f"Price: ${price}\n"
    block += f"Today's move: {pct}%\n" if pct is not None else "Today's move: unavailable\n"
    if filings:
        block += "Recent filings: " + "; ".join(f"{f['form']} ({f['date']})" for f in filings) + "\n"
    else:
        block += "Recent filings: none found\n"
    if news:
        block += "Headlines: " + " | ".join(news) + "\n"
    else:
        block += "Headlines: none found\n"
    return block

def generate_portfolio_review(tickers: list) -> str:
    data_blocks = [gather_ticker_data(t) for t in tickers]
    full_data = "\n".join(data_blocks)

    prompt = (
        "You are an experienced portfolio manager giving a verbal review of a group of holdings to a colleague. "
        "Using ONLY the data below for each ticker, write a single holistic review of this group as a whole \u2014 "
        "not a separate report per ticker. Talk about it the way a PM actually would: which names stand out today "
        "and why, which are lagging, whether there's concentrated risk or correlation across the group (e.g. same sector, "
        "same theme, moving together on the same news), and your overall read on how this collection of positions is behaving. "
        "Write naturally, in your own words \u2014 no headers, no bullet points, no per-ticker sections, no fixed structure. "
        "Do not invent any facts not in the data below. If something is missing, don't mention it or apologize for it. "
        "Write as much as the situation actually warrants.\n\n"
        f"{full_data}"
    )

    try:
        message = client.messages.create(
            model="claude-sonnet-5",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        return f"Portfolio review failed: {e}"

    for block in message.content:
        if block.type == "text":
            return block.text
    return "Something went wrong generating this review — try again."

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "VKC Portfolio Manager\n\n"
        "Send /portfolio followed by all the tickers you want reviewed together, and I'll give you a "
        "holistic portfolio manager's take on the group — standouts, laggards, correlated risk, and overall read.\n\n"
        "Example: /portfolio EDBL AAPL TSLA COIN"
    )

async def portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /portfolio TICKER1 TICKER2 TICKER3 ...")
        return
    tickers = [t.upper() for t in context.args]
    await update.message.reply_text(f"Reviewing {', '.join(tickers)}...")
    text = await asyncio.to_thread(generate_portfolio_review, tickers)
    await update.message.reply_text(text)

app = Application.builder().token(os.environ["BOT_TOKEN"]).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("portfolio", portfolio))

app.run_polling()