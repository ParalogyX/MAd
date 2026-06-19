
// ============================================================================
// USER CONFIGURATION
// ============================================================================

input group "Safety and execution"
input bool              InpAllowOrderExecution       = false;
input long              InpMagicNumber               = 26061901;
input double            InpRiskPerTradePercent       = 1.0;
input int               InpMaximumDeviationPoints    = 20;
input int               InpTimerIntervalSeconds      = 10;
input int               InpMaximumAnalysisDelayMinutes= 30;
input int               InpMaximumOpenDelayMinutes   = 15;
input int               InpBestSignalLimit           = 10;
// The deterministic state CSV between analysis and opening is always written.
// This switch controls only timestamped reporting files.
input bool              InpWriteCsvFiles             = true;

input group "Clock and symbol universe"
input MAdClockSource    InpClockSource               = MAD_CLOCK_EUROPE_AMSTERDAM;
input int               InpManualUtcOffsetMinutes    = 120;
input MAdSymbolUniverse InpSymbolUniverse            = MAD_SYMBOLS_ALL_SERVER;
input bool              InpRefreshUniverseAtAnalysis = true;
input string            InpIncludeSymbols            = "";
input string            InpExcludeSymbols            = "";
input string            InpSymbolGroupOverridesFile  = "MAd\\symbol_groups.csv";

input group "Data requirements"
input int               InpMinimumDailyCandles       = 260;
input int               InpMinimumH4Candles          = 220;
input int               InpMinimumH1Candles          = 160;
input int               InpDailyLookbackDays         = 800;
input int               InpH4LookbackDays            = 240;
input int               InpH1LookbackDays            = 60;

input group "Signal decision rules"
input double            InpDirectionScoreThreshold   = 55.0;
input double            InpNeutralScoreGap           = 8.0;
input double            InpWeightDailyTrend          = 0.25;
input double            InpWeightH4Trend             = 0.25;
input double            InpWeightH4Momentum          = 0.15;
input double            InpWeightH1Confirmation      = 0.10;
input double            InpWeightRsi                 = 0.10;
input double            InpWeightAdx                 = 0.07;
input double            InpWeightCandle              = 0.04;
input double            InpWeightSentiment           = 0.04;

input group "Contradiction penalties"
input double            InpContradictionTrendLevel   = 75.0;
input double            InpDailyContradictionPenalty = 15.0;
input double            InpH4ContradictionPenalty    = 20.0;
input double            InpSentimentPenaltyLevel     = 50.0;
input double            InpSentimentPenalty          = 10.0;
input double            InpExtremeRsiPenalty         = 20.0;
input double            InpLowAdxLevel               = 15.0;
input double            InpLowAdxPenalty             = 20.0;

input group "ATR and entry-price rules"
input double            InpMinimumUsableAtrPercent   = 0.015;
input double            InpMaximumUsableAtrPercent   = 0.12;
input double            InpDriftWithSignalCap        = 0.006;
input double            InpDriftWithSignalAtrFactor  = 0.12;
input double            InpDriftAgainstSignalCap     = 0.010;
input double            InpDriftAgainstSignalAtrFactor = 0.20;

input group "Crypto 24/7"
input bool              InpCryptoEnabled             = true;
input string            InpCryptoAnalysisTime        = "15:00";
input string            InpCryptoOpenTime            = "15:10";
input string            InpCryptoCloseTime           = "21:45";
input string            InpCryptoTradingDays         = "mon-sun";
input double            InpCryptoMinSignal           = 80.0;
input double            InpCryptoSlMultiplier        = 0.40;
input double            InpCryptoTpBaseMultiplier    = 0.50;
input double            InpCryptoTpStrengthMultiplier= 0.20;

input group "Forex major"
input bool              InpForexMajorEnabled         = true;
input string            InpForexMajorAnalysisTime    = "09:00";
input string            InpForexMajorOpenTime        = "09:05";
input string            InpForexMajorCloseTime       = "21:45";
input string            InpForexMajorTradingDays     = "mon-fri";
input double            InpForexMajorMinSignal       = 70.0;
input double            InpForexMajorSlMultiplier    = 0.45;
input double            InpForexMajorTpBaseMultiplier= 0.60;
input double            InpForexMajorTpStrengthMultiplier=0.25;

input group "Forex exotic"
input bool              InpForexExoticEnabled        = true;
input string            InpForexExoticAnalysisTime   = "09:00";
input string            InpForexExoticOpenTime       = "09:15";
input string            InpForexExoticCloseTime      = "18:30";
input string            InpForexExoticTradingDays    = "mon-fri";
input double            InpForexExoticMinSignal      = 75.0;
input double            InpForexExoticSlMultiplier   = 0.45;
input double            InpForexExoticTpBaseMultiplier=0.55;
input double            InpForexExoticTpStrengthMultiplier=0.20;

