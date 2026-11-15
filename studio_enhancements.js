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

// Visual-only pointer feedback; event delegation survives dynamic asset/clip redraws.
let spotlightHost=null;
addEventListener('pointermove',e=>{
  document.documentElement.style.setProperty('--mx',e.clientX+'px');
  document.documentElement.style.setProperty('--my',e.clientY+'px');
  const host=e.target.closest?.('.glass-drawer, .panel, .box');
  if(host!==spotlightHost){
    if(spotlightHost){spotlightHost.style.removeProperty('--spot-x');spotlightHost.style.removeProperty('--spot-y')}
    spotlightHost=host;
  }
  if(host){const r=host.getBoundingClientRect();host.style.setProperty('--spot-x',e.clientX-r.left+'px');host.style.setProperty('--spot-y',e.clientY-r.top+'px')}
},{passive:true});
addEventListener('pointerdown',e=>{
  if(matchMedia('(prefers-reduced-motion: reduce)').matches)return;
  const target=e.target.closest?.('button:not(:disabled), .clip, .glass-drawer summary, #dropzone');
  if(!target)return;
  const r=target.getBoundingClientRect(),ripple=document.createElement('span');
  ripple.className='water-ripple';
  ripple.style.width=ripple.style.height=Math.max(r.width,r.height,36)+'px';
  ripple.style.left=e.clientX-r.left+'px';ripple.style.top=e.clientY-r.top+'px';
  target.append(ripple);
  setTimeout(()=>ripple.remove(),600);
},{passive:true});

