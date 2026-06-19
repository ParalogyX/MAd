
double MAdClipScore(const double value)
  {
   if(!MathIsValidNumber(value))
      return 0.0;
   return MathMax(0.0,MathMin(100.0,value));
  }

double MAdSafeReturn(const double latest,const double previous)
  {
   if(!MathIsValidNumber(previous) || previous==0.0)
      return 0.0;
   return latest/previous-1.0;
  }

bool MAdLoadRates(const string symbol,
                  const ENUM_TIMEFRAMES timeframe,
                  const int lookback_days,
                  const int minimum_candles,
                  MqlRates &rates[],
                  string &error)
  {
   ArrayResize(rates,0);
   datetime stop_time=TimeCurrent();
   datetime start_time=stop_time-(datetime)(lookback_days*86400);
   ResetLastError();
   int copied=CopyRates(symbol,timeframe,start_time,stop_time,rates);
   if(copied<0)
     {
      error=StringFormat("CopyRates failed for %s/%s, error=%d",
                         symbol,EnumToString(timeframe),GetLastError());
      return false;
     }

   ArraySetAsSeries(rates,false);
   if(copied<minimum_candles)
     {
      error=StringFormat("insufficient data for %s/%s: %d candles, %d required",
                         symbol,EnumToString(timeframe),copied,minimum_candles);
      return false;
     }
   return true;
  }

void MAdExtractClose(const MqlRates &rates[],double &close[])
  {
   int count=ArraySize(rates);
   ArrayResize(close,count);
   for(int index=0; index<count; index++)
      close[index]=rates[index].close;
  }

void MAdCalculateEmaSeries(const double &source[],const double alpha,double &ema[])
  {
   int count=ArraySize(source);
   ArrayResize(ema,count);
   if(count<=0)
      return;
   ema[0]=source[0];
   for(int index=1; index<count; index++)
      ema[index]=alpha*source[index]+(1.0-alpha)*ema[index-1];
  }

double MAdLatestEma(const double &source[],const double span)
  {
   double ema[];
   MAdCalculateEmaSeries(source,2.0/(span+1.0),ema);
   int count=ArraySize(ema);
   return (count>0 ? ema[count-1] : 0.0);
  }

double MAdCalculateRsi(const double &close[],const int period)
  {
   int count=ArraySize(close);
   if(count<=period)
      return 0.0;

   double alpha=1.0/(double)period;
   double average_gain=0.0;
   double average_loss=0.0;
   bool initialized=false;

   for(int index=1; index<count; index++)
     {
      double delta=close[index]-close[index-1];
      double gain=MathMax(delta,0.0);
      double loss=MathMax(-delta,0.0);
      if(!initialized)
        {
         average_gain=gain;
         average_loss=loss;
         initialized=true;
        }
      else
        {
         average_gain=alpha*gain+(1.0-alpha)*average_gain;
         average_loss=alpha*loss+(1.0-alpha)*average_loss;
        }
     }

   if(average_loss==0.0 && average_gain>0.0)
      return 100.0;
   if(average_loss==0.0 && average_gain==0.0)
      return 50.0;

   double relative_strength=average_gain/average_loss;
   return 100.0-(100.0/(1.0+relative_strength));
  }

void MAdCalculateMacd(const double &close[],
                      double &latest_macd,
                      double &latest_signal,
                      double &latest_histogram,
                      double &previous_fallback_histogram)
  {
   int count=ArraySize(close);
   latest_macd=0.0;
   latest_signal=0.0;
   latest_histogram=0.0;
   previous_fallback_histogram=0.0;
   if(count<=0)
      return;

   double ema12[],ema26[],macd[];
   MAdCalculateEmaSeries(close,2.0/13.0,ema12);
   MAdCalculateEmaSeries(close,2.0/27.0,ema26);
   ArrayResize(macd,count);
   for(int index=0; index<count; index++)
      macd[index]=ema12[index]-ema26[index];

   // find_signal.py's fallback previous histogram is calculated without
   // min_periods, so its signal EMA starts at MACD[0].
   double fallback_signal[];
   MAdCalculateEmaSeries(macd,2.0/10.0,fallback_signal);
   int previous_index=MathMax(0,count-2);
   previous_fallback_histogram=macd[previous_index]-fallback_signal[previous_index];

   // perform_technical_analysis() overrides the latest MACD values. Its MACD
   // signal starts at the first valid 26-period MACD observation (index 25).
   int signal_start=MathMin(25,count-1);
   double technical_signal=macd[signal_start];
   for(int index=signal_start+1; index<count; index++)
      technical_signal=(2.0/10.0)*macd[index]+(8.0/10.0)*technical_signal;

   latest_macd=macd[count-1];
   latest_signal=technical_signal;
   latest_histogram=latest_macd-latest_signal;
  }

double MAdTrueRangeAt(const MqlRates &rates[],const int index)
  {
   double range1=rates[index].high-rates[index].low;
   if(index<=0)
      return range1;
   double range2=MathAbs(rates[index].high-rates[index-1].close);
   double range3=MathAbs(rates[index].low-rates[index-1].close);
   return MathMax(range1,MathMax(range2,range3));
  }

double MAdCalculateSimpleAtr(const MqlRates &rates[],const int period)
  {
   int count=ArraySize(rates);
   if(count<period)
      return 0.0;
   double total=0.0;
   for(int index=count-period; index<count; index++)
      total+=MAdTrueRangeAt(rates,index);
   return total/(double)period;
  }

double MAdCalculateAdx(const MqlRates &rates[],const int period)
  {
   int count=ArraySize(rates);
   if(count<period)
