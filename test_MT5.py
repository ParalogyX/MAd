import sys
import time
from mt5linux import MetaTrader5

def log(msg):
    print(msg, flush=True)

log("1. importing OK")
log("2. connecting to mt5linux server...")

mt5 = MetaTrader5(host="192.168.2.125", port=8001)

log("3. connected to server")
log("4. calling initialize()...")

result = mt5.initialize()

log(f"5. initialize result: {result}")
log(f"6. version: {mt5.version()}")
log(f"7. terminal_info: {mt5.terminal_info()}")

symbols = mt5.symbols_get()
log(f"8. symbols result type: {type(symbols)}")
log(f"9. symbols count: {len(symbols) if symbols else symbols}")

if symbols:
    log(f"10. first symbol: {symbols[0]}")
print()
print()
print("test2")
print()
print()

symbols = mt5.symbols_get()
visible = [s for s in symbols if s.visible]
tradable = [s for s in symbols if s.trade_mode != 0]

print("all:", len(symbols))
print("visible:", len(visible))
print("tradable:", len(tradable))
print([s.name for s in symbols[:20]])



print()
print()
print("test3")
print()
print()

symbol = "EURUSD"

mt5.symbol_select(symbol, True)

tick = mt5.symbol_info_tick(symbol)
info = mt5.symbol_info(symbol)
rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, 10)

print(tick)
print(info)
print(rates)