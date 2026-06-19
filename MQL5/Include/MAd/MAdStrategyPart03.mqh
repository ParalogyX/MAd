   double return_20=MAdSafeReturn(close,rates[last-20].close);
   double histogram_slope=indicators.macd_histogram-
                          indicators.macd_histogram_previous;

   long_score=0.0;
   if(indicators.macd_histogram>0.0)
      long_score+=35.0;
   if(histogram_slope>0.0)
      long_score+=20.0;
   if(return_10>0.0)
      long_score+=MathMin(20.0,return_10/0.04*20.0);
   if(return_20>0.0)
      long_score+=MathMin(25.0,return_20/0.08*25.0);

   short_score=0.0;
   if(indicators.macd_histogram<0.0)
      short_score+=35.0;
   if(histogram_slope<0.0)
      short_score+=20.0;
   if(return_10<0.0)
      short_score+=MathMin(20.0,MathAbs(return_10)/0.04*20.0);
   if(return_20<0.0)
      short_score+=MathMin(25.0,MathAbs(return_20)/0.08*25.0);

   long_score=MAdClipScore(long_score);
   short_score=MAdClipScore(short_score);
  }

void MAdH1ConfirmationScores(const MqlRates &rates[],
                             const MAdIndicatorValues &indicators,
                             double &long_score,
                             double &short_score)
  {
   int last=ArraySize(rates)-1;
   double close=rates[last].close;

   long_score=0.0;
   if(close>indicators.ema20)
      long_score+=30.0;
   if(indicators.ema20>indicators.ema50)
      long_score+=40.0;
   if(indicators.macd_histogram>0.0)
      long_score+=30.0;

   short_score=0.0;
   if(close<indicators.ema20)
      short_score+=30.0;
   if(indicators.ema20<indicators.ema50)
      short_score+=40.0;
   if(indicators.macd_histogram<0.0)
      short_score+=30.0;

   long_score=MAdClipScore(long_score);
   short_score=MAdClipScore(short_score);
  }

void MAdRsiScores(const double rsi,double &long_score,double &short_score)
  {
   if(rsi>=45.0 && rsi<=65.0)
      long_score=100.0;
   else if(rsi>=35.0 && rsi<45.0)
      long_score=70.0;
   else if(rsi>65.0 && rsi<=72.0)
      long_score=60.0;
   else if(rsi>72.0 && rsi<=80.0)
      long_score=25.0;
   else
      long_score=0.0;

   if(rsi>=35.0 && rsi<=55.0)
      short_score=100.0;
   else if(rsi>55.0 && rsi<=65.0)
      short_score=70.0;
   else if(rsi>=28.0 && rsi<35.0)
      short_score=60.0;
   else if(rsi>=20.0 && rsi<28.0)
      short_score=25.0;
   else
      short_score=0.0;
  }

double MAdAdxScore(const double adx)
  {
   if(adx<15.0)
      return 0.0;
   if(adx<20.0)
      return 30.0;
   if(adx<25.0)
      return 60.0;
   if(adx<35.0)
      return 100.0;
   if(adx<50.0)
      return 85.0;
   return 70.0;
  }

// ---- Deterministic candlestick rules copied from candles.py -------------

double MAdCandleBody(const MqlRates &bar)
  {
   return MathAbs(bar.close-bar.open);
  }

double MAdCandleRange(const MqlRates &bar)
  {
   return MathMax(bar.high-bar.low,0.0);
  }

double MAdUpperShadow(const MqlRates &bar)
  {
   return bar.high-MathMax(bar.open,bar.close);
  }

double MAdLowerShadow(const MqlRates &bar)
  {
   return MathMin(bar.open,bar.close)-bar.low;
  }

bool MAdBullish(const MqlRates &bar)
  {
   return bar.close>bar.open;
  }

bool MAdBearish(const MqlRates &bar)
  {
   return bar.close<bar.open;
  }

bool MAdSmallBody(const MqlRates &bar)
  {
   double range=MAdCandleRange(bar);
   return range>0.0 && MAdCandleBody(bar)<=range*0.35;
  }

bool MAdLongBody(const MqlRates &bar)
  {
   double range=MAdCandleRange(bar);
   return range>0.0 && MAdCandleBody(bar)>=range*0.5;
  }

bool MAdHammerShape(const MqlRates &bar)
  {
   double body=MAdCandleBody(bar);
   return MAdCandleRange(bar)>0.0 &&
          body>0.0 &&
          MAdLowerShadow(bar)>=body*2.0 &&
          MAdUpperShadow(bar)<=body &&
          body<=MAdCandleRange(bar)*0.4;
  }

bool MAdInvertedHammerShape(const MqlRates &bar)
  {
   double body=MAdCandleBody(bar);
   return MAdCandleRange(bar)>0.0 &&
          body>0.0 &&
          MAdUpperShadow(bar)>=body*2.0 &&
          MAdLowerShadow(bar)<=body &&
          body<=MAdCandleRange(bar)*0.4;
  }

int MAdLocalTrend(const MqlRates &rates[],const int index,const int lookback=3)
  {
   if(index<lookback)
      return 0;
   double previous_close=rates[index-1].close;
   double earlier_close=rates[index-lookback].close;
   if(previous_close>earlier_close)
      return 1;
   if(previous_close<earlier_close)
      return -1;
   return 0;
  }

bool MAdBullishEngulfing(const MqlRates &previous,const MqlRates &current)
  {
   return MAdBearish(previous) &&
          MAdBullish(current) &&
          current.open<=previous.close &&
          current.close>=previous.open &&
          MAdCandleBody(current)>MAdCandleBody(previous);
  }

bool MAdBearishEngulfing(const MqlRates &previous,const MqlRates &current)
  {
   return MAdBullish(previous) &&
          MAdBearish(current) &&
          current.open>=previous.close &&
          current.close<=previous.open &&
          MAdCandleBody(current)>MAdCandleBody(previous);
  }

double MAdMidpoint(const MqlRates &bar)
  {
   return (bar.open+bar.close)/2.0;
  }

bool MAdMorningStar(const MqlRates &first,
                    const MqlRates &second,
                    const MqlRates &third)
  {
   return MAdBearish(first) &&
          MAdLongBody(first) &&
          MAdSmallBody(second) &&
