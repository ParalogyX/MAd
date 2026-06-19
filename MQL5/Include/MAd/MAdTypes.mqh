//+------------------------------------------------------------------+
//| MAdTypes.mqh                                                     |
//| Shared data structures for the native MQL5 MAd strategy.         |
//+------------------------------------------------------------------+
#ifndef __MAD_TYPES_MQH__
#define __MAD_TYPES_MQH__

enum MAdDirection
  {
   MAD_DIRECTION_SELL    = -1,
   MAD_DIRECTION_NEUTRAL = 0,
   MAD_DIRECTION_BUY     = 1
  };

enum MAdClockSource
  {
   MAD_CLOCK_EUROPE_AMSTERDAM = 0, // Automatic CET/CEST, matching Python defaults.
   MAD_CLOCK_LOCAL_COMPUTER   = 1,
   MAD_CLOCK_TRADE_SERVER     = 2,
   MAD_CLOCK_GMT_OFFSET       = 3
  };

enum MAdSymbolUniverse
  {
   MAD_SYMBOLS_ALL_SERVER   = 0,
   MAD_SYMBOLS_MARKET_WATCH = 1
  };

struct MAdSessionRule
  {
   string name;
   string code;
   bool   enabled;
   string analysis_time;
   string open_time;
   string close_time;
   string trading_days;
   double min_signal_strength;
   double sl_multiplier;
   double tp_base_multiplier;
   double tp_strength_multiplier;
  };

struct MAdStrategySettings
  {
   int    min_daily_candles;
   int    min_h4_candles;
   int    min_h1_candles;
   int    daily_lookback_days;
   int    h4_lookback_days;
   int    h1_lookback_days;

   double direction_threshold;
   double neutral_score_gap;

   double weight_daily;
   double weight_h4_trend;
   double weight_h4_momentum;
   double weight_h1_confirmation;
   double weight_rsi;
   double weight_adx;
   double weight_candle;
   double weight_sentiment;

   double contradiction_trend_threshold;
   double contradiction_daily_penalty;
   double contradiction_h4_penalty;
   double contradiction_sentiment_threshold;
   double contradiction_sentiment_penalty;
   double contradiction_extreme_rsi_penalty;
   double contradiction_low_adx_threshold;
   double contradiction_low_adx_penalty;

   double minimum_usable_atr_percent;
   double maximum_usable_atr_percent;

   double drift_with_signal_cap;
   double drift_with_signal_atr_multiplier;
   double drift_against_signal_cap;
   double drift_against_signal_atr_multiplier;
  };

struct MAdIndicatorValues
  {
   double ema20;
   double ema50;
   double ema200;
   double rsi14;
   double macd;
   double macd_signal;
   double macd_histogram;
   double macd_histogram_previous;
   double atr14;
   double adx14;
  };

struct MAdSignal
  {
   string       ticker;
   MAdDirection direction;
   // During analysis current_price and analysis_price are identical. At the
   // opening event current_price becomes the validated bid/ask entry price,
   // while analysis_price remains the original analysis snapshot.
   double       current_price;
   double       analysis_price;
   double       price_drift_percent;
   string       entry_validation_result;
   double       signal_strength;

   double daily_long_score;
   double daily_short_score;
   double h4_long_trend_score;
   double h4_short_trend_score;
   double h4_long_momentum_score;
   double h4_short_momentum_score;
   double h1_long_score;
   double h1_short_score;
   double rsi_long_score;
   double rsi_short_score;
   double adx_score;
   double candle_long_score;
   double candle_short_score;
   double sentiment_score;
   double final_long_score;
   double final_short_score;

   double atr_1d;
   double atr_percent_1d;
   double usable_atr_1d;
   double sl_distance;
   double tp_distance;
   double stop_loss;
   double take_profit;
   double risk_reward_ratio;

   string reason;
   string sl_tp_reason;
   datetime timestamp;
  };

struct MAdEntryValidation
  {
   bool   valid;
   double drift;
   double maximum_drift;
   bool   moved_in_signal_direction;
   string reason;
  };

struct MAdRiskCalculation
  {
   bool   valid;
   double target_risk_money;
   double raw_volume;
   double volume;
   double estimated_loss_at_sl;
   string reason;
  };

struct MAdOpenResult
  {
   string ticker;
   string group_name;
   string direction;
   double signal_strength;
   double analysis_price;
   double entry_price;
   double price_drift_percent;
   double stop_loss;
   double take_profit;
   double requested_risk_percent;
   double target_risk_money;
   double estimated_loss_at_sl;
   double volume;
   double actual_volume;
   double actual_price;
   ulong  order_ticket;
   ulong  deal_ticket;
   uint   retcode;
   string status;
   string reason;
   datetime timestamp;
  };

string MAdDirectionText(const MAdDirection direction)
  {
   if(direction==MAD_DIRECTION_BUY)
      return "buy";
   if(direction==MAD_DIRECTION_SELL)
      return "sell";
   return "neutral";
  }

MAdDirection MAdDirectionFromText(string value)
  {
   StringToLower(value);
   if(value=="buy" || value=="long")
      return MAD_DIRECTION_BUY;
   if(value=="sell" || value=="short")
      return MAD_DIRECTION_SELL;
   return MAD_DIRECTION_NEUTRAL;
  }

#endif // __MAD_TYPES_MQH__
