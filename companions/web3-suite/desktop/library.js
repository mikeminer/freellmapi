/* Organization edits are native operations; never model prompts. */
let selectedProject='';
async function libraryAction(data){const result=await rpc('library',data);renderState(await rpc('snapshot'));if(result?.vault_sync&&['unavailable','conflict'].includes(result.vault_sync.state))toast('Organizzazione salvata; collegamenti Obsidian da risincronizzare dalle impostazioni.');return result;}
function chatTitle(row){return (row.title||'').startsWith('Conversation summary')?'Conversazione ripresa':row.title||'Nuova chat';}
function choice(box,label,action,description=''){
 const button=element('button','choice',label);if(description)button.append(element('small','',description));
 button.addEventListener('click',async()=>{button.disabled=true;try{await action();}catch(error){toast(error.message);}finally{button.disabled=false;}});box.append(button);return button;
}
function nameDialog(title,value,action){const box=element('div'),input=element('input');input.value=value;input.maxLength=120;input.placeholder='Nome';box.append(input);const save=choice(box,'Salva',async()=>{await action(input.value);closeDialog();});input.addEventListener('keydown',e=>{if(e.key==='Enter')save.click();});dialog(title,box);input.focus();input.select();}
function renderSessions(){
 const search=$('search-chats').value.toLowerCase(),fragment=document.createDocumentFragment(),rows=state.sessions||[];
 const matches=row=>!search||(chatTitle(row)+' '+(row.root||'')).toLowerCase().includes(search);
 function chat(row){const wrap=element('div','session-wrap'),open=element('button','session'+(row.id===state.session?' active':''));
  open.dataset.session=row.id;open.append(element('span','title',chatTitle(row)));open.title=chatTitle(row);open.addEventListener('click',()=>{if(state.busy){toast('Attendi il turno corrente prima di cambiare chat.');return;}if(row.id!==state.session)command('/resume '+row.id).catch(()=>{});});
  open.draggable=true;open.addEventListener('dragstart',event=>startLibraryDrag(event,'chat',row.id));
  const menu=element('button','chat-menu','⋯');menu.title='Opzioni conversazione';menu.setAttribute('aria-label','Opzioni: '+chatTitle(row));menu.addEventListener('click',()=>chatMenu(row));
  wrap.addEventListener('contextmenu',e=>{e.preventDefault();chatMenu(row);});wrap.append(open,menu);return wrap;
 }
 const projects=state.projects||[],byid=new Map(projects.map(p=>[p.id,p]));function renderProject(project,depth=0){const children=rows.filter(r=>r.project_id===project.id&&matches(r));if(search&&!children.length&&!project.name.toLowerCase().includes(search)&&!projects.some(p=>p.parent===project.id))return;
  const group=element('div','project-group'),head=element('div','project-head'),toggle=element('button','project-toggle'),title=element('button','project-title',project.name),menu=element('button','project-options','⋯');
  head.dataset.project=project.id;group.style.marginLeft=(depth?10:0)+'px';title.draggable=true;title.addEventListener('dragstart',event=>startLibraryDrag(event,'project',project.id));bindLibraryDrop(head,project.id);
  const collapsed=localStorage.getItem('collapsed:'+project.id)==='1'&&!search;toggle.textContent=collapsed?'›':'⌄';toggle.title=collapsed?'Espandi progetto':'Comprimi progetto';toggle.addEventListener('click',()=>{localStorage.setItem('collapsed:'+project.id,collapsed?'0':'1');renderSessions();});
  title.title=project.name;title.addEventListener('click',()=>projectMenu(project));menu.title='Opzioni progetto';menu.addEventListener('click',()=>projectMenu(project));head.append(toggle,title,element('span','chat-count',String(rows.filter(r=>r.project_id===project.id).length)),menu);group.append(head);
  if(!collapsed){children.forEach(row=>group.append(chat(row)));for(const child of projects.filter(p=>p.parent===project.id))renderProjectInto(child,depth+1,group);if(!children.length&&!projects.some(p=>p.parent===project.id)){const empty=element('button','empty-project','+ Nuova chat in questo progetto');empty.addEventListener('click',()=>newProjectChat(project));group.append(empty);}}
  return group;
 }
 function renderProjectInto(project,depth,target){if(depth>32)return;const node=renderProject(project,depth);if(node)target.append(node);}
 for(const project of projects)if(!project.parent||!byid.has(project.parent))renderProjectInto(project,0,fragment);
 const others=rows.filter(r=>!r.project_id&&matches(r));if(others.length){fragment.append(element('div','ungrouped-label','CONVERSAZIONI'));others.forEach(row=>fragment.append(chat(row)));}
 if(!fragment.childNodes.length)fragment.append(element('div','empty-side',search?'Nessun risultato.':'Crea un progetto o inizia una chat.'));
 $('sessions').replaceChildren(fragment);bindLibraryDrop($('section-projects'),null);
}
function createProject(parent=null){const box=element('div'),name=element('input');name.placeholder='Nome del progetto';name.maxLength=120;box.append(name);
 const root=element('div','project-root',state.project||''),pick=choice(box,'Scegli cartella di lavoro',async()=>{const path=await rpc('pick_folder');if(path)root.textContent=path;});box.append(root);
 box.append(element('p','hint','Raggruppa qui le conversazioni. Le nuove chat useranno questa cartella di lavoro.'));
 choice(box,'Crea progetto',async()=>{const project=await libraryAction({action:'create_project',name:name.value,root:root.textContent,parent:parent?.id||null});selectedProject=project.id;closeDialog();toast('Progetto creato. Aprilo per aggiungere o iniziare una chat.');});dialog(parent?.id?'Nuovo sottoprogetto di '+parent.name:'Nuovo progetto',box);name.focus();
}
async function newProjectChat(project){try{await libraryAction({action:'new_project_chat',project:project.id});selectedProject=project.id;closeDialog();}catch(error){toast(error.message);}}
function projectMenu(project){selectedProject=project.id;const box=element('div');box.append(element('p','hint',project.root));
 choice(box,'Nuova chat nel progetto',()=>newProjectChat(project));
 choice(box,'Aggiungi conversazioni esistenti',()=>addExisting(project));
 choice(box,'Crea sottoprogetto',()=>createProject(project));
 if(project.parent)choice(box,'Sposta al livello principale',async()=>{await libraryAction({action:'move_project',project:project.id,parent:null});closeDialog();});
 choice(box,'Rinomina progetto',()=>nameDialog('Rinomina progetto',project.name,name=>libraryAction({action:'rename_project',project:project.id,name})));
 choice(box,'Elimina progetto',()=>{const content=element('div');content.append(element('p','','Le chat rimarranno in Conversazioni. La cartella e i file di lavoro saranno conservati.'));choice(content,'Elimina raggruppamento',async()=>{await libraryAction({action:'delete_project',project:project.id});selectedProject='';closeDialog();});dialog('Eliminare '+project.name+'?',content);});
 dialog(project.name,box);
}
function addExisting(project){const box=element('div'),search=element('input'),list=element('div','chat-select-list'),selected=new Set();search.placeholder='Cerca conversazioni…';box.append(search,list);
 function populate(){list.replaceChildren();for(const row of state.sessions||[]){if(row.project_id===project.id||!chatTitle(row).toLowerCase().includes(search.value.toLowerCase()))continue;const label=element('label','chat-check'),check=element('input');check.type='checkbox';check.checked=selected.has(row.id);check.addEventListener('change',()=>check.checked?selected.add(row.id):selected.delete(row.id));label.append(check,element('span','',chatTitle(row)));list.append(label);}}
 search.addEventListener('input',populate);populate();choice(box,'Aggiungi le conversazioni selezionate',async()=>{if(!selected.size){toast('Seleziona almeno una chat.');return;}await libraryAction({action:'move_many',project:project.id,sessions:[...selected]});closeDialog();});dialog('Aggiungi a '+project.name,box);search.focus();
}
function chatMenu(row){const box=element('div');
 choice(box,'Rinomina conversazione',()=>nameDialog('Rinomina chat',chatTitle(row),name=>libraryAction({action:'rename',session:row.id,name})));
 choice(box,'Sposta nel progetto…',()=>{const content=element('div');for(const project of state.projects||[])choice(content,project.name,async()=>{await libraryAction({action:'move',session:row.id,project:project.id});closeDialog();});choice(content,'Senza progetto',async()=>{await libraryAction({action:'move',session:row.id,project:null});closeDialog();});if(!state.projects?.length)choice(content,'Crea un progetto',createProject);dialog('Sposta conversazione',content);});
 choice(box,'Cancella conversazione',()=>{const content=element('div');content.append(element('p','','La conversazione verrà spostata nel cestino. Potrai ripristinarla dalle impostazioni.'));choice(content,'Sposta nel cestino',async()=>{await libraryAction({action:'delete',session:row.id});closeDialog();});dialog('Cancellare questa chat?',content);});
 dialog(chatTitle(row),box);
}
function trashDialog(){const box=element('div');const rows=state.deleted_sessions||[];if(!rows.length)box.append(element('p','hint','Il cestino è vuoto.'));
 for(const row of rows)choice(box,'Ripristina: '+chatTitle(row),async()=>{await libraryAction({action:'restore',session:row.id});trashDialog();});dialog('Cestino',box);
}

