// DumpSymbolSessions.mq5
//
// Run this script inside MetaTrader 5 to export exact symbol trading sessions.
// It writes mt5_symbol_sessions.csv to the terminal's MQL5/Files directory.
// Copy that CSV into the Python output directory so trade_signal_generator.py
// can use it during the update step.

#property script_show_inputs

string DayName(const ENUM_DAY_OF_WEEK day)
{
   switch(day)
   {
      case SUNDAY:    return "SUNDAY";
      case MONDAY:    return "MONDAY";
      case TUESDAY:   return "TUESDAY";
      case WEDNESDAY: return "WEDNESDAY";
      case THURSDAY:  return "THURSDAY";
      case FRIDAY:    return "FRIDAY";
      case SATURDAY:  return "SATURDAY";
      default:        return "UNKNOWN";
   }
}

int SecondsFromMidnight(const datetime value)
{
   MqlDateTime parts;
   TimeToStruct(value, parts);
   return parts.hour * 3600 + parts.min * 60 + parts.sec;
}

void OnStart()
{
   int handle = FileOpen(
      "mt5_symbol_sessions.csv",
      FILE_WRITE | FILE_CSV | FILE_ANSI,
      ','
   );
   if(handle == INVALID_HANDLE)
   {
      Print("Could not open mt5_symbol_sessions.csv. Error: ", GetLastError());
      return;
   }

   FileWrite(
      handle,
      "symbol",
      "day_of_week",
      "session_index",
      "from_seconds",
      "to_seconds",
      "from_time",
      "to_time"
   );

   int symbol_count = SymbolsTotal(false);
   for(int symbol_index = 0; symbol_index < symbol_count; symbol_index++)
   {
      string symbol = SymbolName(symbol_index, false);
      if(symbol == "")
         continue;

      SymbolSelect(symbol, true);
      if(!SymbolInfoInteger(symbol, SYMBOL_VISIBLE))
         continue;

      for(int day_index = 0; day_index < 7; day_index++)
      {
         ENUM_DAY_OF_WEEK day = (ENUM_DAY_OF_WEEK)day_index;
         for(uint session_index = 0; ; session_index++)
         {
            datetime from_time;
            datetime to_time;
            bool ok = SymbolInfoSessionTrade(
               symbol,
               day,
               session_index,
               from_time,
               to_time
            );
            if(!ok)
               break;

            FileWrite(
               handle,
               symbol,
               DayName(day),
               (int)session_index,
               SecondsFromMidnight(from_time),
               SecondsFromMidnight(to_time),
               TimeToString(from_time, TIME_MINUTES),
               TimeToString(to_time, TIME_MINUTES)
            );
         }
      }
   }

   FileClose(handle);
   Print("Finished writing mt5_symbol_sessions.csv");
}
