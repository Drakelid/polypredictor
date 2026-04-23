// PolyPredictor — Global Data Layer
window.DAYS = ["Apr 9","Apr 10","Apr 11","Apr 12","Apr 13","Apr 14","Apr 15","Apr 16","Apr 17","Apr 18","Apr 19","Apr 20","Apr 21","Apr 22"];

window.CHARTS = {
  1:[[26,32],[27,34],[28,35],[30,37],[29,36],[31,38],[30,37],[32,39],[31,38],[33,40],[33,40],[34,41],[34,41],[34,41]],
  2:[[32,24],[31,23],[30,23],[29,22],[30,22],[29,22],[28,21],[29,21],[28,20],[27,20],[28,20],[28,20],[28,20],[28,20]],
  3:[[14,18],[14,19],[15,19],[15,20],[16,20],[16,21],[17,21],[17,22],[18,22],[17,22],[18,23],[18,24],[18,25],[18,26]],
  4:[[50,55],[52,57],[53,58],[54,59],[55,60],[56,61],[56,61],[57,62],[58,63],[57,62],[58,63],[58,64],[58,64],[58,64]],
  5:[[48,44],[47,43],[48,43],[47,42],[46,42],[46,41],[47,41],[46,41],[45,41],[46,40],[45,40],[45,40],[45,40],[45,40]],
  6:[[14,18],[15,20],[16,21],[17,22],[18,24],[19,25],[19,25],[20,27],[21,28],[21,28],[22,29],[22,30],[22,31],[22,31]],
};

