"""Upgrade the recovered galaxy design without replacing its visual language."""
from pathlib import Path
import json
ROOT=Path(__file__).resolve().parents[1]
source=ROOT/'ano/assets/Galaxy-Watch-Original.html'
s=source.read_text(encoding='utf-8')
def replace(old,new):
    global s
    assert old in s,old[:90]
    s=s.replace(old,new)

replace('<title>ano.watch</title>','<title>Ano / Galaxy Watch</title>')
# Keep the reference's warm black and parchment-white web. Colour belongs to the crawler and its tags.
begin=s.index('function colourOf(s)');end=s.index('// ── Camera',begin)
s=s[:begin]+"function colourOf(s){if(!s)return INK;if(s.state==='stale')return GREY;return data&&crawler.phase==='read'&&data.stations[crawler.at]?.id===s.id?mix(INK,TEAL,.65):s.state==='healthy'?INK:mix(INK,GREY,.4)}\n\n"+s[end:]
replace('const dpr=window.devicePixelRatio||1','const dpr=Math.min(2,window.devicePixelRatio||1)')
replace('bloomC.width=W/2;bloomC.height=H/2;bctx.setTransform(.5,0,0,.5,0,0)',
        'bloomC.width=W;bloomC.height=H;bctx.setTransform(1,0,0,1,0,0)')
replace('#bloom{filter:blur(7px);opacity:.75', '#bloom{filter:blur(4px);opacity:.6')
replace('let S=1;','let S=1;const reduced=matchMedia("(prefers-reduced-motion: reduce)");let paused=reduced.matches,dirty=true,quality="balanced",frozen=0;')
replace('for(let k=0;k<640;k++)','for(let k=0;k<400;k++)')
replace('.slice(0,1).forEach','.slice(0,2).forEach')
replace('for(let k=0;k<170;k++)','for(let k=0;k<85;k++)')
replace('for(let k=0;k<1800;k++)','for(let k=0;k<650;k++)')
replace('Math.cos(a)*780','Math.cos(a)*300')
replace('Math.sin(a)*330','Math.sin(a)*200')
replace('Math.sin(i*2.4+.6)*270','Math.sin(i*2.4+.6)*155')
replace('Math.pow(rnd(),1.5)*200','Math.pow(rnd(),.85)*260')
replace('CELL=42','CELL=64')
replace('near(p,30)','near(p,45)')
replace('F:900,D:600','F:1100,D:300')
replace('[[.03,.4],[.07,.55],[.14,.8]]','[[.05,.4],[.10,.55],[.20,.7]]')
replace('const span=2400,gap=120','const span=1800,gap=160')
replace('if(!crawler.started){crawler.p=', 'crawler.at=Math.min(crawler.at,lobes.length-1);crawler.target=Math.min(crawler.target,lobes.length-1);if(!crawler.started){crawler.p=')
replace(".filter(r=>data.stations[i]&&r.station===data.stations[i].id)",".filter(r=>data.stations[i]&&r.station===data.stations[i].id)")
replace("else facts.push(['fixed',r.what,'g'])", "else facts.push(['recovery evidence',r.what,'g'])")
replace("['repair',s.repair||'supervisor watching','g']", "['recovery',s.repair||'waiting for verified recovery','y']")
replace("mend=crawler.at===g.i&&reading?Math.min(1,(performance.now()-crawler.since)/12000):0", "mend=0")
replace("(.4+.25*Math.sin(t*9+g.i))", "(.32+.06*Math.sin(t*.8+g.i))")
replace("(broken(s)?.7+.3*Math.sin(t*5+i):1)", "(broken(s)?.78:1)")
replace("['1 needs you',PINK]", "['attention',PINK]") if "['1 needs you',PINK]" in s else None
replace("needs===1?'1 needs you':needs+' need you'", "needs===1?'1 under observation':needs+' under observation'")
replace("?['fixing',GREEN]", "?['verified recovery',GREEN]")
replace("'no signal':'needs attention'", "'telemetry stale':'under observation'")
replace('R=14*bs','R=18*bs')
replace("ctx.fillStyle=broken(s)?'#ff4f8b':s&&s.active?'#e8d44d':'#ff4f8b'", "ctx.fillStyle='#ff4f8b'")
replace('ctx.lineWidth=1.3*bs','ctx.lineWidth=.8*bs')
replace('busy?60000:broken(s)||repairsAt(crawler.at).length?14000:10000',
        'busy?12000:broken(s)||repairsAt(crawler.at).length?6500:4500')
