/* Only interface chrome is translated. User/model text and task payloads stay intact. */
const LanguageUI=(()=>{
 let call,manifest={locales:['it'],rtl:[]},code='it',strings={},observer,pending=false;const originals=new WeakMap();
 const skip=node=>node.parentElement?.closest('#messages,#question,#input,#chat-title,#project-label,.session .title,.project-title,option,script,style,pre,code,[data-user-content]');
 function translated(value){const leading=value.match(/^\s*/)[0],trailing=value.match(/\s*$/)[0],key=value.trim();if(strings[key])return leading+strings[key]+trailing;const prefix=Object.keys(strings).filter(s=>s.length>4&&key.startsWith(s)&&/^[\s:(·]/.test(key.slice(s.length))).sort((a,b)=>b.length-a.length)[0];return prefix?leading+strings[prefix]+key.slice(prefix.length)+trailing:value;}
 function apply(){pending=false;const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);let node;
  while(node=walker.nextNode()){if(skip(node))continue;let saved=originals.get(node);if(!saved||node.data!==saved.output)saved={source:node.data};const output=translated(saved.source);if(node.data!==output)node.data=output;originals.set(node,{source:saved.source,output});}
  for(const node of document.querySelectorAll('[title],[placeholder],[aria-label]')){if(node.closest('#messages,#question,.session-wrap,.project-head,[data-user-content]'))continue;for(const attr of ['title','placeholder','aria-label']){if(!node.hasAttribute(attr))continue;const key='source'+attr.replace('-','');let saved=node.dataset[key];if(!saved)saved=node.getAttribute(attr);node.dataset[key]=saved;const output=translated(saved);if(node.getAttribute(attr)!==output)node.setAttribute(attr,output);}}
 }
 function schedule(){if(!pending){pending=true;requestAnimationFrame(apply);}}
 async function change(next){const dictionary=await call('locale',{code:next});code=next;strings=dictionary.strings;document.documentElement.lang=code;document.documentElement.dir=dictionary.rtl?'rtl':'ltr';localStorage.setItem('freellmapi-language',code);apply();}
 async function init(rpc){call=rpc;manifest=await call('locale',{});const preferred=localStorage.getItem('freellmapi-language')||navigator.language;const selected=manifest.locales.includes(preferred)?preferred:manifest.locales.find(c=>c===preferred.split('-')[0])||'en';await change(selected);observer=new MutationObserver(schedule);observer.observe(document.body,{childList:true,subtree:true,characterData:true});}
 function selector(){const box=element('div','language-setting'),label=element('label','','Lingua interfaccia'),select=element('select');select.setAttribute('aria-label','Lingua interfaccia');
  for(const locale of manifest.locales){let native=locale;try{native=new Intl.DisplayNames([locale],{type:'language'}).of(locale);}catch(_){}const option=element('option','',native+' · '+locale);option.value=locale;select.append(option);}select.value=code;
  select.addEventListener('change',async()=>{select.disabled=true;try{await change(select.value);}catch(error){toast(error.message);select.value=code;}finally{select.disabled=false;}});box.append(label,select);return box;
 }
 return {init,selector,apply,change,code:()=>code};
})();
const SectionVisibility=(()=>{
 const defaults={hyperliquid:true,solana:true,game:true};let prefs={...defaults};try{prefs={...defaults,...JSON.parse(localStorage.getItem('freellmapi-sections')||'{}')};}catch(_){}
 function apply(){for(const [key,id] of [['hyperliquid','hyperliquid'],['solana','solana'],['game','game-builder']])document.getElementById(id)?.classList.toggle('hidden',prefs[key]===false);for(const node of document.querySelectorAll('[data-command="/hyperliquid"]'))node.classList.toggle('hidden',prefs.hyperliquid===false);}
 function controls(){const box=element('div','section-settings');box.append(element('h3','','Sezioni visibili'));
  for(const [key,label] of [['hyperliquid','Hyperliquid'],['solana','Solana'],['game','DevFridge Game Builder']]){const row=element('label','section-toggle'),check=element('input');check.type='checkbox';check.checked=prefs[key]!==false;check.addEventListener('change',()=>{prefs[key]=check.checked;localStorage.setItem('freellmapi-sections',JSON.stringify(prefs));apply();});row.append(element('span','',label),check);box.append(row);}return box;
 }
 return {apply,controls};
})();