window.MARKETS = [
  {id:1,category:"BTC",question:"Will BTC close above $100k on April 30, 2026?",
   market:34,estimate:41,volume:"2.4M",age:"2h ago",resolves:"Apr 30",daysLeft:7,
   confidence:{low:37,high:45},watched:true,
   events:[{day:7,label:"CPI"},{day:11,label:"PCE"}],
   signals:[
    {src:"X / CT Sentiment",icon:"𝕏",dir:1,strength:.78,insight:"Bullish divergence — whale accounts accumulating, CT sentiment flipped positive last 6h"},
    {src:"Discord / TG",icon:"💬",dir:1,strength:.55,insight:"Moderate buzz uptick in major BTC channels; no clear coordinated pump narrative"},
    {src:"On-chain Flows",icon:"⛓",dir:1,strength:.82,insight:"158k BTC moved off exchanges in 48h — historically strong buy signal"},
    {src:"Funding / OI",icon:"📊",dir:-1,strength:.42,insight:"Funding slightly negative; OI declining — suggests short pressure remains"},
    {src:"News / Macro",icon:"📰",dir:1,strength:.60,insight:"Fed speakers dovish this week; CPI below expectations strengthens risk-on case"},
    {src:"PM Price History",icon:"📈",dir:0,strength:.35,insight:"Market stagnant at 34% for 18h — likely underreacting to on-chain data"},
  ]},
  {id:2,category:"ETH",question:"Will ETH ETF see >$500M net inflows this week?",
   market:28,estimate:20,volume:"890K",age:"4h ago",resolves:"Apr 27",daysLeft:4,
   confidence:{low:16,high:24},watched:false,
   events:[{day:5,label:"ETF Report"}],
   signals:[
    {src:"X / CT Sentiment",icon:"𝕏",dir:-1,strength:.62,insight:"ETH ETF narrative fading; institutional Twitter quiet on inflows"},
    {src:"Discord / TG",icon:"💬",dir:0,strength:.28,insight:"Minimal discussion around ETH ETF flows in tracked channels"},
    {src:"On-chain Flows",icon:"⛓",dir:-1,strength:.55,insight:"ETH exchange inflows increasing — some selling pressure at current levels"},
    {src:"Funding / OI",icon:"📊",dir:-1,strength:.48,insight:"ETH perpetual funding negative; open interest flat"},
    {src:"News / Macro",icon:"📰",dir:0,strength:.20,insight:"No major ETF catalyst expected this week per Bloomberg intelligence"},
    {src:"PM Price History",icon:"📈",dir:-1,strength:.70,insight:"Market overpriced at 28% vs our 20% estimate — consistent downward pressure"},
  ]},
  {id:3,category:"MACRO",question:"Will the Fed cut rates at the May 2026 FOMC meeting?",
   market:18,estimate:26,volume:"3.1M",age:"6h ago",resolves:"May 7",daysLeft:14,
   confidence:{low:22,high:30},watched:true,
   events:[{day:3,label:"Jobs"},{day:13,label:"FOMC"}],
   signals:[
    {src:"X / CT Sentiment",icon:"𝕏",dir:1,strength:.50,insight:"Macro accounts shifting toward cut expectations after jobs miss Friday"},
    {src:"Discord / TG",icon:"💬",dir:0,strength:.15,insight:"Minimal crypto-adjacent discussion about Fed policy"},
    {src:"On-chain Flows",icon:"⛓",dir:0,strength:.10,insight:"Irrelevant to this market — signal excluded"},
    {src:"Funding / OI",icon:"📊",dir:1,strength:.38,insight:"BTC/ETH funding rates rising — market pricing in looser monetary conditions"},
    {src:"News / Macro",icon:"📰",dir:1,strength:.88,insight:"Jobs report -45k vs +120k expected; PCE came in soft. Historically correlates with cut probability >25%"},
    {src:"PM Price History",icon:"📈",dir:1,strength:.65,insight:"Market at 18% — slow to reprice vs options market which implies ~24%"},
  ]},
  {id:4,category:"SOL",question:"Will SOL outperform ETH on a 30-day return basis by May 1?",
   market:58,estimate:64,volume:"1.2M",age:"1h ago",resolves:"May 1",daysLeft:8,
   confidence:{low:59,high:69},watched:true,
   events:[{day:6,label:"Firedancer"}],
   signals:[
    {src:"X / CT Sentiment",icon:"𝕏",dir:1,strength:.72,insight:"SOL CT consistently bullish; multiple large accounts citing Firedancer upgrade momentum"},
    {src:"Discord / TG",icon:"💬",dir:1,strength:.65,insight:"Solana DeFi channels active; new protocol launches driving ecosystem attention"},
    {src:"On-chain Flows",icon:"⛓",dir:1,strength:.70,insight:"SOL net outflows from exchanges — accumulation pattern consistent with prior breakouts"},
    {src:"Funding / OI",icon:"📊",dir:1,strength:.58,insight:"SOL funding positive and rising; ETH OI declining — relative trade set up clearly"},
    {src:"News / Macro",icon:"📰",dir:0,strength:.30,insight:"No major macro catalyst differentiating SOL vs ETH currently"},
    {src:"PM Price History",icon:"📈",dir:1,strength:.45,insight:"Market at 58% — slightly below our 64% estimate; gap widening over last 12h"},
  ]},
  {id:5,category:"COIN",question:"Will Coinbase (COIN) stock close above $250 by April 30?",
   market:45,estimate:40,volume:"620K",age:"8h ago",resolves:"Apr 30",daysLeft:7,
   confidence:{low:35,high:45},watched:false,
   events:[{day:4,label:"Earnings"}],
   signals:[
    {src:"X / CT Sentiment",icon:"𝕏",dir:-1,strength:.52,insight:"COIN stock mentions declining; focus shifted to DEX narratives"},
    {src:"Discord / TG",icon:"💬",dir:-1,strength:.30,insight:"Limited discussion; some skepticism around COIN given regulatory overhang"},
    {src:"On-chain Flows",icon:"⛓",dir:0,strength:.20,insight:"On-chain data not directly relevant for COIN equity price"},
    {src:"Funding / OI",icon:"📊",dir:-1,strength:.45,insight:"Crypto market OI soft — headwind for Coinbase revenue estimates"},
    {src:"News / Macro",icon:"📰",dir:-1,strength:.60,insight:"DOJ inquiry headlines resurfaced — overhang on institutional buying"},
    {src:"PM Price History",icon:"📈",dir:-1,strength:.55,insight:"Market at 45% vs our 40% — consistent overpricing over 3 days"},
  ]},
  {id:6,category:"MKTCAP",question:"Will total crypto market cap exceed $4T before May 15, 2026?",
   market:22,estimate:31,volume:"4.8M",age:"30m ago",resolves:"May 15",daysLeft:22,
   confidence:{low:27,high:35},watched:true,
   events:[{day:5,label:"SWF Report"},{day:10,label:"CPI"}],
   signals:[
    {src:"X / CT Sentiment",icon:"𝕏",dir:1,strength:.80,insight:"Extremely bullish CT narrative — 'supercycle' talk at highest levels since Nov 2024"},
    {src:"Discord / TG",icon:"💬",dir:1,strength:.75,insight:"Multiple large Discord servers running $4T milestone betting pools — engagement peak"},
    {src:"On-chain Flows",icon:"⛓",dir:1,strength:.85,insight:"Cross-chain: BTC, ETH, SOL, BNB all showing simultaneous exchange outflows — rare convergence"},
    {src:"Funding / OI",icon:"📊",dir:1,strength:.70,insight:"Total perp OI up 18% in 5 days; funding rates rising uniformly across top-10"},
    {src:"News / Macro",icon:"📰",dir:1,strength:.65,insight:"Treasury Secretary comments on digital assets positive; sovereign wealth fund rumors circulating"},
    {src:"PM Price History",icon:"📈",dir:1,strength:.80,insight:"Market at 22% — significantly lagging vs every other signal. Classic PM underreaction"},
  ]},
];

