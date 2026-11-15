/* Source intake and adaptive background; no external source executes code in the UI. */
'use strict';
const postJSON=(path,data)=>api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
let intakeProject='', activeIntakeJob='', backgroundStarted=false;
function updatePipelineButton(){
  $('runPipeline').disabled=!(current?.id===intakeProject && ['newAudio','newMusic','newBlur','newNiche'].every(id=>$(id).value) && ($('newAudio').value!=='keep_raw_low'||$('rawVerified').checked));
}
async function refreshProjectsAndSelect(name){
  let data=await api('/api/projects');$('project').replaceChildren();
  for(let item of data.projects){let opt=document.createElement('option');opt.value=item;opt.textContent=item;$('project').append(opt)}
  $('project').value=name;await loadProject(name);updatePipelineButton();
}
function describeSource(result){
  intakeProject=result.project;
  let m=result.metadata||{};$('ingestInfo').textContent=`${result.source} · ${m.width||'?'}×${m.height||'?'} · ${Number(m.duration||0).toFixed(1)}s · ${m.fps||'?'} fps. Chọn 4 Pre-Flight trước khi chạy.`;
  refreshProjectsAndSelect(result.project).catch(e=>notify(e.message));
}
function initIntake(){
  autoAgents=false;$('agentCursorsLayer').classList.add('hidden');$('swarmToggle').textContent='⚫ Con trỏ Agent: TẮT';
  for(let id of ['s_blur','s_sharp','s_contrast'])$('visualSliders').append($(id).closest('.slider'));
  function mode(link){$('localPane').classList.toggle('hidden',link);$('linkPane').classList.toggle('hidden',!link);$('localTab').classList.toggle('selected',!link);$('linkTab').classList.toggle('selected',link)}
  $('localTab').onclick=()=>mode(false);$('linkTab').onclick=()=>mode(true);
  $('pickLocal').onclick=async()=>{try{let item=await postJSON('/api/pick_local_video',{project_name:$('newProjectName').value});if(!item.cancelled)describeSource(item)}catch(e){notify('Hộp thoại file: '+e.message+' · thử kéo-thả video.')}};
  const zone=$('dropzone');
  zone.ondragover=e=>{e.preventDefault();zone.classList.add('dragging')};zone.ondragleave=()=>zone.classList.remove('dragging');
  zone.ondrop=async e=>{e.preventDefault();zone.classList.remove('dragging');let file=e.dataTransfer.files[0];if(!file)return;
    if(!/\.(mp4|mov|mkv|avi|webm)$/i.test(file.name)){notify('Định dạng video không được hỗ trợ');return}
    try{zone.textContent='Đang tải file lên máy chủ...';let item=await api('/api/upload_local_video',{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Filename':file.name,'X-Project-Name':$('newProjectName').value},body:file});describeSource(item)}
    catch(err){notify('Tải file lỗi: '+err.message)}finally{zone.textContent='Hoặc thả .mp4 / .mov / .mkv / .avi / .webm vào đây'}};
  $('sourceUrl').oninput=()=>{let value=$('sourceUrl').value;let source=/youtu/i.test(value)?'▶ YouTube':/facebook|fb\.watch/i.test(value)?'f Facebook':/tiktok|douyin/i.test(value)?'♪ TikTok / Douyin':'🌐 Link video';$('downloadSource').textContent='⬇️ Tải '+source+' & đưa vào bàn dựng'};
  $('downloadSource').onclick=async()=>{try{let result=await postJSON('/api/download_source_url',{url:$('sourceUrl').value,project_name:$('newProjectName').value,use_cookies:$('useCookies').checked,browser:$('cookieBrowser').value,quality:$('downloadQuality').value});activeIntakeJob=result.job_id;notify('Đã xếp hàng tải nguồn · '+result.job_id)}catch(e){notify('Tải nguồn: '+e.message)}};
  for(let id of ['newAudio','newMusic','newBlur','newNiche','rawVerified'])$(id).onchange=updatePipelineButton;
  $('project').addEventListener('change',()=>{setTimeout(updatePipelineButton,0)});
  $('runPipeline').onclick=async()=>{if(!intakeProject||current?.id!==intakeProject)return;let data={project:intakeProject,source_audio_mode:$('newAudio').value,source_audio_verified:$('rawVerified').checked,text_blur_mode:$('newBlur').value,bgm_style:$('newMusic').value,niche_mode:$('newNiche').value,transition_style:style,clean_outro:$('newOutro').checked,brand_title:'IRONAXIS LAB',sliders:Object.fromEntries(Object.keys(defaults).map(k=>[k,Number($('s_'+k).value)]))};try{let job=await postJSON('/api/run_full_pipeline',data);$('runPipeline').disabled=true;notify('Pipeline đang chạy · '+job.job_id)}catch(e){notify('Pipeline: '+e.message)}};
  $('brandTitle').oninput=drawOverlay;
  const baseDraw=drawOverlay;
  drawOverlay=function(){baseDraw();if(!selected||!$('livePreviewOverlay').width||!['top_blur_brand','both_top_bottom_blur'].includes($('blurMode').value))return;
    let title=$('brandTitle').value.trim();if(!title||title==='IRONAXIS LAB')return;let canvas=$('livePreviewOverlay'),ctx=canvas.getContext('2d');let dpr=Math.min(devicePixelRatio||1,2),w=canvas.width/dpr,h=canvas.height/dpr,short=selected.relative.startsWith('shorts/');ctx.save();ctx.scale(dpr,dpr);ctx.fillStyle='rgba(9,22,38,.97)';ctx.fillRect(w*.04,0,w*.92,h*(short?.095:.075));ctx.fillStyle='#fff';ctx.textAlign='center';ctx.font=`bold ${Math.max(13,w*(short?.045:.027))}px Arial`;ctx.fillText(title.slice(0,64),w/2,h*(short?.066:.035),w*.88);ctx.restore()};
  setInterval(async()=>{try{let data=await api('/api/status');let job=data.jobs?.find(j=>j.job_id===activeIntakeJob);
    if(job){$('downloadBar').style.width=Math.min(100,job.progress_percent||0)+'%';$('ingestInfo').textContent=job.message;
      if(job.status==='completed'){activeIntakeJob='';$('downloadBar').style.width='100%';describeSource(job)}
      if(job.status==='failed'){activeIntakeJob='';notify(job.message)}}
    if(current){let n=Object.values(current.agents||{}).filter(a=>a.state==='available').length;$('agentCount').textContent=`${n}/14 có bằng chứng`}
  }catch(e){/* the existing Studio status panel reports API errors */}},2500);
}
initIntake();

function initThreeBackground(){
  if(backgroundStarted||!window.THREE||matchMedia('(prefers-reduced-motion: reduce)').matches)return initFallbackBackground();
  const canvas=$('threeBgCanvas');let renderer;
  try{renderer=new THREE.WebGLRenderer({canvas,alpha:true,antialias:false,powerPreference:'low-power'})}catch(e){return initFallbackBackground()}
  backgroundStarted=true;renderer.setPixelRatio(Math.min(devicePixelRatio||1,1.5));renderer.setSize(innerWidth,innerHeight);
  const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(53,innerWidth/innerHeight,.1,100);camera.position.z=16;
  const core=new THREE.Mesh(new THREE.TorusKnotGeometry(3,.65,128,12,2,3),new THREE.MeshBasicMaterial({color:0x00e5ff,wireframe:true,transparent:true,opacity:.22}));core.position.x=1.5;scene.add(core);
  const halo=new THREE.Mesh(new THREE.TorusGeometry(4.7,.025,6,128),new THREE.MeshBasicMaterial({color:0x6366f1,transparent:true,opacity:.4}));halo.position.x=1.5;scene.add(halo);
  const pos=new Float32Array(1200*3);for(let i=0;i<1200;i++){pos[i*3]=(Math.random()-.5)*32;pos[i*3+1]=(Math.random()-.5)*21;pos[i*3+2]=(Math.random()-.5)*18}
  const geom=new THREE.BufferGeometry();geom.setAttribute('position',new THREE.BufferAttribute(pos,3));const points=new THREE.Points(geom,new THREE.PointsMaterial({color:0x56b7ee,size:.043,transparent:true,opacity:.55}));scene.add(points);
  let last=0;function frame(now){requestAnimationFrame(frame);if(document.hidden||now-last<16)return;let dt=Math.min((now-last)/1000,.05);last=now;let busy=!video.paused||!!activeIntakeJob;core.rotation.y+=dt*(busy?.27:.12);core.rotation.x+=dt*.07;halo.rotation.z+=dt*(busy?.14:.04);points.rotation.y+=dt*(busy?.018:.008);points.material.color.setHex(busy?0x8b89ff:0x56b7ee);renderer.render(scene,camera)}requestAnimationFrame(frame);
  addEventListener('resize',()=>{camera.aspect=innerWidth/innerHeight;camera.updateProjectionMatrix();renderer.setSize(innerWidth,innerHeight)});
}
function initFallbackBackground(){
  if(backgroundStarted)return;backgroundStarted=true;const canvas=$('threeBgCanvas'),ctx=canvas.getContext('2d');if(!ctx)return;
  let dots=Array.from({length:140},()=>({x:Math.random(),y:Math.random(),r:Math.random()*1.4+.3}));
  function paint(now){requestAnimationFrame(paint);if(document.hidden)return;let w=canvas.width=innerWidth,h=canvas.height=innerHeight;ctx.clearRect(0,0,w,h);
    let t=now*.0002*(video.paused?1:2);for(let d of dots){let x=d.x*w,y=(d.y+.02*Math.sin(t*3+d.x*12))*h;ctx.beginPath();ctx.arc(x,y,d.r,0,Math.PI*2);ctx.fillStyle='rgba(74,198,250,.28)';ctx.fill()}
    ctx.save();ctx.translate(w*.55,h*.38);ctx.rotate(t*.25);ctx.strokeStyle='rgba(0,229,255,.17)';for(let i=0;i<12;i++){ctx.rotate(Math.PI/6);ctx.strokeRect(-75,-75,150,150)}ctx.restore()}
  requestAnimationFrame(paint);
}
setTimeout(()=>{if(!backgroundStarted)initFallbackBackground()},3500);
