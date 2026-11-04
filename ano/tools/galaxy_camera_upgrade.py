"""Department lighting, node-planted gait, and a close third-person camera."""
def upgrade(s):
    def swap(old,new):
        nonlocal s
        assert old in s,old[:90]
        s=s.replace(old,new)
    swap('assistant:[127,157,255]}','assistant:[127,157,255],models:[99,211,185],memory_manager:[191,218,111],work:[225,153,210],risk:[238,139,154]}')
    a=s.index('function colourOf(s)');b=s.index('// ── Camera',a)
    s=s[:a]+'''let frameColours=new Map();
function departmentShade(s){if(!s)return INK;if(s.state==='stale')return GREY;
 const c=deptColour(s.id),here=data&&crawler.phase==='read'&&data.stations[crawler.at]?.id===s.id;
 const light=here?Math.min(1,(performance.now()-crawler.since)/1500):0;
 return mix(INK,c,.14+.79*light)}
function colourOf(s){return s?(frameColours.get(s.id)||departmentShade(s)):INK}

'''+s[b:]
    swap('for(let k=0;k<400;k++)','for(let k=0;k<300;k++)')
    swap('F:1100,D:300,cx:750,cy:565','F:1000,D:40,cx:750,cy:590')
    swap('function proj(p){const yw=cam.yaw+par.x,pt=cam.pitch+par.y,cy=Math.cos(yw),sy=Math.sin(yw),cp=Math.cos(pt),sp=Math.sin(pt),dx=',
         'let view={cy:1,sy:0,cp:1,sp:0};function updateView(){const y=cam.yaw+par.x,p=cam.pitch+par.y;view={cy:Math.cos(y),sy:Math.sin(y),cp:Math.cos(p),sp:Math.sin(p)}}\nfunction proj(p){const {cy,sy,cp,sp}=view,dx=')
    swap('const P=pts.map(proj)', 'updateView();frameColours=new Map(st.map(s=>[s.id,departmentShade(s)]));const P=pts.map(proj)')
    swap('let lastT=null,prevP=null;', 'let lastT=null,prevP=null,paintMs=0,paintCount=0;')
    swap('dirty=false;if(!paused)frozen=ms;', 'dirty=false;const renderStarted=performance.now();if(!paused)frozen=ms;')
    swap('labels(st,body,reading,placed)}','labels(st,body,reading,placed);paintMs=paintCount?paintMs*.9+(performance.now()-renderStarted)*.1:performance.now()-renderStarted;paintCount++}')
    a=s.index('function steer(dt)');b=s.index('const depth=',a)
    s=s[:a]+'''function steer(dt){const p=crawler.p,walking=crawler.phase==='walk'&&crawler.path.length,
 q=walking?crawler.path[Math.min(8,crawler.path.length-1)]:p,e=1-Math.exp(-dt*3.6);
 // The camera trails the body while looking along the upcoming curve, rather than orbiting a diagram.
 const aim={x:p.x+(q.x-p.x)*.18,y:p.y+(q.y-p.y)*.18,z:p.z+(q.z-p.z)*.18};
 for(const k of ['x','y','z'])cam.f[k]+=(aim[k]-cam.f[k])*e;
 if(walking){const want=Math.atan2(q.x-p.x,q.z-p.z),d=Math.atan2(Math.sin(want-cam.yaw),Math.cos(want-cam.yaw));
  cam.yaw+=Math.max(-dt*.9,Math.min(dt*.9,d*(1-Math.exp(-dt*1.35))))}
 else if(!openId)cam.yaw+=dt*.025;
 cam.D+=((walking?-160:40)-cam.D)*(1-Math.exp(-dt*1.8));
 cam.pitch+=(-.24+.025*Math.sin(performance.now()/11000)-cam.pitch)*(1-Math.exp(-dt*1.6));
 cam.cy=590+(walking?Math.sin(performance.now()/580)*1.6:0);
 const m=1-Math.pow(.2,dt);par.x+=(par.tx-par.x)*m;par.y+=(par.ty-par.y)*m}
'''+s[b:]
    swap('length:16},(_,k)','length:24},(_,k)')
    swap('function choose(){const st=data.stations,now=Date.now();','function choose(preferred){const st=data.stations,now=Date.now();')
    swap('let next;if(job)next=job[1];','let next;if(Number.isInteger(preferred)&&preferred>=0&&preferred<st.length)next=preferred;else if(job)next=job[1];')
    swap('crawler.at=Math.min(crawler.at,lobes.length-1);',
         'for(const leg of crawler.legs){leg.foot=null;leg.step=null;leg.node=null;leg.flash=0}crawler.silk=[];crawler.at=Math.min(crawler.at,lobes.length-1);')
    swap('leg.k<8?-1:1,i=leg.k%8,a=[25,43,61,79,101,119,137,155][i]',
         'leg.k<12?-1:1,i=leg.k%12,a=(20+i*140/11)')
    swap("reach=walking?60:85","reach=walking?95:125")
    # Feet latch to actual graph vertices. Light pulses mark contact, not business success.
    a=s.index('function step(dt)')
    s=s[:a]+'''function plant(home,leg){const used=new Set(crawler.legs.filter(l=>l!==leg).map(l=>l.node));
 const candidates=near(home,115).filter(([,i])=>pts[i].r!==-3).sort((a,b)=>a[0]-b[0]);
 const pick=candidates.find(([,i])=>!used.has(i))||candidates[0];
 return pick?{p:{x:pts[pick[1]].x,y:pts[pick[1]].y,z:pts[pick[1]].z},node:pick[1]}:{p:home,node:null}}
'''+s[a:]
    swap('if(!leg.foot)leg.foot={...home};','if(!leg.foot){const anchor=plant(home,leg);leg.foot=anchor.p;leg.node=anchor.node}leg.flash=Math.max(0,(leg.flash||0)-dt);')
    swap('if(st.u>=1)leg.step=null;else stepping=true',
         'if(st.u>=1){leg.step=null;leg.node=st.node;leg.flash=.65;if(leg.node!==null&&pts[leg.node])pts[leg.node].lit=1}else stepping=true')
    swap('leg.step={from:{...leg.foot},to:home,u:0};stepping=true',
         'const anchor=plant(home,leg);leg.step={from:{...leg.foot},to:anchor.p,node:anchor.node,u:0};stepping=true')
    swap('const c=colourOf(s),q=g.screen','const c=deptColour(s.id),q=g.screen')
    swap('col=p.lit>.05?TEAL:',"col=p.lit>.05?deptColour(s?.id||st[crawler.at].id):")
    swap("const interval=paused?1000:quality==='economy'?83:42","const interval=paused?1000:quality==='economy'?50:33")
    swap("scan=reading?(performance.now()-crawler.since)/1000*160:-1", "scan=reading?((performance.now()-crawler.since)/1000*150)%560:-1")
    # Replace six merged areas with all live departments; keep their details and names intact.
    a=s.index('function groupedStations(rows)');b=s.index('window.receiveAnoStatus=function(d)',a)
    s=s[:a]+"function groupedStations(rows){return rows.map(s=>({...s,details:[...s.details],say:[...s.say]}))}\n"+s[b:]
    swap('station:PARENT[r.station]||r.station','station:r.station')
    swap("openId=hit&&openId!==hit[1].id?hit[1].id:null;showCard()", "openId=hit&&openId!==hit[1].id?hit[1].id:null;if(openId)choose(lobes.findIndex(g=>g.id===openId));showCard()")
    swap('window.anoGalaxyDiagnostics=()=>', "window.anoGalaxyFocus=id=>{const i=data?.stations.findIndex(s=>s.id===id);if(!Number.isInteger(i)||i<0)return false;choose(i);dirty=true;return true};window.anoGalaxyDiagnostics=()=>")
    # Pulses run down eight flexible reading tendrils into existing vertices.
    needle=' // trail and repair silk'
    swap(needle,''' if(reading){const g=lobes[crawler.at],col=deptColour(st[crawler.at].id);
  for(let k=0;k<8;k++){const pi=g.pts[(k*43+17)%g.pts.length],q=P[pi];if(q.hidden)continue;
   const u=(t*.55+k/8)%1,ax=body.x+(q.x-body.x)*u,ay=body.y+(q.y-body.y)*u;
   ctx.strokeStyle=rgb(col,.10+.13*Math.sin(u*Math.PI));ctx.lineWidth=.65;ctx.beginPath();ctx.moveTo(body.x,body.y);
   ctx.quadraticCurveTo((body.x+q.x)/2,Math.min(body.y,q.y)-25,q.x,q.y);ctx.stroke();
   ctx.fillStyle=rgb(col,.8);ctx.beginPath();ctx.arc(ax,ay,1.7,0,6.283);ctx.fill();if(u>.94)pts[pi].lit=.9}}
'''+needle)
    swap('const dx=f.x-body.x,dy=f.y-body.y,len=', '''if(leg.flash>0){const node=leg.node!==null?pts[leg.node]:null,c=deptColour(node&&node.r>=0?st[node.r].id:st[crawler.at].id);
  ctx.strokeStyle=rgb(c,leg.flash);ctx.lineWidth=.8;ctx.beginPath();ctx.arc(f.x,f.y,3+(1-leg.flash/.65)*9,0,6.283);ctx.stroke();
  ctx.fillStyle=rgb(c,.85);ctx.fillRect(f.x-1.5,f.y-1.5,3,3)}const dx=f.x-body.x,dy=f.y-body.y,len=''')
    swap('phase:crawler.phase,paused,quality', 'phase:crawler.phase,paintMs,paintCount,legs:crawler.legs.length,contacts:crawler.legs.filter(l=>l.node!==null&&l.node!==undefined).length,camera:{distance:cam.D,yaw:cam.yaw,focus:{...cam.f}},departmentColors:Object.fromEntries((data?.stations||[]).map(s=>[s.id,deptColour(s.id)])),paused,quality')
    return s