replace('length:8},(_,k)','length:16},(_,k)')
replace('leg.k<4?-1:1,i=leg.k%4,a=[40,72,108,142][i]', 'leg.k<8?-1:1,i=leg.k%8,a=[25,43,61,79,101,119,137,155][i]')
replace('col=p.lit>.05?GREEN:', 'col=p.lit>.05?TEAL:')
replace("v.length>40?v.slice(0,39)+'…':v", "v.length>26?v.slice(0,25)+'…':v")
begin=s.index('function factsFor(i)');end=s.index('// Facts hang',begin)
s=s[:begin]+'''function factsFor(i){const s=data.stations[i],facts=[],job=repairsAt(i).find(r=>r.status==='working');
 if(job)facts.push(['recovering',job.what,'g']);else if(broken(s))facts.push(['watch',s.state==='stale'?'stale evidence':s.state,'p']);else if(s.active)facts.push(['live',String(s.headline),'y']);
 for(const [k,v]of s.details)facts.push([k.toLowerCase(),String(v),'w']);
 return facts.slice(0,4).map(([n,v,k])=>[n,v.length>22?v.slice(0,21)+'…':v,severity(n,v,k)])}
''' +s[end:]
replace("side,row:r,rows:col.length,u:0", "side,row:r,rows:col.length,angle:(side>0?-1.0:2.1)+r*.82,u:0")
replace("let tx=tn.side>0?body.x+OFF:body.x-OFF-w,ty=body.y-stack/2+tn.row*(TH+GAP);", "let tx=body.x+Math.cos(tn.angle)*(100+tn.row*20)-(tn.side<0?w:0),ty=body.y+Math.sin(tn.angle)*(95+tn.row*20);" )
replace("const col=s.state==='healthy'?(s.active?'#e8d44d':'#5ee08f'):'#ff4f8b'", "const col=s.state==='healthy'?(s.active?'#e8d44d':'#5ee08f'):'#e7ab70'")
replace("function frame(ms){requestAnimationFrame(frame);if(lastT!==null&&ms-lastT<33)return;const dt=lastT===null?0:Math.min(.1,(ms-lastT)/1000);lastT=ms;", "function frame(ms){requestAnimationFrame(frame);if(document.hidden)return;if(paused&&!dirty)return;const interval=paused?1000:quality==='economy'?83:42;if(lastT!==null&&ms-lastT<interval)return;const dt=paused||lastT===null?0:Math.min(.1,(ms-lastT)/1000);lastT=ms;dirty=false;if(!paused)frozen=ms;")
replace("const t=ms/1000,st=data.stations;", "const t=frozen/1000,st=data.stations;")
replace('step(dt);steer(dt);','if(!paused){step(dt);steer(dt);}')
replace('if(!paused){step(dt);steer(dt);}', 'if(!paused&&Date.now()-data.received<45000){step(dt);steer(dt);}')
replace("crawler.phase==='read'&&(broken(s)||busy)&&crawler.silk.length", "crawler.phase==='read'&&busy&&crawler.silk.length")
replace(' floorGrid();','')
begin=s.index(" // the crawler's shadow on the floor");end=s.index(' // trail and repair silk',begin)
s=s[:begin]+s[end:]
begin=s.index(" // Ano's core at the heart of the cloud");end=s.index(' // Fact tags',begin)
s=s[:begin]+" const jobs=reading?repairsAt(crawler.at):[];\n"+s[end:]

