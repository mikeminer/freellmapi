import * as THREE from 'three';
import identity from '../public/qa-build.json';

// Diagnostics are explicitly enabled locally by the collector. No state setters.
export function createQA(game) {
  const enabled = localStorage.getItem('freellmapi.qa') === '1';
  const noop = () => {};
  if (!enabled) return { frame:noop,touch:noop,shot:noop,spawn:noop,hit:noop,over:noop,restart:noop };
  const generation=crypto.randomUUID();
  const ids=new WeakMap();let nextId=0;
  const entityId=object=>{if(!ids.has(object))ids.set(object,++nextId);return ids.get(object);};
  const counts={shots:0,spawns:0,hits:0,gameOvers:0,restartRequests:0,touchStarts:0,touchMoves:0,touchEnds:0,touchCancels:0,trustedTouches:0};
  let recording=null;
  const project=(position)=>{const p=position.clone().project(game.camera);return {x:p.x,y:p.y,z:p.z};};
  const state=()=>{
    const enemies=(game.enemies||[]).filter(e=>e.active).map(e=>({id:entityId(e),x:e.mesh.position.x,y:e.mesh.position.y,z:e.mesh.position.z,speed:e.speed,ndc:project(e.mesh.position)}));
    const p=game.ship.getPosition();
    const box=new THREE.Box3().setFromObject(game.ship.mesh);
    const corners=[];
    for(const x of [box.min.x,box.max.x])for(const y of [box.min.y,box.max.y])for(const z of [box.min.z,box.max.z])corners.push(project(new THREE.Vector3(x,y,z)));
    return {generation,t:performance.now(),timeOrigin:performance.timeOrigin,phase:game.isGameOver?'results':'gameplay',
      visible:document.visibilityState,foreground:document.documentElement.dataset.nativeForeground==='true',documentFocus:document.hasFocus(),financialApprovalPending:false,
      score:game.score,camera:{halfHeight:Math.tan(game.camera.fov*Math.PI/360)*Math.abs(game.camera.position.z-p.z),halfWidth:Math.tan(game.camera.fov*Math.PI/360)*Math.abs(game.camera.position.z-p.z)*game.camera.aspect},ship:{x:p.x,y:p.y,z:p.z,ndc:project(p),corners,target:game.ship.targetPosition||null},
      input:game.input?.getState(),controlBounds:typeof game.bounds==='function'?game.bounds():null,enemies,bullets:(game.bullets||[]).filter(b=>b.active).map(b=>({x:b.mesh.position.x,y:b.mesh.position.y,z:b.mesh.position.z})),
      visibleEnemies:enemies.filter(e=>Math.abs(e.ndc.x)<1&&Math.abs(e.ndc.y)<1&&e.ndc.z>-1&&e.ndc.z<1).length,
      counts:{...counts},overlays:[...document.querySelectorAll('h2')].filter(n=>n.textContent==='GAME OVER').length,
      viewport:{w:innerWidth,h:innerHeight,dpr:devicePixelRatio,nativeInsets:JSON.parse(document.documentElement.dataset.nativeInsets||'null'),canvas:game.renderer.domElement.getBoundingClientRect().toJSON()},
      render:{...game.renderer.info.render},memory:{...game.renderer.info.memory}};
  };
  const emit=(event)=>console.debug('FREELLMAPI_QA',JSON.stringify({generation,t:performance.now(),...event}));
  const api={
    identity:()=>{const gl=game.renderer.getContext(),ext=gl.getExtension('WEBGL_debug_renderer_info');return {...identity,generation,userAgent:navigator.userAgent,
      renderer:gl.getParameter(ext?ext.UNMASKED_RENDERER_WEBGL:gl.RENDERER),dpr:game.renderer.getPixelRatio(),resolution:[gl.drawingBufferWidth,gl.drawingBufferHeight]};},
    readState:state,
    startRecording:()=>{recording={id:crypto.randomUUID(),start:performance.now(),frames:[],interruptions:[],active:true};return {id:recording.id,start:recording.start};},
    stopRecording:()=>{if(!recording)return null;recording.active=false;return {...recording,end:performance.now()};}
  };
  Object.defineProperty(window,'__seekerQA',{value:Object.freeze(api),configurable:false});
  const interrupt=reason=>{if(recording?.active)recording.interruptions.push({reason,t:performance.now()});};
  document.addEventListener('visibilitychange',()=>interrupt('visibility:'+document.visibilityState));
  window.addEventListener('blur',()=>interrupt('blur'));
  game.renderer.domElement.addEventListener('webglcontextlost',()=>interrupt('webglcontextlost'));
  return {
    frame(){if(!recording?.active)return;const s=state();if(recording.frames.length>=120000){interrupt('overflow');recording.active=false;return;}
      recording.frames.push({id:recording.frames.length+1,t:performance.now(),phase:s.phase,visible:s.visible,foreground:s.foreground,enemies:s.visibleEnemies,bullets:s.bullets.length});},
    touch(e,input){const key={touchstart:'touchStarts',touchmove:'touchMoves',touchend:'touchEnds',touchcancel:'touchCancels'}[e.type];if(key)counts[key]++;if(e.isTrusted)counts.trustedTouches++;
      emit({kind:'input',type:e.type,trusted:e.isTrusted,x:input.x,y:input.y,firing:input.isFiring});},
    shot(){counts.shots++;},spawn(){counts.spawns++;},hit(){counts.hits++;},over(){counts.gameOvers++;},
    restart(source){counts.restartRequests++;emit({kind:'restart_request',source,count:counts.restartRequests});}
  };
}
