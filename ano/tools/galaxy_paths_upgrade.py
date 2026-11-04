"""Distinct department clusters, shared curved travel lanes, and soft tendrils."""
def upgrade(s):
    def swap(old,new):
        nonlocal s
        assert old in s,old[:100]
        s=s.replace(old,new)
    swap('Math.cos(a)*300','Math.cos(a)*900')
    swap('Math.sin(a)*200','Math.sin(a)*620')
    swap('Math.sin(i*2.4+.6)*155','Math.sin(i*2.4+.6)*210')
    swap('Math.pow(rnd(),.85)*260','Math.pow(rnd(),.9)*135')
    swap('for(let k=0;k<300;k++)','for(let k=0;k<150;k++)')
    swap('for(let k=0;k<650;k++)','for(let k=0;k<300;k++)')
    swap('return mix(INK,c,.14+.79*light)','return mix(INK,c,.28+.65*light)')
    a=s.index('function build(stations)')
    s=s[:a]+'''function lanePoint(edge,u){const n=lobes.length,p0=lobes[(edge-1+n)%n].c,p1=lobes[edge].c,p2=lobes[(edge+1)%n].c,p3=lobes[(edge+2)%n].c;
 const t2=u*u,t3=t2*u,out={};for(const k of ['x','y','z'])out[k]=.5*(2*p1[k]+(-p0[k]+p2[k])*u+(2*p0[k]-5*p1[k]+4*p2[k]-p3[k])*t2+(-p0[k]+3*p1[k]-3*p2[k]+p3[k])*t3);
 return out}
'''+s[a:]
    a=s.index(' // filaments joining');b=s.index(' for(let k=0;k<300;k++)',a)
    s=s[:a]+''' // Narrow shared lanes join separated departments. The crawler uses this same curve.
 for(let i=0;i<n;i++)for(let k=0;k<70;k++){const u=k/69,p=lanePoint(i,u);add({x:p.x+g3()*10,y:p.y+g3()*9,z:p.z+g3()*10},-1,.2+rnd()*.35,[i,(i+1)%n,u])}
'''+s[b:]
    a=s.index(' // a long wandering route:');b=s.index(" crawler.phase='walk';",a)
    s=s[:a]+''' // Follow the visible lane graph, taking the shorter way round the connected departments.
 const n=st.length,start=lobes.reduce((best,g,i)=>Math.hypot(g.c.x-crawler.p.x,g.c.y-crawler.p.y,g.c.z-crawler.p.z)<Math.hypot(lobes[best].c.x-crawler.p.x,lobes[best].c.y-crawler.p.y,lobes[best].c.z-crawler.p.z)?i:best,0);
 crawler.path=[];crawler.routeEdges=[];const origin={...crawler.p},home=lobes[start].c;
 if(Math.hypot(origin.x-home.x,origin.y-home.y,origin.z-home.z)>8)for(let k=1;k<=20;k++){const u=k/20; crawler.path.push({x:origin.x+(home.x-origin.x)*u,y:origin.y+(home.y-origin.y)*u,z:origin.z+(home.z-origin.z)*u})}
 const forward=(next-start+n)%n,backward=(start-next+n)%n,dir=forward<=backward?1:-1;let current=start;
 for(let hop=0;current!==next&&hop<n;hop++){const edge=dir>0?current:(current-1+n)%n;crawler.routeEdges.push(edge);
  for(let k=1;k<=65;k++)crawler.path.push(lanePoint(edge,dir>0?k/65:1-k/65));current=(current+dir+n)%n}
 if(!crawler.path.length)crawler.path.push({...lobes[next].c});
'''+s[b:]
    swap("function arrive(){crawler.phase='read';", "function arrive(){crawler.routeEdges=[];crawler.phase='read';")
    swap('crawler.fx=dx/d;crawler.fz=dz/d', 'const ease=1-Math.exp(-dt*4);crawler.fx=(crawler.fx??dx/d)+((dx/d)-(crawler.fx??dx/d))*ease;crawler.fz=(crawler.fz??dz/d)+((dz/d)-(crawler.fz??dz/d))*ease;const norm=Math.hypot(crawler.fx,crawler.fz)||1;crawler.fx/=norm;crawler.fz/=norm')
    # Replace two straight rigid segments with two flowing control points.
    swap('ctx.lineTo(kx,ky);ctx.lineTo(f.x,f.y);ctx.stroke()',
         '''const wave=Math.sin(t*2.6-leg.k*.65)*(8+leg.lift*12)*bs,px=-dy/len,py=dx/len;
  ctx.bezierCurveTo(body.x+dx*.28+px*wave,body.y+dy*.28+py*wave-8*bs,
   body.x+dx*.72-px*wave*.8,body.y+dy*.72-py*wave*.8-leg.lift*14*bs,f.x,f.y);ctx.stroke()''')
    # Reading filaments also wave; packets follow their actual curve rather than a straight shortcut.
    swap('const u=(t*.55+k/8)%1,ax=body.x+(q.x-body.x)*u,ay=body.y+(q.y-body.y)*u;',
         'const u=(t*.55+k/8)%1,mx=(body.x+q.x)/2+Math.sin(t*1.7+k)*14,my=Math.min(body.y,q.y)-25+Math.cos(t*1.9+k)*10,ax=(1-u)*(1-u)*body.x+2*(1-u)*u*mx+u*u*q.x,ay=(1-u)*(1-u)*body.y+2*(1-u)*u*my+u*u*q.y;')
    swap('ctx.quadraticCurveTo((body.x+q.x)/2,Math.min(body.y,q.y)-25,q.x,q.y)', 'ctx.quadraticCurveTo(mx,my,q.x,q.y)')
    # Off-screen departments get distinct edge markers instead of piling up in one corner.
    swap('const tx=q?q.x:SX+(g.c.x-cam.f.x),ty=q?q.y:SY+(g.c.y-cam.f.y),a=Math.atan2(ty-SY,tx-SX),ex=Math.max(EDGE.l,Math.min(EDGE.r-w,SX+Math.cos(a)*700)),ey=Math.max(EDGE.t,Math.min(EDGE.b-16,SY+Math.sin(a)*420));',
         '''const dx=(g.c.x-cam.f.x)*view.cy-(g.c.z-cam.f.z)*view.sy,tx=q&&!q.hidden?q.x:SX+dx,ty=q&&!q.hidden?q.y:SY+(g.c.y-cam.f.y),a=Math.atan2(ty-SY,tx-SX),markerW=ctx.measureText(name).width+24;
   let ex=dx>=0?EDGE.r-markerW:EDGE.l,ey=Math.max(EDGE.t+12,Math.min(EDGE.b-16,SY+Math.sin(a)*420));
   for(let attempt=0;attempt<40&&boxes.some(b=>ex<b.x+b.w&&b.x<ex+markerW&&ey-11<b.y+b.h&&b.y<ey+4);attempt++){ey+=17;if(ey>EDGE.b-16)ey=EDGE.t+12}
   boxes.push({x:ex,y:ey-11,w:markerW,h:15});''')
    # A faint, continuous route stays readable between the dense clusters.
    needle=' // each lobe glows from inside'
    swap(needle,''' // The active route is brighter; other paths remain subtle and individually traceable.
 for(let edge=0;edge<lobes.length;edge++){const active=(crawler.routeEdges||[]).includes(edge),col=mix(deptColour(st[edge].id),deptColour(st[(edge+1)%st.length].id),.5);
  ctx.strokeStyle=rgb(col,active?.42:.13);ctx.lineWidth=active?1.25:.7;ctx.beginPath();let drawing=false;
  for(let j=0;j<=40;j++){const q=proj(lanePoint(edge,j/40));if(q.hidden){drawing=false;continue}if(!drawing){ctx.moveTo(q.x,q.y);drawing=true}else ctx.lineTo(q.x,q.y)}ctx.stroke();
  if(active){const q=proj(lanePoint(edge,(t*.13+edge*.21)%1));if(!q.hidden){ctx.fillStyle=rgb(col,.9);ctx.beginPath();ctx.arc(q.x,q.y,2.2,0,6.283);ctx.fill()}}}
'''+needle)
    swap('phase:crawler.phase,paintMs,paintCount,', 'phase:crawler.phase,clusterRadius:135,clusterCenters:lobes.map(g=>({...g.c})),paths:lobes.length,routeEdges:crawler.routeEdges||[],wigglyArms:true,paintMs,paintCount,')
    # Batch particle drawing by quantised colour/opacity rather than changing canvas state per vertex.
    a=s.index('function frame(ms)')
    s=s[:a]+'''function dotsAdd(batches,col,alpha,x,y,w,h,dp=1){if(alpha<.035)return;const fog=(1-Math.round(dp*3)/3)*.55;
 const c=col.map((v,k)=>Math.max(0,Math.min(255,Math.round((v+(FOG[k]-v)*fog)/16)*16))),a=Math.max(1/12,Math.min(1,Math.round(alpha*12)/12));
 const style=`rgba(${c[0]},${c[1]},${c[2]},${a})`;let points=batches.get(style);if(!points){points=[];batches.set(style,points)}points.push(x,y,w,h)}
function dotsPaint(context,batches){for(const [style,points]of batches){context.fillStyle=style;context.beginPath();for(let j=0;j<points.length;j+=4)context.rect(points[j],points[j+1],points[j+2],points[j+3]);context.fill()}}
'''+s[a:]
    swap(' // particles (also drawn into the half-size bloom layer for a soft glow)',
         ' const dotBatches=new Map(),bloomBatches=new Map();\n // particles (also drawn into the half-size bloom layer for a soft glow)')
    swap('ctx.fillStyle=rgb(mix(col,FOG,(1-dp)*.55),a);ctx.fillRect(q.x-z/2,q.y-z/2,z,z);',
         'dotsAdd(dotBatches,col,a,q.x-z/2,q.y-z/2,z,z,dp);')
    swap('bctx.fillStyle=rgb(col,a*.35);bctx.fillRect(q.x-r,q.y-r,r*2,r*2)',
         'dotsAdd(bloomBatches,col,a*.35,q.x-r,q.y-r,r*2,r*2)')
    swap('bctx.fillStyle=rgb(col,a*.9);bctx.fillRect(q.x-1,q.y-1,z+2,z+2)',
         'dotsAdd(bloomBatches,col,a*.9,q.x-1,q.y-1,z+2,z+2)')
    swap(' for(const g of lobes){const q=proj(g.c);g.screen=', ' dotsPaint(ctx,dotBatches);dotsPaint(bctx,bloomBatches);\n for(const g of lobes){const q=proj(g.c);g.screen=')
    # Keep the entire web in view: a shallow, screen-facing web rather than a
    # chase-camera perspective that sends half the departments behind the viewer.
    a=s.index('let view={');b=s.index('function steer(dt)',a)
    s=s[:a]+'''let view={cy:1,sy:0,cp:1,sp:0},webFit={scale:.7,x:0,y:0};
function rawProjection(p){const x=p.x*view.cy-p.z*view.sy,z0=p.x*view.sy+p.z*view.cy,
 y=p.y*view.cp-z0*view.sp,z=p.y*view.sp+z0*view.cp,k=1800/(1800+z);return {x:x*k,y:y*k,k,z}}
function updateView(){const yaw=Math.sin(frozen/19000)*.16+par.x*.55,pitch=-.12+Math.cos(frozen/23000)*.07+par.y*.6;
 view={cy:Math.cos(yaw),sy:Math.sin(yaw),cp:Math.cos(pitch),sp:Math.sin(pitch)};
 let minX=Infinity,maxX=-Infinity,minY=Infinity,maxY=-Infinity;
 for(const p of pts){if(p.r<0)continue;const q=rawProjection(p);minX=Math.min(minX,q.x);maxX=Math.max(maxX,q.x);minY=Math.min(minY,q.y);maxY=Math.max(maxY,q.y)}
 if(Number.isFinite(minX)){webFit.scale=Math.min(1450/Math.max(1,maxX-minX),890/Math.max(1,maxY-minY));webFit.x=(minX+maxX)/2;webFit.y=(minY+maxY)/2}}
function proj(p){const q=rawProjection(p);return {x:750+(q.x-webFit.x)*webFit.scale,y:470+(q.y-webFit.y)*webFit.scale,k:q.k*webFit.scale,z:q.z}}
'''+s[b:]
    swap('x:Math.cos(a)*900+g3()*40,y:Math.sin(i*2.4+.6)*210+g3()*30,z:Math.sin(a)*620+g3()*40',
         'x:({mail:-510,assistant:250,stocks:-510,forex:190,scorpred:610,system:-650,models:-160,memory_manager:610,work:-170,risk:600}[s.id]??Math.cos(a)*600),y:({mail:-190,assistant:-160,stocks:130,forex:180,scorpred:160,system:420,models:-390,memory_manager:-360,work:390,risk:490}[s.id]??Math.sin(a)*350),z:Math.sin(i*2.4)*24')
    # Explicit radial spokes and scalloped cross-threads make a spider web,
    # with all geometry inside the same fitted envelope as the departments.
    # Organic node clouds and branching filament bridges, as in the reference.
    # No geometric rings or radial diagram overlay.
    swap('for(let k=0;k<150;k++)','for(let k=0;k<180;k++)')
    swap('x:p.x+g3()*10,y:p.y+g3()*9,z:p.z+g3()*10',
         'x:p.x+g3()*19,y:p.y+g3()*18,z:p.z+g3()*14')
    swap('active?.42:.13','active?.25:.035')
    # Travel remains internal animation data; no corridor particles or route strokes.
    a=s.index(' // Narrow shared lanes');b=s.index(' for(let k=0;k<300;k++)',a)
    s=s[:a]+s[b:]
    a=s.index(' // The active route is brighter');b=s.index(' // each lobe glows from inside',a)
    s=s[:a]+s[b:]
    swap('for(const g of lobes)g.cut=', 'links=links.filter(([a,b])=>pts[a].r>=0&&pts[a].r===pts[b].r);\n for(const g of lobes)g.cut=')
    # A minimum spanning web connects every department with short, fine silk.
    # Endpoints attach to existing cluster vertices; no corridor or road overlay.
    a=s.index('function lanePoint(')
    s=s[:a]+'''let silkBridges=[];
function connectWeb(){silkBridges=[];const parent=lobes.map((_,i)=>i),root=i=>parent[i]===i?i:(parent[i]=root(parent[i])),pairs=[];
 for(let i=0;i<lobes.length;i++)for(let j=i+1;j<lobes.length;j++)pairs.push({i,j,d:Math.hypot(lobes[i].c.x-lobes[j].c.x,lobes[i].c.y-lobes[j].c.y,lobes[i].c.z-lobes[j].c.z)});
 pairs.sort((a,b)=>a.d-b.d);
 for(const pair of pairs){const {i,j}=pair;if(root(i)===root(j))continue;parent[root(i)]=root(j);
  const nearest=(g,c)=>g.pts.slice().sort((a,b)=>Math.hypot(pts[a].x-c.x,pts[a].y-c.y,pts[a].z-c.z)-Math.hypot(pts[b].x-c.x,pts[b].y-c.y,pts[b].z-c.z)).slice(0,3);
  const aa=nearest(lobes[i],lobes[j].c),bb=nearest(lobes[j],lobes[i].c);
  silkBridges.push({i,j,ends:aa.map((a,k)=>[a,bb[k%bb.length]])})}}
function drawConnectedWeb(t){for(const bridge of silkBridges){const col=mix(deptColour(data.stations[bridge.i].id),deptColour(data.stations[bridge.j].id),.5);
 ctx.strokeStyle=rgb(mix(INK,col,.22),.22);ctx.lineWidth=.65;
 bridge.ends.forEach(([ai,bi],strand)=>{const a=pts[ai],b=pts[bi];ctx.beginPath();
  for(let k=0;k<=16;k++){const u=k/16,envelope=Math.sin(Math.PI*u),w=Math.sin(u*17+strand*2+bridge.i)*12*envelope;
   const q=proj({x:a.x+(b.x-a.x)*u+w*.35,y:a.y+(b.y-a.y)*u+w,z:a.z+(b.z-a.z)*u+Math.sin(u*13+strand)*9*envelope});
   if(k===0)ctx.moveTo(q.x,q.y);else ctx.lineTo(q.x,q.y)}ctx.stroke()})}}
'''+s[a:]
    swap('for(const leg of crawler.legs){leg.foot=null;', 'connectWeb();for(const leg of crawler.legs){leg.foot=null;')
    swap(' // each lobe glows from inside',' drawConnectedWeb(t);\n // each lobe glows from inside')
    # Reference-style continuous tissue, rather than isolated balls joined by cables.
    swap('for(let k=0;k<180;k++)','for(let k=0;k<260;k++)')
    swap('Math.pow(rnd(),.9)*135','Math.pow(rnd(),.72)*205')
    swap('clusterRadius:135','clusterRadius:205')
    swap('return mix(INK,c,.28+.65*light)','return mix(INK,c,.12+.82*light)')
    swap('[[.05,.4],[.10,.55],[.20,.7]]','[[.09,.45],[.18,.6],[.30,.75]]')
    # Broad, irregular connective tissue contains actual linked nodes, never a road stroke.
    needle=' for(let k=0;k<300;k++)'
    swap(needle,''' for(let i=0;i<n;i++){const g=lobes[i],others=lobes.filter(h=>h!==g).sort((a,b)=>Math.hypot(a.c.x-g.c.x,a.c.y-g.c.y)-Math.hypot(b.c.x-g.c.x,b.c.y-g.c.y));
 const h=others[0];if(!h)continue;for(let k=0;k<75;k++){const u=rnd(),width=25+65*Math.sin(Math.PI*u);
 add({x:g.c.x+(h.c.x-g.c.x)*u+g3()*width,y:g.c.y+(h.c.y-g.c.y)*u+g3()*width,z:g.c.z+(h.c.z-g.c.z)*u+g3()*70},g.i,.3+rnd()*.45)}}
'''+needle)
    # Connect nearby tissue vertices across station boundaries as one organic mesh.
    swap('links=links.filter(([a,b])=>pts[a].r>=0&&pts[a].r===pts[b].r);',
         'links=links.filter(([a,b])=>pts[a].r>=0&&pts[b].r>=0);')
    swap(' drawConnectedWeb(t);',' // Node tissue provides the connections; no cable overlay.')
    # The view glides toward the crawler, with a fitted wide envelope preserving labels.
    swap('yaw=Math.sin(frozen/19000)*.16+par.x*.55,pitch=-.12+Math.cos(frozen/23000)*.07+par.y*.6',
         'yaw=Math.sin(frozen/19000)*.28+Math.sin(cam.yaw)*.1+par.x*.55,pitch=-.18+Math.cos(frozen/23000)*.1+par.y*.6')
    swap('webFit.x=(minX+maxX)/2;webFit.y=(minY+maxY)/2',
         'const follow=rawProjection(crawler.p);webFit.x=(minX+maxX)/2+follow.x*.025;webFit.y=(minY+maxY)/2+follow.y*.02')
    # Department identity stays visible on every node and filament, not only on arrival.
    swap('return mix(INK,c,.12+.82*light)', 'return mix(INK,c,.86+.14*light)')
    swap("if(s.state==='stale')return GREY;", "if(s.state==='stale')return mix(deptColour(s.id),GREY,.55);")
    a=s.index(' const bands=[[],[],[]]');b=s.index(' for(const g of lobes){if(!broken',a)
    s=s[:a]+''' const silkBatches=new Map();for(const [a,b]of links){const A=P[a],B=P[b];if(A.hidden||B.hidden)continue;
 const station=st[pts[a].r],band=depth(A)<.35?0:depth(A)<.65?1:2,key=station.id+':'+band;
 let batch=silkBatches.get(key);if(!batch){batch={col:colourOf(station),band,points:[]};silkBatches.set(key,batch)}batch.points.push(A,B)}
 for(const batch of silkBatches.values()){ctx.lineWidth=[.45,.6,.8][batch.band];ctx.strokeStyle=rgb(batch.col,[.13,.23,.36][batch.band]);ctx.beginPath();
 for(let j=0;j<batch.points.length;j+=2){const a=batch.points[j],b=batch.points[j+1];ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y)}ctx.stroke()}
'''+s[b:]
    # Volumetric clouds at genuinely different depths, with stronger perspective.
    swap('z:Math.sin(i*2.4)*24','z:Math.sin(i*2.4)*170')
    swap('z:g.c.z+Math.sin(v)*Math.sin(u)*d}', 'z:g.c.z+Math.sin(v)*Math.sin(u)*d*1.5}')
    swap('k=1800/(1800+z)', 'k=1350/(1350+z)')
    swap('wigglyArms:true,paintMs', 'wigglyArms:true,permanentDepartmentColours:true,colouredFilaments:true,screenFit:true,perspective3D:true,visiblePathways:false,connectedWeb:silkBridges.length===Math.max(0,lobes.length-1),webConnections:silkBridges.length,webRings:0,organicStructure:true,paintMs')
    swap('paintMs,paintCount,legs:', 'paintMs,paintCount,resolution:{width:canvas.width,height:canvas.height,pixelRatio:Math.min(2,devicePixelRatio||1),bloomWidth:bloomC.width},legs:')
    from galaxy_labels_upgrade import upgrade as upgrade_labels
    return upgrade_labels(s)
