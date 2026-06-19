   signal.entry_validation_result="not_checked";
   signal.reason=MAdBuildReason(signal.direction,
                                signal.final_long_score,
                                signal.final_short_score,
                                signal.daily_long_score,
                                signal.daily_short_score,
                                signal.h4_long_trend_score,
                                signal.h4_short_trend_score,
                                signal.h4_long_momentum_score,
                                signal.h4_short_momentum_score,
                                h4_indicators.rsi14,
                                h4_indicators.adx14,
                                signal.sentiment_score,
                                settings.neutral_score_gap);

   string sltp_error="";
   if(!MAdCalculateSlTp(settings,
                        signal.current_price,
                        signal.direction,
                        signal.signal_strength,
                        daily_indicators.atr14,
                        0.45,
                        0.60,
                        0.25,
                        signal,
                        sltp_error))
     {
      signal.atr_1d=daily_indicators.atr14;
      signal.atr_percent_1d=(signal.current_price>0.0
                             ? daily_indicators.atr14/signal.current_price
                             : 0.0);
      signal.stop_loss=0.0;
      signal.take_profit=0.0;
      signal.sl_tp_reason=sltp_error;
     }
   return true;
  }

MAdEntryValidation MAdValidateEntryPrice(const MAdStrategySettings &settings,
                                         const double analysis_price,
                                         const double entry_price,
                                         const double atr_percent_1d,
                                         const MAdDirection direction)
  {
   MAdEntryValidation result;
   result.valid=false;
   result.drift=0.0;
   result.maximum_drift=0.0;
   result.moved_in_signal_direction=false;
   result.reason="Invalid entry validation input";

   if(analysis_price<=0.0 || entry_price<=0.0)
     {
      result.reason="Invalid price";
      return result;
     }
   if(atr_percent_1d<=0.0)
     {
      result.reason="Invalid ATR percent";
      return result;
     }
   if(direction==MAD_DIRECTION_NEUTRAL)
     {
      result.reason="Invalid direction";
      return result;
     }

   result.drift=MathAbs(entry_price-analysis_price)/analysis_price;
   result.moved_in_signal_direction=
      (direction==MAD_DIRECTION_BUY && entry_price>analysis_price) ||
      (direction==MAD_DIRECTION_SELL && entry_price<analysis_price);

   if(result.moved_in_signal_direction)
      result.maximum_drift=MathMin(settings.drift_with_signal_cap,
                                  settings.drift_with_signal_atr_multiplier*
                                  atr_percent_1d);
   else
      result.maximum_drift=MathMin(settings.drift_against_signal_cap,
                                  settings.drift_against_signal_atr_multiplier*
                                  atr_percent_1d);

   if(result.drift>result.maximum_drift)
     {
      result.reason=StringFormat("price moved %.4f%%, limit %.4f%%",
                                 result.drift*100.0,
                                 result.maximum_drift*100.0);
      return result;
     }

   result.valid=true;
   result.reason=StringFormat("entry valid: price moved %.4f%%, limit %.4f%%",
                              result.drift*100.0,
                              result.maximum_drift*100.0);
   return result;
  }