# Status and motion controls stay in the margins; the galaxy remains the interface.
replace('</style>', '''
#watchHud{position:fixed;left:5%;top:5%;color:#d2dde9;font:11px Consolas,monospace;letter-spacing:2px;pointer-events:none;text-shadow:0 1px 8px #000}#watchHud small{display:block;margin-top:9px;letter-spacing:1px;color:#8292a5;font-size:10px}#defense{position:fixed;bottom:6%;left:5%;max-width:65%;font:11px Consolas,monospace;color:#8ba79f;line-height:1.7;text-shadow:0 1px 8px #000;pointer-events:none}#defense b{font-weight:400;color:#b5d8c9;letter-spacing:2px}#defense span{display:block;color:#8394a5;font-size:10px}#watchControls{position:fixed;bottom:6%;right:5%;display:flex;gap:8px}#watchControls button{background:#08121bce;color:#a9bfce;border:1px solid #29404d;border-radius:4px;font:10px Consolas,monospace;padding:9px 12px;cursor:pointer}#watchControls button:hover{border-color:#8bb8c9}#help{position:fixed;right:5%;top:8%;width:min(350px,85vw);padding:20px;background:#08121bf5;border:1px solid #365062;color:#b6c6d5;font:12px/1.8 Consolas,monospace;box-shadow:0 15px 50px #0009}#help[hidden]{display:none}#help b{color:#d6e4f1;font-weight:400}#card{border-radius:8px;background:linear-gradient(125deg,#10202bf5,#07101af7);border-color:#36505d;box-shadow:0 18px 60px #000b,inset 0 1px #a2c8dd12;max-height:520px;overflow:auto}#card td:first-child{color:#8b9eae}#card .role{color:#8b9eae}
@media(max-width:600px){#watchHud{top:3%;font-size:10px}#defense{max-width:60%;bottom:4%;font-size:9px}#watchControls{bottom:4%;right:3%}}
#watchHud,#defense{display:none}#watchControls{opacity:0;transition:opacity .25s;bottom:3%;right:3%}#watchControls:hover,#watchControls:focus-within{opacity:.8}#card{border:0;border-radius:0;background:#100e0bd9;box-shadow:none;padding:12px;max-height:480px}.fact{margin:7px 0;color:#d6cfc1}.fact b{color:#92897d;font-weight:400;display:block;font-size:10px}.fact span{display:block;overflow-wrap:anywhere}#card .say{display:none}
</style>''')
replace('<table>${rows.map(([k,v])=>`<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join(\'\')}</table>',
        '<div class="facts">${rows.map(([k,v])=>`<div class="fact"><b>${esc(k)}</b><span>${esc(v)}</span></div>`).join(\'\')}</div>')
