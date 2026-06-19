     {
      reason="manual symbol_groups.csv override";
      return override_group;
     }
   return MAdClassifySymbol(symbol,reason);
  }

void MAdRefreshSymbolUniverse()
  {
   ArrayResize(g_symbols,0);
   ArrayResize(g_symbol_groups,0);

   bool selected_only=(InpSymbolUniverse==MAD_SYMBOLS_MARKET_WATCH);
   int total=SymbolsTotal(selected_only);
   for(int index=0; index<total; index++)
     {
      string symbol=SymbolName(index,selected_only);
      if(symbol=="")
         continue;
      if(InpIncludeSymbols!="" && !MAdListContainsSymbol(InpIncludeSymbols,symbol))
         continue;
      if(MAdListContainsSymbol(InpExcludeSymbols,symbol))
         continue;

      if(!selected_only && !SymbolSelect(symbol,true))
         continue;
      if(!MAdIsTradableSymbol(symbol))
         continue;

      string reason="";
      string group=MAdGroupForSymbol(symbol,reason);
      if(group=="unknown" || group=="")
         continue;

      int size=ArraySize(g_symbols);
      ArrayResize(g_symbols,size+1);
      ArrayResize(g_symbol_groups,size+1);
      g_symbols[size]=symbol;
      g_symbol_groups[size]=group;
     }

   PrintFormat("Refreshed MAd universe: %d classified tradable symbols.",
               ArraySize(g_symbols));
  }

void MAdSymbolsForGroup(const string group_name,string &symbols[])
  {
   ArrayResize(symbols,0);
   for(int index=0; index<ArraySize(g_symbols); index++)
      if(g_symbol_groups[index]==group_name)
        {
         int size=ArraySize(symbols);
         ArrayResize(symbols,size+1);
         symbols[size]=g_symbols[index];
        }
  }

// ============================================================================
// SCHEDULED ACTIONS
// ============================================================================

void MAdHandleScheduledAnalysis(const MAdSessionRule &group,
                                const datetime schedule_now)
  {
   string event_key=MAdEventKey(group,schedule_now,"A");
   if(MAdEventDone(event_key))
      return;

   if(InpRefreshUniverseAtAnalysis)
      MAdRefreshSymbolUniverse();

   if(MAdRunAnalysis(group,schedule_now))
      MAdMarkEventDone(event_key);
  }

void MAdHandleScheduledOpen(const MAdSessionRule &group,
                            const datetime schedule_now)
  {
   string event_key=MAdEventKey(group,schedule_now,"O");
   if(MAdEventDone(event_key))
      return;

   if(MAdRunOpen(group,schedule_now))
      MAdMarkEventDone(event_key);
  }

void MAdHandleScheduledClose(const MAdSessionRule &group,
                             const datetime schedule_now)
  {
   string event_key=MAdEventKey(group,schedule_now,"C");
   if(MAdEventDone(event_key))
      return;

   MAdCloseGroupPositions(group,schedule_now);
   MAdMarkEventDone(event_key);
  }

// ============================================================================
// ANALYSIS AND CANDIDATE RANKING
// ============================================================================

void MAdAppendCandidate(MAdSignal &candidates[],const MAdSignal &signal)
  {
   int size=ArraySize(candidates);
   ArrayResize(candidates,size+1);
   candidates[size]=signal;
  }

void MAdSortCandidates(MAdSignal &candidates[])
  {
   // Stable insertion sort: equal scores preserve source-symbol order, matching
   // Python's stable mergesort.
   for(int index=1; index<ArraySize(candidates); index++)
     {
      MAdSignal value=candidates[index];
      int position=index-1;
      while(position>=0 &&
            candidates[position].signal_strength<value.signal_strength)
        {
         candidates[position+1]=candidates[position];
         position--;
        }
      candidates[position+1]=value;
     }
  }

bool MAdRunAnalysis(const MAdSessionRule &group,const datetime schedule_now)
  {
   string symbols[];
   MAdSymbolsForGroup(group.name,symbols);
   PrintFormat("START analysis group=%s symbols=%d",
               group.name,ArraySize(symbols));

   MAdSignal candidates[];
   for(int index=0; index<ArraySize(symbols); index++)
     {
      string symbol=symbols[index];
      PrintFormat("[%d/%d] analysing %s",index+1,ArraySize(symbols),symbol);

      MAdSignal signal;
      string error="";
      if(!MAdAnalyzeSymbol(symbol,g_strategy,signal,error))
        {
         PrintFormat("Analysis skipped for %s: %s",symbol,error);
         continue;
        }
      if(signal.direction==MAD_DIRECTION_BUY ||
         signal.direction==MAD_DIRECTION_SELL)
         MAdAppendCandidate(candidates,signal);
     }

   MAdSortCandidates(candidates);
   if(ArraySize(candidates)>InpBestSignalLimit)
      ArrayResize(candidates,InpBestSignalLimit);

   // The state file is mandatory: it is the restart-safe hand-off between
   // the analysis and opening events. Timestamped CSVs are optional reports.
   string state_path=StringFormat(
      "MAd\\state\\candidates_%s_%s.csv",
      group.name,MAdDateFile(schedule_now));
   if(!MAdWriteCandidateCsv(state_path,candidates,group.name))
      return false;

   if(InpWriteCsvFiles)
     {
      string timestamp_path=StringFormat(
         "MAd\\best_signals\\best_signals_%s_%s.csv",
         group.name,MAdDateTimeFile(schedule_now));
