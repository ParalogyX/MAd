bool MAdEventDue(const datetime value,const string hhmm,const int maximum_delay)
  {
   int delay=MAdMinuteOfDay(value)-MAdMinuteOfDay(hhmm);
   return delay>=0 && delay<=MathMax(0,maximum_delay);
  }

int MAdDayIndex(const string token)
  {
   string value=MAdLower(StringSubstr(token,0,3));
   if(value=="sun") return 0;
   if(value=="mon") return 1;
   if(value=="tue") return 2;
   if(value=="wed") return 3;
   if(value=="thu") return 4;
   if(value=="fri") return 5;
   if(value=="sat") return 6;
   return -1;
  }

int MAdTradingDayMask(string rules)
  {
   rules=MAdLower(rules);
   StringReplace(rules," ","");
   string parts[];
   int count=StringSplit(rules,',',parts);
   int mask=0;
   for(int index=0; index<count; index++)
     {
      int dash=StringFind(parts[index],"-");
      if(dash<0)
        {
         int day=MAdDayIndex(parts[index]);
         if(day>=0)
            mask|=(1<<day);
         continue;
        }

      int start=MAdDayIndex(StringSubstr(parts[index],0,dash));
      int finish=MAdDayIndex(StringSubstr(parts[index],dash+1));
      if(start<0 || finish<0)
         continue;
      int day=start;
      while(true)
        {
         mask|=(1<<day);
         if(day==finish)
            break;
         day=(day+1)%7;
        }
     }
   return mask;
  }

bool MAdIsTradingDay(const datetime value,const string rules)
  {
   MqlDateTime parts;
   TimeToStruct(value,parts);
   int mask=MAdTradingDayMask(rules);
   return (mask & (1<<parts.day_of_week))!=0;
  }

string MAdDateCompact(const datetime value)
  {
   MqlDateTime parts;
   TimeToStruct(value,parts);
   return StringFormat("%04d%02d%02d",parts.year,parts.mon,parts.day);
  }

string MAdDateFile(const datetime value)
  {
   MqlDateTime parts;
   TimeToStruct(value,parts);
   return StringFormat("%04d-%02d-%02d",parts.year,parts.mon,parts.day);
  }

string MAdDateTimeFile(const datetime value)
  {
   MqlDateTime parts;
   TimeToStruct(value,parts);
   return StringFormat("%04d-%02d-%02d_%02d-%02d",
                       parts.year,parts.mon,parts.day,parts.hour,parts.min);
  }

string MAdDateTimeText(const datetime value)
  {
   MqlDateTime parts;
   TimeToStruct(value,parts);
   return StringFormat("%04d-%02d-%02d %02d:%02d:%02d",
                       parts.year,parts.mon,parts.day,
                       parts.hour,parts.min,parts.sec);
  }

string MAdUtcIsoText(const datetime value)
  {
   MqlDateTime parts;
   TimeToStruct(value,parts);
   return StringFormat("%04d-%02d-%02dT%02d:%02d:%02d+00:00",
                       parts.year,parts.mon,parts.day,
                       parts.hour,parts.min,parts.sec);
  }

string MAdEventKey(const MAdSessionRule &group,
                   const datetime schedule_now,
                   const string event_code)
  {
   return StringFormat("MAd.%I64d.%s.%s.%s",
                       InpMagicNumber,
                       MAdDateCompact(schedule_now),
                       group.code,
                       event_code);
  }

bool MAdEventDone(const string key)
  {
   return GlobalVariableCheck(key);
  }

void MAdMarkEventDone(const string key)
  {
   GlobalVariableSet(key,(double)TimeCurrent());
  }

// ============================================================================
// SYMBOL UNIVERSE AND OVERRIDES
// ============================================================================

bool MAdListContainsSymbol(const string list,const string symbol)
  {
   if(list=="")
      return false;
   string requested=MAdSymbolKey(symbol);
   string values[];
   int count=StringSplit(list,';',values);
   for(int index=0; index<count; index++)
      if(MAdSymbolKey(values[index])==requested)
         return true;
   return false;
  }

void MAdLoadSymbolGroupOverrides()
  {
   ArrayResize(g_override_symbols,0);
   ArrayResize(g_override_groups,0);

   int handle=FileOpen(InpSymbolGroupOverridesFile,
                       FILE_READ|FILE_CSV|FILE_ANSI,',');
   if(handle==INVALID_HANDLE)
     {
      PrintFormat("No optional symbol override file found at %s.",
                  InpSymbolGroupOverridesFile);
      return;
     }

   bool first_row=true;
   while(!FileIsEnding(handle))
     {
      string symbol=FileReadString(handle);
      string group=FileReadString(handle);
      if(symbol=="" && group=="")
         continue;
      if(first_row && MAdLower(symbol)=="symbol")
        {
         first_row=false;
         continue;
        }
      first_row=false;
      int size=ArraySize(g_override_symbols);
      ArrayResize(g_override_symbols,size+1);
      ArrayResize(g_override_groups,size+1);
      g_override_symbols[size]=MAdSymbolKey(symbol);
      g_override_groups[size]=group;
     }
   FileClose(handle);
   PrintFormat("Loaded %d symbol group overrides.",ArraySize(g_override_symbols));
  }

string MAdOverrideGroup(const string symbol)
  {
   string key=MAdSymbolKey(symbol);
   for(int index=0; index<ArraySize(g_override_symbols); index++)
      if(g_override_symbols[index]==key)
         return g_override_groups[index];
   return "";
  }

string MAdGroupForSymbol(const string symbol,string &reason)
  {
   string override_group=MAdOverrideGroup(symbol);
   if(override_group!="")
