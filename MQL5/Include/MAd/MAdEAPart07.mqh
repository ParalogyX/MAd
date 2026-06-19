      if(!MAdWriteCandidateCsv(timestamp_path,candidates,group.name))
         return false;
     }

   PrintFormat("DONE analysis group=%s candidates=%d",
               group.name,ArraySize(candidates));
   return true;
  }

// ============================================================================
// CSV PERSISTENCE
// ============================================================================

void MAdEnsureFolders()
  {
   FolderCreate("MAd");
   FolderCreate("MAd\\best_signals");
   FolderCreate("MAd\\trade_plans");
   FolderCreate("MAd\\results");
   FolderCreate("MAd\\state");
  }

string MAdCleanCsvText(string value)
  {
   StringReplace(value,"\r"," ");
   StringReplace(value,"\n"," ");
   // MQL5's CSV writer does not provide Python-style quoting control. Keep
   // fixed-width records deterministic by removing the active delimiter.
   StringReplace(value,",",";");
   return value;
  }

bool MAdWriteCandidateCsv(const string path,
                          const MAdSignal &candidates[],
                          const string group_name)
  {
   int handle=FileOpen(path,FILE_WRITE|FILE_CSV|FILE_ANSI,',');
   if(handle==INVALID_HANDLE)
     {
      PrintFormat("Could not write %s, error=%d",path,GetLastError());
      return false;
     }

   FileWrite(handle,
      "ticker","current_price","direction","signal_strength",
      "daily_trend_score","h4_trend_score","h4_momentum_score",
      "h1_confirmation_score","rsi_score","adx_score","candle_score",
      "sentiment_score","final_long_score","final_short_score","reason",
      "timestamp_utc","atr_1d","atr_percent_1d","usable_atr_1d",
      "sl_distance","tp_distance","stop_loss","take_profit",
      "risk_reward_ratio","sl_tp_reason","analysis_price",
      "session_group","description");

   for(int index=0; index<ArraySize(candidates); index++)
     {
      MAdSignal signal=candidates[index];
      string description=SymbolInfoString(signal.ticker,SYMBOL_DESCRIPTION);
      FileWrite(handle,
         signal.ticker,
         signal.current_price,
         MAdDirectionText(signal.direction),
         NormalizeDouble(signal.signal_strength,2),
         NormalizeDouble(MathMax(signal.daily_long_score,
                                  signal.daily_short_score),2),
         NormalizeDouble(MathMax(signal.h4_long_trend_score,
                                  signal.h4_short_trend_score),2),
         NormalizeDouble(MathMax(signal.h4_long_momentum_score,
                                  signal.h4_short_momentum_score),2),
         NormalizeDouble(MathMax(signal.h1_long_score,
                                  signal.h1_short_score),2),
         NormalizeDouble(MathMax(signal.rsi_long_score,
                                  signal.rsi_short_score),2),
         NormalizeDouble(signal.adx_score,2),
         NormalizeDouble(MathMax(signal.candle_long_score,
                                  signal.candle_short_score),2),
         NormalizeDouble(signal.sentiment_score,2),
         NormalizeDouble(signal.final_long_score,2),
         NormalizeDouble(signal.final_short_score,2),
         MAdCleanCsvText(signal.reason),
         MAdUtcIsoText(signal.timestamp),
         signal.atr_1d,
         signal.atr_percent_1d,
         signal.usable_atr_1d,
         signal.sl_distance,
         signal.tp_distance,
         signal.stop_loss,
         signal.take_profit,
         signal.risk_reward_ratio,
         MAdCleanCsvText(signal.sl_tp_reason),
         (signal.analysis_price>0.0 ? signal.analysis_price
                                     : signal.current_price),
         group_name,
         MAdCleanCsvText(description));
     }

   FileClose(handle);
   return true;
  }

bool MAdReadCandidateCsv(const string path,MAdSignal &candidates[])
  {
   ArrayResize(candidates,0);
   int handle=FileOpen(path,FILE_READ|FILE_CSV|FILE_ANSI,',');
   if(handle==INVALID_HANDLE)
     {
      PrintFormat("Candidate file is missing: %s",path);
      return false;
     }

   // Skip the fixed 28-column header.
   for(int column=0; column<28 && !FileIsEnding(handle); column++)
      FileReadString(handle);

   while(!FileIsEnding(handle))
     {
      MAdSignal signal;
      string ticker=FileReadString(handle);
      if(ticker=="")
        {
         if(FileIsEnding(handle))
            break;
         continue;
        }

      signal.ticker=ticker;
      signal.current_price=StringToDouble(FileReadString(handle));
      signal.direction=MAdDirectionFromText(FileReadString(handle));
      signal.signal_strength=StringToDouble(FileReadString(handle));

      // Display-only score columns.
      FileReadString(handle); // daily_trend_score
      FileReadString(handle); // h4_trend_score
      FileReadString(handle); // h4_momentum_score
      FileReadString(handle); // h1_confirmation_score
      FileReadString(handle); // rsi_score
      FileReadString(handle); // adx_score
      FileReadString(handle); // candle_score
      signal.sentiment_score=StringToDouble(FileReadString(handle));
      signal.final_long_score=StringToDouble(FileReadString(handle));
      signal.final_short_score=StringToDouble(FileReadString(handle));