window.ALERTS = [
  {id:1,type:"edge",marketId:6,title:"Strong edge detected",body:"MKTCAP market shows +9pp edge — all 5 bullish signals aligned. Highest conviction setup today.",time:"4m ago",read:false},
  {id:2,type:"diverge",marketId:3,title:"Signal divergence on MACRO",body:"Fed cut market moved from 15% → 18% but PP estimate held at 26%. Edge widening.",time:"32m ago",read:false},
  {id:3,type:"move",marketId:1,title:"BTC market price moved",body:"PM price dipped from 38% → 34% while on-chain signals remain strongly bullish. Edge increased.",time:"1h ago",read:false},
  {id:4,type:"update",marketId:4,title:"PP estimate updated",body:"SOL vs ETH estimate revised up from 61% → 64% after Firedancer benchmark data incorporated.",time:"2h ago",read:true},
  {id:5,type:"edge",marketId:2,title:"NO edge on ETH ETF",body:"PP estimate (20%) now 8pp below market (28%). Consider fading YES position.",time:"3h ago",read:true},
];

window.WATCHLIST_POSITIONS = [
  {marketId:1,entryPM:30,entryEst:38,shares:120,direction:"YES"},
  {marketId:3,entryPM:15,entryEst:22,shares:200,direction:"YES"},
  {marketId:4,entryPM:55,entryEst:61,shares:80,direction:"YES"},
  {marketId:6,entryPM:18,entryEst:27,shares:300,direction:"YES"},
];

