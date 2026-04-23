// pp-detail.jsx — EdgeGauge, ProbabilityChart, SignalRow, DetailPanel
const {useState,useEffect,useRef,useMemo} = React;

/* ── EDGE GAUGE ── */
const EdgeGauge = ({edge}) => {
  const [animPct,setAnimPct]=useState(0);
  const raf=useRef(null);
  useEffect(()=>{
    const target=Math.min(Math.abs(edge)/15,1),start=performance.now(),dur=700;
    const ease=t=>t<.5?2*t*t:(4-2*t)*t-1;
    const tick=now=>{const t=Math.min((now-start)/dur,1);setAnimPct(ease(t)*target);if(t<1)raf.current=requestAnimationFrame(tick);};
    raf.current=requestAnimationFrame(tick);
    return()=>cancelAnimationFrame(raf.current);
  },[edge]);
  const isYes=edge>0,col=Math.abs(edge)>=8?(isYes?"var(--green)":"var(--red)"):Math.abs(edge)>=4?"var(--amber)":"var(--muted)";
  const cx=90,cy=82,r=58,PI=Math.PI;
  const arc=(a1,a2,rr)=>{const x1=cx+rr*Math.cos(a1),y1=cy+rr*Math.sin(a1),x2=cx+rr*Math.cos(a2),y2=cy+rr*Math.sin(a2);return `M${x1} ${y1} A${rr} ${rr} 0 ${a2-a1>PI?1:0} 1 ${x2} ${y2}`;};
  const na=PI+PI*(0.5+(isYes?animPct:-animPct)*0.5);
  const nx=cx+(r-14)*Math.cos(na),ny=cy+(r-14)*Math.sin(na);
  return (
    <div style={{textAlign:"center"}}>
      <svg width="180" height="95" viewBox="0 0 180 95">
        <defs>
          <linearGradient id="gaugeGrad" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="oklch(0.68 0.20 25)" stopOpacity=".3"/>
            <stop offset="50%" stopColor="var(--muted2)" stopOpacity=".4"/>
            <stop offset="100%" stopColor="var(--green)" stopOpacity=".3"/>
          </linearGradient>
          <filter id="gaugeGlow"><feGaussianBlur stdDeviation="2" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
        </defs>
        <path d={arc(PI,2*PI,r)} fill="none" stroke="url(#gaugeGrad)" strokeWidth="7" strokeLinecap="round"/>
        {animPct>.01&&<path d={arc(Math.min(PI+PI*.5,PI+PI*(0.5+(isYes?animPct:-animPct)*.5)),Math.max(PI+PI*.5,PI+PI*(0.5+(isYes?animPct:-animPct)*.5)),r)} fill="none" stroke={col} strokeWidth="7" strokeLinecap="round" filter="url(#gaugeGlow)" opacity=".9"/>}
        <line x1={cx} y1={cy} x2={nx} y2={ny} stroke={col} strokeWidth="2.5" strokeLinecap="round"/>
        <circle cx={cx} cy={cy} r="5" fill={col} filter="url(#gaugeGlow)"/>
        <text x="20" y="90" fill="var(--red)" fontSize="9" fontFamily="Space Grotesk" opacity=".6">NO</text>
        <text x="150" y="90" fill="var(--green)" fontSize="9" fontFamily="Space Grotesk" opacity=".6">YES</text>
        <text x={cx} y="52" textAnchor="middle" fill={col} fontSize="20" fontFamily="JetBrains Mono" fontWeight="600" filter="url(#gaugeGlow)">{edge>0?"+":""}{edge}pp</text>
        <text x={cx} y="66" textAnchor="middle" fill="var(--muted)" fontSize="9" fontFamily="Space Grotesk">edge vs. market</text>
      </svg>
    </div>
  );
};

