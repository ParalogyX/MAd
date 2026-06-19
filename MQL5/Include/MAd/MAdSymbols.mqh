//+------------------------------------------------------------------+
//| MAdSymbols.mqh                                                   |
//| Offline symbol classification matching ticker_classification_rules.py. |
//+------------------------------------------------------------------+
#ifndef __MAD_SYMBOLS_MQH__
#define __MAD_SYMBOLS_MQH__

string MAdUpper(string value)
  {
   StringToUpper(value);
   return value;
  }

string MAdLower(string value)
  {
   StringToLower(value);
   return value;
  }

bool MAdIsAlphaNumeric(const ushort character)
  {
   return ((character>='0' && character<='9') ||
           (character>='A' && character<='Z') ||
           (character>='a' && character<='z'));
  }

string MAdSymbolKey(const string value)
  {
   string result="";
   for(int index=0; index<StringLen(value); index++)
     {
      ushort character=(ushort)StringGetCharacter(value,index);
      if(MAdIsAlphaNumeric(character))
         result+=ShortToString(character);
     }
   return MAdUpper(result);
  }

string MAdTokenizedText(const string value)
  {
   string result=" ";
   for(int index=0; index<StringLen(value); index++)
     {
      ushort character=(ushort)StringGetCharacter(value,index);
      if(MAdIsAlphaNumeric(character))
         result+=MAdLower(ShortToString(character));
      else
         result+=" ";
     }

   // Collapse repeated spaces so token searches are deterministic.
   while(StringFind(result,"  ")>=0)
      StringReplace(result,"  "," ");
   return result+" ";
  }

bool MAdSetContains(const string delimited_set,const string value)
  {
   if(value=="")
      return false;
   return StringFind(delimited_set,"|"+MAdUpper(value)+"|")>=0;
  }

bool MAdTextHasToken(const string tokenized_text,string marker)
  {
   marker=MAdLower(marker);
   if(StringFind(marker," ")>=0 || StringFind(marker,".")>=0)
      return StringFind(tokenized_text,marker)>=0;
   return StringFind(tokenized_text," "+marker+" ")>=0;
  }

const string MAD_CRYPTO_CODES=
   "|AAVE|ADA|ALGO|APT|ARB|ATOM|AVAX|BCH|BNB|BTC|DOGE|DOT|ETC|ETH|FIL|"
   "IOTA|LINK|LTC|LUNA|NEAR|OP|PEPE|SHIB|SOL|TRX|TURBO|UNI|XLM|XRP|XVG|";

const string MAD_CURRENCY_CODES=
   "|AUD|BRL|CAD|CHF|CLP|CNH|CNY|CZK|DKK|EUR|GBP|HKD|HUF|JPY|MXN|NOK|"
   "NZD|PLN|RUB|SEK|SGD|TRY|USD|ZAR|";

const string MAD_FOREX_MAJORS=
   "|AUDUSD|EURUSD|GBPUSD|NZDUSD|USDCAD|USDCHF|USDJPY|";

const string MAD_US_STOCKS=
   "|ADOBE|ALIBABA|AMAZON|AMC|AMERICANEXPRESS|APPLE|ATT|BAIDU|"
   "BANKOFAMERICA|BOEING|CATERPILLAR|CISCO|CITIGROUP|COIN|DISNEY|DROPBOX|"
   "EBAY|EXXON|FORD|GENERALELECTRICS|GILD|GME|GOOGLE|GTLB|HARLEYDAVIDSON|"
   "HEWLETTPACKARD|HOMEDEPOT|HOOD|IBM|INTEL|JOHNSONJOHNSON|JPMORGAN|LYFT|"
   "MARA|MASTERCARD|MCDONALD|MICROSOFT|MRNA|NETFLIX|NIKE|NVIDIA|ORACLE|"
   "PFIZER|PHILIPMORRIS|PINS|PROCTERGAMBLE|SALESFORCE|SNAP|SPOTIFY|"
   "STARBUCKS|TESLA|TRAVELERS|TRIPADVISOR|UBER|UNITEDHEALTH|VERIZON|VISA|"
   "WELLSFARGO|WILLIAMSSONOMA|WYNN|";

const string MAD_US_ETFS="|AGG|EWG|EWU|EWW|EWZ|FXI|IJH|ILF|SPY|";