window.BACKTEST = [
  {q:"Will BTC hit $80k before Feb 1?",         cat:"BTC",    pp:72,pm:65,outcome:true, days:82},
  {q:"Will ETH ETF launch before March?",        cat:"ETH",    pp:85,pm:78,outcome:true, days:68},
  {q:"Will Fed cut rates in March 2026?",        cat:"MACRO",  pp:28,pm:22,outcome:false,days:54},
  {q:"Will SOL reach $300 by end of Q1?",        cat:"SOL",    pp:45,pm:38,outcome:false,days:47},
  {q:"Will BTC dominance exceed 60%?",           cat:"BTC",    pp:62,pm:55,outcome:true, days:41},
  {q:"Will DOGE outperform BTC in Jan?",         cat:"DOGE",   pp:35,pm:42,outcome:true, days:38},
  {q:"Will crypto mkt cap exceed $3.5T in Feb?", cat:"MKTCAP", pp:58,pm:50,outcome:true, days:35},
  {q:"Will Coinbase stock hit $300?",            cat:"COIN",   pp:32,pm:28,outcome:false,days:30},
  {q:"Will ETH/BTC ratio exceed 0.06?",          cat:"ETH",    pp:40,pm:35,outcome:false,days:27},
  {q:"Will Fed pause at March FOMC?",            cat:"MACRO",  pp:78,pm:71,outcome:true, days:25},
  {q:"Will BTC close above $95k by Feb 15?",     cat:"BTC",    pp:55,pm:48,outcome:true, days:22},
  {q:"Will SOL flip ETH in market cap?",         cat:"SOL",    pp:18,pm:22,outcome:false,days:20},
  {q:"Will XRP ETF approval come before Apr?",   cat:"XRP",    pp:42,pm:38,outcome:false,days:18},
  {q:"Will stablecoin supply exceed $200B?",     cat:"STABLE", pp:80,pm:74,outcome:true, days:15},
  {q:"Will BTC mining difficulty hit ATH?",      cat:"BTC",    pp:88,pm:82,outcome:true, days:12},
  {q:"Will ETH gas fees average <5 gwei?",       cat:"ETH",    pp:65,pm:58,outcome:true, days:10},
  {q:"Will Nasdaq 100 outperform BTC in Feb?",   cat:"MACRO",  pp:30,pm:35,outcome:false,days:8},
  {q:"Will Solana DEX volume exceed Ethereum?",  cat:"SOL",    pp:52,pm:46,outcome:true, days:6},
  {q:"Will COIN stock drop below $200?",         cat:"COIN",   pp:25,pm:30,outcome:false,days:4},
  {q:"Will crypto mktcap drop below $2.5T?",     cat:"MKTCAP", pp:15,pm:20,outcome:false,days:2},
];

window.CALIB_BUCKETS = [[20,.25,4],[35,.40,5],[55,.60,5],[75,.80,4],[88,1.0,2]];

window.SIGNAL_ACCURACY = [
  {src:"On-chain Flows",   icon:"⛓", accuracy:.78,correct:13,total:16},
  {src:"News / Macro",     icon:"📰", accuracy:.73,correct:11,total:14},
  {src:"Funding / OI",     icon:"📊", accuracy:.71,correct:13,total:18},
  {src:"PM Price History", icon:"📈", accuracy:.66,correct:14,total:20},
  {src:"X / CT Sentiment", icon:"𝕏", accuracy:.64,correct:12,total:18},
  {src:"Discord / TG",     icon:"💬", accuracy:.58,correct: 9,total:15},
];

window.DEFAULT_SETTINGS = {
  bankroll:1000,theme:"dark",accent:"mint",
  alertEdge:true,alertDiverge:true,alertMove:true,alertUpdate:false,
  refreshInterval:10,
};

window.edgeMeta = (edge) => {
  const a=Math.abs(edge);
  if(a>=8) return {label:edge>0?"Strong YES edge":"Strong NO edge",tier:"strong"};
  if(a>=5) return {label:edge>0?"YES edge":"NO edge",tier:"moderate"};
  if(a>=2) return {label:edge>0?"Slight YES":"Slight NO",tier:"slight"};
  return {label:"No edge",tier:"none"};
};
window.edgeCol = (tier,dir=1) => {
  if(tier==="none") return "var(--muted)";
  if(tier==="strong"||tier==="moderate") return dir>0?"var(--green)":"var(--red)";
  return "var(--amber)";
};