async function workspaceAction(area,action){await rpc('workspace_action',{area,action});closeDialog();}
function solanaMenu(){const box=element('div');choice(box,'Wallet e firma locale',()=>rpc('connector',{name:'solana'}));
 for(const [label,action] of [['Saldo e portafoglio','balance'],['Verifica RPC e Helius','rpc'],['Analizza token / Pump.fun','token'],['Operazioni e ricevute','receipts']])choice(box,label,()=>workspaceAction('solana',action));dialog('Solana',box);}
function gameMenu(){const box=element('div');box.append(element('p','hint','DevFridge Game Builder · browser e Seeker · progetto: '+(state.project||'')));
 for(const [label,action] of [['Crea un nuovo gioco','new'],['Riprendi il gioco corrente','resume'],['Migliora grafica e asset 3D','art'],['Build APK e collaudo Seeker','apk']])choice(box,label,()=>workspaceAction('game',action));dialog('DevFridge Game Builder',box);}

let activeLibraryDrag=null;
function startLibraryDrag(event,type,id){activeLibraryDrag={type,id};event.dataTransfer.effectAllowed='move';event.dataTransfer.setData('application/x-freellmapi-library',JSON.stringify(activeLibraryDrag));event.stopPropagation();}
function bindLibraryDrop(node,target){if(!node)return;node.ondragover=event=>{if(!event.dataTransfer.types.includes('application/x-freellmapi-library'))return;event.preventDefault();event.stopPropagation();event.dataTransfer.dropEffect='move';node.classList.add('drop-target');};node.ondragleave=()=>node.classList.remove('drop-target');node.ondrop=async event=>{event.preventDefault();event.stopPropagation();node.classList.remove('drop-target');let value;try{value=JSON.parse(event.dataTransfer.getData('application/x-freellmapi-library'));if(!['chat','project'].includes(value.type)||!value.id)throw new Error('Trascinamento non valido.');await libraryAction(value.type==='chat'?{action:'move',session:value.id,project:target}:{action:'move_project',project:value.id,parent:target});if(target)localStorage.setItem('collapsed:'+target,'0');renderSessions();}catch(error){toast(error.message);}finally{activeLibraryDrag=null;}};}

document.addEventListener('dragend',()=>document.querySelectorAll('.drop-target').forEach(n=>n.classList.remove('drop-target')));
