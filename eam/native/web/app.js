const $ = id => document.getElementById(id);
let folder, parent, socket, term, fit, timer, selectedSession, connectionId, selectedName;
let lastSeen=0, terminalReady=false, resumeProbe=null;
let reconnectAllowed=false, reconnectTask=null, retryTimer=0, retryCount=0, reconnectStarted=0;
function scheduleReconnect() {
  if(!reconnectAllowed || document.hidden || navigator.onLine===false || retryTimer || reconnectTask || retryCount>=3)return;
  retryTimer=setTimeout(()=>{retryTimer=0;reconnectSession().catch(report);},Math.min(4000,500*2**retryCount));
}
async function reconnectSession() {
  if(reconnectTask)return reconnectTask;
  if(!selectedSession || $('workspace').hidden)return;
  clearTimeout(retryTimer);retryTimer=0;reconnectAllowed=true;retryCount++;
  $('reconnect').disabled=true; reconnectStarted=performance.now();
  reconnectTask=(async()=>{
    try {await api('/api/takeover',{session:selectedSession});if(reconnectAllowed)connect(selectedSession);}
    catch(error){connectionLost('재연결 실패');throw error;}
    finally{$('reconnect').disabled=false;reconnectTask=null;}
  })();
  try {await reconnectTask;} finally {if(!terminalReady && socket?.readyState!==WebSocket.CONNECTING)scheduleReconnect();}
}
function clearResumeProbe(){clearTimeout(resumeProbe?.timeout);resumeProbe=null;}
function connectionLost(label='연결 끊김') {
  clearResumeProbe(); clearInterval(timer); terminalReady=false;
  setConnection(false,label); updateLiveCursor();
  if(!$('workspace').hidden){$('reconnect').hidden=false;$('detach').hidden=true;}
  scheduleReconnect();
}
function checkResumeConnection() {
  if(document.hidden)return;
  updateViewport();
  if($('workspace').hidden){if(!$('home').hidden)refresh().catch(report);return;}
  if(!reconnectAllowed)return;
  retryCount=0;
  if(socket?.readyState===WebSocket.CONNECTING || reconnectTask)return;
  if(!socket || socket.readyState!==WebSocket.OPEN){connectionLost();return;}
  clearResumeProbe();
  const ws=socket, at=performance.now();
  lastSeen=Date.now();
  setConnection(false,'연결 확인 중');
  resumeProbe={at,timeout:setTimeout(()=>{
    if(socket!==ws)return;
    connectionLost('응답 없음 · 재연결 필요');ws.close();
  },4000)};
  send({type:'ping',at});
}
const report = error => {
  const text=error.message || error;
  $('status').textContent=text;
  $('status').dataset.error=String(error instanceof Error);
  if($('composePanel').open) $('composeStatus').textContent=text;
  if(error instanceof Error && !$('workspace').hidden) $('notice').textContent=text;
};
function setConnection(connected, label) {
  const light=$('connectionState');
  light.dataset.connected=String(connected);
  if(!connected)$('latency').textContent='—';
  light.setAttribute('aria-label',label || (connected?'연결됨':'연결 끊김'));
  light.title=label || (connected?'연결됨':'연결 끊김');
}
function showHome() {
  $('workspace').hidden=true; $('home').hidden=false;
  $('detach').hidden=true; $('reconnect').hidden=true; $('sessionList').hidden=true;
  $('sessionName').textContent='EAM'; $('sessionName').title='EAM'; $('latency').textContent='—';
}
function terminalTheme() {
  const light=document.documentElement.dataset.theme==='light';
  const palette=light?{
    black:'#161616',red:'#a4262c',green:'#236b32',yellow:'#795500',
    blue:'#204fad',magenta:'#82358b',cyan:'#126675',white:'#44443f',
    brightBlack:'#595953',brightRed:'#ac2028',brightGreen:'#19672c',brightYellow:'#765000',
    brightBlue:'#164ca7',brightMagenta:'#7b2c86',brightCyan:'#005f70',brightWhite:'#242421'
  }:{
    black:'#282828',red:'#ef7777',green:'#8dc782',yellow:'#e5c679',
    blue:'#89aff0',magenta:'#cc9bdf',cyan:'#79c5cc',white:'#d4d4cf',
    brightBlack:'#9b9b94',brightRed:'#ff9696',brightGreen:'#ace09f',brightYellow:'#f5dc96',
    brightBlue:'#adc8ff',brightMagenta:'#e2b6f0',brightCyan:'#a0dfe5',brightWhite:'#eeeeea'
  };
  return {...palette,background:light?'#f4f2e9':'#111111',foreground:light?'#161616':'#eeeeea',
    cursor:light?'#161616':'#eeeeea',cursorAccent:light?'#f4f2e9':'#111111',
    selectionBackground:light?'#00000030':'#ffffff40'};
}
function setTheme(theme) {
  document.documentElement.dataset.theme=theme;
  const label=theme==='dark'?'라이트 모드':'다크 모드';
  $('themeToggle').title=label; $('themeToggle').setAttribute('aria-label',label);
  document.querySelector('meta[name="theme-color"]').content=terminalTheme().background;
  if(term){term.options.theme=terminalTheme();term.options.minimumContrastRatio=theme==='light'?7:4.5;}
  try { localStorage.setItem('eam-theme',theme); } catch {}
}
let savedTheme;
try { savedTheme=localStorage.getItem('eam-theme'); } catch {}
setTheme(savedTheme==='light'||savedTheme==='dark'?savedTheme:(matchMedia('(prefers-color-scheme: light)').matches?'light':'dark'));
action('themeToggle',()=>setTheme(document.documentElement.dataset.theme==='dark'?'light':'dark'));
async function api(path, data) {
  let response;
  try { response = await fetch(path, {signal:AbortSignal.timeout(10000),...(data === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})}); }
  catch(error) { if($('workspace').hidden)setConnection(false); throw error; }
  if($('workspace').hidden)setConnection(response.ok);
  if (!response.ok) { let q = await response.json().catch(()=>({})); throw Error(q.error || `요청 실패 (${response.status})`); }
  return response.json();
}
function action(id, callback) { $(id).onclick = () => Promise.resolve().then(callback).catch(report); }
async function folders(path) {
  const q = await api('/api/folders' + (path ? '?path='+encodeURIComponent(path) : ''));
  folder=q.path; parent=q.parent; $('path').textContent=folder; $('parent').disabled=!parent;
  $('folders').replaceChildren(...q.children.map(entry=>{
    const button=document.createElement('button'); button.textContent='▸ '+entry.name;
    button.onclick=()=>folders(entry.path).catch(report); return button;
  }));
}
async function refresh() {
  const q=await api('/api/sessions');
  $('sessions').replaceChildren(...q.sessions.map(s=>{
    const b=document.createElement('button');
    b.textContent=`${s.name && s.name!==s.session.split('/').pop()?s.name:s.provider}${s.remote?' · remote':''}\n${s.directory}`;
    b.disabled=!!s.temporary;
    const label=b.textContent;
    b.onclick=async()=>{
      b.disabled=true; b.textContent='조작권 가져오는 중…\n'+s.directory;
      report('PC 연결을 전환하고 있습니다…');
      try { await api('/api/takeover',{session:s.session}); connect(s.session,s.name && s.name!==s.session.split('/').pop()?s.name:s.provider); }
      catch(e) { report(e); b.textContent=label+'\n전환 실패: '+e.message; b.disabled=false; }
    }; return b;
  }));
  if(!q.sessions.length) $('sessions').textContent='실행 중인 세션이 없습니다.';
}
async function enter() {
  const config=await api('/api/config');
  if(config.demo && !$('provider').querySelector('[value="Demo"]')) $('provider').add(new Option('Demo','Demo'));
  $('login').hidden=true; $('home').hidden=false;
  await Promise.all([refresh(),folders()]);
  report('PC에서 사용 중이어도 세션을 선택하면 조작권을 가져옵니다.');
}
function send(q) { if(socket?.readyState===WebSocket.OPEN) socket.send(JSON.stringify(q)); }
let resizeFrame;
function resize() {
  cancelAnimationFrame(resizeFrame);
  resizeFrame=requestAnimationFrame(()=>{
    if (!fit || !term || $('workspace').hidden) return;
    const before=[term.cols,term.rows];
    fit.fit();
    if(revealInput){term.scrollToBottom();revealInput=false;}
    if(before[0]!==term.cols || before[1]!==term.rows) send({type:'resize',rows:term.rows,cols:term.cols});
    updateScroll();
  });
}
let viewportBaseline=0, viewportOrientation='', revealInput=false;
function updateViewport() {
  const viewport=window.visualViewport;
  const height=viewport?.height || innerHeight;
  const orientation=matchMedia('(orientation: portrait)').matches?'portrait':'landscape';
  // screen.orientation is stable while an on-screen keyboard changes aspect ratio.
  const direction=screen.orientation?.type || orientation;
  if(direction!==viewportOrientation){viewportBaseline=0;viewportOrientation=direction;}
  viewportBaseline=Math.max(viewportBaseline,innerHeight,height);
  const focused=document.activeElement?.matches('textarea,input');
  const keyboard=!!focused && viewportBaseline-height>120 && (!viewport || viewport.scale===1);
  const wasOpen=document.body.dataset.keyboard==='true';
  document.body.dataset.keyboard=String(keyboard);
  document.body.style.setProperty('--app-height', `${height}px`);
  document.body.style.setProperty('--app-top', `${viewport?.offsetTop || 0}px`);
  if(keyboard && !wasOpen && editMode==='live')revealInput=true;
  resize();
}
let cursorFrame=0;
function updateLiveCursor() {
  if(cursorFrame)return;
  cursorFrame=requestAnimationFrame(()=>{cursorFrame=0;renderLiveCursor();});
}
function renderLiveCursor() {
  const caret=$('liveCursor');
  if(!caret || !term)return;
  const b=term.buffer.active, row=b.baseY+b.cursorY-b.viewportY;
  caret.hidden=editMode!=='live' || row<0 || row>=term.rows || !terminalReady;
  if(caret.hidden)return;
  const screen=$('terminal').querySelector('.xterm-screen').getBoundingClientRect();
  const parent=$('terminal').getBoundingClientRect();
  caret.style.left=`${screen.left-parent.left+Math.min(b.cursorX,term.cols-1)*screen.width/term.cols}px`;
  caret.style.top=`${screen.top-parent.top+row*screen.height/term.rows}px`;
  caret.style.height=`${screen.height/term.rows}px`;
}
function updateScroll() {
  updateLiveCursor();
  if(!term) return;
  const b=term.buffer.active, bottom=b.viewportY>=b.baseY;
  $('latest').disabled=bottom;
  $('latest').title=bottom?'맨 아래':'최신 출력으로 이동';
  $('pageUp').disabled=b.viewportY===0;
  $('pageDown').disabled=bottom;
}
function scrollOutput(pages) { stopTouchScroll(); term?.scrollPages(pages); updateScroll(); }