/* ── PROBABILITY CHART ── */
const ProbabilityChart = ({market,chartType,adjustedEstimate,showConf}) => {
  const rawData=CHARTS[market.id];
  const data=rawData.map((d,i)=>i===rawData.length-1?[d[0],adjustedEstimate]:d);
  const [hover,setHover]=useState(null);
  const events=market.events||[];
  const W=460,H=140,pad={t:20,r:10,b:26,l:30};
  const IW=W-pad.l-pad.r,IH=H-pad.t-pad.b;
  const cl=market.confidence.low,ch=market.confidence.high;
  const allVals=[...data.flatMap(d=>d),cl,ch];
  const mn=Math.max(0,Math.min(...allVals)-8),mx=Math.min(100,Math.max(...allVals)+8);
  const sx=i=>pad.l+i/(data.length-1)*IW,sy=v=>pad.t+IH-(v-mn)/(mx-mn)*IH;
  const pmPath=data.map((d,i)=>`${i===0?"M":"L"}${sx(i)} ${sy(d[0])}`).join(" ");
  const ppPath=data.map((d,i)=>`${i===0?"M":"L"}${sx(i)} ${sy(d[1])}`).join(" ");
  const pmArea=`${pmPath} L${sx(data.length-1)} ${sy(mn)} L${sx(0)} ${sy(mn)} Z`;
  const ppArea=`${ppPath} L${sx(data.length-1)} ${sy(mn)} L${sx(0)} ${sy(mn)} Z`;
  const confArea=`M${sx(0)} ${sy(cl)} ${data.map((_,i)=>`L${sx(i)} ${sy(cl)}`).join(" ")} L${sx(data.length-1)} ${sy(ch)} ${[...data].reverse().map((_,i)=>`L${sx(data.length-1-i)} ${sy(ch)}`).join(" ")} Z`;
  const ticks=[mn+5,Math.round((mn+mx)/2),mx-5];
  return (
    <div>
      <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:8}}>
        <span style={{fontSize:10,fontWeight:700,letterSpacing:".09em",textTransform:"uppercase",color:"var(--muted)"}}>14-Day Probability History</span>
        <div style={{display:"flex",gap:10}}>
          {[["var(--violet)","PM Market"],["var(--green)","PP Estimate"],...(showConf?[["var(--green)","Conf. band"]]:[])].map(([c,l],i)=>(
            <div key={i} style={{display:"flex",alignItems:"center",gap:4}}>
              {l==="Conf. band"?<div style={{width:14,height:8,background:c,opacity:.15,border:`1px solid ${c}55`,borderRadius:2}}/>:<div style={{width:14,height:2,background:c,borderRadius:1}}/>}
              <span style={{fontSize:9,color:"var(--muted)"}}>{l}</span>
            </div>
          ))}
        </div>
      </div>
      <svg width="100%" viewBox={`0 0 ${W} ${H}`} style={{overflow:"visible"}}
        onMouseLeave={()=>setHover(null)}
        onMouseMove={e=>{const rect=e.currentTarget.getBoundingClientRect();const x=(e.clientX-rect.left)/rect.width*W;setHover(Math.max(0,Math.min(data.length-1,Math.round((x-pad.l)/IW*(data.length-1)))));}} >
        <defs>
          <linearGradient id="pmGrd" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="oklch(0.72 0.18 290)" stopOpacity=".2"/><stop offset="100%" stopColor="oklch(0.72 0.18 290)" stopOpacity="0"/></linearGradient>
          <linearGradient id="ppGrd" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="var(--green)" stopOpacity=".15"/><stop offset="100%" stopColor="var(--green)" stopOpacity="0"/></linearGradient>
        </defs>
        {ticks.map(v=>(<g key={v}><line x1={pad.l} y1={sy(v)} x2={W-pad.r} y2={sy(v)} stroke="var(--border)" strokeWidth="1"/><text x={pad.l-5} y={sy(v)+4} textAnchor="end" fontSize="8" fill="var(--muted)" fontFamily="JetBrains Mono">{Math.round(v)}%</text></g>))}
        {[0,4,8,13].map(i=><text key={i} x={sx(i)} y={H} textAnchor="middle" fontSize="7.5" fill="var(--muted)" fontFamily="Space Grotesk">{DAYS[i]}</text>)}
        {/* confidence band */}
        {showConf&&<path d={confArea} fill="var(--green)" opacity=".07"/>}
        {showConf&&<line x1={pad.l} y1={sy(cl)} x2={W-pad.r} y2={sy(cl)} stroke="var(--green)" strokeWidth="1" strokeDasharray="3,3" opacity=".3"/>}
        {showConf&&<line x1={pad.l} y1={sy(ch)} x2={W-pad.r} y2={sy(ch)} stroke="var(--green)" strokeWidth="1" strokeDasharray="3,3" opacity=".3"/>}
        {/* areas */}
        {chartType!=="Line"&&<><path d={pmArea} fill="url(#pmGrd)"/><path d={ppArea} fill="url(#ppGrd)"/></>}
        {/* event markers */}
        {events.map((ev,i)=>{const ex=sx(ev.day);return(<g key={i}>
          <line x1={ex} y1={pad.t} x2={ex} y2={H-pad.b} stroke="var(--amber)" strokeWidth="1" strokeDasharray="3,3" opacity=".7"/>
          <rect x={ex-14} y={pad.t-16} width={28} height={14} rx="3" fill="var(--surface3)" stroke="var(--amber)" strokeWidth=".8"/>
          <text x={ex} y={pad.t-5} textAnchor="middle" fontSize="7" fill="var(--amber)" fontFamily="Space Grotesk" fontWeight="600">{ev.label}</text>
        </g>);})}
        {/* lines */}
        <path d={pmPath} fill="none" stroke="var(--violet)" strokeWidth="2" strokeLinejoin="round"/>
        <path d={ppPath} fill="none" stroke="var(--green)" strokeWidth="2" strokeLinejoin="round"/>
        {/* last point glow */}
        <circle cx={sx(data.length-1)} cy={sy(data[data.length-1][1])} r="4" fill="var(--green)" filter="url(#gaugeGlow)"/>
        {/* hover */}
        {hover!=null&&<>
          <line x1={sx(hover)} y1={pad.t} x2={sx(hover)} y2={H-pad.b} stroke="var(--border2)" strokeWidth="1" strokeDasharray="3,3"/>
          <circle cx={sx(hover)} cy={sy(data[hover][0])} r="4" fill="var(--violet)" stroke="var(--bg)" strokeWidth="2"/>
          <circle cx={sx(hover)} cy={sy(data[hover][1])} r="4" fill="var(--green)" stroke="var(--bg)" strokeWidth="2"/>
          <rect x={Math.min(sx(hover)-32,W-pad.r-66)} y={pad.t} width="66" height="36" rx="4" fill="var(--surface3)" stroke="var(--border2)"/>
          <text x={Math.min(sx(hover),W-pad.r-33)} y={pad.t+13} textAnchor="middle" fontSize="8" fill="var(--muted)" fontFamily="Space Grotesk">{DAYS[hover]}</text>
          <text x={Math.min(sx(hover),W-pad.r-33)-5} y={pad.t+27} textAnchor="end" fontSize="10" fill="var(--violet)" fontFamily="JetBrains Mono" fontWeight="600">{data[hover][0]}%</text>
          <text x={Math.min(sx(hover),W-pad.r-33)+5} y={pad.t+27} textAnchor="start" fontSize="10" fill="var(--green)" fontFamily="JetBrains Mono" fontWeight="600">{data[hover][1]}%</text>
        </>}
      </svg>
    </div>
  );
};

