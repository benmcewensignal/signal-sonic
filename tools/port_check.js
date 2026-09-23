// Run the browser port on the same samples the analyser measured, and compare.
const fs=require('fs'); const {JSDOM}=require('jsdom');
let page=fs.readFileSync(process.argv[2],'utf8'); const feats=fs.readFileSync(process.argv[3],'utf8');
page=page.replace('function embedding(', feats+'\nwindow.RF={embedding:embedding,melOnsetOf:melOnsetOf,rhythmVectorOf:rhythmVectorOf,loudnessOf:loudnessOf,energyCurveOf:energyCurveOf,bassWeightOf:bassWeightOf,drumDensityOf:drumDensityOf,swingOf:swingOf};\nfunction embedding(');
const dom=new JSDOM(page,{runScripts:'dangerously',pretendToBeVisual:true,url:'https://www.earlysignal.live/sonic',beforeParse(w){ w.matchMedia=()=>({matches:false,addListener(){},removeListener(){}}); w.HTMLCanvasElement.prototype.getContext=()=>null; w.navigator.serviceWorker=undefined; w.fetch=(u)=>{ const f='site'+String(u).replace(/^https?:\/\/[^/]+/,'').split('?')[0]; if(!fs.existsSync(f)) return Promise.resolve({ok:false,json:()=>Promise.reject()}); return Promise.resolve({ok:true,json:()=>Promise.resolve(JSON.parse(fs.readFileSync(f,'utf8')))}); }; w.atob=(x)=>Buffer.from(x,'base64').toString('binary'); }});
const w=dom.window; const O=JSON.parse(fs.readFileSync('portcheck/oracle.json','utf8'));
setTimeout(async()=>{ await w.READER.load(); let agree=0, accO=0, accP=0; const R=w.RF, D={embedding:[],rhythm:[],loud:[],energy:[],bass:[],density:[],swing:[]};
  const mx=(a,b)=>Math.max(...a.map((v,k)=>Math.abs(v-b[k])));
  for(const o of O){ const b=fs.readFileSync('portcheck/'+o.i+'.f32'); const y=new Float32Array(b.buffer,b.byteOffset,b.length/4); const on=R.melOnsetOf(y);
    D.embedding.push(mx(Array.from(R.embedding(y)),o.embedding)); D.rhythm.push(mx(R.rhythmVectorOf(y),o.rhythm_vector)); D.loud.push(Math.abs(R.loudnessOf(y)-o.loudness));
    D.energy.push(mx(R.energyCurveOf(y),o.energy_curve)); D.bass.push(Math.abs(R.bassWeightOf(y)-o.bass_weight)); D.density.push(Math.abs(R.drumDensityOf(on)-o.drum_density)); D.swing.push(Math.abs(R.swingOf(on)-o.drum_swing));
    const vO=(await w.READER.readEmb(o.embedding)).votes[0][0], vP=(await w.READER.readEmb(Array.from(R.embedding(y)))).votes[0][0]; agree+= vO===vP; accO+= vO===o.scene; accP+= vP===o.scene; }
  const med=a=>{ const s=[...a].sort((p,q)=>p-q); return s[Math.floor(s.length/2)]; }, wst=a=>Math.max(...a);
  const n=O.length, vote=`scene vote: analyser numbers right ${Math.round(100*accO/n)}%, port numbers right ${Math.round(100*accP/n)}%, same call ${Math.round(100*agree/n)}%`;
  const line=vote+' | '+Object.entries(D).map(([k,v])=>`${k}: median ${med(v).toExponential(1)}, worst ${wst(v).toExponential(1)}`).join(' | ');
  console.log(`::notice title=port check on ${O.length} real previews::${line}`); console.log(line);
  const bad=Object.entries(D).filter(([k,v])=>k!=='embedding' && med(v)>1e-4); if(bad.length){ console.log('::error::port differs from the analyser on: '+bad.map(x=>x[0]).join(', ')); process.exit(1); } process.exit(0); },1500);
