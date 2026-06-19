         !MAdValidTimeText(g_groups[index].open_time) ||
         !MAdValidTimeText(g_groups[index].close_time))
        {
         PrintFormat("Invalid schedule time in group %s.",g_groups[index].name);
         return false;
        }
     }
   return true;
  }

int OnInit()
  {
   MAdBuildConfiguration();
   if(!MAdValidateConfiguration())
      return INIT_PARAMETERS_INCORRECT;

   MAdEnsureFolders();
   MAdLoadSymbolGroupOverrides();
   MAdRefreshSymbolUniverse();

   g_trade.SetExpertMagicNumber((ulong)InpMagicNumber);
   g_trade.SetDeviationInPoints((ulong)MathMax(0,InpMaximumDeviationPoints));
   g_trade.SetAsyncMode(false);

   int interval=MathMax(1,InpTimerIntervalSeconds);
   if(!EventSetTimer(interval))
     {
      PrintFormat("EventSetTimer failed, error=%d",GetLastError());
      return INIT_FAILED;
     }

   PrintFormat("MAdStrategyEA initialized. symbols=%d, execution=%s, risk=%.2f%%",
               ArraySize(g_symbols),
               (InpAllowOrderExecution ? "ENABLED" : "DRY RUN"),
               InpRiskPerTradePercent);
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
  }

void OnTick()
  {
   // All work is timer driven because the EA scans and trades multiple symbols.
  }

void OnTimer()
  {
   if(g_timer_busy)
      return;
   g_timer_busy=true;

   for(int index=0; index<ArraySize(g_groups); index++)
     {
      datetime schedule_now=MAdScheduleNow();
      if(!g_groups[index].enabled)
         continue;
      if(!MAdIsTradingDay(schedule_now,g_groups[index].trading_days))
         continue;

      // Small catch-up windows prevent a long multi-symbol scan from making
      // the timer miss an opening minute. Normal runs still start at the exact
      // configured minute; late starts are bounded by the inputs above.
      if(MAdEventDue(schedule_now,g_groups[index].analysis_time,
                     InpMaximumAnalysisDelayMinutes))
         MAdHandleScheduledAnalysis(g_groups[index],schedule_now);

      schedule_now=MAdScheduleNow();
      if(MAdEventDue(schedule_now,g_groups[index].open_time,
                     InpMaximumOpenDelayMinutes))
         MAdHandleScheduledOpen(g_groups[index],schedule_now);

      schedule_now=MAdScheduleNow();
      if(MAdEventDue(schedule_now,g_groups[index].close_time,24*60))
         MAdHandleScheduledClose(g_groups[index],schedule_now);
     }

   g_timer_busy=false;
  }

// ============================================================================
// CLOCK, DAYS AND EVENT IDEMPOTENCY
// ============================================================================

datetime MAdBuildDateTime(const int year,const int month,const int day,
                              const int hour,const int minute=0,
                              const int second=0)
  {
   MqlDateTime parts;
   ZeroMemory(parts);
   parts.year=year;
   parts.mon=month;
   parts.day=day;
   parts.hour=hour;
   parts.min=minute;
   parts.sec=second;
   return StructToTime(parts);
  }

int MAdLastSundayOfMonth(const int year,const int month)
  {
   int next_year=year;
   int next_month=month+1;
   if(next_month>12)
     {
      next_month=1;
      next_year++;
     }
   datetime last_day=MAdBuildDateTime(next_year,next_month,1,0)-86400;
   MqlDateTime parts;
   TimeToStruct(last_day,parts);
   return parts.day-parts.day_of_week;
  }

int MAdAmsterdamUtcOffsetMinutes(const datetime utc_now)
  {
   MqlDateTime current;
   TimeToStruct(utc_now,current);
   int march_sunday=MAdLastSundayOfMonth(current.year,3);
   int october_sunday=MAdLastSundayOfMonth(current.year,10);
   datetime dst_start=MAdBuildDateTime(current.year,3,march_sunday,1);
   datetime dst_end=MAdBuildDateTime(current.year,10,october_sunday,1);
   return (utc_now>=dst_start && utc_now<dst_end ? 120 : 60);
  }

datetime MAdScheduleNow()
  {
   if(InpClockSource==MAD_CLOCK_EUROPE_AMSTERDAM)
     {
      datetime utc_now=TimeGMT();
      return utc_now+(datetime)(MAdAmsterdamUtcOffsetMinutes(utc_now)*60);
     }
   if(InpClockSource==MAD_CLOCK_TRADE_SERVER)
      return TimeTradeServer();
   if(InpClockSource==MAD_CLOCK_GMT_OFFSET)
      return TimeGMT()+(datetime)(InpManualUtcOffsetMinutes*60);
   return TimeLocal();
  }

bool MAdValidTimeText(const string value)
  {
   if(StringLen(value)!=5 || StringSubstr(value,2,1)!=":")
      return false;
   int hour=(int)StringToInteger(StringSubstr(value,0,2));
   int minute=(int)StringToInteger(StringSubstr(value,3,2));
   return hour>=0 && hour<=23 && minute>=0 && minute<=59;
  }

bool MAdTimeMatches(const datetime value,const string hhmm)
  {
   MqlDateTime parts;
   TimeToStruct(value,parts);
   int expected_hour=(int)StringToInteger(StringSubstr(hhmm,0,2));
   int expected_minute=(int)StringToInteger(StringSubstr(hhmm,3,2));
   return parts.hour==expected_hour && parts.min==expected_minute;
  }

int MAdMinuteOfDay(const datetime value)
  {
   MqlDateTime parts;
   TimeToStruct(value,parts);
   return parts.hour*60+parts.min;
  }

int MAdMinuteOfDay(const string hhmm)
  {
   return (int)StringToInteger(StringSubstr(hhmm,0,2))*60+
          (int)StringToInteger(StringSubstr(hhmm,3,2));
  }

