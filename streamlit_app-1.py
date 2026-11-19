import json
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone

import streamlit as st

# Oliver Signals — educational / paper tracking only.
# No broker connection and no real orders are placed.

PAIRS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "SOLUSDT"]
BINANCE_KLINES = "https://api.binance.com/api/v3/klines"
BINANCE_PRICE = "https://api.binance.com/api/v3/ticker/price"
HEADERS = {"User-Agent": "OliverSignals/1.0"}
MIN_SAMPLES = 5
TRADE_DURATION_MINUTES = 2
START_BALANCE = 500.00
SIMULATED_RESULT_AMOUNT = 5.00


def fetch_json(url, payload=None):
    data = None
    headers = dict(HEADERS)
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=12) as response:
        return json.loads(response.read().decode("utf-8"))


def get_market_data(symbol):
    params = urllib.parse.urlencode(
        {"symbol": symbol, "interval": "1m", "limit": 60}
    )
    candles = fetch_json(f"{BINANCE_KLINES}?{params}")
    # Ignore the candle that is still forming, just like the supplied script.
    closes = [float(candle[4]) for candle in candles[:-1]]
    if len(closes) < 22:
        raise ValueError("Not enough closed candles.")
    price_params = urllib.parse.urlencode({"symbol": symbol})
    current_price = float(
        fetch_json(f"{BINANCE_PRICE}?{price_params}")["price"]
    )
    return closes, current_price


def ema(values, period):
    multiplier = 2.0 / (period + 1)
    result = values[0]
    for value in values[1:]:
        result = value * multiplier + result * (1.0 - multiplier)
    return result


def rsi(values, period=14):
    recent = values[-(period + 1):]
    if len(recent) < period + 1:
        raise ValueError("Not enough values for RSI.")
    gains = 0.0
    losses = 0.0
    for index in range(1, len(recent)):
        change = recent[index] - recent[index - 1]
        if change > 0:
            gains += change
        else:
            losses -= change
    if losses == 0:
        return 100.0
    relative_strength = (gains / period) / (losses / period)
    return 100.0 - 100.0 / (1.0 + relative_strength)


def analyze(symbol):
    closes, current_price = get_market_data(symbol)
    fast_ema = ema(closes, 9)
    slow_ema = ema(closes, 21)
    current_rsi = rsi(closes)

    direction = None
    if fast_ema > slow_ema and 50 < current_rsi < 70:
        direction = "UP"
        reason = (
            f"EMA9 > EMA21 and RSI {current_rsi:.1f} "
            "(uptrend momentum)"
        )
    elif fast_ema < slow_ema and 30 < current_rsi < 50:
        direction = "DOWN"
        reason = (
            f"EMA9 < EMA21 and RSI {current_rsi:.1f} "
            "(downtrend momentum)"
        )
    else:
        reason = "Indicators do not meet the signal conditions."

    return {
        "العملة": symbol,
        "السعر الحالي": current_price,
        "EMA 9": fast_ema,
        "EMA 21": slow_ema,
        "RSI 14": current_rsi,
        "الاتجاه": direction or "WAIT",
        "قراءة المؤشرات": (
            "اتجاه صاعد" if direction == "UP"
            else "اتجاه هابط" if direction == "DOWN"
            else "لا توجد إشارة واضحة"
        ),
        "السبب": reason,
        "_direction": direction,
    }


def reliability(stats, symbol, direction):
    record = stats.get(symbol + "|" + direction)
    if not record or record["trades"] < MIN_SAMPLES:
        return None
    return record["wins"] * 100.0 / record["trades"]


def send_telegram(message, token, chat_id):
    payload = urllib.parse.urlencode(
        {"chat_id": chat_id, "text": message}
    ).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(request, timeout=12) as response:
        return json.loads(response.read().decode("utf-8"))


