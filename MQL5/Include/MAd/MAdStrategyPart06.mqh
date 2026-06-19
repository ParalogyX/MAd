                        tp_strength_multiplier*(strength/100.0);
   double tp_distance=tp_multiplier*usable_atr;

   signal.atr_1d=atr_1d;
   signal.atr_percent_1d=atr_percent;
   signal.usable_atr_1d=usable_atr;
   signal.sl_distance=sl_distance;
   signal.tp_distance=tp_distance;
   signal.risk_reward_ratio=(sl_distance>0.0 ? tp_distance/sl_distance : 0.0);

   if(direction==MAD_DIRECTION_BUY)
     {
      signal.stop_loss=current_price-sl_distance;
      signal.take_profit=current_price+tp_distance;
     }
   else
     {
      signal.stop_loss=current_price+sl_distance;
      signal.take_profit=MathMax(current_price-tp_distance,current_price*0.0001);
     }

   string clamp_note="ATR not clamped";
   if(usable_atr_percent>atr_percent)
      clamp_note=StringFormat("ATR clamped up from %.2f%% to %.2f%%",
                              atr_percent*100.0,usable_atr_percent*100.0);
   else if(usable_atr_percent<atr_percent)
      clamp_note=StringFormat("ATR clamped down from %.2f%% to %.2f%%",
                              atr_percent*100.0,usable_atr_percent*100.0);

   signal.sl_tp_reason=StringFormat(
      "ATR-based daily SL/TP: SL=%.2f*ATR, TP=%.2f*ATR, %s",
      sl_multiplier,tp_multiplier,clamp_note);
   return true;
  }

bool MAdAnalyzeSymbol(const string symbol,
                      const MAdStrategySettings &settings,
                      MAdSignal &signal,
                      string &error)
  {
   MqlRates daily_rates[],h4_rates[],h1_rates[];
   if(!MAdLoadRates(symbol,PERIOD_D1,settings.daily_lookback_days,
                    settings.min_daily_candles,daily_rates,error))
      return false;
   if(!MAdLoadRates(symbol,PERIOD_H4,settings.h4_lookback_days,
                    settings.min_h4_candles,h4_rates,error))
      return false;
   if(!MAdLoadRates(symbol,PERIOD_H1,settings.h1_lookback_days,
                    settings.min_h1_candles,h1_rates,error))
      return false;

   MAdIndicatorValues daily_indicators,h4_indicators,h1_indicators;
   if(!MAdCalculateIndicators(daily_rates,daily_indicators,error))
      return false;
   if(!MAdCalculateIndicators(h4_rates,h4_indicators,error))
      return false;
   if(!MAdCalculateIndicators(h1_rates,h1_indicators,error))
      return false;

   signal.ticker=symbol;
   signal.timestamp=TimeGMT();

   MAdDailyTrendScores(daily_rates,daily_indicators,
                       signal.daily_long_score,signal.daily_short_score);
   MAdH4TrendScores(h4_rates,h4_indicators,
                    signal.h4_long_trend_score,signal.h4_short_trend_score);
   MAdH4MomentumScores(h4_rates,h4_indicators,
                       signal.h4_long_momentum_score,
                       signal.h4_short_momentum_score);
   MAdH1ConfirmationScores(h1_rates,h1_indicators,
                           signal.h1_long_score,signal.h1_short_score);
   MAdRsiScores(h4_indicators.rsi14,
                signal.rsi_long_score,signal.rsi_short_score);
   signal.adx_score=MAdAdxScore(h4_indicators.adx14);
   MAdCandleScores(h4_rates,signal.candle_long_score,signal.candle_short_score);

   signal.sentiment_score=MAdGetSentimentScore(symbol);
   double sentiment_long=0.0;
   double sentiment_short=0.0;
   MAdSentimentScores(signal.sentiment_score,sentiment_long,sentiment_short);

   signal.final_long_score=
      settings.weight_daily*signal.daily_long_score+
      settings.weight_h4_trend*signal.h4_long_trend_score+
      settings.weight_h4_momentum*signal.h4_long_momentum_score+
      settings.weight_h1_confirmation*signal.h1_long_score+
      settings.weight_rsi*signal.rsi_long_score+
      settings.weight_adx*signal.adx_score+
      settings.weight_candle*signal.candle_long_score+
      settings.weight_sentiment*sentiment_long;

   signal.final_short_score=
      settings.weight_daily*signal.daily_short_score+
      settings.weight_h4_trend*signal.h4_short_trend_score+
      settings.weight_h4_momentum*signal.h4_short_momentum_score+
      settings.weight_h1_confirmation*signal.h1_short_score+
      settings.weight_rsi*signal.rsi_short_score+
      settings.weight_adx*signal.adx_score+
      settings.weight_candle*signal.candle_short_score+
      settings.weight_sentiment*sentiment_short;

   MAdApplyContradictionPenalties(settings,
                                  signal.daily_long_score,
                                  signal.daily_short_score,
                                  signal.h4_long_trend_score,
                                  signal.h4_short_trend_score,
                                  signal.sentiment_score,
                                  h4_indicators.rsi14,
                                  h4_indicators.adx14,
                                  signal.final_long_score,
                                  signal.final_short_score);

   signal.direction=MAdChooseDirection(settings,
                                       signal.final_long_score,
                                       signal.final_short_score,
                                       signal.signal_strength);
   signal.current_price=h1_rates[ArraySize(h1_rates)-1].close;
   signal.analysis_price=signal.current_price;
   signal.price_drift_percent=0.0;
