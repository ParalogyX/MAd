input group "European stocks and indices"
input bool              InpEuropeEnabled             = true;
input string            InpEuropeAnalysisTime        = "09:00";
input string            InpEuropeOpenTime            = "09:10";
input string            InpEuropeCloseTime           = "17:20";
input string            InpEuropeTradingDays         = "mon-fri";
input double            InpEuropeMinSignal           = 80.0;
input double            InpEuropeSlMultiplier        = 0.40;
input double            InpEuropeTpBaseMultiplier    = 0.50;
input double            InpEuropeTpStrengthMultiplier= 0.20;

input group "US stocks and indices"
input bool              InpUsEnabled                 = true;
input string            InpUsAnalysisTime            = "15:00";
input string            InpUsOpenTime                = "15:45";
input string            InpUsCloseTime               = "21:45";
input string            InpUsTradingDays             = "mon-fri";
input double            InpUsMinSignal               = 85.0;
input double            InpUsSlMultiplier            = 0.40;
input double            InpUsTpBaseMultiplier        = 0.50;
input double            InpUsTpStrengthMultiplier    = 0.20;

input group "US commodities"
input bool              InpCommodityEnabled          = true;
input string            InpCommodityAnalysisTime     = "15:00";
input string            InpCommodityOpenTime         = "15:45";
input string            InpCommodityCloseTime        = "21:45";
input string            InpCommodityTradingDays      = "mon-fri";
input double            InpCommodityMinSignal        = 70.0;
input double            InpCommoditySlMultiplier     = 0.40;
input double            InpCommodityTpBaseMultiplier = 0.50;
input double            InpCommodityTpStrengthMultiplier=0.20;

input group "Asian indices"
input bool              InpAsiaEnabled               = false;
input string            InpAsiaAnalysisTime          = "01:30";
input string            InpAsiaOpenTime              = "02:15";
input string            InpAsiaCloseTime             = "08:30";
input string            InpAsiaTradingDays           = "mon-fri";
input double            InpAsiaMinSignal             = 60.0;
input double            InpAsiaSlMultiplier          = 0.40;
input double            InpAsiaTpBaseMultiplier      = 0.50;
input double            InpAsiaTpStrengthMultiplier  = 0.20;

input group "Israel indices"
input bool              InpIsraelEnabled             = false;
input string            InpIsraelAnalysisTime        = "08:30";
input string            InpIsraelOpenTime            = "09:00";
input string            InpIsraelCloseTime           = "16:00";
input string            InpIsraelTradingDays         = "sun-thu";
input double            InpIsraelMinSignal           = 60.0;
input double            InpIsraelSlMultiplier        = 0.40;
input double            InpIsraelTpBaseMultiplier    = 0.50;
input double            InpIsraelTpStrengthMultiplier= 0.20;

// ============================================================================
// RUNTIME STATE
// ============================================================================

CTrade               g_trade;
MAdStrategySettings  g_strategy;
MAdSessionRule       g_groups[];
string               g_symbols[];
string               g_symbol_groups[];
string               g_override_symbols[];
string               g_override_groups[];
bool                 g_timer_busy=false;

// ============================================================================
// INITIALIZATION
// ============================================================================

void MAdFillGroup(MAdSessionRule &rule,
                  const string name,
                  const string code,
                  const bool enabled,
                  const string analysis_time,
                  const string open_time,
                  const string close_time,
                  const string trading_days,
                  const double minimum_signal,
                  const double sl_multiplier,
                  const double tp_base_multiplier,
                  const double tp_strength_multiplier)
  {
   rule.name=name;
   rule.code=code;
   rule.enabled=enabled;
   rule.analysis_time=analysis_time;
   rule.open_time=open_time;
   rule.close_time=close_time;
   rule.trading_days=trading_days;
   rule.min_signal_strength=minimum_signal;
   rule.sl_multiplier=sl_multiplier;
   rule.tp_base_multiplier=tp_base_multiplier;
   rule.tp_strength_multiplier=tp_strength_multiplier;
  }

void MAdBuildConfiguration()
  {
   g_strategy.min_daily_candles=InpMinimumDailyCandles;
   g_strategy.min_h4_candles=InpMinimumH4Candles;
   g_strategy.min_h1_candles=InpMinimumH1Candles;
   g_strategy.daily_lookback_days=InpDailyLookbackDays;
   g_strategy.h4_lookback_days=InpH4LookbackDays;
   g_strategy.h1_lookback_days=InpH1LookbackDays;

   g_strategy.direction_threshold=InpDirectionScoreThreshold;
   g_strategy.neutral_score_gap=InpNeutralScoreGap;

   g_strategy.weight_daily=InpWeightDailyTrend;
   g_strategy.weight_h4_trend=InpWeightH4Trend;
   g_strategy.weight_h4_momentum=InpWeightH4Momentum;
