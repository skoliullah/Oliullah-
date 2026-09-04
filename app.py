import streamlit as st
import pandas as pd
import numpy as np
import sqlite3

# ==========================================
# PAGE CONFIGURATION & INSTITUTIONAL TERMINAL CSS
# ==========================================
st.set_page_config(
    page_title="Quant Institutional Option Terminal",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# Dark High-Contrast Pro Terminal Theme
st.markdown("""
    <style>
    .main { padding: 1.2rem; background-color: #0B0E14; }
    .stMetric {
        background-color: #151922;
        padding: 14px;
        border-radius: 10px;
        border: 1px solid #232836;
        box-shadow: 0 4px 6px rgba(0,0,0,0.3);
    }
    .order-box {
        background-color: #121824;
        border: 2px solid #2563EB;
        padding: 20px;
        border-radius: 12px;
        margin-top: 15px;
    }
    .order-field {
        background-color: #1E2638;
        border-left: 4px solid #3B82F6;
        padding: 10px 15px;
        margin-bottom: 10px;
        border-radius: 6px;
    }
    .copy-code-box {
        background-color: #090D16;
        border: 1px dashed #3B82F6;
        padding: 12px;
        border-radius: 8px;
        font-family: monospace;
        color: #60A5FA;
        margin-top: 10px;
    }
    .green-badge {
        background-color: #065F46;
        color: #34D399;
        padding: 6px 12px;
        border-radius: 6px;
        font-weight: bold;
    }
    .red-badge {
        background-color: #881337;
        color: #F87171;
        padding: 6px 12px;
        border-radius: 6px;
        font-weight: bold;
    }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# DATABASE PERSISTENCE (SQLite Engine)
# ==========================================
DB_NAME = "options_scanner_v6.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS option_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            atm_strike REAL,
            pcr REAL,
            max_pain REAL,
            call_trap REAL,
            put_trap REAL,
            signal TEXT,
            otm3_strike REAL,
            otm3_type TEXT,
            otm3_ltp REAL,
            total_call_chng_oi REAL,
            total_put_chng_oi REAL
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def save_snapshot(data):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''
        INSERT INTO option_snapshots 
        (atm_strike, pcr, max_pain, call_trap, put_trap, signal, otm3_strike, otm3_type, otm3_ltp, total_call_chng_oi, total_put_chng_oi)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        data['atm'], data['pcr'], data['max_pain'], data['call_trap'], data['put_trap'],
        data['signal'], data['otm3_strike'], data['otm3_type'], data['otm3_ltp'],
        data['call_chng'], data['put_chng']
    ))
    conn.commit()
    conn.close()

def get_last_two_snapshots():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('SELECT * FROM option_snapshots ORDER BY id DESC LIMIT 2')
    rows = c.fetchall()
    conn.close()
    return rows

def reset_database():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('DELETE FROM option_snapshots')
    conn.commit()
    conn.close()

# ==========================================
# DATA PARSER & QUANT ENGINE
# ==========================================
def parse_nse_csv(uploaded_file):
    cols = [
        'DUMMY_0',
        'CALL_OI', 'CALL_CHNG_IN_OI', 'CALL_VOLUME', 'CALL_IV', 'CALL_LTP', 'CALL_CHNG', 
        'CALL_BID_QTY', 'CALL_BID', 'CALL_ASK', 'CALL_ASK_QTY', 
        'STRIKE', 
        'PUT_BID_QTY', 'PUT_BID', 'PUT_ASK', 'PUT_ASK_QTY', 
        'PUT_CHNG', 'PUT_LTP', 'PUT_IV', 'PUT_VOLUME', 'PUT_CHNG_IN_OI', 'PUT_OI'
    ]
    df = pd.read_csv(uploaded_file, skiprows=2, names=cols, usecols=range(22))
    df = df.drop(columns=['DUMMY_0'])

    def clean_num(val):
        if pd.isna(val) or str(val).strip() in ['-', '']:
            return 0.0
        val_str = str(val).replace(',', '').strip()
        try:
            return float(val_str)
        except:
            return 0.0

    for c in df.columns:
        df[c] = df[c].apply(clean_num)

    return df[df['STRIKE'] > 0].sort_values('STRIKE').reset_index(drop=True)

def analyze_option_data(df):
    total_call_oi = df['CALL_OI'].sum()
    total_put_oi = df['PUT_OI'].sum()
    total_call_chng_oi = df['CALL_CHNG_IN_OI'].sum()
    total_put_chng_oi = df['PUT_CHNG_IN_OI'].sum()
    total_call_vol = df['CALL_VOLUME'].sum()
    total_put_vol = df['PUT_VOLUME'].sum()

    pcr = total_put_oi / total_call_oi if total_call_oi > 0 else 0.0

    # 1. Automatic Spot ATM Strike Detection
    valid_df = df[(df['CALL_LTP'] > 0) & (df['PUT_LTP'] > 0)].copy()
    valid_df['LTP_DIFF'] = (valid_df['CALL_LTP'] - valid_df['PUT_LTP']).abs()
    atm_row = valid_df.sort_values('LTP_DIFF').iloc[0]
    atm_idx = df[df['STRIKE'] == atm_row['STRIKE']].index[0]
    atm_strike = df.loc[atm_idx, 'STRIKE']

    # 2. Max Pain Level Calculation
    strikes = df['STRIKE'].values
    call_oi = df['CALL_OI'].values
    put_oi = df['PUT_OI'].values
    total_losses = [np.sum(np.maximum(0, k - strikes) * call_oi + np.maximum(0, strikes - k) * put_oi) for k in strikes]
    max_pain_strike = strikes[np.argmin(total_losses)]

    # 3. Traps
    call_trap = df.loc[df['CALL_OI'].idxmax(), 'STRIKE']
    put_trap = df.loc[df['PUT_OI'].idxmax(), 'STRIKE']

    # 4. Average IV for Dynamic SL Buffer Adjustment
    avg_iv = (df['CALL_IV'].mean() + df['PUT_IV'].mean()) / 2.0
    sl_buffer_pct = 0.28 if avg_iv > 18.0 else 0.22  # Wider SL buffer during high IV

    # 5. RELATIVE MOMENTUM SCORING ENGINE
    bullish_score = (total_put_chng_oi * 0.6) + (total_put_vol * 0.4)
    bearish_score = (total_call_chng_oi * 0.6) + (total_call_vol * 0.4)

    if bullish_score >= bearish_score:
        signal = "BULLISH_MOMENTUM"
        otm3_idx = min(atm_idx + 3, len(df) - 1)
        otm3_strike = df.loc[otm3_idx, 'STRIKE']
        otm3_type = "CE"
        otm3_ltp = df.loc[otm3_idx, 'CALL_LTP']
        total_breakeven = otm3_strike + otm3_ltp
    else:
        signal = "BEARISH_MOMENTUM"
        otm3_idx = max(atm_idx - 3, 0)
        otm3_strike = df.loc[otm3_idx, 'STRIKE']
        otm3_type = "PE"
        otm3_ltp = df.loc[otm3_idx, 'PUT_LTP']
        total_breakeven = otm3_strike - otm3_ltp

    return {
        'atm': atm_strike,
        'pcr': pcr,
        'max_pain': max_pain_strike,
        'call_trap': call_trap,
        'put_trap': put_trap,
        'signal': signal,
        'otm3_strike': otm3_strike,
        'otm3_type': otm3_type,
        'otm3_ltp': otm3_ltp,
        'total_breakeven': total_breakeven,
        'sl_buffer_pct': sl_buffer_pct,
        'avg_iv': avg_iv,
        'call_chng': total_call_chng_oi,
        'put_chng': total_put_chng_oi
    }

# ==========================================
# MAIN DASHBOARD UI
# ==========================================
st.title("⚡ QUANT INSTITUTIONAL OPTION TERMINAL")
st.caption("Automatic OTM-3 Strike Identification • IV-Safe SL • One-Click Order Blueprint")

# --- FILE UPLOADER ON MAIN SCREEN ---
st.subheader("📁 Upload NSE Option Chain CSV File")
uploaded_file = st.file_uploader(
    "Select your downloaded NSE Option Chain CSV file:", 
    type=None, 
    help="Tap here to select the CSV file from your mobile or desktop storage."
)

st.markdown("---")

# ==========================================
# SIDEBAR CONTROL PANEL
# ==========================================
with st.sidebar:
    st.title("⚙️ Controls & History")
    if st.button("🗑️ Reset Snapshot History"):
        reset_database()
        st.success("Database history cleared!")

if uploaded_file is not None:
    try:
        df = parse_nse_csv(uploaded_file)
        res = analyze_option_data(df)
        save_snapshot(res)

        st.success("✅ Option Chain Analyzed Successfully!")

        # ------------------------------------------
        # SECTION 1: LIVE MARKET METRICS & SIGNAL BADGE
        # ------------------------------------------
        st.markdown("### 📊 Market Snapshot & Momentum Direction")
        
        m1, m2 = st.columns(2)
        m1.metric("📍 Spot ATM Strike", f"{res['atm']:,.0f}")
        m2.metric("💀 Max Pain Level", f"{res['max_pain']:,.0f}")

        m3, m4 = st.columns(2)
        m3.metric("📊 Overall PCR Ratio", f"{res['pcr']:.2f}")
        
        if res['signal'] == "BULLISH_MOMENTUM":
            m4.markdown("#### **Best Trade Direction:** <span class='green-badge'>🟢 CALL BUYING (CE)</span>", unsafe_allow_html=True)
        else:
            m4.markdown("#### **Best Trade Direction:** <span class='red-badge'>🔴 PUT BUYING (PE)</span>", unsafe_allow_html=True)

        st.markdown("---")

        # ------------------------------------------
        # SECTION 2: DELTA SHIFT & WRITER PANIC MATRIX (FIXED SQL INDEXING)
        # ------------------------------------------
        st.markdown("### 🔄 Delta Shift & Seller Panic Tracker")
        history = get_last_two_snapshots()

        if len(history) > 1:
            curr = history[0]
            prev = history[1]

            shift_atm = curr[2] - prev[2]
            shift_mp = curr[4] - prev[4]

            d1, d2 = st.columns(2)
            d1.metric("📍 ATM Shift", f"{curr[2]:,.0f}", delta=f"{shift_atm:+,.0f} pts")
            d2.metric("💀 Max Pain Shift", f"{curr[4]:,.0f}", delta=f"{shift_mp:+,.0f} pts")

            # Correct SQL Index Access: Index 11 = Call Change OI, Index 12 = Put Change OI
            st.markdown("#### 🚨 Option Writer Panic Alerts")
            if curr[2] > prev[4] and curr[11] < 0:
                st.error(f"🔥 CALL WRITER PANIC DETECTED: Spot breached previous Max Pain ({prev[4]:,.0f}) with Call unwinding!")
            elif curr[2] < prev[4] and curr[12] < 0:
                st.error(f"🔥 PUT WRITER PANIC DETECTED: Spot dropped below previous Max Pain ({prev[4]:,.0f}) with Put unwinding!")
            else:
                st.info("ℹ️ Market structure is steady. Directional bias active.")
        else:
            st.warning("ℹ️ First CSV uploaded. Delta Shift comparison will display on your next CSV upload.")

        st.markdown("---")

        # ------------------------------------------
        # SECTION 3: AUTOMATED STRIKE & DYNAMIC PRICE CALCULATION
        # ------------------------------------------
        st.markdown("### 🎯 Selected Strike & Target Calculation")

        ltp = res['otm3_ltp']
        
        # DISCOUNT BUYING ZONE
        buy_limit_min = round(ltp * 0.94, 2)  # 6% Discount
        buy_limit_max = round(ltp * 0.98, 2)  # 2% Discount
        
        # THETA-SAFE DYNAMIC TARGETS
        target_fast = round(ltp * 1.25, 2)     # +25% Fast Scalp Target
        target_trend = round(ltp * 1.50, 2)    # +50% Trend Target
        
        # IV-ADAPTIVE NOISE-PROOF STOP LOSS
        noise_proof_sl = round(ltp * (1.0 - res['sl_buffer_pct']), 2)
        trailing_step = round(ltp * 0.05, 2)

        t1, t2 = st.columns(2)
        t1.metric("📌 Best Strike Selected", f"{res['otm3_strike']:,.0f} {res['otm3_type']}")
        t2.metric("🛒 Discount Buy Zone (Limit)", f"₹{buy_limit_min:.2f} - ₹{buy_limit_max:.2f}")

        t3, t4, t5 = st.columns(3)
        t3.metric("⚡ Fast Scalp Target (+25%)", f"₹{target_fast:.2f}")
        t4.metric("🚀 Trend Target (+50%)", f"₹{target_trend:.2f}")
        t5.metric(f"🛡️ Noise-Proof SL (-{int(res['sl_buffer_pct']*100)}%)", f"₹{noise_proof_sl:.2f}")

        st.markdown("---")

        # ------------------------------------------
        # SECTION 4: ALL-IN-ONE BRACKET ORDER / OCO BLUEPRINT
        # ------------------------------------------
        st.markdown("### 🚀 ONE-CLICK BRACKET ORDER / OCO ORDER BLUEPRINT")
        st.caption("Copy and paste these exact parameters into your broker's Bracket Order / GTT / OCO Form:")

        st.markdown(f"""
        <div class="order-box">
            <h3 style="color: #60A5FA; margin-top:0;">📋 Broker Order Form Blueprint ({res['otm3_strike']:,.0f} {res['otm3_type']})</h3>
            <div class="order-field">
                <strong>1. Order Type:</strong> LIMIT / BRACKET ORDER (OCO / GTT)
            </div>
            <div class="order-field">
                <strong>2. Selected Instrument:</strong> NIFTY {res['otm3_strike']:,.0f} {res['otm3_type']}
            </div>
            <div class="order-field">
                <strong>3. Buy Limit Price (Trigger Entry):</strong> ₹{buy_limit_max:.2f} <i>(Buy in range ₹{buy_limit_min:.2f} - ₹{buy_limit_max:.2f})</i>
            </div>
            <div class="order-field">
                <strong>4. Target / Take Profit (TP):</strong> ₹{target_fast:.2f} <i>(Auto-Exits at +25% profit)</i>
            </div>
            <div class="order-field">
                <strong>5. Stop Loss Price (Hard SL):</strong> ₹{noise_proof_sl:.2f} <i>(IV-Adjusted Noise Buffer)</i>
            </div>
            <div class="order-field">
                <strong>6. Trailing Stop Loss Step:</strong> ₹{trailing_step:.2f} <i>(Trails SL up by ₹{trailing_step:.2f} per 5% gain)</i>
            </div>
            <div class="order-field">
                <strong>7. Total Price Break-Even Level:</strong> ₹{res['total_breakeven']:,.2f}
            </div>
        </div>
        """, unsafe_allow_html=True)

        # Quick Copy Code Snippet
        st.markdown("#### 📋 Quick Copy Format for Traders:")
        copy_text = f"SYMBOL: NIFTY {res['otm3_strike']:,.0f} {res['otm3_type']} | BUY_LIMIT: {buy_limit_max:.2f} | TARGET: {target_fast:.2f} | SL: {noise_proof_sl:.2f} | TRAILING_STEP: {trailing_step:.2f}"
        st.code(copy_text, language="text")

        st.info(f"""
        💡 **How to place this order in Zerodha / AngelOne / Groww / Dhan:**
        1. Open your trading terminal and select **{res['otm3_strike']:,.0f} {res['otm3_type']}**.
        2. Choose **Bracket Order (BO)** or **GTT / OCO Order**.
        3. Enter **Buy Price = ₹{buy_limit_max:.2f}**, **Target = ₹{target_fast:.2f}**, and **Stop Loss = ₹{noise_proof_sl:.2f}**.
        4. Click **Submit**. Entry, Target, and Stop Loss will execute automatically.
        """)

    except Exception as e:
        st.error(f"❌ Error processing file: {e}")
