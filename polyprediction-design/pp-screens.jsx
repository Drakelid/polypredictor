// pp-screens.jsx — Watchlist, Backtest, Correlation, Settings, EmptyState
const {useState} = React;

/* ── EMPTY STATE ── */
const EmptyState = ({icon,title,sub}) => (
  <div style={{display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",flex:1,padding:"60px 24px",gap:12,color:"var(--muted)",minHeight:200}}>
    <div style={{fontSize:36,opacity:.25}}>{icon}</div>
    <div style={{fontSize:14,fontWeight:600,color:"var(--text)"}}>{title}</div>
    {sub&&<div style={{fontSize:12,color:"var(--muted)",textAlign:"center",maxWidth:280,lineHeight:1.5}}>{sub}</div>}
  </div>
);

/* ── WATCHLIST SCREEN ── */
const WatchlistScreen = ({onSelect,trackedIds}) => {
  const positions=WATCHLIST_POSITIONS.map(p=>({...p,market:MARKETS.find(m=>m.id===p.marketId)}));
  if(!positions.length) return <div style={{flex:1,display:"flex"}}><EmptyState icon="★" title="No tracked markets" sub="Click 'Track market' on any market detail panel to add it here."/></div>;
  const totalEdge=positions.reduce((a,p)=>a+(p.market.estimate-p.market.market),0);
  const totalPnL=positions.reduce((a,{market:m,entryPM,shares,direction})=>a+(m.market-entryPM)/100*shares*(direction==="YES"?1:-1),0);
  return (
    <div style={{flex:1,overflowY:"auto",padding:"20px 24px"}}>
      <div style={{display:"grid",gridTemplateColumns:"repeat(3,1fr)",gap:12,marginBottom:22}}>
        {[
          {label:"Tracked positions",val:positions.length,col:"var(--text)"},
          {label:"Total edge exposure",val:`${totalEdge>0?"+":""}${totalEdge}pp`,col:"var(--green)"},
          {label:"Unrealized P&L",val:`${totalPnL>=0?"+":""}$${Math.abs(totalPnL).toFixed(0)}`,col:totalPnL>=0?"var(--green)":"var(--red)"},
        ].map((s,i)=>(
          <div key={i} style={{background:"var(--surface)",border:"1px solid var(--border)",borderRadius:8,padding:"12px 16px"}}>
            <div style={{fontSize:10,color:"var(--muted)",fontWeight:700,textTransform:"uppercase",letterSpacing:".08em",marginBottom:6}}>{s.label}</div>
            <div style={{fontFamily:"'JetBrains Mono'",fontSize:20,fontWeight:600,color:s.col}}>{s.val}</div>
          </div>
        ))}
      </div>
      <div style={{fontSize:11,fontWeight:700,letterSpacing:".09em",textTransform:"uppercase",color:"var(--muted)",marginBottom:10}}>Positions</div>
      <div style={{display:"flex",flexDirection:"column",gap:8}}>
        {positions.map(({market:m,entryPM,entryEst,shares,direction})=>{
          const curEdge=m.estimate-m.market,entryEdge=entryEst-entryPM,delta=curEdge-entryEdge;
          const pnl=(m.market-entryPM)/100*shares*(direction==="YES"?1:-1);
          const {tier}=edgeMeta(curEdge),col=edgeCol(tier,curEdge);
          return(
            <div key={m.id} onClick={()=>onSelect(m)} style={{background:"var(--surface)",border:"1px solid var(--border)",borderRadius:10,padding:"14px 18px",cursor:"pointer",transition:"border-color .15s"}}
              onMouseEnter={e=>e.currentTarget.style.borderColor="var(--border2)"}
              onMouseLeave={e=>e.currentTarget.style.borderColor="var(--border)"}>
              <div style={{display:"flex",gap:10,alignItems:"flex-start",marginBottom:12}}>
                <span style={{background:"var(--surface3)",color:"var(--muted)",fontSize:10,fontWeight:700,letterSpacing:".08em",padding:"2px 7px",borderRadius:4}}>{m.category}</span>
                <span style={{fontSize:12,fontWeight:500,flex:1,lineHeight:1.4}}>{m.question}</span>
                <span style={{background:direction==="YES"?"var(--green-dim)":"var(--red-dim)",color:direction==="YES"?"var(--green)":"var(--red)",fontSize:10,fontWeight:700,padding:"2px 8px",borderRadius:4,flexShrink:0}}>{direction}</span>
              </div>
              <div style={{display:"grid",gridTemplateColumns:"repeat(5,1fr)",gap:10}}>
                {[{label:"Entry PM",val:`${entryPM}%`,col:"var(--muted)"},{label:"Current PM",val:`${m.market}%`,col:"var(--violet)"},{label:"PP Est.",val:`${m.estimate}%`,col:"var(--green)"},
                  {label:"Edge Δ",val:`${delta>0?"+":""}${delta}pp`,col:delta>=0?"var(--green)":"var(--red)"},
                  {label:"P&L",val:`${pnl>=0?"+":""}$${Math.abs(pnl).toFixed(0)}`,col:pnl>=0?"var(--green)":"var(--red)"}].map((s,i)=>(
                  <div key={i}><div style={{fontSize:9,color:"var(--muted)",marginBottom:2}}>{s.label}</div><div style={{fontFamily:"'JetBrains Mono'",fontSize:12,fontWeight:600,color:s.col}}>{s.val}</div></div>
                ))}
              </div>
            </div>
          );
        })}
      </div>
      <div style={{height:20}}/>
    </div>
  );
};

/* ── BACKTEST SCREEN ── */
const BacktestScreen = () => {
  const [catFilter,setCatFilter]=useState("All");
  const correct=BACKTEST.filter(b=>(b.pp>50&&b.outcome)||(b.pp<50&&!b.outcome)).length;
  const accuracy=Math.round(correct/BACKTEST.length*100);
  const avgEdge=Math.round(BACKTEST.reduce((a,b)=>a+Math.abs(b.pp-b.pm),0)/BACKTEST.length*10)/10;
  const brier=(BACKTEST.reduce((a,b)=>a+Math.pow((b.pp/100)-(b.outcome?1:0),2),0)/BACKTEST.length).toFixed(3);
  const cats=["All",...new Set(BACKTEST.map(b=>b.cat))];
  const filtered=catFilter==="All"?BACKTEST:BACKTEST.filter(b=>b.cat===catFilter);
  const CW=400,CH=150,pad={t:14,r:10,b:30,l:34};
  const IW=CW-pad.l-pad.r,IH=CH-pad.t-pad.b;
  const bw=IW/CALIB_BUCKETS.length*.55;
  const cx=i=>pad.l+(i+.5)*(IW/CALIB_BUCKETS.length),cy=v=>pad.t+IH*(1-v);
  return (
    <div style={{flex:1,overflowY:"auto",padding:"20px 24px"}}>
      <div style={{display:"grid",gridTemplateColumns:"repeat(4,1fr)",gap:12,marginBottom:20}}>
        {[{label:"Directional accuracy",val:`${accuracy}%`,sub:`${correct}/${BACKTEST.length} correct`,col:"var(--green)"},
          {label:"Avg edge captured",val:`${avgEdge}pp`,sub:"vs PM at prediction",col:"var(--violet)"},
          {label:"Brier score",val:brier,sub:"lower = better",col:"var(--amber)"},
          {label:"Markets analyzed",val:BACKTEST.length,sub:"last 90 days",col:"var(--text)"}].map((s,i)=>(
          <div key={i} style={{background:"var(--surface)",border:"1px solid var(--border)",borderRadius:8,padding:"12px 14px"}}>
            <div style={{fontSize:10,color:"var(--muted)",fontWeight:700,textTransform:"uppercase",letterSpacing:".08em",marginBottom:5}}>{s.label}</div>
            <div style={{fontFamily:"'JetBrains Mono'",fontSize:20,fontWeight:600,color:s.col}}>{s.val}</div>
            <div style={{fontSize:10,color:"var(--muted)",marginTop:3}}>{s.sub}</div>
          </div>
        ))}
      </div>

      {/* signal source accuracy */}
      <div style={{background:"var(--surface)",border:"1px solid var(--border)",borderRadius:10,padding:"16px 20px",marginBottom:16}}>
        <div style={{fontSize:10,fontWeight:700,letterSpacing:".09em",textTransform:"uppercase",color:"var(--muted)",marginBottom:14}}>Signal Source Accuracy — historical performance</div>
        <div style={{display:"flex",flexDirection:"column",gap:10}}>
          {SIGNAL_ACCURACY.map((s,i)=>{
            const col=s.accuracy>=.75?"var(--green)":s.accuracy>=.65?"var(--amber)":"var(--muted)";
            return(<div key={i} style={{display:"flex",alignItems:"center",gap:10}}>
              <span style={{fontSize:12,width:18,flexShrink:0}}>{s.icon}</span>
              <span style={{fontSize:11,width:140,flexShrink:0}}>{s.src}</span>
              <div style={{flex:1,height:5,background:"var(--border)",borderRadius:3}}>
                <div style={{height:"100%",width:`${s.accuracy*100}%`,background:col,borderRadius:3,transition:"width .6s ease"}}/>
              </div>
              <span style={{fontFamily:"'JetBrains Mono'",fontSize:11,fontWeight:600,color:col,minWidth:36,textAlign:"right"}}>{Math.round(s.accuracy*100)}%</span>
              <span style={{fontSize:10,color:"var(--muted)",minWidth:44}}>{s.correct}/{s.total}</span>
            </div>);
          })}
        </div>
      </div>

      {/* calibration */}
      <div style={{background:"var(--surface)",border:"1px solid var(--border)",borderRadius:10,padding:"16px 20px",marginBottom:16}}>
        <div style={{fontSize:10,fontWeight:700,letterSpacing:".09em",textTransform:"uppercase",color:"var(--muted)",marginBottom:4}}>Calibration Chart</div>
        <div style={{fontSize:11,color:"var(--muted)",marginBottom:12}}>Bars = actual resolution rate. Dots = PP predicted. Dashed diagonal = perfect calibration.</div>
        <svg width="100%" viewBox={`0 0 ${CW} ${CH}`} style={{overflow:"visible"}}>
          {[0,.25,.5,.75,1].map(v=>(<g key={v}>
            <line x1={pad.l} y1={cy(v)} x2={CW-pad.r} y2={cy(v)} stroke="var(--border)" strokeWidth="1"/>
            <text x={pad.l-5} y={cy(v)+4} textAnchor="end" fontSize="8" fill="var(--muted)" fontFamily="JetBrains Mono">{Math.round(v*100)}%</text>
          </g>))}
          <line x1={pad.l} y1={cy(0)} x2={CW-pad.r} y2={cy(1)} stroke="var(--border2)" strokeWidth="1.5" strokeDasharray="5,4"/>
          {CALIB_BUCKETS.map(([ppC,actual,count],i)=>{
            const predicted=ppC/100;
            const bc=Math.abs(actual-predicted)<.08?"var(--green)":actual<predicted?"var(--red)":"oklch(0.72 0.18 290)";
            return(<g key={i}>
              <rect x={cx(i)-bw/2} y={cy(actual)} width={bw} height={IH*actual} fill={bc} opacity=".7" rx="3"/>
              <circle cx={cx(i)} cy={cy(predicted)} r="4" fill="white" opacity=".4" stroke={bc} strokeWidth="1.5"/>
              <text x={cx(i)} y={CH-pad.b+12} textAnchor="middle" fontSize="8" fill="var(--muted)" fontFamily="JetBrains Mono">{ppC}%</text>
              <text x={cx(i)} y={CH-pad.b+22} textAnchor="middle" fontSize="7" fill="var(--muted2)" fontFamily="Space Grotesk">n={count}</text>
            </g>);
          })}
        </svg>
      </div>

      {/* table */}
      <div style={{background:"var(--surface)",border:"1px solid var(--border)",borderRadius:10,overflow:"hidden"}}>
        <div style={{padding:"13px 18px",borderBottom:"1px solid var(--border)",display:"flex",alignItems:"center",gap:8,flexWrap:"wrap"}}>
          <span style={{fontSize:12,fontWeight:600,flex:1}}>Resolved Markets</span>
          <div style={{display:"flex",gap:4,flexWrap:"wrap"}}>
            {cats.slice(0,8).map(c=>(
              <button key={c} onClick={()=>setCatFilter(c)} style={{padding:"2px 8px",borderRadius:5,border:"1px solid",
                borderColor:catFilter===c?"var(--green)":"var(--border)",background:catFilter===c?"var(--green-dim)":"none",
                color:catFilter===c?"var(--green)":"var(--muted)",fontSize:11,fontFamily:"inherit",cursor:"pointer"}}>{c}</button>
            ))}
          </div>
        </div>
        {filtered.length===0?<EmptyState icon="◈" title="No markets" sub="No resolved markets for this category."/>
        :<div style={{overflowX:"auto"}}>
          <table style={{width:"100%",borderCollapse:"collapse",fontSize:12}}>
            <thead><tr style={{borderBottom:"1px solid var(--border)"}}>
              {["Market","Cat","PP","PM","Edge","Result","✓?","Days"].map(h=>(
                <th key={h} style={{padding:"8px 12px",textAlign:"left",fontSize:10,fontWeight:700,letterSpacing:".07em",textTransform:"uppercase",color:"var(--muted)",whiteSpace:"nowrap"}}>{h}</th>
              ))}
            </tr></thead>
            <tbody>
              {filtered.map((b,i)=>{
                const ok=(b.pp>50&&b.outcome)||(b.pp<50&&!b.outcome),e=b.pp-b.pm;
                return(<tr key={i} style={{borderBottom:"1px solid var(--border)",transition:"background .12s"}}
                  onMouseEnter={e=>e.currentTarget.style.background="var(--surface2)"}
                  onMouseLeave={e=>e.currentTarget.style.background="none"}>
                  <td style={{padding:"8px 12px",color:"var(--text)",maxWidth:220}}><div style={{overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}} title={b.q}>{b.q}</div></td>
                  <td style={{padding:"8px 12px"}}><span style={{background:"var(--surface3)",color:"var(--muted)",fontSize:10,fontWeight:700,padding:"1px 6px",borderRadius:3}}>{b.cat}</span></td>
                  <td style={{padding:"8px 12px",fontFamily:"'JetBrains Mono'",color:"var(--green)"}}>{b.pp}%</td>
                  <td style={{padding:"8px 12px",fontFamily:"'JetBrains Mono'",color:"var(--violet)"}}>{b.pm}%</td>
                  <td style={{padding:"8px 12px",fontFamily:"'JetBrains Mono'",color:e>0?"var(--green)":"var(--red)"}}>{e>0?"+":""}{e}pp</td>
                  <td style={{padding:"8px 12px",fontWeight:700,color:b.outcome?"var(--green)":"var(--red)"}}>{b.outcome?"YES":"NO"}</td>
                  <td style={{padding:"8px 12px",fontWeight:700,color:ok?"var(--green)":"var(--red)"}}>{ok?"✓":"✗"}</td>
                  <td style={{padding:"8px 12px",color:"var(--muted)",fontFamily:"'JetBrains Mono'"}}>{b.days}d</td>
                </tr>);
              })}
            </tbody>
          </table>
        </div>}
      </div>
      <div style={{height:24}}/>
    </div>
  );
};

/* ── CORRELATION SCREEN ── */
const CorrelationScreen = () => {
  const [pinned,setPinned]=useState(null);
  const cos=(a,b)=>{const d=a.reduce((s,ai,i)=>s+ai*b[i],0),ma=Math.sqrt(a.reduce((s,ai)=>s+ai*ai,0)),mb=Math.sqrt(b.reduce((s,bi)=>s+bi*bi,0));return(ma*mb)===0?0:d/(ma*mb);};
  const vecs=MARKETS.map(m=>m.signals.map(s=>s.dir*s.strength));
  const mtx=MARKETS.map((_,i)=>MARKETS.map((_,j)=>cos(vecs[i],vecs[j])));
  const cellBg=v=>v>.6?`oklch(0.75 0.20 155 / ${.15+v*.35})`:v<-.1?`oklch(0.68 0.20 25 / ${.15+Math.abs(v)*.5})`:`oklch(0.72 0.18 250 / 0.06)`;
  const pair=pinned&&pinned[0]!==pinned[1]?{a:MARKETS[pinned[0]],b:MARKETS[pinned[1]],corr:mtx[pinned[0]][pinned[1]]}:null;
  return (
    <div style={{flex:1,overflowY:"auto",padding:"20px 24px"}}>
      <div style={{fontSize:11,color:"var(--muted)",marginBottom:18,lineHeight:1.6,maxWidth:520}}>
        Correlation is computed from signal vector alignment across all 6 sources. <strong style={{color:"var(--text)"}}>Click a cell to pin the pair breakdown.</strong> High correlation = shared signal exposure — opening both amplifies risk.
      </div>
      <div style={{background:"var(--surface)",border:"1px solid var(--border)",borderRadius:10,padding:"18px 20px",marginBottom:14,overflowX:"auto"}}>
        <div style={{fontSize:10,fontWeight:700,letterSpacing:".09em",textTransform:"uppercase",color:"var(--muted)",marginBottom:12}}>Signal Correlation Matrix</div>
        <div style={{display:"grid",gridTemplateColumns:`70px repeat(${MARKETS.length},1fr)`,gap:4,minWidth:440}}>
          <div/>
          {MARKETS.map(m=><div key={m.id} style={{textAlign:"center",fontSize:10,fontWeight:700,color:"var(--muted)",padding:"2px"}}>{m.category}</div>)}
          {MARKETS.map((mR,i)=>(
            <React.Fragment key={i}>
              <div style={{fontSize:10,fontWeight:600,color:"var(--muted)",display:"flex",alignItems:"center"}}>{mR.category}</div>
              {MARKETS.map((_,j)=>{
                const v=mtx[i][j],diag=i===j,isPinned=pinned&&pinned[0]===i&&pinned[1]===j;
                const col=v>.3?"var(--green)":v<-.05?"var(--red)":"var(--muted)";
                return(<div key={j} onClick={()=>!diag&&setPinned(isPinned?null:[i,j])}
                  style={{background:diag?"var(--surface3)":cellBg(v),borderRadius:6,padding:"10px 4px",textAlign:"center",cursor:diag?"default":"pointer",
                    border:`1px solid ${isPinned?"var(--green)":"transparent"}`,boxShadow:isPinned?"0 0 0 1px var(--green)":"none",transition:"all .12s"}}>
                  <div style={{fontFamily:"'JetBrains Mono'",fontSize:11,fontWeight:600,color:diag?"var(--muted)":col}}>{diag?"—":v.toFixed(2)}</div>
                </div>);
              })}
            </React.Fragment>
          ))}
        </div>
        <div style={{display:"flex",gap:12,marginTop:12,alignItems:"center",flexWrap:"wrap"}}>
          {[["var(--green)","Aligned (>0.6)"],["var(--muted)","Uncorrelated"],["var(--red)","Opposed (<-0.1)"]].map(([c,l])=>(
            <div key={l} style={{display:"flex",alignItems:"center",gap:4}}><div style={{width:10,height:10,borderRadius:2,background:c,opacity:.7}}/><span style={{fontSize:10,color:"var(--muted)"}}>{l}</span></div>
          ))}
          {pinned&&<button onClick={()=>setPinned(null)} style={{marginLeft:"auto",fontSize:10,color:"var(--muted)",background:"none",border:"none",cursor:"pointer"}}>✕ Clear</button>}
        </div>
      </div>

      {pair&&(
        <div style={{background:"var(--surface)",border:"1px solid var(--border2)",borderRadius:10,padding:"16px 20px",marginBottom:14}}>
          <div style={{fontSize:12,fontWeight:600,marginBottom:10,display:"flex",alignItems:"center",gap:10,flexWrap:"wrap"}}>
            {pair.a.category} × {pair.b.category}
            <span style={{fontFamily:"'JetBrains Mono'",fontSize:12,color:pair.corr>.3?"var(--green)":pair.corr<0?"var(--red)":"var(--muted)"}}>r = {pair.corr.toFixed(3)}</span>
            <span style={{fontSize:11,color:"var(--muted)"}}>{pair.corr>.6?"Highly correlated — similar exposure":pair.corr<0?"Inversely correlated — natural hedge":"Low correlation"}</span>
          </div>
          {pair.a.signals.map((sA,i)=>{
            const sB=pair.b.signals[i],aligned=sA.dir===sB.dir&&sA.dir!==0,opposed=sA.dir!==0&&sB.dir!==0&&sA.dir!==sB.dir;
            return(<div key={i} style={{display:"flex",alignItems:"center",gap:10,padding:"7px 10px",borderRadius:6,marginBottom:4,background:aligned?"var(--green-dim)":opposed?"var(--red-dim)":"transparent"}}>
              <span style={{fontSize:11}}>{sA.icon}</span>
              <span style={{fontSize:11,flex:1}}>{sA.src}</span>
              <span style={{fontFamily:"'JetBrains Mono'",fontSize:10,color:sA.dir>0?"var(--green)":sA.dir<0?"var(--red)":"var(--muted)"}}>{sA.dir>0?"↑":sA.dir<0?"↓":"→"} {Math.round(sA.strength*100)}%</span>
              <span style={{color:"var(--muted)",fontSize:10}}>vs</span>
              <span style={{fontFamily:"'JetBrains Mono'",fontSize:10,color:sB.dir>0?"var(--green)":sB.dir<0?"var(--red)":"var(--muted)"}}>{sB.dir>0?"↑":sB.dir<0?"↓":"→"} {Math.round(sB.strength*100)}%</span>
              <span style={{fontSize:10,fontWeight:700,minWidth:52,textAlign:"right",color:aligned?"var(--green)":opposed?"var(--red)":"var(--muted)"}}>{aligned?"aligned":opposed?"opposed":"neutral"}</span>
            </div>);
          })}
        </div>
      )}

      <div style={{background:"var(--surface)",border:"1px solid var(--border)",borderRadius:10,overflow:"hidden"}}>
        <div style={{padding:"13px 18px",borderBottom:"1px solid var(--border)",fontSize:12,fontWeight:600}}>Signal Exposure by Market</div>
        <div style={{overflowX:"auto"}}>
          <table style={{width:"100%",borderCollapse:"collapse"}}>
            <thead><tr style={{borderBottom:"1px solid var(--border)"}}>
              <th style={{padding:"8px 14px",textAlign:"left",fontSize:10,fontWeight:700,textTransform:"uppercase",letterSpacing:".07em",color:"var(--muted)",minWidth:120}}>Source</th>
              {MARKETS.map(m=><th key={m.id} style={{padding:"8px 10px",textAlign:"center",fontSize:10,fontWeight:700,textTransform:"uppercase",letterSpacing:".07em",color:"var(--muted)"}}>{m.category}</th>)}
            </tr></thead>
            <tbody>
              {MARKETS[0].signals.map((_,si)=>(
                <tr key={si} style={{borderBottom:"1px solid var(--border)"}}>
                  <td style={{padding:"8px 14px"}}><div style={{display:"flex",alignItems:"center",gap:6}}><span>{MARKETS[0].signals[si].icon}</span><span style={{fontSize:11}}>{MARKETS[0].signals[si].src}</span></div></td>
                  {MARKETS.map(m=>{const s=m.signals[si],c=s.dir>0?"var(--green)":s.dir<0?"var(--red)":"var(--muted)";return(
                    <td key={m.id} style={{padding:"8px 10px",textAlign:"center"}}>
                      <div style={{display:"flex",flexDirection:"column",alignItems:"center",gap:3}}>
                        <span style={{color:c,fontWeight:700}}>{s.dir>0?"↑":s.dir<0?"↓":"→"}</span>
                        <div style={{width:28,height:3,background:"var(--border)",borderRadius:2}}><div style={{height:"100%",width:`${s.strength*100}%`,background:c,borderRadius:2,opacity:.8}}/></div>
                      </div>
                    </td>
                  );})}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <div style={{height:24}}/>
    </div>
  );
};

/* ── SETTINGS SCREEN ── */
const SettingsScreen = ({settings,onUpdate}) => {
  const SR=({label,sub,children})=>(
    <div style={{display:"flex",alignItems:"center",gap:16,padding:"14px 0",borderBottom:"1px solid var(--border)"}}>
      <div style={{flex:1}}><div style={{fontSize:13,fontWeight:500}}>{label}</div>{sub&&<div style={{fontSize:11,color:"var(--muted)",marginTop:2}}>{sub}</div>}</div>
      {children}
    </div>
  );
  const Tog=({val,onChange})=>(
    <div onClick={()=>onChange(!val)} style={{width:36,height:20,borderRadius:10,background:val?"var(--green)":"var(--border2)",cursor:"pointer",position:"relative",transition:"background .2s",flexShrink:0}}>
      <div style={{position:"absolute",top:2,left:val?18:2,width:16,height:16,borderRadius:"50%",background:"white",transition:"left .2s",boxShadow:"0 1px 3px #0004"}}/>
    </div>
  );
  const Sec=({title,children})=>(
    <div style={{marginBottom:28}}>
      <div style={{fontSize:10,fontWeight:700,letterSpacing:".1em",textTransform:"uppercase",color:"var(--muted)",marginBottom:2,paddingBottom:8,borderBottom:"1px solid var(--border)"}}>{title}</div>
      {children}
    </div>
  );
  return(
    <div style={{flex:1,overflowY:"auto",padding:"24px"}}>
      <div style={{maxWidth:560}}>
        <Sec title="General">
          <SR label="Default bankroll" sub="Starting value for Kelly position sizing">
            <div style={{display:"flex",alignItems:"center",gap:5,background:"var(--surface2)",border:"1px solid var(--border)",borderRadius:7,padding:"5px 12px"}}>
              <span style={{color:"var(--muted)",fontFamily:"'JetBrains Mono'",fontSize:13}}>$</span>
              <input type="number" value={settings.bankroll} min={10} onChange={e=>onUpdate({bankroll:Math.max(0,parseInt(e.target.value)||0)})} style={{width:80,background:"none",border:"none",outline:"none",color:"var(--text)",fontSize:14,fontFamily:"'JetBrains Mono'",fontWeight:600}}/>
            </div>
          </SR>
          <SR label="Interface theme" sub="Dark or light mode">
            <div style={{display:"flex",gap:6}}>
              {["dark","light"].map(t=>(
                <button key={t} onClick={()=>onUpdate({theme:t})} style={{padding:"5px 14px",borderRadius:6,border:"1px solid",fontFamily:"inherit",fontSize:12,cursor:"pointer",
                  borderColor:settings.theme===t?"var(--green)":"var(--border)",background:settings.theme===t?"var(--green-dim)":"none",color:settings.theme===t?"var(--green)":"var(--muted)"}}>
                  {t==="dark"?"◑ Dark":"○ Light"}
                </button>
              ))}
            </div>
          </SR>
          <SR label="Accent color" sub="Primary highlight color throughout the app">
            <div style={{display:"flex",gap:8}}>
              {[["mint","oklch(0.75 0.20 155)"],["cyan","oklch(0.72 0.20 210)"],["violet","oklch(0.72 0.18 290)"]].map(([name,c])=>(
                <div key={name} onClick={()=>onUpdate({accent:name})} title={name}
                  style={{width:22,height:22,borderRadius:"50%",background:c,cursor:"pointer",
                    boxShadow:settings.accent===name?`0 0 0 2px var(--bg), 0 0 0 4px ${c}`:"none",transition:"box-shadow .15s"}}/>
              ))}
            </div>
          </SR>
          <SR label="Signal refresh interval" sub="How often live signal data updates">
            <div style={{display:"flex",alignItems:"center",gap:8}}>
              <input type="range" min={5} max={30} step={5} value={settings.refreshInterval} onChange={e=>onUpdate({refreshInterval:+e.target.value})} style={{width:90,accentColor:"var(--green)"}}/>
              <span style={{fontFamily:"'JetBrains Mono'",fontSize:12,color:"var(--text)",minWidth:32}}>{settings.refreshInterval}s</span>
            </div>
          </SR>
        </Sec>
        <Sec title="Alert Preferences">
          {[{key:"alertEdge",label:"Strong edge detected",sub:"PP edge ≥8pp on any market"},
            {key:"alertDiverge",label:"Signal divergence",sub:"Source changes direction significantly"},
            {key:"alertMove",label:"PM price moved",sub:"Polymarket price moves >5pp"},
            {key:"alertUpdate",label:"PP estimate updated",sub:"Model revises probability"},
          ].map(({key,label,sub})=>(
            <SR key={key} label={label} sub={sub}><Tog val={settings[key]} onChange={v=>onUpdate({[key]:v})}/></SR>
          ))}
        </Sec>
        <Sec title="About">
          <div style={{padding:"12px 0",fontSize:12,color:"var(--muted)",lineHeight:1.8}}>
            PolyPredictor v0.5 · Signal aggregation for Polymarket decisions<br/>
            6 active sources · Signals refresh every {settings.refreshInterval}s<br/>
            <span style={{color:"var(--muted2)"}}>Research tool only. Not financial advice.</span>
          </div>
        </Sec>
      </div>
    </div>
  );
};

Object.assign(window,{EmptyState,WatchlistScreen,BacktestScreen,CorrelationScreen,SettingsScreen});