replace('<div id="card"></div></div>', '''<div id="card"></div></div><div id="watchHud">ANO / GALAXY WATCH<small id="telemetryState">Waiting for station evidence</small></div><div id="defense"><b id="defenseMode">WATCHING</b><span id="defenseDetail">No verified status received yet.</span></div><div id="watchControls"><button id="motion" type="button">Pause motion</button><button id="helpToggle" type="button">Recovery scope</button></div><aside id="help" hidden><b>CALM RECOVERY</b><p>Colours identify departments. Amber means observation is needed; grey means stale or unavailable evidence.</p><p>Ano can restore its own verified wallpaper files, recover stopped services under existing limits, and unload idle local models. It never invents a repair result.</p><p>Degraded data feeds wait for fresh evidence. Unknown corruption needs inspection. Trading permissions and risk limits are unchanged.</p><p>Click a cluster for its live details. These controls do not send commands.</p></aside>''')
start=s.index('window.receiveAnoStatus=function(d)')
end=s.index('refresh();setInterval(refresh,5000);})();',start)+len('refresh();setInterval(refresh,5000);})();')
s=s[:start]+'''
function hud(){const fresh=data&&Date.now()-data.received<45000,def=data?.defense||{};
 document.getElementById('telemetryState').textContent=fresh?data.stations.length+' STATIONS · '+new Date(data.received).toLocaleTimeString()+' · '+quality.toUpperCase():'TELEMETRY STALE / LAST OBSERVATIONS';
 document.getElementById('defenseMode').textContent=!fresh?'WAITING FOR EVIDENCE':def.mode==='recovering'?'VERIFIED RECOVERY IN PROGRESS':def.mode==='attention'?'OBSERVING / SYSTEM ISOLATED':'MONITORING / STEADY';
 document.getElementById('defenseDetail').textContent=!fresh?'No fresh data; the last graph remains visible.':(def.active_repairs?.length?def.active_repairs.join(' · '):def.asset_integrity==='attention'?def.asset_detail:def.issues?.length?def.issues.length+' station(s) need observation. Safe recovery '+(def.automatic_repairs?'enabled.':'off.'):'Verified components online. Local monitoring; no paid AI.');}
const PARENT={models:'assistant',work:'assistant',memory_manager:'system',risk:'forex'};
function groupedStations(rows){const by=new Map(rows.map(s=>[s.id,{...s,details:[...s.details],say:[...s.say]}]));
 for(const [child,parent]of Object.entries(PARENT)){const c=by.get(child),p=by.get(parent);if(!c||!p)continue;
  p.details.push([c.name,c.headline],...c.details.map(([k,v])=>[c.name+' / '+k,v]));p.say=[...new Set([...p.say,...c.say])];
  if(c.state!=='healthy'&&p.state==='healthy')p.state='attention';by.delete(child)}
 return [...['scorpred','forex','stocks','system','mail','assistant'].filter(k=>by.has(k)).map(k=>by.get(k)),...[...by.values()].filter(s=>!['scorpred','forex','stocks','system','mail','assistant'].includes(s.id))]}
window.receiveAnoStatus=function(d){
 if(!d||!Array.isArray(d.stations)||!d.stations.length||!Number.isFinite(Date.parse(d.at)))return;
 if(d.stations.some(s=>!s||typeof s.id!=='string'||!Array.isArray(s.details)||s.details.some(r=>!Array.isArray(r)||r.length!==2)||!Array.isArray(s.say)))return;
 if(new Set(d.stations.map(s=>s.id)).size!==d.stations.length)return;
 const at=Date.parse(d.at);if(at>Date.now()+5000)return;
 const repairRows=Array.isArray(d.repairs)?d.repairs.filter(r=>r&&typeof r.station==='string'&&['working','done','needs-you'].includes(r.status)).map(r=>({...r,station:PARENT[r.station]||r.station,steps:Array.isArray(r.steps)?r.steps:[]})):[];
 const first=!data;data={...d,repairs:repairRows,rawCount:d.stations.length,stations:groupedStations(d.stations),received:at};
 const mem=d.stations.find(s=>s.id==='memory_manager'),ram=Number((mem?.details.find(r=>r[0]==='RAM')?.[1]||'').replace('%',''));quality=ram>=85?'economy':'balanced';
 if(Date.now()-at>=45000){data.repairs=[];for(const st of data.stations){st.state='stale';st.active=false}}
 if(data.stations.map(s=>s.id).join('|')!==built)build(data.stations);
 if(first)choose();else if(crawler.phase==='read'){crawler.tent=tentacles(factsFor(crawler.at));for(const tn of crawler.tent)tn.u=1}
 dirty=true;hud();if(openId)showCard();};
function refresh(){if(document.hidden)return;document.getElementById('telemetry-source')?.remove();const script=document.createElement('script');script.id='telemetry-source';script.src='telemetry.js?t='+Date.now();document.body.append(script);
 if(data&&Date.now()-data.received>=45000){data.repairs=[];for(const st of data.stations){st.state='stale';st.active=false}dirty=true;if(openId)showCard()}hud()}
document.getElementById('motion').onclick=()=>{paused=!paused;dirty=true;document.getElementById('motion').textContent=paused?'Resume motion':'Pause motion'};
document.getElementById('motion').textContent=paused?'Resume motion':'Pause motion';
document.getElementById('helpToggle').onclick=()=>{const panel=document.getElementById('help');panel.hidden=!panel.hidden};
document.addEventListener('visibilitychange',()=>{lastT=null;if(!document.hidden){dirty=true;refresh()}});
window.anoGalaxyDiagnostics=()=>({stations:data?.stations.length||0,rawStations:data?.rawCount||0,phase:crawler.phase,paused,quality,points:pts.length,links:links.length,
 stale:!data||Date.now()-data.received>=45000,defense:data?.defense?.mode||'unverified',hubs:lobes.map(g=>({id:g.id,screen:g.screen}))});
refresh();setInterval(refresh,5000);})();
''' +s[end:]
from galaxy_camera_upgrade import upgrade
s=upgrade(s)
from galaxy_paths_upgrade import upgrade as paths_upgrade
s=paths_upgrade(s)
(ROOT/'ano/assets/Galaxy-Watch-Live.html').write_text(s,encoding='utf-8')
(ROOT/'ano/assets/Galaxy-Watch-LivelyInfo.json').write_text(json.dumps({'AppVersion':'2.2.1.0','Title':'Ano / Galaxy Watch',
 'Desc':'Original-style pale organic web and teal prompt crawler. Live Ano facts; no tables or dashboard panels.',
 'Author':'Ano','Type':1,'FileName':'index.html','IsAbsolutePath':False}),encoding='utf-8')
print('Built the recovered galaxy design:',len(s),'characters')