/* ── SIGNAL ROW ── */
const SignalRow = ({sig,pulsing,prevStrength}) => {
  const col=sig.dir>0?"var(--green)":sig.dir<0?"var(--red)":"var(--muted)";
  const arr=sig.dir>0?"↑":sig.dir<0?"↓":"→";
  const delta=prevStrength!=null?Math.round((sig.strength-prevStrength)*100):0;
  return (
    <div style={{padding:"11px 0",borderBottom:"1px solid var(--border)",animation:pulsing?"signalFlash 1.4s ease":"none",borderRadius:4}}>
      <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:5}}>
        <span style={{fontSize:12,opacity:.7}}>{sig.icon}</span>
        <span style={{fontSize:12,fontWeight:600,flex:1}}>{sig.src}</span>
        {pulsing&&delta!==0&&<span style={{fontSize:10,fontWeight:700,color:delta>0?"var(--green)":"var(--red)",animation:"tickIn .3s ease",fontFamily:"'JetBrains Mono'"}}>{delta>0?"+":""}{delta}pp</span>}
        {pulsing&&<span style={{width:6,height:6,borderRadius:"50%",background:"var(--green)",boxShadow:"0 0 6px var(--green)",animation:"pulse 1s ease 3",flexShrink:0}}/>}
        <span style={{color:col,fontSize:13,fontWeight:700}}>{arr}</span>
        <span style={{fontFamily:"'JetBrains Mono'",fontSize:11,color:col}}>{Math.round(sig.strength*100)}%</span>
      </div>
      <div style={{height:3,background:"var(--border)",borderRadius:2,marginBottom:6}}>
        <div style={{height:"100%",width:`${sig.strength*100}%`,background:col,borderRadius:2,opacity:.8,transition:"width .8s ease"}}/>
      </div>
      <div style={{fontSize:11,color:"var(--muted)",lineHeight:1.5,textWrap:"pretty"}}>{sig.insight}</div>
    </div>
  );
};

