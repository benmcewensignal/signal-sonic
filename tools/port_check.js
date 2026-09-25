// Run the browser port on the same samples the analyser measured, and compare.
const fs=require('fs'); const {JSDOM}=require('jsdom');
let page=fs.readFileSync(process.argv[2],'utf8'); const feats=fs.readFileSync(process.argv[3],'utf8');
page=page.replace('function embedding(', feats+'\nwindow.RF={embedding:embedding,melOnsetOf:melOnsetOf,rhythmVectorOf:rhythmVectorOf,loudnessOf:loudnessOf,energyCurveOf:energyCurveOf,bassWeightOf:bassWeightOf,drumDensityOf:drumDensityOf,swingOf:swingOf};\nfunction embedding(');
const dom=new JSDOM(page,{runScripts:'dangerously',pretendToBeVisual:true,url:'https://www.earlysignal.live/sonic',beforeParse(w){ w.matchMedia=()=>({matches:false,addListener(){},removeListener(){}}); w.HTMLCanvasElement.prototype.getContext=()=>null; w.navigator.serviceWorker=undefined; w.fetch=(u)=>{ const f='site'+String(u).replace(/^https?:\/\/[^/]+/,'').split('?')[0]; if(!fs.existsSync(f)) return Promise.resolve({ok:false,json:()=>Promise.reject()}); return Promise.resolve({ok:true,json:()=>Promise.resolve(JSON.parse(fs.readFileSync(f,'utf8')))}); }; w.atob=(x)=>Buffer.from(x,'base64').toString('binary'); }});
const w=dom.window; const O=JSON.parse(fs.readFileSync('portcheck/oracle.json','utf8'));
setTimeout(async()=>{ try{ await w.READER.load(); let agree=0, accO=0, accP=0; const R=w.RF, D={embedding:[],rhythm:[],loud:[],energy:[],bass:[],density:[],swing:[]};
  const mx=(a,b)=>Math.max(...a.map((v,k)=>Math.abs(v-b[k])));
  for(const o of O){ const b=fs.readFileSync('portcheck/'+o.i+'.f32'); const y=new Float32Array(b.buffer,b.byteOffset,b.length/4); const on=R.melOnsetOf(y);
    D.embedding.push(mx(Array.from(R.embedding(y)),o.embedding)); D.rhythm.push(mx(R.rhythmVectorOf(y),o.rhythm_vector)); D.loud.push(Math.abs(R.loudnessOf(y)-o.loudness));
    D.energy.push(mx(R.energyCurveOf(y),o.energy_curve)); D.bass.push(Math.abs(R.bassWeightOf(y)-o.bass_weight)); D.density.push(Math.abs(R.drumDensityOf(on)-o.drum_density)); D.swing.push(Math.abs(R.swingOf(on)-o.drum_swing));
    const vO=(await w.READER.readEmb(o.embedding)).votes[0][0], vP=(await w.READER.readEmb(Array.from(R.embedding(y)))).votes[0][0]; agree+= vO===vP; accO+= vO===o.scene; accP+= vP===o.scene; }
  const med=a=>{ const s=[...a].sort((p,q)=>p-q); return s[Math.floor(s.length/2)]; }, wst=a=>Math.max(...a);
  const n=O.length, vote=`scene vote: analyser numbers right ${Math.round(100*accO/n)}%, port numbers right ${Math.round(100*accP/n)}%, same call ${Math.round(100*agree/n)}%`;
  const rd=JSON.parse(fs.readFileSync('site/data/reader.json','utf8')); const mur=rd.mu_raw, a1=rd.ax1, a2=rd.ax2, sp=rd.spread;
  const proj=e=>{ let x=0,y=0; for(let k=0;k<45;k++){ const r=e[k]-mur[k]; x+=r*a1[k]; y+=r*a2[k]; } return [x,y]; };
  const edge=p=>(p[0]<sp[0]||p[0]>sp[1]||p[1]<sp[2]||p[1]>sp[3]);
  const neutral=e=>{ const v=Array.from(e); for(let k=26;k<38;k++) v[k]=mur[k]; const n=Math.sqrt(v.reduce((a,b)=>a+b*b,0)); return v.map(x=>x/n); };
  let dP=[],dN=[],eP=0,e9=0,eN=0;
  for(const o of O){ const b=fs.readFileSync('portcheck/'+o.i+'.f32'); const y=new Float32Array(b.buffer,b.byteOffset,b.length/4); const pe=Array.from(R.embedding(y));
    const p9=proj(o.emb29), pp=proj(pe), pn=proj(neutral(pe)); dP.push(Math.hypot(pp[0]-p9[0],pp[1]-p9[1])); dN.push(Math.hypot(pn[0]-p9[0],pn[1]-p9[1])); eP+=edge(pp); e9+=edge(p9); eN+=edge(pn); }
  const md=a=>{ if(!a.length) return 'n/a'; const s=[...a].sort((p,q)=>p-q); return s[Math.floor(s.length/2)].toFixed(3); };
  const place=`map position vs 2.9: device median shift ${md(dP)}, chroma-neutral ${md(dN)} | off the map edge: 2.9 ${e9}, device ${eP}, chroma-neutral ${eN} of ${O.length} | map spans ${(sp[1]-sp[0]).toFixed(2)} by ${(sp[3]-sp[2]).toFixed(2)}`;
  console.log('::notice title=map placement on real previews::'+place);
  // the exact path a listener's file takes: the page's reader, scaled by the span the page uses,
  // against the position the map already holds for the same record
  try{
    const wm=JSON.parse(fs.readFileSync('site/data/walkmap.json','utf8')); const i8=x=>{ const b=Buffer.from(x,'base64'); return new Int8Array(b.buffer,b.byteOffset,b.length); };
    const WD=i8(wm.driving), WF=i8(wm.defined), at={}; wm.ids.forEach((t,k)=>{ at[t]=k; });
    const q=(v,lo,hi)=>Math.max(-127,Math.min(127,Math.round((v-lo)/(hi-lo)*254-127)));
    let dx=[],dy=[],clampDev=0,clampMap=0,n=0,sx=0,sy=0;
    for(const o of O){ const k=at[o.track_id]; if(k===undefined) continue;
      const b=fs.readFileSync('portcheck/'+o.i+'.f32'); const y=new Float32Array(b.buffer,b.byteOffset,b.length/4);
      const r=await w.READER.read(y); const px=q(r.pos[0],sp[0],sp[1]), py=q(r.pos[1],sp[2],sp[3]);
      dx.push(Math.abs(px-WD[k])); dy.push(Math.abs(py-WF[k])); sx+=px-WD[k]; sy+=py-WF[k]; n++;
      clampDev+=(Math.abs(px)===127||Math.abs(py)===127); clampMap+=(Math.abs(WD[k])===127||Math.abs(WF[k])===127); }
    const msg=`${n} previews on the map: the page places them a median ${md(dx)} across and ${md(dy)} up from where the map holds them (scale -127 to 127), average offset ${(sx/Math.max(n,1)).toFixed(1)} across and ${(sy/Math.max(n,1)).toFixed(1)} up; pinned to an edge: page ${clampDev}, map ${clampMap}`;
    console.log('::notice title=page placement against the map::'+msg);
  }catch(e){ console.log('::warning::placement check failed: '+String(e).slice(0,200)); }
  let devFail=0; const dev=O.map(o=>{ try{ const b=fs.readFileSync('portcheck/'+o.i+'.f32'); const x=w.readerNumbers(new Float32Array(b.buffer,b.byteOffset,b.length/4)); return x.every(Number.isFinite)?x:(devFail++,null); }catch(e){ devFail++; console.log('::warning::device numbers failed on preview '+o.i+': '+String(e&&e.message).slice(0,120)); return null; } });
  if(devFail) console.log('::warning::device numbers unavailable for '+devFail+' of '+O.length+' previews');
  fs.writeFileSync('portcheck/device.json', JSON.stringify(dev));
  const line=vote+' | '+Object.entries(D).map(([k,v])=>`${k}: median ${med(v).toExponential(1)}, worst ${wst(v).toExponential(1)}`).join(' | ');
  console.log(`::notice title=port check on ${O.length} real previews::${line}`); console.log(line);
  const bad=Object.entries(D).filter(([k,v])=>k!=='embedding' && med(v)>1e-4); if(bad.length){ console.log('::error::port differs from the analyser on: '+bad.map(x=>x[0]+' (median '+med(x[1])+')').join(', ')); process.exit(1); } process.exit(0); }catch(e){ console.log('::error title=port check crashed::'+String(e&&e.stack||e).replace(/\n/g,' | ').slice(0,600)); process.exit(1); } },1500);
