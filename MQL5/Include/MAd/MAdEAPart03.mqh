   g_strategy.weight_h1_confirmation=InpWeightH1Confirmation;
   g_strategy.weight_rsi=InpWeightRsi;
   g_strategy.weight_adx=InpWeightAdx;
   g_strategy.weight_candle=InpWeightCandle;
   g_strategy.weight_sentiment=InpWeightSentiment;

   g_strategy.contradiction_trend_threshold=InpContradictionTrendLevel;
   g_strategy.contradiction_daily_penalty=InpDailyContradictionPenalty;
   g_strategy.contradiction_h4_penalty=InpH4ContradictionPenalty;
   g_strategy.contradiction_sentiment_threshold=InpSentimentPenaltyLevel;
   g_strategy.contradiction_sentiment_penalty=InpSentimentPenalty;
   g_strategy.contradiction_extreme_rsi_penalty=InpExtremeRsiPenalty;
   g_strategy.contradiction_low_adx_threshold=InpLowAdxLevel;
   g_strategy.contradiction_low_adx_penalty=InpLowAdxPenalty;

   g_strategy.minimum_usable_atr_percent=InpMinimumUsableAtrPercent;
   g_strategy.maximum_usable_atr_percent=InpMaximumUsableAtrPercent;
   g_strategy.drift_with_signal_cap=InpDriftWithSignalCap;
   g_strategy.drift_with_signal_atr_multiplier=InpDriftWithSignalAtrFactor;
   g_strategy.drift_against_signal_cap=InpDriftAgainstSignalCap;
   g_strategy.drift_against_signal_atr_multiplier=InpDriftAgainstSignalAtrFactor;

   ArrayResize(g_groups,8);
   MAdFillGroup(g_groups[0],"crypto_24_7","CRYPTO",
                InpCryptoEnabled,InpCryptoAnalysisTime,InpCryptoOpenTime,
                InpCryptoCloseTime,InpCryptoTradingDays,InpCryptoMinSignal,
                InpCryptoSlMultiplier,InpCryptoTpBaseMultiplier,
                InpCryptoTpStrengthMultiplier);
   MAdFillGroup(g_groups[1],"forex_major","FXMAJ",
                InpForexMajorEnabled,InpForexMajorAnalysisTime,
                InpForexMajorOpenTime,InpForexMajorCloseTime,
                InpForexMajorTradingDays,InpForexMajorMinSignal,
                InpForexMajorSlMultiplier,InpForexMajorTpBaseMultiplier,
                InpForexMajorTpStrengthMultiplier);
   MAdFillGroup(g_groups[2],"forex_exotic","FXEX",
                InpForexExoticEnabled,InpForexExoticAnalysisTime,
                InpForexExoticOpenTime,InpForexExoticCloseTime,
                InpForexExoticTradingDays,InpForexExoticMinSignal,
                InpForexExoticSlMultiplier,InpForexExoticTpBaseMultiplier,
                InpForexExoticTpStrengthMultiplier);
   MAdFillGroup(g_groups[3],"europe_stock_index","EU",
                InpEuropeEnabled,InpEuropeAnalysisTime,InpEuropeOpenTime,
                InpEuropeCloseTime,InpEuropeTradingDays,InpEuropeMinSignal,
                InpEuropeSlMultiplier,InpEuropeTpBaseMultiplier,
                InpEuropeTpStrengthMultiplier);
   MAdFillGroup(g_groups[4],"us_stock_index","US",
                InpUsEnabled,InpUsAnalysisTime,InpUsOpenTime,
                InpUsCloseTime,InpUsTradingDays,InpUsMinSignal,
                InpUsSlMultiplier,InpUsTpBaseMultiplier,
                InpUsTpStrengthMultiplier);
   MAdFillGroup(g_groups[5],"commodity_us","CMD",
                InpCommodityEnabled,InpCommodityAnalysisTime,
                InpCommodityOpenTime,InpCommodityCloseTime,
                InpCommodityTradingDays,InpCommodityMinSignal,
                InpCommoditySlMultiplier,InpCommodityTpBaseMultiplier,
                InpCommodityTpStrengthMultiplier);
   MAdFillGroup(g_groups[6],"asia_index","ASIA",
                InpAsiaEnabled,InpAsiaAnalysisTime,InpAsiaOpenTime,
                InpAsiaCloseTime,InpAsiaTradingDays,InpAsiaMinSignal,
                InpAsiaSlMultiplier,InpAsiaTpBaseMultiplier,
                InpAsiaTpStrengthMultiplier);
   MAdFillGroup(g_groups[7],"israel_index","ISR",
                InpIsraelEnabled,InpIsraelAnalysisTime,InpIsraelOpenTime,
                InpIsraelCloseTime,InpIsraelTradingDays,InpIsraelMinSignal,
                InpIsraelSlMultiplier,InpIsraelTpBaseMultiplier,
                InpIsraelTpStrengthMultiplier);
  }

bool MAdValidateConfiguration()
  {
   if(InpRiskPerTradePercent<=0.0 || InpRiskPerTradePercent>100.0)
     {
      Print("Invalid InpRiskPerTradePercent.");
      return false;
     }
   if(InpBestSignalLimit<=0)
     {
      Print("InpBestSignalLimit must be positive.");
      return false;
     }
   if(InpMaximumAnalysisDelayMinutes<0 || InpMaximumOpenDelayMinutes<0)
     {
      Print("Event catch-up delays cannot be negative.");
      return false;
     }
   if(InpMinimumDailyCandles<21 || InpMinimumH4Candles<21 ||
      InpMinimumH1Candles<2)
     {
      Print("Minimum candle counts are too small for the strategy lookbacks.");
      return false;
     }
   if(InpMinimumUsableAtrPercent<=0.0 ||
      InpMaximumUsableAtrPercent<InpMinimumUsableAtrPercent)
     {
      Print("Invalid usable ATR percentage limits.");
      return false;
     }

   double weight_sum=InpWeightDailyTrend+InpWeightH4Trend+
                     InpWeightH4Momentum+InpWeightH1Confirmation+
                     InpWeightRsi+InpWeightAdx+InpWeightCandle+
                     InpWeightSentiment;
   if(MathAbs(weight_sum-1.0)>0.000001)
      PrintFormat("WARNING: signal weights sum to %.6f instead of 1.0.",weight_sum);

   for(int index=0; index<ArraySize(g_groups); index++)
     {
      if(!MAdValidTimeText(g_groups[index].analysis_time) ||
