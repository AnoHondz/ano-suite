"""Readable labels with shared collision-aware placement."""
def upgrade(s):
    s=s.replace("v.length>22?v.slice(0,21)+'…':v", "v")
    a=s.index(' const placed=[],TH=17');b=s.index(' // ── the crawler:',a)
    s=s[:a]+''' const placed=[];
 // Quiet wallpaper: a single fading observation appears only after arrival.
 const facts=reading?crawler.tent.filter(tn=>tn.u*2.2>tn.delay):[];
 if(facts.length){const elapsed=Math.max(0,(performance.now()-crawler.since)/1000),tn=facts[Math.floor(elapsed/4)%facts.length],age=elapsed%4;
 const [name,value,kind]=tn.fact,alpha=Math.min(1,age/.5,(4-age)/.6)*.78;
 ctx.font=FONT(10);const text=labelLines(`${name} · ${value}`,285,1)[0]||'',w=ctx.measureText(text).width;
 const x=Math.max(28,Math.min(W-28-w,body.x-w/2)),y=Math.max(50,Math.min(890,body.y+65));
 ctx.save();ctx.shadowColor='#0c0a08';ctx.shadowBlur=5;ctx.textAlign='left';ctx.fillStyle=rgb(kind==='p'?PINK:INK,alpha);ctx.fillText(text,x,y);ctx.restore();
 placed.push({x,y:y-12,w,h:18})}
'''+s[b:]
    a=s.index('function labels(st,body,reading,taken)');b=s.index('requestAnimationFrame(frame);',a)
    s=s[:a]+'''let labelBounds=[];
function labelLines(value,width,max){const words=value.split(/\\s+/),lines=[];let line='';
 for(const word of words){const next=line?line+' '+word:word;if(ctx.measureText(next).width>width&&line){lines.push(line);line=word}else line=next}if(line)lines.push(line);
 if(lines.length>max){lines.length=max;let last=lines[max-1];while(last&&ctx.measureText(last+'…').width>width)last=last.slice(0,-1);lines[max-1]=last+'…'}return lines}
function labelSpot(w,h,preferred,boxes){let best=null,score=Infinity;
 const choices=[...preferred];for(const p of preferred)for(let k=1;k<=12;k++)for(const sign of [-1,1])choices.push({x:p.x,y:p.y+sign*k*35});
 for(const p of choices){const x=Math.max(24,Math.min(W-24-w,p.x)),y=Math.max(24,Math.min(910-h,p.y));let penalty=0;
  for(const b of boxes){const overlap=Math.max(0,Math.min(x+w+8,b.x+b.w)-Math.max(x-8,b.x))*Math.max(0,Math.min(y+h+8,b.y+b.h)-Math.max(y-8,b.y));penalty+=overlap*100}
  const cost=penalty+Math.hypot(x-preferred[0].x,y-preferred[0].y);if(cost<score){score=cost;best={x,y,w,h}}}return best}
function labels(st,body,reading,taken){const boxes=[...taken,{x:body.x-45,y:body.y-45,w:90,h:90}];labelBounds=[];
 const order=[...lobes].sort((a,b)=>(b.i===crawler.at)-(a.i===crawler.at));
 for(const g of order){const s=st[g.i],q=g.screen;if(!s||!q)continue;const c=deptColour(s.id),here=reading&&g.i===crawler.at;
 const name=s.name.toLowerCase();
 ctx.font=FONT(here?12:10);const w=ctx.measureText(name).width,h=17;
 const spot=labelSpot(w,h,here?[{x:q.x-w/2,y:q.y-65},{x:q.x-w/2,y:q.y+45}]:[{x:q.x+(q.x<750?-w-35:35),y:q.y-12},{x:q.x-w/2,y:q.y-65}],boxes);
 boxes.push(spot);labelBounds.push({id:s.id,...spot});const {x,y}=spot;
 ctx.save();ctx.shadowColor='#0c0a08';ctx.shadowBlur=4;ctx.textAlign='left';ctx.font=FONT(here?12:10);ctx.fillStyle=rgb(c,here?.95:.65);ctx.fillText(name,x,y+12);ctx.restore()}}
'''+s[b:]
    s=s.replace('resolution:{width:', 'labelBounds,resolution:{width:')
    return s
