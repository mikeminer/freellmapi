/** Confirmed public event feed; stdin EOF terminates when Python parent dies. */
import readline from 'node:readline';
import {Connection} from '@solana/web3.js';
import {PUMP_PROGRAM_ID} from './sdk.mjs';
import {parsePumpLogs} from './events.mjs';
const lines=readline.createInterface({input:process.stdin});
const first=await new Promise(resolve=>lines.once('line',resolve));
try{
  const a=JSON.parse(first);if(Object.keys(a).some(k=>!['rpc_url','network','mints'].includes(k)))throw Error();
  const url=new URL(a.rpc_url);if(url.protocol!=='https:'||url.username||url.password)throw Error();
  const c=new Connection(url.href,{commitment:'confirmed',disableRetryOnRateLimit:true});const pinned=new Set(a.mints??[]);const tracked=new Map([...pinned].map(m=>[m,Date.now()])),seen=new Set();
  let at=Date.now(),count=0,dropped=0;
  const emit=x=>process.stdout.write(JSON.stringify(x)+'\n');
  const id=await c.onLogs(PUMP_PROGRAM_ID,(value,ctx)=>{
    if(value.err)return;
    for(const event of parsePumpLogs(value.logs,value.signature,ctx.slot)){
      const now=Date.now();if(now-at>1000){if(dropped)emit({kind:'gap',reason:'local_rate_bound',dropped});at=now;count=0;dropped=0;}
      if(event.kind==='CreateEvent'){tracked.set(event.mint,now);if(tracked.size>1000){tracked.delete(tracked.keys().next().value);emit({kind:'gap',reason:'candidate_memory_bound'});}}
      if(!tracked.has(event.mint)||seen.has(event.id))continue;
      if(++count>100){dropped++;continue;}seen.add(event.id);if(seen.size>20000)seen.clear();
      emit(event);
    }
  },'confirmed');
  const heartbeat=setInterval(()=>{const now=Date.now();for(const [mint,t] of tracked)if(now-t>900000&&!pinned.has(mint))tracked.delete(mint);emit({kind:'heartbeat',observed_ms:now,tracked:tracked.size,socket_open:Boolean(c._rpcWebSocketConnected),coverage:'since subscription; gaps possible'});},5000);
  process.stdin.on('end',()=>{clearInterval(heartbeat);c.removeOnLogsListener(id).finally(()=>process.exit(0));});
}catch{process.stdout.write(JSON.stringify({kind:'gap',reason:'subscription_failed'})+'\n');process.exit(1);}