const string MAD_EUROPE_STOCKS=
   "|ADIDAS|AF|AIR|AIRBUS|BAS|BASF|BAYER|BBVA|BMW|BNP|DAIMLER|"
   "DEUTSCHEBANK|DG|EDF|ENEL|ENI|FERRARI|JUVE|P911|REP|RNO|RYAAY|SAP|"
   "SIE|SIEMENS|TUI|VODAFONE|VOLKSWAGEN|";

const string MAD_US_INDEXES="|NQ|NQCASH|TF|USDX|VIX|YM|YMCASH|";
const string MAD_EUROPE_INDEXES="|AEX|CAC|DAX|DE40|FTI|FRA40|FTSE|GER40|UK100|";
const string MAD_ASIA_INDEXES="|HSI|NIY|NIYCASH|TA35|XU|XUCASH|";

bool MAdLooksLikeForexPair(const string symbol_key)
  {
   if(StringLen(symbol_key)!=6)
      return false;
   string base=StringSubstr(symbol_key,0,3);
   string quote=StringSubstr(symbol_key,3,3);
   return MAdSetContains(MAD_CURRENCY_CODES,base) &&
          MAdSetContains(MAD_CURRENCY_CODES,quote);
  }

bool MAdLooksLikeCommodity(const string symbol_key)
  {
   if(StringFind(symbol_key,"XAU")==0 || StringFind(symbol_key,"XAG")==0)
      return true;

   const string commodity_codes=
      "|BRENT|WTI|OIL|CRUDEOIL|NGAS|NATGAS|GOLD|SILVER|COCOA|COFFEE|"
      "CORN|COTTON|SOYBEAN|SUGAR|WHEAT|";
   if(MAdSetContains(commodity_codes,symbol_key))
      return true;

   // Broker suffixes such as BRENTCash or XAUUSD are normalized into one key.
   string markers[]={"BRENT","WTI","CRUDE","NGAS","NATGAS","GOLD","SILVER",
                     "COCOA","COFFEE","CORN","COTTON","SOYBEAN","SUGAR","WHEAT"};
   for(int index=0; index<ArraySize(markers); index++)
      if(StringFind(symbol_key,markers[index])>=0)
         return true;
   return false;
  }

bool MAdLooksLikeIndexText(const string text_upper)
  {
   return StringFind(text_upper,"INDEX")>=0 ||
          StringFind(text_upper,"CASH")>=0 ||
          StringFind(text_upper,"FUTURE")>=0 ||
          StringFind(text_upper," 100")>=0 ||
          StringFind(text_upper," 225")>=0 ||
          StringFind(text_upper," 30")>=0 ||
          StringFind(text_upper," 40")>=0;
  }

string MAdBuildClassificationText(const string symbol)
  {
   string description=SymbolInfoString(symbol,SYMBOL_DESCRIPTION);
   string path=SymbolInfoString(symbol,SYMBOL_PATH);
   string currency_base=SymbolInfoString(symbol,SYMBOL_CURRENCY_BASE);
   string currency_profit=SymbolInfoString(symbol,SYMBOL_CURRENCY_PROFIT);
   return symbol+" "+description+" "+path+" "+currency_base+" "+currency_profit;
  }

