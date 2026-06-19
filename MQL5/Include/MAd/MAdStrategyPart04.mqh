          MathMax(second.open,second.close)<=first.close+MAdCandleBody(first)*0.25 &&
          MAdBullish(third) &&
          third.close>MAdMidpoint(first);
  }

bool MAdEveningStar(const MqlRates &first,
                    const MqlRates &second,
                    const MqlRates &third)
  {
   return MAdBullish(first) &&
          MAdLongBody(first) &&
          MAdSmallBody(second) &&
          MathMin(second.open,second.close)>=first.close-MAdCandleBody(first)*0.25 &&
          MAdBearish(third) &&
          third.close<MAdMidpoint(first);
  }

bool MAdPiercingPattern(const MqlRates &previous,const MqlRates &current)
  {
   return MAdBearish(previous) &&
          MAdBullish(current) &&
          current.open<previous.close &&
          MAdMidpoint(previous)<current.close &&
          current.close<previous.open;
  }

bool MAdDarkCloudCover(const MqlRates &previous,const MqlRates &current)
  {
   return MAdBullish(previous) &&
          MAdBearish(current) &&
          current.open>previous.close &&
          MAdMidpoint(previous)>current.close &&
          current.close>previous.open;
  }

bool MAdThreeWhiteSoldiers(const MqlRates &first,
                           const MqlRates &second,
                           const MqlRates &third)
  {
   return MAdBullish(first) && MAdLongBody(first) &&
          MAdBullish(second) && MAdLongBody(second) &&
          MAdBullish(third) && MAdLongBody(third) &&
          second.close>first.close &&
          third.close>second.close &&
          second.open>=MathMin(first.open,first.close) &&
          third.open>=MathMin(second.open,second.close);
  }

bool MAdThreeBlackCrows(const MqlRates &first,
                        const MqlRates &second,
                        const MqlRates &third)
  {
   return MAdBearish(first) && MAdLongBody(first) &&
          MAdBearish(second) && MAdLongBody(second) &&
          MAdBearish(third) && MAdLongBody(third) &&
          second.close<first.close &&
          third.close<second.close &&
          second.open<=MathMax(first.open,first.close) &&
          third.open<=MathMax(second.open,second.close);
  }

void MAdCandleScores(const MqlRates &rates[],double &long_score,double &short_score)
  {
   int count=ArraySize(rates);
   if(count<=0)
     {
      long_score=50.0;
      short_score=50.0;
      return;
     }

   bool bullish_found=false;
   bool bearish_found=false;
   int start=MathMax(0,count-3);
   for(int index=start; index<count; index++)
     {
      MqlRates current=rates[index];

      if(MAdHammerShape(current))
        {
         if(MAdLocalTrend(rates,index)==1)
            bearish_found=true; // hanging man
         else
            bullish_found=true; // hammer
        }

      if(MAdInvertedHammerShape(current))
        {
         if(MAdLocalTrend(rates,index)==1)
            bearish_found=true; // shooting star
         else
            bullish_found=true; // inverted hammer
        }

      if(index>=1)
        {
         MqlRates previous=rates[index-1];
         if(MAdBullishEngulfing(previous,current))
            bullish_found=true;
         if(MAdBearishEngulfing(previous,current))
            bearish_found=true;
         if(MAdPiercingPattern(previous,current))
            bullish_found=true;
         if(MAdDarkCloudCover(previous,current))
            bearish_found=true;
        }

      if(index>=2)
        {
         MqlRates first=rates[index-2];
         MqlRates second=rates[index-1];
         if(MAdMorningStar(first,second,current))
            bullish_found=true;
         if(MAdEveningStar(first,second,current))
            bearish_found=true;
         if(MAdThreeWhiteSoldiers(first,second,current))
            bullish_found=true;
         if(MAdThreeBlackCrows(first,second,current))
            bearish_found=true;
        }
     }

   if(bullish_found && !bearish_found)
     {
      long_score=100.0;
      short_score=0.0;
     }
   else if(bearish_found && !bullish_found)
     {
      long_score=0.0;
      short_score=100.0;
     }
   else
     {
      long_score=50.0;
      short_score=50.0;
     }
  }

// Sentiment hook. It intentionally returns neutral for this first MQL5 version.
// Replace only this function when a reliable sentiment transport is added.
double MAdGetSentimentScore(const string symbol)
  {
   return 0.0;
  }

void MAdSentimentScores(const double sentiment,double &long_score,double &short_score)
  {
   double normalized=MathMax(-100.0,MathMin(100.0,sentiment));
   long_score=(normalized+100.0)/2.0;
   short_score=(100.0-normalized)/2.0;
  }

void MAdApplyContradictionPenalties(const MAdStrategySettings &settings,
                                    const double daily_long,
                                    const double daily_short,
                                    const double h4_long,
                                    const double h4_short,
                                    const double sentiment,
                                    const double rsi,
                                    const double adx,
                                    double &final_long,
                                    double &final_short)
  {