def init_state():
    defaults = {
        "market_results": [],
        "last_scan": None,
        "active_signal": None,
        "history": [],
        "stats": {},
        "signal_number": 1,
        "virtual_balance": START_BALANCE,
        "last_signal_message": "",
        "last_report_message": "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def scan_market():
    results = []
    progress = st.progress(0)
    status = st.empty()
    for index, symbol in enumerate(PAIRS):
        status.write(f"جارٍ فحص {symbol}...")
        try:
            results.append(analyze(symbol))
        except Exception as error:
            results.append({
                "العملة": symbol,
                "خطأ": str(error),
                "قراءة المؤشرات": "تعذّر جلب البيانات",
                "الاتجاه": "ERROR",
            })
        progress.progress((index + 1) / len(PAIRS))
    status.empty()
    progress.empty()
    st.session_state.market_results = results
    st.session_state.last_scan = datetime.now(timezone.utc)

    # Pick the strongest historical direction rate; new directions start at 50.
    if st.session_state.active_signal is None:
        best = None
        best_score = -1.0
        for item in results:
            direction = item.get("_direction")
            if not direction:
                continue
            rate = reliability(
                st.session_state.stats, item["العملة"], direction
            )
            score = 50.0 if rate is None else rate
            if score > best_score:
                best_score = score
                best = (item, rate)

        if best:
            item, rate = best
            now = datetime.now(timezone.utc)
            close_time = (
                now + timedelta(minutes=TRADE_DURATION_MINUTES)
            ).replace(second=0, microsecond=0)
            number = st.session_state.signal_number
            st.session_state.signal_number = number % 100 + 1
            st.session_state.active_signal = {
                "number": number,
                "symbol": item["العملة"],
                "direction": item["_direction"],
                "entry": item["السعر الحالي"],
                "reason": item["السبب"],
                "close_time": close_time,
                "reliability": rate,
            }
            rate_text = (
                "NEW (not enough results yet)"
                if rate is None else f"{rate:.1f}% past win rate"
            )
            st.session_state.last_signal_message = (
                f"SIGNAL #{number}\n"
                f"Candle: {close_time.strftime('%H:%M UTC')}\n"
                f"{item['العملة']} {item['_direction']}\n"
                f"Entry: {item['السعر الحالي']}\n"
                f"Reason: {item['السبب']}\n"
                f"Reliability: {rate_text}\n"
                "PAPER MODE ONLY — not financial advice."
            )


def check_active_signal():
    active = st.session_state.active_signal
    if not active:
        return "لا توجد إشارة معلّقة حالياً."
    now = datetime.now(timezone.utc)
    if now < active["close_time"]:
        remaining = int((active["close_time"] - now).total_seconds())
        return f"الإشارة ما زالت قيد المحاكاة. المتبقي تقريباً {remaining} ثانية."
    try:
        closing_price = float(
            fetch_json(
                BINANCE_PRICE + "?" +
                urllib.parse.urlencode({"symbol": active["symbol"]})
            )["price"]
        )
    except Exception as error:
        return f"تعذّر جلب سعر الإغلاق: {error}"

    difference = closing_price - active["entry"]
    if difference == 0:
        result = "DRAW"
    elif (difference > 0) == (active["direction"] == "UP"):
        result = "WIN"
    else:
        result = "LOSS"

    key = active["symbol"] + "|" + active["direction"]
    stats = st.session_state.stats
    if key not in stats:
        stats[key] = {"wins": 0, "trades": 0}
    if result != "DRAW":
        stats[key]["trades"] += 1
        if result == "WIN":
            stats[key]["wins"] += 1
            st.session_state.virtual_balance += SIMULATED_RESULT_AMOUNT
        else:
            st.session_state.virtual_balance -= SIMULATED_RESULT_AMOUNT

    st.session_state.history.append(result)
    st.session_state.history = st.session_state.history[-30:]
    history = st.session_state.history
    wins = history.count("WIN")
    losses = history.count("LOSS")
    draws = history.count("DRAW")
    report = (
        f"REPORT #{active['number']}\n"
        f"{active['symbol']} {active['direction']}\n"
        f"Entry: {active['entry']}\n"
        f"Close: {closing_price}\n"
        f"Result: {result}\n"
        f"Virtual balance: ${st.session_state.virtual_balance:.2f}\n"
        f"Last {len(history)}: {wins} WIN / {losses} LOSS / {draws} DRAW\n"
        "Demo accounting only; not actual profit/loss."
    )
    st.session_state.last_report_message = report
    st.session_state.active_signal = None
    return report


# -------------------- STREAMLIT INTERFACE --------------------

st.set_page_config(
    page_title="Oliver Signals",
    page_icon="📊",
    layout="wide",
)

st.markdown(
    """
    <style>
    .stApp {background: #0b1220; color: #f4f7fb;}
    [data-testid="stMetric"] {
        background: #141f32;
        border: 1px solid #263650;
        padding: 14px;
        border-radius: 12px;
    }
    div.stButton > button {
        background: #2563eb;
        color: white;
        border: 0;
        border-radius: 9px;
        padding: 0.6rem 1.2rem;
        font-weight: 600;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

init_state()

st.title("📊 Oliver Signals")
st.caption("لوحة متابعة تعليمية للسوق — بيانات مباشرة من Binance")

st.warning(
    "للتعلّم والمحاكاة الورقية فقط. الإشارات ليست توصية شراء أو بيع "
    "ولا تضمن أي نتيجة. لا توجد صفقات حقيقية ولا اتصال بمحفظة أو وسيط."
)

st.subheader("الحساب الافتراضي")
c1, c2, c3 = st.columns(3)
c1.metric("الرصيد الافتراضي", f"${st.session_state.virtual_balance:,.2f}")
c2.metric("عدد النتائج", len(st.session_state.history))
c3.metric("مدة المحاكاة", f"{TRADE_DURATION_MINUTES} دقيقة")

st.divider()
left, right = st.columns([3, 1])
with left:
    st.subheader("مراقبة العملات")
    st.write("يفحص التطبيق الأزواج الخمسة ويختار إشارة واحدة حسب سجل النتائج السابق.")
with right:
    scan_clicked = st.button("🔄 فحص السوق", use_container_width=True)

if scan_clicked:
    with st.spinner("جارٍ جلب بيانات Binance وتحليل الأزواج..."):
        scan_market()

if st.session_state.last_scan:
    st.caption(
        "آخر فحص: " +
        st.session_state.last_scan.strftime("%Y-%m-%d %H:%M:%S UTC")
    )

active = st.session_state.active_signal
if active:
    st.subheader(f"الإشارة الحالية #{active['number']}")
    a, b, c = st.columns(3)
    a.metric("الزوج", active["symbol"])
    b.metric("الاتجاه", active["direction"])
    c.metric("سعر الدخول الافتراضي", f"{active['entry']:,.6f}")
    st.write("**السبب:** " + active["reason"])
    st.write("**موعد فحص النتيجة:** " +
             active["close_time"].strftime("%H:%M:%S UTC"))
    if active["reliability"] is None:
        st.info("Reliability: NEW — لا توجد 5 نتائج سابقة كافية لهذا الزوج والاتجاه.")
    else:
        st.info(f"نسبة النتائج السابقة المسجلة: {active['reliability']:.1f}% (ليست ضماناً).")
    if st.button("⏱️ فحص نتيجة الإشارة"):
        st.session_state.check_message = check_active_signal()
        st.rerun()
elif st.session_state.get("check_message"):
    st.info(st.session_state.check_message)
    st.session_state.check_message = ""
elif st.session_state.last_report_message:
    st.subheader("آخر تقرير")
    st.code(st.session_state.last_report_message)

results = st.session_state.market_results
if results:
    good_results = [item for item in results if "خطأ" not in item]
    if good_results:
        st.subheader("ملخص السوق")
        metric_columns = st.columns(len(good_results))
        for column, item in zip(metric_columns, good_results):
            with column:
                direction = item["الاتجاه"]
                st.metric(
                    item["العملة"],
                    f'{item["السعر الحالي"]:,.6f} USDT',
                    direction,
                )
        st.subheader("تفاصيل المؤشرات")
        display_rows = [
            {key: value for key, value in item.items() if not key.startswith("_")}
            for item in good_results
        ]
        st.dataframe(display_rows, use_container_width=True, hide_index=True)

    errors = [item for item in results if "خطأ" in item]
    if errors:
        with st.expander("الأزواج التي تعذّر فحصها"):
            for item in errors:
                st.write(f'{item["العملة"]}: {item["خطأ"]}')
else:
    st.info("لم يتم إجراء فحص بعد. اضغط «فحص السوق» لعرض المؤشرات.")

if st.session_state.history:
    st.subheader("سجل آخر النتائج")
    h1, h2, h3 = st.columns(3)
    h1.metric("WIN", st.session_state.history.count("WIN"))
    h2.metric("LOSS", st.session_state.history.count("LOSS"))
    h3.metric("DRAW", st.session_state.history.count("DRAW"))
    st.write("الأحدث أولاً: " + " · ".join(reversed(st.session_state.history)))

st.divider()
st.subheader("إشعارات Telegram (اختياري)")
st.write(
    "يمكنك إرسال الإشارة أو التقرير إلى Telegram بعد وضع بيانات البوت "
    "بشكل آمن في Streamlit Secrets."
)

with st.expander("إعدادات Telegram"):
    st.code(
        '[telegram]\n'
        'bot_token = "ضع_توكن_جديد_هنا"\n'
        'chat_id = "ضع_chat_id_هنا"',
        language="toml",
    )
    telegram_enabled = st.checkbox("تفعيل إرسال الرسائل")
    message_choice = st.radio(
        "اختر الرسالة",
        ["آخر إشارة", "آخر تقرير"],
        horizontal=True,
    )
    if st.button("إرسال إلى Telegram"):
        if not telegram_enabled:
            st.error("فعّل خيار إرسال الرسائل أولاً.")
        else:
            message = (
                st.session_state.last_signal_message
                if message_choice == "آخر إشارة"
                else st.session_state.last_report_message
            )
            if not message:
                st.error("لا توجد رسالة من هذا النوع بعد.")
            else:
                try:
                    token = st.secrets["telegram"]["bot_token"]
                    chat_id = st.secrets["telegram"]["chat_id"]
                    send_telegram(message, token, chat_id)
                    st.success("تم إرسال الرسالة إلى Telegram.")
                except Exception:
                    st.error(
                        "تعذّر الإرسال. تأكد من إضافة bot_token و chat_id "
                        "داخل Streamlit Secrets."
                    )

st.caption(
    "المصدر: واجهة بيانات Binance العامة. قد تتأخر البيانات أو تنقطع. "
    "النتائج الافتراضية تعليمية فقط ولا تمثل أرباحاً أو خسائر حقيقية."
)