const terminalMetrics={receivedBytes:0,writeMs:0,writeCount:0,readyMs:0};
function connect(path,name) {
  reconnectAllowed=true;clearTimeout(retryTimer);retryTimer=0;
  const connectedAt=performance.now(), restoreAt=reconnectStarted || connectedAt; reconnectStarted=0;
  Object.assign(terminalMetrics,{receivedBytes:0,writeMs:0,writeCount:0,readyMs:0,restoreMs:0});
  if(name) selectedName=name;
  terminalReady=false; lastSeen=Date.now(); setConnection(false,'연결 중');
  selectedSession=path; connectionId=null;
  $('reconnect').hidden=true; $('detach').hidden=false; $('sessionList').hidden=false;
  clearInterval(timer); clearResumeProbe();
  if(socket) socket.close();
  stopTouchScroll();
  term?.dispose();
  $('terminal').replaceChildren();
  $('home').hidden=true; $('workspace').hidden=false; $('notice').textContent='연결 중…';
  $('sessionName').textContent=selectedName || '터미널'; $('sessionName').title=$('sessionName').textContent; report('웹에서 조작 중입니다. PC로 돌아가려면 연결을 해제하세요.');
  term=new Terminal({fontSize:parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--text-size')),fontFamily:getComputedStyle(document.documentElement).fontFamily,minimumContrastRatio:document.documentElement.dataset.theme==='light'?7:4.5,scrollback:10000,theme:terminalTheme(),allowProposedApi:false});
  fit=new FitAddon.FitAddon(); term.loadAddon(fit); term.open($('terminal')); fit.fit();
  const caret=document.createElement('span'); caret.id='liveCursor'; caret.hidden=true; caret.setAttribute('aria-hidden','true'); $('terminal').append(caret);
  term.onCursorMove(updateLiveCursor);
  term.onRender(updateLiveCursor);
  term.onScroll(updateScroll);
  term.onWriteParsed(updateScroll);
  updateScroll();
  const ws=new WebSocket(`${location.protocol==='https:'?'wss:':'ws:'}//${location.host}/ws?session=${encodeURIComponent(path)}`);
  socket=ws; ws.binaryType='arraybuffer'; const currentTerm=term;
  term.onData(data=>{if(ws.readyState===1)ws.send(JSON.stringify({type:'input',data}));});
  ws.onmessage=event=>{
    if(socket!==ws)return;
    lastSeen=Date.now(); if(terminalReady && !resumeProbe)setConnection(true);
    if(event.data instanceof ArrayBuffer) {
      const bytes=new Uint8Array(event.data), writeAt=performance.now();
      terminalMetrics.receivedBytes+=bytes.length;
      currentTerm.write(bytes,()=>{if(socket!==ws)return;terminalMetrics.writeMs+=performance.now()-writeAt;terminalMetrics.writeCount++;if(ws.readyState===1)ws.send(JSON.stringify({type:'ack',bytes:bytes.length}));});
    } else {
      const q=JSON.parse(event.data);
      if(q.type==='connection') connectionId=q.id;
      if(q.type==='ready') { retryCount=0;terminalMetrics.readyMs=performance.now()-connectedAt;currentTerm.write('',()=>{if(socket===ws)terminalMetrics.restoreMs=performance.now()-restoreAt;});terminalReady=true; setConnection(true); send({type:'resize',rows:term.rows,cols:term.cols}); $('notice').textContent=q.replay_complete?'연결됨 · 승인과 선택은 아래 키로 응답하세요.':'이전 출력 재생 한도 초과 · 현재 화면을 다시 그립니다.'; resize(); }
      if(q.type==='pong') { if(resumeProbe?.at===q.at){clearResumeProbe();if(terminalReady)setConnection(true);} $('latency').textContent=`${Math.round(performance.now()-q.at)} ms`;  $('connectionState').title=`연결됨 · 왕복 ${$('latency').textContent}`; }
    }
  };
  ws.onopen=()=>{if(socket!==ws)return; resize();timer=setInterval(()=>{if(Date.now()-lastSeen>10000 && !document.hidden){connectionLost('응답 대기 시간 초과');ws.close();return;}send({type:'ping',at:performance.now()});},3000);};
  ws.onerror=()=>{if(socket===ws){setConnection(false);report(new Error('연결 실패: 재연결 버튼으로 다시 시도하세요.'));}};
  ws.onclose=()=>{if(socket!==ws)return;connectionLost();if($('workspace').hidden)return;$('reconnect').hidden=false; $('detach').hidden=true; $('notice').textContent='연결이 끊겼습니다. CLI는 계속 실행됩니다. 재연결할 수 있습니다.';};
}
action('loginButton',async()=>{await api('/login',{token:$('token').value});$('token').value='';await enter();});
action('fullscreen',async()=>{
  if(document.fullscreenElement) await document.exitFullscreen();
  else if(document.fullscreenEnabled) await document.documentElement.requestFullscreen();
  else throw Error('이 브라우저에서는 전체화면을 지원하지 않습니다.');
});
document.addEventListener('fullscreenchange',()=>{
  const active=!!document.fullscreenElement;
  $('fullscreen').title=active?'전체화면 나가기':'전체화면';
  $('fullscreen').setAttribute('aria-label',$('fullscreen').title);
  $('fullscreen').setAttribute('aria-pressed',String(active));
  updateViewport();
});
action('pageUp',()=>scrollOutput(-1));
action('pageDown',()=>scrollOutput(1));
action('latest',()=>{stopTouchScroll();term?.scrollToBottom();updateScroll();});
function openComposer() {
  if($('workspace').hidden || $('composePanel').open)return;
  $('composeStatus').textContent=socket?.readyState===WebSocket.OPEN?'':'연결이 끊겼습니다. 초안을 작성한 뒤 재연결해 주세요.';
  $('composePanel').showModal();
  $('composer').focus({preventScroll:true});
}
let editMode='buffer';
try { if(localStorage.getItem('eam-edit-mode')==='live')editMode='live'; } catch {}
function setEditMode(mode) {
  editMode=mode;
  const live=mode==='live';
  $('editMode').textContent=live?'라이브':'버퍼';
  $('editMode').setAttribute('aria-checked',String(live));
  $('editMode').title=live?'라이브 입력 · 클릭하여 버퍼 입력으로 전환':'버퍼 입력 · 클릭하여 라이브 입력으로 전환';
  try { localStorage.setItem('eam-edit-mode',mode); } catch {}
  term?.blur();
  updateLiveCursor();
}
setEditMode(editMode);
$('editMode').onclick=()=>setEditMode(editMode==='live'?'buffer':'live');
$('composeClose').onclick=()=>$('composePanel').close();
$('composePanel').addEventListener('close',()=>{
  $('composer').blur();
  $('editMode').focus({preventScroll:true});
});
// A PTY exposes cursor position, not semantic input fields. Only the current
// logical cursor line (including soft wraps) can initiate mobile input.
function isCurrentInput(event) {
  if(!term)return false;
  const screen=$('terminal').querySelector('.xterm-screen');
  if(!screen)return false;
  const rect=screen.getBoundingClientRect(), b=term.buffer.active;
  if(event.clientX<rect.left || event.clientX>=rect.right || event.clientY<rect.top || event.clientY>=rect.bottom)return false;
  const row=b.viewportY+Math.floor((event.clientY-rect.top)/(rect.height/term.rows));
  let first=b.baseY+b.cursorY, last=first;
  while(first>0 && b.getLine(first)?.isWrapped)first--;
  while(last+1<b.length && b.getLine(last+1)?.isWrapped)last++;
  // Claude/Codex-style bordered editors can contain explicit newlines. Require
  // both borders and a prompt in the same box; never match a historical box.
  const textAt=i=>b.getLine(i)?.translateToString(true) || '';
  const border=i=>/^\s*[╭╰┌└│|+]?[-─━═]{3,}[╮╯┐┘+]?\s*$/.test(textAt(i));
  let top=first-1, bottom=last+1;
  while(top>=Math.max(b.baseY,first-12) && !border(top))top--;
  while(bottom<Math.min(b.length,last+13) && !border(bottom))bottom++;
  if(top>=b.baseY && border(top) && bottom<b.length && border(bottom)) {
    let prompt=false;
    for(let i=top+1;i<bottom;i++)if(/^\s*[│|]?\s*[❯›>]\s/.test(textAt(i)))prompt=true;
    if(prompt){first=top+1;last=bottom-1;}
  }
  return row>=first && row<=last;
}
let tapStart=null, tapMoved=false;
$('terminal').addEventListener('pointerdown',event=>{
  tapStart={x:event.clientX,y:event.clientY}; tapMoved=false; touchScrolling=false;
  if((editMode==='buffer' || !isCurrentInput(event)) && event.pointerType==='touch' && !event.target.closest('.scrollbar')) event.preventDefault();
},{capture:true});
$('terminal').addEventListener('pointermove',event=>{
  if(tapStart && Math.hypot(event.clientX-tapStart.x,event.clientY-tapStart.y)>8)tapMoved=true;
},{capture:true});
$('terminal').addEventListener('pointercancel',()=>{tapMoved=true;tapStart=null;},{capture:true});
$('terminal').addEventListener('click',event=>{
  if(tapMoved || touchScrolling || event.ctrlKey || event.metaKey || event.target.closest('a,.scrollbar'))return;
  event.preventDefault(); event.stopImmediatePropagation();
  if(!isCurrentInput(event))return;
  if(editMode==='live')term?.focus();
  else openComposer();
},{capture:true});
// Keep virtual keyboards from opening when only using terminal control buttons.
for(const id of ['keys','pageUp','pageDown','latest']) {
  $(id).addEventListener('pointerdown',event=>event.preventDefault());
}
// One-finger vertical drags browse local scrollback; arrow buttons send CLI keys.
let touchStartY=null, touchLastY=0, touchPixels=0, touchScrolling=false;
let scrollFrame=0, scrollVelocity=0, touchTime=0, momentumTime=0;
function stopTouchScroll() {
  cancelAnimationFrame(scrollFrame); scrollFrame=0; scrollVelocity=0; touchPixels=0;
}
function flushTouchScroll() {
  if(!term)return;
  const height=$('terminal').querySelector('.xterm-screen').clientHeight/term.rows;
  const lines=Math.trunc(touchPixels/Math.max(1,height));
  if(lines){term.scrollLines(lines);touchPixels-=lines*height;}
}
function animateTouchScroll(now) {
  scrollFrame=0;
  if(touchStartY===null) {
    const dt=Math.min(32,now-momentumTime); momentumTime=now;
    touchPixels+=scrollVelocity*dt;
    scrollVelocity*=Math.exp(-dt/240);
  }
  flushTouchScroll();
  const b=term?.buffer.active;
  if(touchStartY===null && Math.abs(scrollVelocity)>.02 && b &&
     !(scrollVelocity<0 && b.viewportY===0) && !(scrollVelocity>0 && b.viewportY>=b.baseY))
    scrollFrame=requestAnimationFrame(animateTouchScroll);
}
$('terminal').addEventListener('touchstart',event=>{
  stopTouchScroll(); touchTime=performance.now();
  touchStartY=event.touches.length===1 && !event.target.closest('.scrollbar')?event.touches[0].clientY:null;
  touchLastY=touchStartY; touchPixels=0; touchScrolling=false;
},{passive:true,capture:true});
// xterm focuses its textarea on mousedown/touchstart before click fires.
for(const type of ['mousedown','touchstart']) {
  $('terminal').addEventListener(type,event=>{
    const point=event.touches?.[0] || event;
    if(event.target.closest('a,.scrollbar'))return;
    if(editMode==='buffer' || !isCurrentInput(point))event.stopImmediatePropagation();
  },{capture:true,passive:true});
}
$('terminal').addEventListener('touchmove',event=>{
  if(touchStartY===null || event.touches.length!==1 || !term)return;
  const y=event.touches[0].clientY;
  if(!touchScrolling && Math.abs(y-touchStartY)<6)return;
  touchScrolling=true;
  event.preventDefault(); event.stopImmediatePropagation();
  const now=performance.now(), delta=touchLastY-y, dt=Math.max(8,now-touchTime);
  scrollVelocity=Math.max(-4,Math.min(4,.25*scrollVelocity+.75*delta/dt));
  touchPixels+=delta; touchLastY=y; touchTime=now;
  if(!scrollFrame)scrollFrame=requestAnimationFrame(animateTouchScroll);
},{passive:false,capture:true});
$('terminal').addEventListener('touchend',()=>{
  touchStartY=null; momentumTime=performance.now();
  if(momentumTime-touchTime>100)scrollVelocity=0;
  if(touchScrolling && !scrollFrame)scrollFrame=requestAnimationFrame(animateTouchScroll);
},{passive:true});
$('terminal').addEventListener('touchcancel',()=>{touchStartY=null;stopTouchScroll();},{passive:true});
window.visualViewport?.addEventListener('resize',updateViewport);
window.visualViewport?.addEventListener('scroll',updateViewport);
document.addEventListener('focusin',updateViewport);
document.addEventListener('focusout',()=>requestAnimationFrame(updateViewport));
window.addEventListener('resize',updateViewport);
updateViewport();
action('refresh',refresh); action('parent',()=>folders(parent));
action('mkdir',async()=>{const q=await api('/api/folders',{parent:folder,name:$('folderName').value});$('folderName').value='';await folders(q.path);});
action('start',async()=>{ $('start').disabled=true;try{const q=await api('/api/sessions',{directory:folder,provider:$('provider').value});connect(q.session,$('provider').value);}finally{$('start').disabled=false;} });
action('reconnect',()=>{retryCount=0;return reconnectSession();});
async function release(toList=false) {
  reconnectAllowed=false;clearTimeout(retryTimer);retryTimer=0;clearResumeProbe();
  if(reconnectTask)await reconnectTask.catch(()=>{});
  $('detach').disabled=true; $('sessionList').disabled=true;
  try {
    let result;
    if(socket?.readyState===WebSocket.OPEN && connectionId) {
      result=await api('/api/detach',{session:selectedSession,connection:connectionId});
    }
    socket?.close(); clearInterval(timer); terminalReady=false; setConnection(false);
    $('detach').hidden=true; $('reconnect').hidden=false;
    $('notice').textContent='연결 해제됨 · 재연결하거나 목록으로 돌아갈 수 있습니다.';
    if(toList) { showHome(); await refresh(); }
    report(result?.restored?'Emacs에 다시 연결했습니다.':'연결을 해제했습니다.');
  } finally { $('detach').disabled=false; $('sessionList').disabled=false; }
}
action('detach',()=>release());
action('sessionList',()=>release(true));
window.addEventListener('offline',()=>{connectionLost('네트워크 연결 끊김');socket?.close();});
window.addEventListener('online',checkResumeConnection);
window.addEventListener('pageshow',checkResumeConnection);
window.addEventListener('focus',()=>{if(!terminalReady || Date.now()-lastSeen>10000)checkResumeConnection();});
document.addEventListener('visibilitychange',()=>{if(document.hidden){clearResumeProbe();stopTouchScroll();}else checkResumeConnection();});
action('paste',()=>{const text=$('composer').value;if(new TextEncoder().encode(text).length>12000)throw Error('입력은 UTF-8 기준 12KB 이내로 나눠주세요.');if(socket?.readyState!==1)throw Error('연결된 세션이 없습니다.');term.paste(text);$('composer').value='';$('composePanel').close();});
const keys={enter:'\r',escape:'\x1b',up:'\x1b[A',down:'\x1b[B',left:'\x1b[D',right:'\x1b[C',interrupt:'\x03',eof:'\x04',backtab:'\x1b[Z'};
$('keys').onclick=e=>{if(e.target.dataset.key)send({type:'input',data:keys[e.target.dataset.key]});};
new ResizeObserver(()=>resize()).observe($('terminal'));
async function bootstrap() {
  const auth = await api('/auth');
  if (auth.mode === 'tailscale') {
    await enter();
  } else {
    $('login').hidden = false;
    try { await enter(); } catch (error) { report(error); }
  }
}
bootstrap().catch(report);

action('selectOutput',()=>{
  if(!term)return;
  stopTouchScroll();term.blur();
  const b=term.buffer.active, lines=[];
  for(let i=0;i<b.length;i++) {
    const line=b.getLine(i), text=line.translateToString(!b.getLine(i+1)?.isWrapped);
    if(line.isWrapped && lines.length)lines[lines.length-1]+=text;
    else lines.push(text);
  }
  $('outputText').value=lines.join('\n');
  $('copyStatus').textContent='';
  $('outputPanel').showModal();
  $('outputText').setSelectionRange(0,0);
  $('outputText').scrollTop=$('outputText').scrollHeight;
});
$('outputClose').onclick=()=>$('outputPanel').close();
async function copyOutput(all=false) {
  const field=$('outputText');
  const text=all?field.value:field.value.slice(field.selectionStart,field.selectionEnd);
  if(!text){$('copyStatus').textContent='복사할 텍스트를 선택하세요.';return;}
  try{await navigator.clipboard.writeText(text);$('copyStatus').textContent='복사됨';}
  catch{$('copyStatus').textContent='길게 눌러 기기의 복사 메뉴를 사용하세요.';}
}
action('copySelection',()=>copyOutput());
action('copyAll',()=>copyOutput(true));