/* ── DETAIL PANEL ── */
const DetailPanel = ({market,onClose,showConf,isMobile,chartType,defaultBankroll=1000,tracked,onTrack}) => {
  const [tab,setTab]=useState("overview");
  const [weights,setWeights]=useState(()=>Object.fromEntries(market.signals.map((_,i)=>[i,1])));
  const [showWeights,setShowWeights]=useState(false);
  const [bankroll,setBankroll]=useState(defaultBankroll);
  const [liveSignals,setLiveSignals]=useState(market.signals.map(s=>({...s})));
  const [pulsing,setPulsing]=useState(new Set());
  const [prevStr,setPrevStr]=useState({});
  const [lastRefresh,setLastRefresh]=useState(Date.now());
  const [secsSince,setSecsSince]=useState(0);
  const tid=useRef(null);

  useEffect(()=>{const t=setInterval(()=>setSecsSince(Math.floor((Date.now()-lastRefresh)/1000)),1000);return()=>clearInterval(t);},[lastRefresh]);

  useEffect(()=>{
    const schedule=()=>{
      return setTimeout(()=>{
        const count=Math.random()<.4?2:1, idxs=[];
        while(idxs.length<count){const i=Math.floor(Math.random()*market.signals.length);if(!idxs.includes(i))idxs.push(i);}
        setLiveSignals(prev=>{
          const next=[...prev],snaps={};
          idxs.forEach(i=>{snaps[i]=next[i].strength;next[i]={...next[i],strength:Math.max(.05,Math.min(.99,next[i].strength+(Math.random()-.45)*.10))};});
          setPrevStr(snaps);return next;
        });
        setPulsing(new Set(idxs));setLastRefresh(Date.now());
        setTimeout(()=>setPulsing(new Set()),2200);
        tid.current=schedule();
      },8000+Math.random()*6000);
    };
    tid.current=schedule();
    return()=>clearTimeout(tid.current);
  },[market.id]);

  useEffect(()=>{const h=e=>{if(e.key==="Escape")onClose();};window.addEventListener("keydown",h);return()=>window.removeEventListener("keydown",h);},[onClose]);

  const adjEst=useMemo(()=>{
    const adj=liveSignals.reduce((a,s,i)=>a+s.dir*s.strength*(weights[i]-1),0)/liveSignals.length;
    return Math.round(Math.max(5,Math.min(95,market.estimate+adj*22)));
  },[weights,liveSignals,market.estimate]);

  const wChanged=Object.values(weights).some(w=>w!==1);
  const adjEdge=adjEst-market.market;
  const {label:eLabel,tier}=edgeMeta(adjEdge);
  const col=edgeCol(tier,adjEdge);
  const conflicted=liveSignals.filter(s=>s.dir>0&&s.strength>.4).length>=2&&liveSignals.filter(s=>s.dir<0&&s.strength>.4).length>=2;

  // Kelly
  const p=adjEst/100,mp=market.market/100,b=(1-mp)/mp;
  const kFull=Math.max(0,(p*b-(1-p))/b);

  // Related markets
  const cosine=(a,bv)=>{const dot=a.reduce((s,ai,i)=>s+ai*bv[i],0);const ma=Math.sqrt(a.reduce((s,ai)=>s+ai*ai,0));const mb=Math.sqrt(bv.reduce((s,bi)=>s+bi*bi,0));return(ma*mb)===0?0:dot/(ma*mb);};
  const myVec=market.signals.map(s=>s.dir*s.strength);
  const related=MARKETS.filter(m=>m.id!==market.id).map(m=>({...m,corr:cosine(myVec,m.signals.map(s=>s.dir*s.strength))})).sort((a,b)=>b.corr-a.corr).slice(0,2);

  const TB=({id,label,badge})=>(
    <button onClick={()=>setTab(id)} style={{flex:1,padding:"9px 4px",background:"none",border:"none",
      borderBottom:`2px solid ${tab===id?"var(--green)":"transparent"}`,
      color:tab===id?"var(--green)":"var(--muted)",fontSize:12,fontWeight:tab===id?600:400,
      fontFamily:"inherit",cursor:"pointer",transition:"all .15s",display:"flex",alignItems:"center",justifyContent:"center",gap:5}}>
      {label}{badge&&<span style={{width:6,height:6,borderRadius:"50%",background:"var(--amber)",display:"inline-block"}}/>}
    </button>
  );

  const refreshLabel=secsSince<5?"just now":secsSince<60?`${secsSince}s ago`:"1m ago";

  return (
    <div style={{position:"fixed",top:0,right:0,width:isMobile?"100vw":"min(600px,100vw)",height:"100dvh",
      background:"var(--surface)",borderLeft:"1px solid var(--border2)",display:"flex",flexDirection:"column",
      zIndex:100,animation:"slideInRight .25s ease",boxShadow:"-20px 0 60px #000a"}}>

      {/* header */}
      <div style={{padding:"13px 20px",borderBottom:"1px solid var(--border)",display:"flex",gap:12,alignItems:"flex-start",flexShrink:0}}>
        <div style={{flex:1}}>
          <div style={{display:"flex",gap:7,alignItems:"center",flexWrap:"wrap",marginBottom:6}}>
            <span style={{background:"var(--surface3)",color:"var(--muted)",fontSize:10,fontWeight:700,letterSpacing:".09em",padding:"2px 7px",borderRadius:4}}>{market.category}</span>
            <span style={{fontSize:11,color:"var(--muted)"}}>Vol ${market.volume} · {market.age}</span>
            {market.daysLeft!=null&&<span style={{fontSize:11,fontWeight:600,color:market.daysLeft<=4?"var(--red)":"var(--amber)"}}>⏱ {market.daysLeft}d left</span>}
            {conflicted&&<span style={{fontSize:10,fontWeight:700,color:"var(--amber)",background:"var(--amber-dim)",padding:"2px 7px",borderRadius:4}}>⚡ Signal conflict</span>}
          </div>
          <div style={{fontSize:13,fontWeight:500,lineHeight:1.45,textWrap:"pretty"}}>{market.question}</div>
        </div>
        <button onClick={onClose} style={{background:"none",border:"none",color:"var(--muted)",cursor:"pointer",fontSize:20,padding:4,flexShrink:0}}>✕</button>
      </div>

      {/* gauge row */}
      <div style={{padding:"13px 20px",borderBottom:"1px solid var(--border)",display:"flex",gap:12,alignItems:"center",flexShrink:0}}>
        <EdgeGauge edge={adjEdge}/>
        <div style={{flex:1}}>
          <div style={{marginBottom:9}}>
            <div style={{fontSize:10,fontWeight:700,letterSpacing:".08em",color:"var(--muted)",textTransform:"uppercase",marginBottom:3}}>PP Estimate</div>
            <div style={{display:"flex",alignItems:"baseline",gap:7}}>
              <span style={{fontFamily:"'JetBrains Mono'",fontSize:28,fontWeight:600,color:"var(--green)"}}>{adjEst}%</span>
              {wChanged&&adjEst!==market.estimate&&<span style={{fontSize:12,color:adjEst>market.estimate?"var(--green)":"var(--red)",fontFamily:"'JetBrains Mono'",fontWeight:600}}>{adjEst>market.estimate?"+":""}{adjEst-market.estimate}pp</span>}
            </div>
            {showConf&&<div style={{fontSize:11,color:"var(--muted)",marginTop:1}}>Band: {market.confidence.low}% – {market.confidence.high}%</div>}
            {wChanged&&<div style={{fontSize:10,color:"var(--amber)",marginTop:2}}>⚡ Custom weights applied</div>}
          </div>
          <div style={{display:"flex",gap:14,alignItems:"center"}}>
            <div>
              <div style={{fontSize:10,color:"var(--muted)",marginBottom:2}}>PM Market</div>
              <div style={{fontFamily:"'JetBrains Mono'",fontSize:18,color:"var(--violet)"}}>{market.market}%</div>
            </div>
            <span style={{padding:"3px 10px",borderRadius:6,fontSize:11,fontWeight:600,background:col+"22",color:col,border:`1px solid ${col}44`}}>{eLabel}</span>
          </div>
        </div>
      </div>

      {/* tabs */}
      <div style={{display:"flex",borderBottom:"1px solid var(--border)",flexShrink:0}}>
        <TB id="overview" label="Overview"/>
        <TB id="signals" label="Signals" badge={conflicted}/>
        <TB id="sizing" label="Sizing"/>
      </div>

      {/* content */}
      <div style={{flex:1,overflowY:"auto",padding:"16px 20px"}}>

        {tab==="overview"&&<>
          <ProbabilityChart market={market} chartType={chartType} adjustedEstimate={adjEst} showConf={showConf}/>
          <div style={{marginTop:18}}>
            <div style={{fontSize:10,fontWeight:700,letterSpacing:".09em",textTransform:"uppercase",color:"var(--muted)",marginBottom:10}}>Related Markets</div>
            {related.map(rm=>{
              const re=rm.estimate-rm.market;const{tier:rt}=edgeMeta(re);const rc=edgeCol(rt,re);
              return(<div key={rm.id} style={{display:"flex",gap:10,alignItems:"center",padding:"10px 13px",borderRadius:8,border:"1px solid var(--border)",marginBottom:7,background:"var(--surface2)"}}>
                <span style={{background:"var(--surface3)",color:"var(--muted)",fontSize:10,fontWeight:700,padding:"1px 6px",borderRadius:3,flexShrink:0}}>{rm.category}</span>
                <span style={{fontSize:11,flex:1,lineHeight:1.35,textWrap:"pretty"}}>{rm.question}</span>
                <div style={{textAlign:"right",flexShrink:0}}>
                  <div style={{fontFamily:"'JetBrains Mono'",fontSize:12,fontWeight:700,color:rc}}>{re>0?"+":""}{re}pp</div>
                  <div style={{fontSize:9,color:"var(--muted)"}}>r={rm.corr.toFixed(2)}</div>
                </div>
              </div>);
            })}
          </div>
          <div style={{height:12}}/>
        </>}

        {tab==="signals"&&<>
          {conflicted&&<div style={{background:"var(--amber-dim)",border:"1px solid var(--amber)",borderRadius:8,padding:"10px 14px",marginBottom:12,fontSize:11,color:"var(--amber)",lineHeight:1.5}}>
            ⚡ <strong>Signal conflict detected.</strong> Bullish and bearish sources are both strong — higher uncertainty. Consider reducing Kelly sizing.
          </div>}
          <div style={{display:"flex",alignItems:"center",gap:8,padding:"10px 0 6px",borderBottom:"1px solid var(--border)",marginBottom:2}}>
            <span style={{fontSize:10,fontWeight:700,letterSpacing:".1em",textTransform:"uppercase",color:"var(--muted)",flex:1}}>Signal Breakdown</span>
            <div style={{display:"flex",alignItems:"center",gap:5}}>
              {pulsing.size>0&&<span style={{width:6,height:6,borderRadius:"50%",background:"var(--green)",boxShadow:"0 0 6px var(--green)",display:"inline-block"}}/>}
              <span style={{fontSize:10,color:"var(--muted)",fontFamily:"'JetBrains Mono'"}}>{pulsing.size>0?"updating…":refreshLabel}</span>
            </div>
          </div>
          {liveSignals.map((s,i)=><SignalRow key={i} sig={s} pulsing={pulsing.has(i)} prevStrength={prevStr[i]}/>)}
          <div style={{height:12}}/>
        </>}

        {tab==="sizing"&&<>
          {/* weight editor */}
          <button onClick={()=>setShowWeights(v=>!v)} style={{width:"100%",padding:"10px 14px",borderRadius:8,border:"1px solid var(--border2)",background:showWeights?"var(--surface3)":"none",color:showWeights?"var(--text)":"var(--muted)",fontSize:12,fontWeight:600,fontFamily:"inherit",cursor:"pointer",textAlign:"left",display:"flex",alignItems:"center",gap:8,marginBottom:0,transition:"all .15s"}}>
            <span>⚖</span>Signal Weight Editor
            {wChanged&&<span style={{marginLeft:"auto",fontSize:10,color:"var(--amber)",fontWeight:700}}>Custom</span>}
            <span style={{marginLeft:wChanged?"0":"auto",color:"var(--muted)",fontSize:11}}>{showWeights?"▲":"▼"}</span>
          </button>
          {showWeights&&<div style={{background:"var(--surface3)",border:"1px solid var(--border)",borderRadius:"0 0 8px 8px",padding:"14px 16px",borderTop:"none",marginBottom:14}}>
            <div style={{fontSize:11,color:"var(--muted)",marginBottom:12}}>Drag sliders to reweight sources. PP Estimate and Kelly update live.</div>
            {liveSignals.map((s,i)=>{const w=weights[i];const wc=w>1?"var(--green)":w<1?"var(--red)":"var(--muted)";return(
              <div key={i} style={{marginBottom:11}}>
                <div style={{display:"flex",alignItems:"center",gap:7,marginBottom:4}}>
                  <span style={{fontSize:11,opacity:.7}}>{s.icon}</span>
                  <span style={{fontSize:11,fontWeight:500,flex:1}}>{s.src}</span>
                  <span style={{fontFamily:"'JetBrains Mono'",fontSize:11,fontWeight:700,color:wc,minWidth:28,textAlign:"right"}}>{w.toFixed(1)}×</span>
                  {w!==1&&<button onClick={()=>setWeights(p=>({...p,[i]:1}))} style={{fontSize:9,color:"var(--muted)",background:"none",border:"none",cursor:"pointer",padding:"1px 4px"}}>reset</button>}
                </div>
                <div style={{display:"flex",alignItems:"center",gap:7}}>
                  <span style={{fontSize:9,color:"var(--muted)",minWidth:14}}>0×</span>
                  <input type="range" min="0" max="2" step="0.1" value={w} onChange={e=>setWeights(p=>({...p,[i]:parseFloat(e.target.value)}))} style={{flex:1,accentColor:"var(--green)",cursor:"pointer"}}/>
                  <span style={{fontSize:9,color:"var(--muted)",minWidth:14}}>2×</span>
                </div>
              </div>
            );})}
            {wChanged&&<button onClick={()=>setWeights(Object.fromEntries(market.signals.map((_,i)=>[i,1])))} style={{marginTop:4,padding:"5px 12px",borderRadius:6,border:"1px solid var(--border2)",background:"none",color:"var(--muted)",fontSize:11,fontFamily:"inherit",cursor:"pointer"}}>Reset all</button>}
          </div>}

          {/* Kelly */}
          <div style={{border:"1px solid var(--border2)",borderRadius:8,padding:"16px",marginTop:showWeights?0:12}}>
            <div style={{fontSize:12,fontWeight:600,marginBottom:4,display:"flex",gap:8,alignItems:"center"}}>
              ₭ Kelly Position Sizing
              {kFull>0&&<span style={{fontFamily:"'JetBrains Mono'",fontSize:11,color:"var(--green)"}}>{(kFull*100).toFixed(1)}% full Kelly</span>}
            </div>
            {kFull<=0
              ?<div style={{fontSize:12,color:"var(--red)",padding:"8px 0"}}>No edge — Kelly recommends no position at current prices.</div>
              :<>
                <div style={{fontSize:11,color:"var(--muted)",marginBottom:12}}>PP <span style={{color:"var(--green)",fontFamily:"'JetBrains Mono'"}}>{adjEst}%</span> · PM <span style={{color:"var(--violet)",fontFamily:"'JetBrains Mono'"}}>{market.market}%</span> · odds <span style={{fontFamily:"'JetBrains Mono'"}}>{b.toFixed(2)}:1</span></div>
                <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:12,background:"var(--surface2)",border:"1px solid var(--border)",borderRadius:7,padding:"7px 12px"}}>
                  <span style={{color:"var(--muted)"}}>$</span>
                  <input type="number" value={bankroll} min={10} onChange={e=>setBankroll(Math.max(0,parseInt(e.target.value)||0))} style={{flex:1,background:"none",border:"none",outline:"none",color:"var(--text)",fontSize:14,fontFamily:"'JetBrains Mono'",fontWeight:600}}/>
                  <span style={{fontSize:11,color:"var(--muted)"}}>bankroll</span>
                </div>
                <div style={{display:"grid",gridTemplateColumns:"repeat(3,1fr)",gap:8,marginBottom:12}}>
                  {[[kFull,"Full Kelly","var(--green)"],[kFull/2,"Half Kelly","var(--amber)"],[kFull/4,"Quarter","var(--muted)"]].map(([k,l,c])=>(
                    <div key={l} style={{background:"var(--surface2)",border:"1px solid var(--border)",borderRadius:7,padding:"10px 12px"}}>
                      <div style={{fontSize:9,color:"var(--muted)",fontWeight:700,textTransform:"uppercase",letterSpacing:".07em",marginBottom:4}}>{l}</div>
                      <div style={{fontFamily:"'JetBrains Mono'",fontSize:16,fontWeight:700,color:c}}>${Math.round(bankroll*k).toLocaleString()}</div>
                      <div style={{fontSize:10,color:"var(--muted)",marginTop:2}}>{(k*100).toFixed(1)}%</div>
                    </div>
                  ))}
                </div>
                <div style={{height:5,background:"var(--border)",borderRadius:3}}>
                  <div style={{height:"100%",width:`${Math.min(kFull*100,100)}%`,background:"var(--green)",borderRadius:3,transition:"width .4s",boxShadow:"0 0 8px var(--green)"}}/>
                </div>
                <div style={{fontSize:10,color:"var(--muted2)",marginTop:10,lineHeight:1.5}}>Half Kelly reduces variance while capturing ~75% of max long-run growth.</div>
              </>
            }
          </div>
          <div style={{height:20}}/>
        </>}
      </div>

      {/* footer */}
      <div style={{padding:"12px 20px",borderTop:"1px solid var(--border)",display:"flex",gap:10,flexShrink:0}}>
        <a href="https://polymarket.com" target="_blank" rel="noreferrer"
          style={{flex:1,padding:"9px",borderRadius:8,border:"1px solid var(--border2)",background:"none",color:"var(--text)",fontSize:12,fontWeight:500,textAlign:"center",textDecoration:"none",display:"block"}}>
          View on Polymarket ↗</a>
        <button onClick={()=>onTrack(market.id)} style={{flex:1,padding:"9px",borderRadius:8,border:tracked?"1px solid var(--green)":"none",
          background:tracked?"none":"var(--green)",color:tracked?"var(--green)":"#001a10",fontSize:12,fontWeight:700,cursor:"pointer",transition:"all .2s"}}>
          {tracked?"✓ Tracking":"Track market"}
        </button>
      </div>
    </div>
  );
};

Object.assign(window,{EdgeGauge,ProbabilityChart,SignalRow,DetailPanel});