string MAdClassifySymbol(const string symbol,string &reason)
  {
   string symbol_key=MAdSymbolKey(symbol);
   string raw_text=MAdBuildClassificationText(symbol);
   string text_tokens=MAdTokenizedText(raw_text);
   string text_upper=MAdUpper(raw_text);

   string crypto_base=symbol_key;
   if(StringLen(symbol_key)>3 &&
      StringSubstr(symbol_key,StringLen(symbol_key)-3)=="USD")
      crypto_base=StringSubstr(symbol_key,0,StringLen(symbol_key)-3);

   if(MAdSetContains(MAD_CRYPTO_CODES,crypto_base))
     {
      reason="symbol matched crypto code";
      return "crypto_24_7";
     }

   string crypto_markers[]={"aave","ada","algo","apt","arb","atom","avax","avalanche",
                            "bch","bitcoin","bnb","cardano","chainlink","crypto","doge",
                            "dot","etc","eth","ethereum","fil","iota","link","litecoin",
                            "ltc","luna","near","op","pepe","polkadot","ripple","shib",
                            "sol","solana","stellar","tron","trx","turbo","uni","xlm",
                            "xrp","xvg"};
   for(int index=0; index<ArraySize(crypto_markers); index++)
      if(MAdTextHasToken(text_tokens,crypto_markers[index]))
        {
         reason="text matched crypto marker";
         return "crypto_24_7";
        }

   if(MAdLooksLikeCommodity(symbol_key))
     {
      reason="symbol matched commodity pattern";
      return "commodity_us";
     }
   string commodity_markers[]={"brent","cocoa","coffee","corn","cotton","gold",
                               "natural gas","ngas","oil","silver","soybean","sugar",
                               "wheat","wti","xag","xau"};
   for(int index=0; index<ArraySize(commodity_markers); index++)
      if(MAdTextHasToken(text_tokens,commodity_markers[index]))
        {
         reason="text matched commodity marker";
         return "commodity_us";
        }

   if(MAdSetContains(MAD_FOREX_MAJORS,symbol_key))
     {
      reason="symbol matched forex major";
      return "forex_major";
     }
   if(MAdLooksLikeForexPair(symbol_key))
     {
      reason="symbol matched six-letter forex pair";
      return "forex_exotic";
     }

   if(MAdSetContains(MAD_US_INDEXES,symbol_key))
     {
      reason="symbol matched US index";
      return "us_stock_index";
     }
   if(MAdSetContains(MAD_EUROPE_INDEXES,symbol_key))
     {
      reason="symbol matched European index";
      return "europe_stock_index";
     }
   if(MAdSetContains(MAD_ASIA_INDEXES,symbol_key))
     {
      reason="symbol matched Asian/Israel index";
      return (symbol_key=="TA35" ? "israel_index" : "asia_index");
     }

   if(StringFind(text_upper,"NDAQ 100")>=0 ||
      StringFind(text_upper,"NASDAQ 100")>=0 ||
      StringFind(text_upper,"DJ 30")>=0)
     {
      reason="description matched US index";
      return "us_stock_index";
     }
   if(StringFind(text_upper,"NETHERLANDS 25")>=0 ||
      StringFind(text_upper,"FRANCE 40")>=0)
     {
      reason="description matched European index";
      return "europe_stock_index";
     }
   if(StringFind(text_upper,"CHINA 50")>=0 ||
      StringFind(text_upper,"JAPAN 225")>=0 ||
      StringFind(text_upper,"NIKKEI")>=0)
     {
      reason="description matched Asian index";
      return "asia_index";
     }

   if(MAdSetContains(MAD_US_ETFS,symbol_key))
     {
      reason="symbol matched known US ETF";
      return "us_stock_index";
     }
   if(MAdSetContains(MAD_US_STOCKS,symbol_key))
     {
      reason="symbol matched known US stock";
      return "us_stock_index";
     }

   string us_markers[]={"amex","nasdaq","nyse","united states","usa","us stock","u.s."};
   for(int index=0; index<ArraySize(us_markers); index++)
      if(MAdTextHasToken(text_tokens,us_markers[index]))
        {
         reason="metadata matched US marker";
         return "us_stock_index";
        }

   if(MAdSetContains(MAD_EUROPE_STOCKS,symbol_key))
     {
      reason="symbol matched known European stock";
      return "europe_stock_index";
     }

   string europe_markers[]={"deutsche borse","euronext","europe","france","germany",
                            "italy","lse","london","spain","uk","united kingdom","xetra"};
   for(int index=0; index<ArraySize(europe_markers); index++)
      if(MAdTextHasToken(text_tokens,europe_markers[index]))
        {
         reason="metadata matched European marker";
         return "europe_stock_index";
        }

   if(MAdTextHasToken(text_tokens,"stock") ||
      MAdTextHasToken(text_tokens,"shares") ||
      MAdTextHasToken(text_tokens,"equities") ||
      MAdTextHasToken(text_tokens,"etf"))
     {
      reason="metadata indicated stock/equity/ETF";
      return "us_stock_index";
     }

   reason="no metadata or offline rule matched";
   return "unknown";
  }

bool MAdIsTradableSymbol(const string symbol)
  {
   long trade_mode=SYMBOL_TRADE_MODE_DISABLED;
   if(!SymbolInfoInteger(symbol,SYMBOL_TRADE_MODE,trade_mode))
      return false;
   return trade_mode!=SYMBOL_TRADE_MODE_DISABLED;
  }

#endif // __MAD_SYMBOLS_MQH__