function initThreeBackground(){
  if(backgroundStarted)return;
  if(!window.THREE)return initFallbackBackground();
  const canvas=$('threeBgCanvas');let renderer;
  try{
    renderer=new THREE.WebGLRenderer({canvas,alpha:true,antialias:false,powerPreference:'low-power'});
    renderer.setPixelRatio(Math.min(devicePixelRatio||1,1.5));
    renderer.setSize(innerWidth,innerHeight);
    renderer.toneMapping=THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure=1.25;
    const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(45,innerWidth/innerHeight,.1,100);
    scene.background=new THREE.Color(0x000000);
    camera.position.z=26;
    scene.add(new THREE.AmbientLight(0x323b54,.7));
    const key=new THREE.DirectionalLight(0xe9f9ff,1.75);key.position.set(10,16,18);scene.add(key);
    const cyan=new THREE.PointLight(0x00f5ff,5.2,48);cyan.position.set(-11,6,11);scene.add(cyan);
    const violet=new THREE.PointLight(0x8b5cf6,4.8,48);violet.position.set(12,-7,10);scene.add(violet);
    const emerald=new THREE.PointLight(0x10b981,2.5,36);emerald.position.set(-7,-8,7);scene.add(emerald);

    // Separate crisp neon dust clouds keep the background dark, not a tinted wash.
    const stars=[];
    for(const color of [0x00f5ff,0x8b5cf6,0x10b981]){
      const count=110,positions=new Float32Array(count*3);
      for(let i=0;i<count;i++){
        positions[i*3]=(Math.random()-.5)*50;
        positions[i*3+1]=(Math.random()-.5)*28;
        positions[i*3+2]=-6-Math.random()*19;
      }
      const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.BufferAttribute(positions,3));
      const cloud=new THREE.Points(geometry,new THREE.PointsMaterial({color,size:.105,transparent:true,opacity:.82,sizeAttenuation:true,depthWrite:false}));
      scene.add(cloud);stars.push(cloud);
    }
    const water=new THREE.MeshPhysicalMaterial({color:0xd8e8ff,transmission:1.0,opacity:1,transparent:true,roughness:.02,metalness:0,ior:1.5,thickness:4.0,specularIntensity:1.0,clearcoat:1.0,clearcoatRoughness:.02,side:THREE.FrontSide});
    const mainGeometry=new THREE.SphereGeometry(2.75,40,32),original=mainGeometry.attributes.position.array.slice();
    const mainDrop=new THREE.Mesh(mainGeometry,water);mainDrop.position.z=4;scene.add(mainDrop);
    const smallGeometry=new THREE.SphereGeometry(1,24,20),drops=[];
    for(let i=0;i<6;i++){
      const mesh=new THREE.Mesh(smallGeometry,water),angle=i*Math.PI/3;
      mesh.scale.setScalar(.55+(i%3)*.18);mesh.position.z=2+(i%2);
      scene.add(mesh);drops.push({mesh,angle,speed:.2+i*.055});
    }
    let targetX=0,targetY=0,last=0,active=true;
    const pointer=e=>{targetX=(e.clientX/innerWidth-.5)*27;targetY=(.5-e.clientY/innerHeight)*15};
    const resize=()=>{camera.aspect=innerWidth/innerHeight;camera.updateProjectionMatrix();renderer.setSize(innerWidth,innerHeight)};
    addEventListener('pointermove',pointer);addEventListener('resize',resize);
    canvas.addEventListener('webglcontextlost',e=>{e.preventDefault();active=false;backgroundStarted=false;removeEventListener('pointermove',pointer);removeEventListener('resize',resize);initFallbackBackground()},{once:true});
    backgroundStarted=true;
    function frame(now){
      if(!active)return;
      if(!matchMedia('(prefers-reduced-motion: reduce)').matches)requestAnimationFrame(frame);
      if(document.hidden||now-last<32)return;last=now;
      const t=now*.001;
      mainDrop.position.x+=(targetX-mainDrop.position.x)*.075;
      mainDrop.position.y+=(targetY-mainDrop.position.y)*.075;
      cyan.position.x=mainDrop.position.x-6;cyan.position.y=mainDrop.position.y+4;
      const attr=mainGeometry.attributes.position,positions=attr.array;
      for(let i=0;i<positions.length;i+=3){
        const x=original[i],y=original[i+1],z=original[i+2];
        const wave=1+.035*Math.sin(x*1.5+t*2.3)*Math.cos(y*1.3+t*1.7);
        positions[i]=x*wave;positions[i+1]=y*wave;positions[i+2]=z*wave;
      }
      attr.needsUpdate=true;mainGeometry.computeVertexNormals();
      for(const {mesh,angle,speed} of drops){
        mesh.position.x=Math.cos(angle+t*speed)*12;
        mesh.position.y=Math.sin(angle+t*speed*1.2)*6;
        mesh.scale.y=mesh.scale.x*(1+.055*Math.sin(t*1.7+angle));
      }
      stars.forEach((cloud,i)=>{cloud.rotation.y=t*(.012+i*.006);cloud.position.y=Math.sin(t*.18+i)*.35});
      renderer.render(scene,camera);
    }
    requestAnimationFrame(frame);
  }catch(e){
    console.warn('Studio 3D background unavailable; using Canvas 2D:',e);
    if(renderer)renderer.dispose();
    backgroundStarted=false;
    initFallbackBackground();
  }
}
function initFallbackBackground(){
  if(backgroundStarted)return;
  let canvas=$('threeBgCanvas'),ctx=canvas.getContext('2d');
  // A WebGL canvas cannot switch context types after a partially initialized renderer.
  if(!ctx){const replacement=canvas.cloneNode(false);canvas.replaceWith(replacement);canvas=replacement;ctx=canvas.getContext('2d')}
  if(!ctx)return;
  backgroundStarted=true;
  const sparks=Array.from({length:180},(_,i)=>({x:Math.random(),y:Math.random(),radius:.45+Math.random()*1.15,speed:.000001+(i%5)*.0000006,color:['#00f5ff','#8b5cf6','#10b981'][i%3]}));
  function paint(now){
    if(!document.hidden){
      const w=innerWidth,h=innerHeight;
      if(canvas.width!==w||canvas.height!==h){canvas.width=w;canvas.height=h}
      ctx.fillStyle='#000000';ctx.fillRect(0,0,w,h);
      for(const s of sparks){
        const x=((s.x+now*s.speed)%1)*w,y=((s.y+now*s.speed*.55)%1)*h;
        ctx.shadowColor=s.color;ctx.shadowBlur=7;ctx.fillStyle=s.color;
        ctx.beginPath();ctx.arc(x,y,s.radius,0,Math.PI*2);ctx.fill();
      }
      ctx.shadowBlur=0;
    }
    if(!matchMedia('(prefers-reduced-motion: reduce)').matches)requestAnimationFrame(paint);
  }
  requestAnimationFrame(paint);
}
setTimeout(()=>{if(!backgroundStarted)initFallbackBackground()},3500);
