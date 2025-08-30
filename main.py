import os
import requests
import json
from datetime import datetime, timedelta
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ============ SETUP GOOGLE SHEETS ============
def auth_gsheet_from_env():
    json_creds = os.getenv("GOOGLE_CREDS_JSON")
    with open("temp-creds.json", "w") as f:
        f.write(json_creds)

    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("temp-creds.json", scope)
    client = gspread.authorize(creds)
    return client

sheet_client = auth_gsheet_from_env()
sheet = sheet_client.open("Forex_Gap_Logger").sheet1

# ============ SETUP CONFIG ============
TD_API_KEY = os.getenv("TD_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

PAIR_LIST = ["GBP/USD", "EUR/USD", "USD/JPY", "EUR/JPY", "AUD/JPY"]
TF_MAP = {"4h": "240", "1day": "D"}
MIN_GAP_PIPS = 20

# ============ UTILITIES ============
def send_to_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg}
    try:
        requests.post(url, data=payload)
    except Exception as e:
        print("Telegram error:", e)

def get_rsi(pair, tf):
    url = f"https://api.twelvedata.com/rsi?symbol={pair}&interval={tf}&apikey={TD_API_KEY}"
    try:
        data = requests.get(url).json()
        return float(data["values"][0]["rsi"]) if "values" in data else None
    except:
        return None

def get_candles(pair, tf, count=2):
    url = f"https://api.twelvedata.com/time_series?symbol={pair}&interval={tf}&outputsize={count}&apikey={TD_API_KEY}"
    try:
        return requests.get(url).json().get("values", [])
    except:
        return []

def build_chart_url(pair, tf):
    tv_pair = pair.replace("/", "")
    tv_tf = TF_MAP.get(tf, "240")
    return f"https://www.tradingview.com/chart/?symbol=FX:{tv_pair}&interval={tv_tf}"

# ============ MAIN GAP CHECK & LOGGING ============
def check_gap(pair, tf):
    candles = get_candles(pair, tf)
    if len(candles) < 2:
        return

    curr_open = float(candles[0]['open'])
    prev_close = float(candles[1]['close'])
    pip_value = 0.0001 if "JPY" not in pair else 0.01
    gap_pips = abs(curr_open - prev_close) / pip_value

    if gap_pips < MIN_GAP_PIPS:
        return

    direction = "GAP UP" if curr_open > prev_close else "GAP DOWN"
    rsi = get_rsi(pair, tf)
    rsi_str = f"{rsi:.1f}" if rsi else "N/A"
    chart_url = build_chart_url(pair, tf)

    # Suggestion logic
    if direction == "GAP UP":
        suggestion = "Overbought GAP UP. Consider SHORT." if rsi and rsi > 70 else "GAP UP. Wait for structure."
    else:
        suggestion = "Oversold GAP DOWN. Consider LONG." if rsi and rsi < 30 else "GAP DOWN. Wait for confirmation."

    # Telegram Message
    message = f"""
📊 {direction} Detected!
📍 Pair: {pair}
🕒 TF: {tf}
📏 Gap: {gap_pips:.1f} pips
📉 RSI: {rsi_str}

🧠 Suggestion:
{suggestion}

🔗 {chart_url}
    """
    send_to_telegram(message.strip())

    # Log to Google Sheet
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    sheet.append_row([
        timestamp, pair, tf, f"{gap_pips:.1f}",
        rsi_str, direction, suggestion, "Pending", chart_url
    ])

# ============ OUTCOME TRACKING ============
def update_outcomes():
    rows = sheet.get_all_records()
    for idx, entry in enumerate(rows, start=2):  # skip header (row 1)
        if entry["Outcome"] != "Pending":
            continue

        pair = entry["Pair"]
        tf = entry["TF"]
        gap_pips = float(entry["Gap (pips)"])
        direction = entry["Direction"]
        timestamp = entry["Timestamp"]
        pip_value = 0.0001 if "JPY" not in pair else 0.01

        occured_at = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
        age = (datetime.utcnow() - occured_at).total_seconds()

        # Check outcome only if older than 6h
        wait_time = 6 * 3600 if tf == "4h" else 24 * 3600
        if age < wait_time:
            continue

        # Re-check price to see if gap filled
        candles = get_candles(pair, tf, 1)
        if len(candles) < 1:
            continue

        open_price = float(candles[0]['open'])
        curr_price = float(candles[0]['close'])

        if direction == "GAP UP":
            expected_fill = open_price - gap_pips * pip_value
            if curr_price <= expected_fill:
                sheet.update_cell(idx, 8, "Filled ✅")
            else:
                sheet.update_cell(idx, 8, "Not Filled ❌")
        elif direction == "GAP DOWN":
            expected_fill = open_price + gap_pips * pip_value
            if curr_price >= expected_fill:
                sheet.update_cell(idx, 8, "Filled ✅")
            else:
                sheet.update_cell(idx, 8, "Not Filled ❌")

# ============ MAIN RUN ============
def run_bot():
    for pair in PAIR_LIST:
        for tf in TF_MAP:
            check_gap(pair, tf)
    update_outcomes()

run_bot()
