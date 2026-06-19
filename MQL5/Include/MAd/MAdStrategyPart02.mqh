      return 0.0;

   double plus_smoothed=0.0;
   double minus_smoothed=0.0;
   double adx=0.0;
   bool dm_initialized=false;
   bool adx_initialized=false;
   double alpha=1.0/(double)period;

   double true_ranges[];
   ArrayResize(true_ranges,count);
   for(int index=0; index<count; index++)
      true_ranges[index]=MAdTrueRangeAt(rates,index);

   for(int index=0; index<count; index++)
     {
      double plus_dm=0.0;
      double minus_dm=0.0;
      if(index>0)
        {
         double up_move=rates[index].high-rates[index-1].high;
         double down_move=rates[index-1].low-rates[index].low;
         if(up_move>down_move && up_move>0.0)
            plus_dm=up_move;
         if(down_move>up_move && down_move>0.0)
            minus_dm=down_move;
        }

      if(!dm_initialized)
        {
         plus_smoothed=plus_dm;
         minus_smoothed=minus_dm;
         dm_initialized=true;
        }
      else
        {
         plus_smoothed=alpha*plus_dm+(1.0-alpha)*plus_smoothed;
         minus_smoothed=alpha*minus_dm+(1.0-alpha)*minus_smoothed;
        }

      if(index<period-1)
         continue;

      double atr_sum=0.0;
      for(int atr_index=index-period+1; atr_index<=index; atr_index++)
         atr_sum+=true_ranges[atr_index];
      double atr=atr_sum/(double)period;
      if(atr<=0.0)
         continue;

      double plus_di=100.0*plus_smoothed/atr;
      double minus_di=100.0*minus_smoothed/atr;
      double denominator=plus_di+minus_di;
      if(denominator<=0.0)
         continue;

      double dx=100.0*MathAbs(plus_di-minus_di)/denominator;
      if(!adx_initialized)
        {
         adx=dx;
         adx_initialized=true;
        }
      else
         adx=alpha*dx+(1.0-alpha)*adx;
     }

   return (adx_initialized ? adx : 0.0);
  }

bool MAdCalculateIndicators(const MqlRates &rates[],
                            MAdIndicatorValues &values,
                            string &error)
  {
   int count=ArraySize(rates);
   if(count<2)
     {
      error="not enough rates for indicators";
      return false;
     }

   double close[];
   MAdExtractClose(rates,close);
   values.ema20=MAdLatestEma(close,20.0);
   values.ema50=MAdLatestEma(close,50.0);
   values.ema200=MAdLatestEma(close,200.0);
   values.rsi14=MAdCalculateRsi(close,14);
   MAdCalculateMacd(close,
                    values.macd,
                    values.macd_signal,
                    values.macd_histogram,
                    values.macd_histogram_previous);
   values.atr14=MAdCalculateSimpleAtr(rates,14);
   values.adx14=MAdCalculateAdx(rates,14);

   if(!MathIsValidNumber(values.ema20) ||
      !MathIsValidNumber(values.ema50) ||
      !MathIsValidNumber(values.ema200) ||
      !MathIsValidNumber(values.rsi14) ||
      !MathIsValidNumber(values.macd_histogram) ||
      !MathIsValidNumber(values.adx14) ||
      !MathIsValidNumber(values.atr14))
     {
      error="one or more calculated indicators are invalid";
      return false;
     }
   return true;
  }

void MAdDailyTrendScores(const MqlRates &rates[],
                         const MAdIndicatorValues &indicators,
                         double &long_score,
                         double &short_score)
  {
   int last=ArraySize(rates)-1;
   double close=rates[last].close;
   double return_20d=MAdSafeReturn(close,rates[last-20].close);

   long_score=0.0;
   if(close>indicators.ema200)
      long_score+=45.0;
   if(indicators.ema50>indicators.ema200)
      long_score+=35.0;
   if(return_20d>0.0)
      long_score+=MathMin(20.0,return_20d/0.10*20.0);

   short_score=0.0;
   if(close<indicators.ema200)
      short_score+=45.0;
   if(indicators.ema50<indicators.ema200)
      short_score+=35.0;
   if(return_20d<0.0)
      short_score+=MathMin(20.0,MathAbs(return_20d)/0.10*20.0);

   long_score=MAdClipScore(long_score);
   short_score=MAdClipScore(short_score);
  }

void MAdH4TrendScores(const MqlRates &rates[],
                      const MAdIndicatorValues &indicators,
                      double &long_score,
                      double &short_score)
  {
   int last=ArraySize(rates)-1;
   double close=rates[last].close;
   double spread=(close!=0.0 ? MathAbs(indicators.ema20-indicators.ema50)/close : 0.0);

   long_score=0.0;
   if(close>indicators.ema200)
      long_score+=30.0;
   if(close>indicators.ema50)
      long_score+=25.0;
   if(indicators.ema20>indicators.ema50)
      long_score+=30.0;
   long_score+=MathMin(15.0,spread/0.03*15.0);

   short_score=0.0;
   if(close<indicators.ema200)
      short_score+=30.0;
   if(close<indicators.ema50)
      short_score+=25.0;
   if(indicators.ema20<indicators.ema50)
      short_score+=30.0;
   short_score+=MathMin(15.0,spread/0.03*15.0);

   long_score=MAdClipScore(long_score);
   short_score=MAdClipScore(short_score);
  }

void MAdH4MomentumScores(const MqlRates &rates[],
                         const MAdIndicatorValues &indicators,
                         double &long_score,
                         double &short_score)
  {
   int last=ArraySize(rates)-1;
   double close=rates[last].close;
   double return_10=MAdSafeReturn(close,rates[last-10].close);
