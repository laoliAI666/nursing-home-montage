// Shared browser/production Canvas layout; all dimensions are native 1080x1920 pixels.
export const BOUNDS={title:{initial:168,min:112,max:184},tags:{initial:60,min:48,max:64},identity:{initial:72,min:54,max:78},brand:{initial:104,min:64,max:112}};
export async function mountCover(canvas,input,onchange=()=>{}) {
 const CREAM='#FFF6DE',INK='#003F46',CORAL='#FF8569',B=BOUNDS;
 const SEMANTIC_WORDS=['养老院','老年公寓','养老生活','生活照护','照护服务','服务中心','居住环境','活动空间','院区环境'];
 const state={title:input.title,highlights:input.highlights.join('、'),tags:[...input.labels],tagPreferred:60,brand:input.institution_name,boundName:input.identity.social_media_name};
 canvas.width=1080;canvas.height=1920;
 const ctx=canvas.getContext('2d'),probe=document.createElement('canvas').getContext('2d');
 const font=new FontFace('CoverBlack',`url(${input.font})`,{weight:'900'});await font.load();document.fonts.add(font);
 function image(url){return new Promise((resolve,reject)=>{const im=new Image();im.onload=()=>resolve(im);im.onerror=()=>reject(new Error('图片无法加载'));im.src=url;});}
 const photos=await Promise.all(input.photos.map(p=>image(p.data)));const avatar=await image(input.avatar);
 const background=document.createElement('canvas');background.width=1080;background.height=1920;
 const bc=background.getContext('2d'),low=document.createElement('canvas');low.width=135;low.height=240;const lc=low.getContext('2d'),pixels=lc.createImageData(135,240);
 for(let y=0;y<240;y++)for(let x=0;x<135;x++){const u=x/134,v=y/239,g=Math.exp(-((u-.95)**2/.25+(v-.72)**2/.20)),l=Math.exp(-((u-.15)**2/.4+(v-.10)**2/.10)),i=(y*135+x)*4;pixels.data.set([Math.round(18*g+4*l),Math.round(103+50*g+12*l),Math.round(110+43*g+14*l),255],i);}
 lc.putImageData(pixels,0,0);bc.drawImage(low,0,0,1080,1920);
 const polygon=(c,points,fill)=>{c.beginPath();points.forEach(([x,y],i)=>i?c.lineTo(x,y):c.moveTo(x,y));c.closePath();if(fill){c.fillStyle=fill;c.fill();}};
 polygon(bc,[[0,0],[280,0],[0,158]],'#39ABA8');polygon(bc,[[0,541],[95,594],[318,639],[0,611]],'#78D5CD');polygon(bc,[[0,590],[193,643],[0,669]],CORAL);polygon(bc,[[531,1920],[1080,1765],[1080,1825]],'#69D4D0');
 const bases={background};let lastLayout;
function metrics(text,em){probe.font=`900 ${em}px CoverBlack`;const m=probe.measureText(text||' ');return {w:Math.max(1,Math.ceil((m.actualBoundingBoxLeft||0)+(m.actualBoundingBoxRight||m.width))),h:Math.max(1,Math.ceil((m.actualBoundingBoxAscent||em*.85)+(m.actualBoundingBoxDescent||em*.1))),left:m.actualBoundingBoxLeft||0,up:m.actualBoundingBoxAscent||em*.85};}
function raster(text,em,color){const m=metrics(text,em),c=document.createElement('canvas');c.width=m.w+4;c.height=m.h+4;const p=c.getContext('2d');p.font=`900 ${em}px CoverBlack`;p.fillStyle=color;p.textBaseline='alphabetic';p.fillText(text,2+m.left,2+m.up);return c;}
function put(text,em,color,x,y,align='left'){if(!text)return;const im=raster(text,em,color);ctx.drawImage(im,x-(align==='center'?im.width/2:0),y);}
function roundPath(x,y,w,h,r){ctx.beginPath();ctx.roundRect(x,y,w,h,Math.min(r,h/2,w/2));}
function card(x,y,w,h,{fill=CREAM,angle=0,stroke=false,tail=false}={}){ctx.save();ctx.translate(x+w/2,y+h/2);ctx.rotate(-angle*Math.PI/180);roundPath(-w/2,-h/2+7,w,h,30);ctx.fillStyle=CORAL;ctx.fill();roundPath(-w/2,-h/2,w,h,30);ctx.fillStyle=fill;ctx.fill();if(stroke){ctx.strokeStyle=CREAM;ctx.lineWidth=7;ctx.stroke();}if(tail){ctx.beginPath();ctx.moveTo(w*.04,h/2-3);ctx.lineTo(w*.12,h/2+17);ctx.lineTo(w*.105,h/2-3);ctx.closePath();ctx.fillStyle=CREAM;ctx.fill();}ctx.restore();}
function sizedFont(lines,min,max,width,height,gap=0){const fits=em=>{const ms=lines.map(t=>metrics(t,em));return ms.every(m=>m.w<=width)&&ms.reduce((a,m)=>a+m.h,0)+gap*(ms.length-1)<=height;};if(!fits(min))return {em:min,fit:false};let lo=min,hi=max;while(lo<hi){const mid=Math.ceil((lo+hi)/2);if(fits(mid))lo=mid;else hi=mid-1;}return {em:lo,fit:true};}
function words(){return state.highlights.split(/[,，、]/).map(s=>s.trim()).filter(Boolean);}
function splitCandidates(text,protect=[]){const chars=Array.from(text),list=[];const protectedWords=[...protect,...SEMANTIC_WORDS];for(let i=1;i<chars.length;i++){const a=chars.slice(0,i).join(''),b=chars.slice(i).join('');if(/^[，。！？、；：）】》,.!?:;]/.test(b))continue;let crossed=false;for(const word of protectedWords){let start=text.indexOf(word);while(start>=0){if(a.length>start&&a.length<start+word.length)crossed=true;start=text.indexOf(word,start+1);}}if(!crossed)list.push([a,b]);}return list;}
function chooseTwo(text,{min,max,width,height,gap=14,protect=[],manual=false}){let candidates;if(manual&&text.includes('\n')){const lines=text.split('\n').map(s=>s.trim());if(lines.length!==2||lines.some(s=>!s))return {lines,em:min,fit:false,reason:'请只保留一次换行，并填写两行内容。'};const joined=lines.join(''),at=lines[0].length;for(const w of protect){const i=joined.indexOf(w);if(i>=0&&at>i&&at<i+w.length)return {lines,em:min,fit:false,reason:'手动换行拆开了高亮词，请调整断行位置。'};}candidates=[lines];}else candidates=splitCandidates(text.replace(/\n/g,''),protect);
 if(!candidates.length)return {lines:[text],em:min,fit:false,reason:'请填写足够分成两行的标题，并保留高亮词完整。'};
 let best;for(const lines of candidates){const r=sizedFont(lines,min,max,width,height,gap);const widths=lines.map(t=>metrics(t,r.em).w);const score=r.em*100-(Math.abs(widths[0]-widths[1])/width)*12;const item={...r,lines,score};if(!best||(item.fit&&!best.fit)||(item.fit===best.fit&&score>best.score))best=item;}return best;
}
function titleLayout(){const text=state.title.trim();const r=chooseTwo(text,{...B.title,width:950,height:345,gap:16,protect:words(),manual:true});const missing=words().filter(w=>!text.replace(/\n/g,'').includes(w));return {...r,error:!text?'请填写主标题。':missing.length?`高亮词“${missing.join('、')}”不在标题中，请修改高亮词或清空。`:!r.fit?(r.reason||'主标题在 112px 下仍放不进两行，请改短或调整排版边界。'):null};}
function tagsLayout(){let em=Math.min(B.tags.max,Math.max(B.tags.min,state.tagPreferred));while(em>B.tags.min&&state.tags.some(t=>metrics(t,em).w+44>324))em--;const ok=state.tags.every(t=>t.trim()&&metrics(t,em).w+44<=324);const h=Math.max(84,...state.tags.map(t=>metrics(t,em).h+28));const cards=state.tags.map(t=>({w:Math.max(140,Math.min(324,metrics(t,em).w+44)),h}));return {em,cards,fit:ok,error:ok?null:'标签在共用 48px 下仍过长，或有空值；请改短文字。三个标签不会各自缩成不同字号。'};}
function brandLayout(){const text=state.brand.trim();const single=sizedFont([text],B.brand.min,B.brand.max,920,105);let r={...single,lines:[text]};if(!single.fit)r=chooseTwo(text,{min:B.brand.min,max:96,width:920,height:190,gap:12});const ms=r.lines.map(t=>metrics(t,r.em));const h=Math.max(145,Math.min(230,ms.reduce((a,m)=>a+m.h,0)+(ms.length-1)*12+36));const w=Math.max(640,Math.min(1000,Math.max(...ms.map(m=>m.w))+80));return {...r,w,h,x:(1080-w)/2,y:1798-h,error:!text?'请填写机构完整名称。':!r.fit?'完整院名达到 64px 仍放不下，请扩大布局边界；完整名称不会被截断。':null};}
function identityLayout(brand){const text=state.boundName.trim();let r={...sizedFont([text],54,78,564,96),lines:[text]};if(!r.fit)r=chooseTwo(text,{min:54,max:72,width:564,height:130,gap:8});const ms=r.lines.map(t=>metrics(t,r.em));const h=Math.max(126,Math.min(158,ms.reduce((a,m)=>a+m.h,0)+(ms.length-1)*8+25));const w=Math.max(493,Math.min(620,Math.max(...ms.map(m=>m.w))+56));const y=Math.min(1530,brand.y+8-h);return {...r,w,h,x:177,y,error:!text?'缺少与数字人绑定的社交媒体名，请先指定并确认。':!r.fit?'绑定名在字号下限仍放不下，需要调整身份区布局；不会截断真实署名。':null};}
function layout(){const title=titleLayout(),tags=tagsLayout(),brand=brandLayout(),identity=identityLayout(brand);const positions=[[44,1006],[1060-tags.cards[1].w,670],[44,Math.min(1450,identity.y-24-tags.cards[2].h)]];return {title,tags,brand,identity,positions,errors:[title.error,tags.error,identity.error,brand.error].filter(Boolean)};}
function richLine(text,em,centerX,y){const ws=words().sort((a,b)=>b.length-a.length);const runs=[];let i=0;while(i<text.length){const hit=ws.find(w=>text.startsWith(w,i));if(hit){runs.push({text:hit,color:CORAL});i+=hit.length;}else{const ch=Array.from(text.slice(i))[0];if(runs.length&&runs[runs.length-1].color===CREAM)runs[runs.length-1].text+=ch;else runs.push({text:ch,color:CREAM});i+=ch.length;}}
 // Advance widths keep highlight runs on the same baseline and at the same font size.
 probe.font=`900 ${em}px CoverBlack`;const widths=runs.map(r=>probe.measureText(r.text).width),total=widths.reduce((a,b)=>a+b,0);const full=metrics(text,em);let x=centerX-full.w/2+full.left;ctx.font=`900 ${em}px CoverBlack`;ctx.textBaseline='alphabetic';runs.forEach((r,n)=>{ctx.fillStyle=r.color;ctx.fillText(r.text,x,y+full.up);x+=widths[n];});
}
function drawTitle(r){if(r.error){put('主标题待调整',70,CORAL,540,330,'center');return;}const heights=r.lines.map(t=>metrics(t,r.em).h);let y=225+(345-heights.reduce((a,b)=>a+b,0)-16)/2;r.lines.forEach((t,i)=>{richLine(t,r.em,540,y);y+=heights[i]+16;});}
function drawTag(text,r,pos,em,angle,blank){card(pos[0],pos[1],r.w,r.h,{angle});if(blank)return;ctx.save();ctx.translate(pos[0]+r.w/2,pos[1]+r.h/2);ctx.rotate(-angle*Math.PI/180);put(text,em,INK,0,-metrics(text,em).h/2,'center');ctx.restore();}
function drawName(r,blank){card(r.x,r.y,r.w,r.h);if(blank)return;if(r.error){put('完整院名待调整',60,INK,540,r.y+45,'center');return;}const heights=r.lines.map(t=>metrics(t,r.em).h);let y=r.y+(r.h-heights.reduce((a,b)=>a+b,0)-(r.lines.length-1)*12)/2;r.lines.forEach((t,i)=>{put(t,r.em,INK,540,y,'center');y+=heights[i]+12;});}
function drawIdentity(r,blank){card(r.x,r.y,r.w,r.h,{fill:CORAL,angle:3,stroke:true,tail:true});if(blank)return;const lines=r.error?['待指定社交媒体名']:r.lines,em=r.error?50:r.em;const hs=lines.map(t=>metrics(t,em).h);ctx.save();ctx.translate(r.x+r.w/2,r.y+r.h/2);ctx.rotate(-3*Math.PI/180);let y=-(hs.reduce((a,b)=>a+b,0)+(lines.length-1)*8)/2;lines.forEach((t,i)=>{put(t,em,CREAM,0,y,'center');y+=hs[i]+8;});ctx.restore();}
function footerFade(brand){const start=Math.round(brand.y-34),end=Math.round(brand.y+20);ctx.save();for(let y=start;y<end;y++){const t=(y-start)/(end-start);ctx.globalAlpha=t*t*(3-2*t);ctx.drawImage(bases.background,0,y,1080,1,0,y,1080,1);}ctx.globalAlpha=1;ctx.drawImage(bases.background,0,end,1080,1920-end,0,end,1080,1920-end);ctx.restore();}

 function paintPhoto(i,points){
   const spec=input.photos[i],im=photos[i],crop=spec.crop||[0,0,1,1],focus=spec.focus||[.5,.5];
   const xs=points.map(p=>p[0]),ys=points.map(p=>p[1]),x=Math.min(...xs),y=Math.min(...ys),w=Math.max(...xs)-x,h=Math.max(...ys)-y;
   const cw=(crop[2]-crop[0])*im.width,ch=(crop[3]-crop[1])*im.height,scale=Math.max(w/cw,h/ch),sw=w/scale,sh=h/scale;
   ctx.save();polygon(ctx,points);ctx.clip();ctx.drawImage(im,crop[0]*im.width+(cw-sw)*focus[0],crop[1]*im.height+(ch-sh)*focus[1],sw,sh,x,y,w,h);ctx.restore();
   polygon(ctx,points);ctx.strokeStyle=CREAM;ctx.lineWidth=7;ctx.lineJoin='round';ctx.stroke();
 }
 function render(){
   const l=layout();lastLayout=l;ctx.drawImage(background,0,0);
   paintPhoto(0,[[0,643],[694,590],[601,1100],[0,1138]]);paintPhoto(1,[[700,619],[1080,553],[1080,1042],[600,1120]]);paintPhoto(2,[[0,1143],[606,1101],[526,1651],[0,1690]]);
   ctx.save();ctx.shadowColor=CREAM;ctx.shadowBlur=0;ctx.shadowOffsetX=0;ctx.shadowOffsetY=0;
   // Alpha-silhouette dilation produces the outline without changing face pixels.
   const mask=document.createElement('canvas');mask.width=avatar.width;mask.height=avatar.height;const mc=mask.getContext('2d');mc.drawImage(avatar,0,0);mc.globalCompositeOperation='source-in';mc.fillStyle=CREAM;mc.fillRect(0,0,mask.width,mask.height);
   const av=input.avatar_box||[344,717,avatar.width,avatar.height];for(let a=0;a<Math.PI*2;a+=Math.PI/12)ctx.drawImage(mask,av[0]+Math.cos(a)*4,av[1]+Math.sin(a)*4,av[2],av[3]);ctx.drawImage(avatar,...av);ctx.restore();
   const header=input.header_text,fit=sizedFont([header],56,64,660,75),em=fit.em,w=Math.max(560,Math.min(760,metrics(header,em).w+100));card(88,92,w,100);polygon(ctx,[[76,111],[137,145],[70,185]],CORAL);put(header,em,INK,88+w/2,111,'center');
   footerFade(l.brand);drawTitle(l.title);for(let i=0;i<3;i++)drawTag(state.tags[i],l.tags.cards[i],l.positions[i],l.tags.em,i?4:3,false);drawIdentity(l.identity,false);drawName(l.brand,false);
   if(!fit.fit)l.errors.push('固定头标过长，请调整项目头标。');
   canvas.dataset.ready=String(!l.errors.length);canvas.dataset.labelFontSizes=[l.tags.em,l.tags.em,l.tags.em].join(',');onchange(l);return l;
 }
 const originalTitle = state.title;
 let titleAdjustment;
 if (input.automaticTitleFallback && titleLayout().error) {
   state.title = originalTitle.replace(/\n/g, '');
   if (titleLayout().error) state.highlights = '';
   if (titleLayout().error) state.title = input.automaticTitleFallback;
   if (titleLayout().error) state.title = '养老机构实地探访';
   if (!titleLayout().error) titleAdjustment = {original:originalTitle, resolved:state.title};
 }
 render();
 return {ready:true,report:()=>({...lastLayout,identity:{...input.identity},institution_name:state.brand,title_text:state.title,title_highlights:words(),titleAdjustment,label_texts:[...state.tags],imageGenerationUsed:false}),png:()=>{if(lastLayout.errors.length)throw new Error(lastLayout.errors.join('; '));return canvas.toDataURL('image/png');},
 set:patch=>{Object.assign(state,patch);return render();},replacePhoto:async(i,url)=>{photos[i]=await image(url);input.photos[i]={data:url};return render();}};
}
