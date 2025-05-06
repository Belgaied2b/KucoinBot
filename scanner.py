 # … en tête du fichier …

 async def scan_and_send_signals(bot):
     symbols = get_kucoin_perps()
     total   = len(symbols)
     logger.info(f"🔍 Démarrage du scan — {total} contracts détectés")

-    # compteurs de rejet par filtre
+    # compteurs de rejet par filtre
     cnt_len      = 0  # trop peu de données
     cnt_trend    = 0  # trend
     cnt_rsmacd   = 0  # RSI/MACD
     cnt_fvg      = 0  # Fair Value Gap
+    cnt_fibo_ote = 0  # Fibo/OTE

     # compteurs d’acceptation
     accepted_l   = 0
     accepted_s   = 0

     for symbol in symbols:
         try:
             # 1) OHLCV & Swing high/low
             df_low = fetch_klines(symbol, interval="1min", limit=200)
             if len(df_low) < WINDOW:
                 cnt_len += 1
                 logger.info(f"{symbol} skip length ({len(df_low)}<{WINDOW})")
                 continue
             swing_high = df_low['high'].rolling(WINDOW).max().iat[-2]
             swing_low  = df_low['low'].rolling(WINDOW).min().iat[-2]

             # 2) Fibonacci zones (OTE)
             fib_min = swing_low  + 0.618 * (swing_high - swing_low)
             fib_max = swing_low  + 0.786 * (swing_high - swing_low)
             last_price = df_low['close'].iat[-1]

+            # 2a) Filtre FIBO/OTE : price doit être dans [fib_min, fib_max]
+            if not (fib_min <= last_price <= fib_max):
+                cnt_fibo_ote += 1
+                logger.info(f"{symbol} skip Fibo/OTE: price={last_price:.4f} hors [{fib_min:.4f}-{fib_max:.4f}]")
+                continue

             # 3) Trend filter (MA50 vs MA200)
             ma50  = df_low['close'].rolling(50).mean().iat[-1]
             ma200 = df_low['close'].rolling(200).mean().iat[-1]
             if not ((ma50 > ma200 and last_price > ma200) or (ma50 < ma200 and last_price < ma200)):
                 cnt_trend += 1
                 logger.info(f"{symbol} skip trend (ma50/200): ma50={ma50:.4f}, ma200={ma200:.4f}")
                 continue

             # 4) RSI & MACD
             rsi = compute_rsi(df_low['close'], 14).iat[-1]
             macd_line, sig_line, _ = compute_macd(df_low['close'])
             macd_val = macd_line.iat[-1]
             sig_val  = sig_line.iat[-1]
             cond_long  = (rsi < 30 and macd_val > sig_val)
             cond_short = (rsi > 70 and macd_val < sig_val)
             if not (cond_long or cond_short):
                 cnt_rsmacd += 1
                 logger.info(f"{symbol} skip RSI/MACD: RSI={rsi:.1f}, MACD={macd_val:.4f}, SIG={sig_val:.4f}")
                 continue

             # 5) FVG filter
             if side == "long":
                 has_fvg = detect_fvg(df_low)
             else:
                 has_fvg = detect_fvg_short(df_low)
             if not has_fvg:
                 cnt_fvg += 1
                 logger.info(f"{symbol} skip FVG")
                 continue

             # … le reste de ton pipeline (OrderBook, multi‐TF, sizing, alertes) …

         except Exception as e:
             logger.error(f"❌ Erreur sur {symbol} : {e}")

     # ─── Récapitulatif par filtre ───
     logger.info("📊 **RÉCAPITULATIF FILTRAGE**")
     logger.info(f"• Total symbols      : {total}")
     logger.info(f"• Rejet longueur      : {cnt_len} ({cnt_len/total*100:.1f}%)")
     logger.info(f"• Rejet Fibo/OTE      : {cnt_fibo_ote} ({cnt_fibo_ote/total*100:.1f}%)")
     logger.info(f"• Rejet trend         : {cnt_trend} ({cnt_trend/total*100:.1f}%)")
     logger.info(f"• Rejet RSI/MACD      : {cnt_rsmacd} ({cnt_rsmacd/total*100:.1f}%)")
     logger.info(f"• Rejet FVG           : {cnt_fvg} ({cnt_fvg/total*100:.1f}%)")
     logger.info(f"• LONGs acceptés      : {accepted_l} ({accepted_l/total*100:.1f}%)")
     logger.info(f"• SHORTs acceptés     : {accepted_s} ({accepted_s/total*100:.1f}%)")
