/* Account sign-in uses the native verified Tailscale identity; no web tokens. */
const AuthUI=(()=>{
 let access={authenticated:false},busy=false,leaving=false;
 const allowed=()=>access.authenticated===true&&!leaving;
 function clearPrivate(){
  if(currentSession)localStorage.setItem('draft:'+currentSession,$('input').value);
  for(const [id,call] of calls)if(!['ready','locale','window','auth_status','auth_login','auth_logout','auth_open'].includes(call.method)){
   calls.delete(id);call.reject(new Error('Accesso desktop terminato.'));
  }
  state={};currentSession='';files=[];activity=[];selectedProject='';questionId='';recording=false;lastMessageSignature='';messageNodes.clear();
  for(const id of ['messages','sessions','attachments','question','activity-log','activity-status','dialog-content'])$(id).replaceChildren();
  for(const id of ['overlay','activity','question','working','scroll-latest','toast'])$(id).classList.add('hidden');
  $('input').value='';$('search-chats').value='';$('chat-title').textContent='Nuova chat';$('project-label').textContent='Workspace';
  $('working-detail').textContent='';$('telemetry').textContent='';$('mic').classList.remove('recording');
 }
 function apply(value){
  const was=allowed();access=value||{authenticated:false};if(!access.authenticated)leaving=false;
  const signed=allowed();if(!signed&&(was||currentSession))clearPrivate();
  document.body.classList.toggle('auth-locked',!signed);
  for(const node of document.querySelectorAll('.sidebar,body>main,#activity,#overlay'))node.inert=!signed;
  $('auth-gate').classList.toggle('hidden',signed);$('auth-message').textContent=value.message||'';
  const identity=value.identity||{};$('account-name').textContent=signed?identity.name:'FREELLMAPI';
  $('account-avatar').textContent=signed?(identity.name||'F').slice(0,1).toUpperCase():'F';
  $('account-email').textContent=signed?identity.login:'';
  $('auth-login').disabled=busy;$('auth-open').disabled=busy;
 }
 async function login(){if(busy)return;busy=true;apply(access);
  try{leaving=false;apply(await rpc('auth_login'));}catch(error){$('auth-message').textContent=error.message;}finally{busy=false;$('auth-login').disabled=false;$('auth-open').disabled=false;}
 }
 async function logout(){if(busy)return;busy=true;leaving=true;clearPrivate();apply(access);
  try{apply(await rpc('auth_logout'));}catch(error){$('auth-message').textContent=error.message;}finally{busy=false;$('auth-login').disabled=false;$('auth-open').disabled=false;}
 }
 function settings(){const box=element('div','account-setting');box.append(element('h3','','Account'));
  const who=element('p','account-identity',(access.identity?.name||'')+' · '+(access.identity?.login||''));who.dataset.userContent='';box.append(who);
  const out=element('button','choice','Esci');out.id='account-logout';out.addEventListener('click',logout);
  box.append(out,element('p','hint','Esci solo da FREELLMAPI Desktop; Tailscale e Seeker restano connessi.'));return box;
 }
 function init(){
  $('auth-login').addEventListener('click',login);
  $('auth-open').addEventListener('click',()=>rpc('auth_open').catch(error=>{$('auth-message').textContent=error.message;}));
  $('account').addEventListener('click',()=>{if(allowed())window.settings();});
 }
 return {apply,allowed,init,settings,logout};
})();
