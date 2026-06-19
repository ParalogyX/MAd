   if(daily_short>=settings.contradiction_trend_threshold)
      final_long-=settings.contradiction_daily_penalty;
   if(h4_short>=settings.contradiction_trend_threshold)
      final_long-=settings.contradiction_h4_penalty;
   if(sentiment<=-settings.contradiction_sentiment_threshold)
      final_long-=settings.contradiction_sentiment_penalty;
   if(rsi>80.0)
      final_long-=settings.contradiction_extreme_rsi_penalty;
   if(adx<settings.contradiction_low_adx_threshold)
      final_long-=settings.contradiction_low_adx_penalty;

   if(daily_long>=settings.contradiction_trend_threshold)
      final_short-=settings.contradiction_daily_penalty;
   if(h4_long>=settings.contradiction_trend_threshold)
      final_short-=settings.contradiction_h4_penalty;
   if(sentiment>=settings.contradiction_sentiment_threshold)
      final_short-=settings.contradiction_sentiment_penalty;
   if(rsi<20.0)
      final_short-=settings.contradiction_extreme_rsi_penalty;
   if(adx<settings.contradiction_low_adx_threshold)
      final_short-=settings.contradiction_low_adx_penalty;

   final_long=MAdClipScore(final_long);
   final_short=MAdClipScore(final_short);
  }

MAdDirection MAdChooseDirection(const MAdStrategySettings &settings,
                                const double final_long,
                                const double final_short,
                                double &signal_strength)
  {
   signal_strength=MathMax(final_long,final_short);
   if(MathAbs(final_long-final_short)<settings.neutral_score_gap)
      return MAD_DIRECTION_NEUTRAL;
   if(final_long>=settings.direction_threshold && final_long>final_short)
     {
      signal_strength=final_long;
      return MAD_DIRECTION_BUY;
     }
   if(final_short>=settings.direction_threshold && final_short>final_long)
     {
      signal_strength=final_short;
      return MAD_DIRECTION_SELL;
     }
   return MAD_DIRECTION_NEUTRAL;
  }

string MAdBuildReason(const MAdDirection direction,
                      const double final_long,
                      const double final_short,
                      const double daily_long,
                      const double daily_short,
                      const double h4_long,
                      const double h4_short,
                      const double h4_momentum_long,
                      const double h4_momentum_short,
                      const double rsi,
                      const double adx,
                      const double sentiment,
                      const double neutral_gap)
  {
   string reason="";
   if(MathAbs(final_long-final_short)<neutral_gap)
      reason="Neutral: long and short scores too close";
   else if(direction==MAD_DIRECTION_BUY)
      reason="Buy: consensus favors long";
   else if(direction==MAD_DIRECTION_SELL)
      reason="Sell: consensus favors short";
   else
      reason="Neutral: no score crossed threshold";

   if(daily_long>daily_short+10.0)
      reason+=", daily uptrend";
   else if(daily_short>daily_long+10.0)
      reason+=", daily downtrend";
   else
      reason+=", daily mixed";

   if(h4_long>h4_short+10.0)
      reason+=", 4H uptrend";
   else if(h4_short>h4_long+10.0)
      reason+=", 4H downtrend";
   else
      reason+=", 4H mixed";

   if(h4_momentum_long>h4_momentum_short+10.0)
      reason+=", positive 4H momentum";
   else if(h4_momentum_short>h4_momentum_long+10.0)
      reason+=", negative 4H momentum";

   if(adx<15.0)
      reason+=", ADX too low";
   else if(adx>=25.0)
      reason+=", ADX trend confirmed";

   if(rsi>80.0)
      reason+=", RSI overextended high";
   else if(rsi<20.0)
      reason+=", RSI overextended low";
   else
      reason+=", RSI acceptable";

   if(sentiment>=20.0)
      reason+=", sentiment positive";
   else if(sentiment<=-20.0)
      reason+=", sentiment negative";
   else
      reason+=", sentiment neutral";

   if(StringLen(reason)>500)
      reason=StringSubstr(reason,0,500);
   return reason;
  }

bool MAdCalculateSlTp(const MAdStrategySettings &settings,
                      const double current_price,
                      const MAdDirection direction,
                      const double signal_strength,
                      const double atr_1d,
                      const double sl_multiplier,
                      const double tp_base_multiplier,
                      const double tp_strength_multiplier,
                      MAdSignal &signal,
                      string &error)
  {
   if(current_price<=0.0)
     {
      error="Invalid current price";
      return false;
     }
   if(direction==MAD_DIRECTION_NEUTRAL)
     {
      error="Neutral signal, SL/TP not calculated";
      return false;
     }
   if(atr_1d<=0.0)
     {
      error="Invalid ATR";
      return false;
     }

   double strength=MathMax(0.0,MathMin(100.0,signal_strength));
   double atr_percent=atr_1d/current_price;
   double usable_atr_percent=MathMin(
      MathMax(atr_percent,settings.minimum_usable_atr_percent),
      settings.maximum_usable_atr_percent);
   double usable_atr=current_price*usable_atr_percent;
   double sl_distance=sl_multiplier*usable_atr;
   double tp_multiplier=tp_base_multiplier+
