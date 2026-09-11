const $ = id => document.getElementById(id);
let folder, parent, socket, term, fit, timer, selectedSession, connectionId;
const report = error => $('status').textContent = error.message || error;
async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
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
    b.textContent=`${s.name || s.provider} · ${s.attached_clients ? '조작권 가져오기' : '연결 가능'}\n${s.directory}`;
    b.disabled=!!s.temporary;
    const label=b.textContent;
    b.onclick=async()=>{
      b.disabled=true; b.textContent='조작권 가져오는 중…\n'+s.directory;
      report('PC 연결을 전환하고 있습니다…');
      try { await api('/api/takeover',{session:s.session}); connect(s.session); }
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
function resize(){ if(fit && term){fit.fit();send({type:'resize',rows:term.rows,cols:term.cols});} }
function connect(path) {
  selectedSession=path; connectionId=null;
  if(socket) socket.close();
  term?.dispose();
  $('home').hidden=true; $('workspace').hidden=false; $('notice').textContent='연결 중…';
  $('sessionName').textContent=path.split('/').pop(); report('웹에서 조작 중입니다. PC로 돌아가려면 연결을 해제하세요.');
  term=new Terminal({fontSize:13,scrollback:3000,theme:{background:'#111720'},allowProposedApi:false});
  fit=new FitAddon.FitAddon(); term.loadAddon(fit); term.open($('terminal')); fit.fit();
  const ws=new WebSocket(`${location.protocol==='https:'?'wss:':'ws:'}//${location.host}/ws?session=${encodeURIComponent(path)}`);
  socket=ws; ws.binaryType='arraybuffer'; const currentTerm=term;
  term.onData(data=>{if(ws.readyState===1)ws.send(JSON.stringify({type:'input',data}));});
  ws.onmessage=event=>{
    if(event.data instanceof ArrayBuffer) {
      const bytes=new Uint8Array(event.data);
      currentTerm.write(bytes,()=>{if(ws.readyState===1)ws.send(JSON.stringify({type:'ack',bytes:bytes.length}));});
    } else {
      const q=JSON.parse(event.data);
      if(q.type==='connection') connectionId=q.id;
      if(q.type==='ready') { $('notice').textContent=q.replay_complete?'연결됨 · 승인과 선택은 아래 키로 응답하세요.':'출력 재생 한도 초과: 화면이 복원되지 않았습니다. PC에서 상태를 확인하세요.'; resize(); }
      if(q.type==='pong') $('latency').textContent=`왕복 ${Math.round(performance.now()-q.at)} ms`;
    }
  };
  ws.onopen=()=>{resize();timer=setInterval(()=>send({type:'ping',at:performance.now()}),3000);};
  ws.onerror=()=>report('연결 실패: 목록으로 돌아가 조작권 가져오기를 다시 시도하세요.');
  ws.onclose=()=>{clearInterval(timer);$('notice').textContent='연결 해제됨 · 원래 Emacs로 자동 복귀합니다. CLI는 계속 실행됩니다.';};
}
action('loginButton',async()=>{await api('/login',{token:$('token').value});$('token').value='';await enter();});
action('refresh',refresh); action('parent',()=>folders(parent));
action('mkdir',async()=>{const q=await api('/api/folders',{parent:folder,name:$('folderName').value});$('folderName').value='';await folders(q.path);});
action('start',async()=>{ $('start').disabled=true;try{const q=await api('/api/sessions',{directory:folder,provider:$('provider').value});connect(q.session);}finally{$('start').disabled=false;} });
action('detach',async()=>{
  $('detach').disabled=true; report('웹 연결을 해제하고 PC로 돌려주는 중…');
  try {
    const result=await api('/api/detach',{session:selectedSession,connection:connectionId});
    socket?.close(); clearInterval(timer);
    $('workspace').hidden=true; $('home').hidden=false;
    await refresh(); report(result.restored?'Emacs에 다시 연결했습니다.':'연결을 해제했습니다. PC에서 eam-attach로 연결할 수 있습니다.');
  } finally { $('detach').disabled=false; }
});
action('paste',()=>{const text=$('composer').value;if(new TextEncoder().encode(text).length>12000)throw Error('입력은 UTF-8 기준 12KB 이내로 나눠주세요.');if(socket?.readyState!==1)throw Error('연결된 세션이 없습니다.');term.paste(text);$('composer').value='';});
const keys={enter:'\r',escape:'\x1b',tab:'\t',up:'\x1b[A',down:'\x1b[B',left:'\x1b[D',right:'\x1b[C',interrupt:'\x03'};
$('keys').onclick=e=>{if(e.target.dataset.key)send({type:'input',data:keys[e.target.dataset.key]});};
new ResizeObserver(()=>resize()).observe($('terminal'));
enter().catch(()=>{});
