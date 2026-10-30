import os, time
from datetime import datetime, timezone
import pandas as pd
import requests
import streamlit as st
from streamlit_autorefresh import st_autorefresh

st.set_page_config(page_title='1m Demo Trade Monitor', page_icon='📈', layout='wide')
st.title('1-Minute Auto Demo Trader')
st.caption('Independent paper trading only. No Quotex account connection or OTC price feed.')
API_KEY = st.sidebar.text_input('Twelve Data API key', type='password', value=os.getenv('TWELVE_DATA_API_KEY',''))
PAIR = st.sidebar.selectbox('Real-market symbol', ['EUR/USD','GBP/USD','USD/JPY','AUD/USD'])
STAKE = st.sidebar.number_input('Virtual stake ($)', min_value=1.0, max_value=100.0, value=10.0, step=1.0, disabled=True)
PAYOUT = st.sidebar.slider('Assumed winning payout (%)', 50, 95, 80) / 100
LOSS_LIMIT = st.sidebar.number_input('Session loss limit ($)', min_value=1.0, value=50.0, disabled=True)
MAX_TRADES = st.sidebar.number_input('Maximum session trades', min_value=1, max_value=100, value=20)
if 'day' not in st.session_state: st.session_state.day = datetime.now(timezone.utc).date().isoformat()
if 'running' not in st.session_state: st.session_state.running = False
if 'balance' not in st.session_state: st.session_state.balance = 1000.0
if 'trades' not in st.session_state: st.session_state.trades = []
if 'last_candle' not in st.session_state: st.session_state.last_candle = None
if 'initial_balance' not in st.session_state: st.session_state.initial_balance = 1000.0
c1,c2,c3=st.columns(3)
if c1.button('▶ Start demo', use_container_width=True): st.session_state.running=True
if c2.button('■ Emergency stop', use_container_width=True): st.session_state.running=False
if c3.button('Reset session', use_container_width=True):
    st.session_state.running=False; st.session_state.balance=1000.0; st.session_state.initial_balance=1000.0; st.session_state.trades=[]; st.session_state.last_candle=None
today = datetime.now(timezone.utc).date().isoformat()
if today != st.session_state.day:
    st.session_state.running = False
    st.session_state.day = today
    st.session_state.initial_balance = st.session_state.balance
st_autorefresh(interval=15000, key='refresh')

def fetch_candles():
    r=requests.get('https://api.twelvedata.com/time_series', params={'symbol':PAIR,'interval':'1min','outputsize':80,'apikey':API_KEY}, timeout=12)
    r.raise_for_status(); data=r.json()
    if 'values' not in data: raise ValueError(data.get('message','No candle data available'))
    df=pd.DataFrame(data['values']); df['datetime']=pd.to_datetime(df['datetime']); df['close']=pd.to_numeric(df['close']); df=df.sort_values('datetime').reset_index(drop=True)
    return df

if not API_KEY:
    st.info('Enter a Twelve Data API key to load real-market candles. No trades are placed without valid data.')
    st.stop()
try:
    df=fetch_candles()
except Exception as e:
    st.error(f'Market data unavailable: {e}'); st.session_state.running=False; st.stop()
# Only use candles whose minute has fully completed, based on UTC timestamps.
now=datetime.now(timezone.utc)
if len(df)<35: st.warning('Not enough data'); st.stop()
# Twelve Data timestamps are exchange-local and lack offsets. Conservative fallback:
# exclude the newest candle, and require a new completed candle timestamp for entry.
completed=df.iloc[:-1].copy()
completed['ema9']=completed.close.ewm(span=9,adjust=False).mean()
completed['ema21']=completed.close.ewm(span=21,adjust=False).mean()
delta=completed.close.diff(); gain=delta.clip(lower=0).ewm(alpha=1/14,adjust=False).mean(); loss=(-delta.clip(upper=0)).ewm(alpha=1/14,adjust=False).mean(); completed['rsi']=100-100/(1+gain/loss.replace(0,1e-12))
ema12=completed.close.ewm(span=12,adjust=False).mean()
ema26=completed.close.ewm(span=26,adjust=False).mean()
macd=ema12-ema26
macd_signal=macd.ewm(span=9,adjust=False).mean()
completed['macd_hist']=macd-macd_signal
last=completed.iloc[-1]; candle=str(last['datetime']); price=float(last.close)
signal='WAIT'
if last.ema9>last.ema21 and 52<last.rsi<70 and last.macd_hist>0: signal='UP'
elif last.ema9<last.ema21 and 30<last.rsi<48 and last.macd_hist<0: signal='DOWN'
# Settle at the first observed quote after the 60-second deadline.
# This is an approximation, not a broker's official expiry settlement.
for trade in st.session_state.trades:
    if trade['status']=='OPEN' and time.time()>=trade['deadline']:
        win=(price>trade['entry'] if trade['direction']=='UP' else price<trade['entry'])
        tie=(price==trade['entry'])
        pnl=0 if tie else (trade['stake']*PAYOUT if win else -trade['stake'])
        trade.update(status='TIE' if tie else 'WIN' if win else 'LOSS',exit=price,pnl=round(pnl,2))
        st.session_state.balance+=pnl
realized=st.session_state.balance-st.session_state.initial_balance
open_trades=sum(t['status']=='OPEN' for t in st.session_state.trades)
loss_streak = 0
for t in reversed(st.session_state.trades):
    if t['status'] == 'LOSS': loss_streak += 1
    elif t['status'] in ('WIN', 'TIE'): break
if realized <= -LOSS_LIMIT or loss_streak >= 3 or len(st.session_state.trades)>=MAX_TRADES: st.session_state.running=False
# One position at a time, at most one per completed candle.
if st.session_state.running and signal!='WAIT' and not open_trades and candle!=st.session_state.last_candle and realized-STAKE >= -LOSS_LIMIT:
    st.session_state.trades.append({'time':now.strftime('%Y-%m-%d %H:%M:%S UTC'),'candle':candle,'symbol':PAIR,'direction':signal,'entry':price,'stake':STAKE,'deadline':time.time()+60,'status':'OPEN','exit':None,'pnl':None})
    st.session_state.last_candle=candle
m1,m2,m3,m4=st.columns(4)
m1.metric('Demo balance',f'${st.session_state.balance:.2f}')
m2.metric('Session P/L',f'${realized:+.2f}')
m3.metric('Signal',signal)
st.caption(f'Consecutive losses: {loss_streak}/3 | Daily loss limit: ${LOSS_LIMIT:.0f} (UTC reset) | Fixed stake: ${STAKE:.0f}')
m4.metric('Status','RUNNING' if st.session_state.running else 'STOPPED')
st.line_chart(completed.set_index('datetime')['close'].tail(40))
st.caption(f'Last completed candle: {candle} | RSI {last.rsi:.1f} | Latest fetched price {price:.5f}')
st.subheader('Simulated trades')
if st.session_state.trades: st.dataframe(pd.DataFrame(st.session_state.trades).drop(columns=['deadline']).iloc[::-1],use_container_width=True)
else: st.info('No demo trades yet.')
st.warning('Educational simulation. A Streamlit session can pause, disconnect, or restart. Do not treat this as an always-on execution engine or a Quotex price/expiry match.')
