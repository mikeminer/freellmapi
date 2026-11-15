/** Read raw confirmed logs including version 1; never deserialize/sign v1. */
import {PUMP_PROGRAM_ID} from './sdk.mjs';
import {parsePumpLogs} from './events.mjs';

export async function discoverConfirmed(c,a,{fetcher=globalThis.fetch,parse=parsePumpLogs}={}){
  const limit=a.limit??10;
  if(!Number.isInteger(limit)||limit<1||limit>20)throw Error('invalid_integer');
  const signatures=await c.getSignaturesForAddress(PUMP_PROGRAM_ID,{limit,...(a.before?{before:a.before}:{})},'confirmed');
  const coins=[],gaps=[];
  for(const s of signatures){
    if(s.err)continue;
    let tx;
    try{
      const response=await fetcher(c.rpcEndpoint,{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({jsonrpc:'2.0',id:'freellmapi-discovery',method:'getTransaction',params:[s.signature,{encoding:'json',commitment:'confirmed',maxSupportedTransactionVersion:1}]}),
        signal:AbortSignal.timeout(12000),redirect:'error'});
      if(!response.ok){gaps.push({signature:s.signature,reason:response.status===429?'rpc_rate_limited':'transaction_http_error'});continue;}
      const raw=await response.text();if(raw.length>2_000_000){gaps.push({signature:s.signature,reason:'transaction_too_large'});continue;}
      const value=JSON.parse(raw);
      if(value.error){gaps.push({signature:s.signature,reason:value.error.code===-32015?'unsupported_future_transaction_version':'transaction_rpc_error'});continue;}
      tx=value.result;
      if(!tx){gaps.push({signature:s.signature,reason:'transaction_unavailable'});continue;}
    }catch{gaps.push({signature:s.signature,reason:'transaction_read_failed'});continue;}
    if(tx.meta?.err)continue;
    const keys=tx.transaction?.message?.staticAccountKeys??tx.transaction?.message?.accountKeys;
    if(!Array.isArray(keys)||!keys.some(k=>(typeof k==='string'?k:k?.pubkey)===PUMP_PROGRAM_ID.toBase58())){gaps.push({signature:s.signature,reason:'transaction_program_keys_unavailable'});continue;}
    for(const e of parse(tx.meta?.logMessages??[],s.signature,s.slot))if(e.kind==='CreateEvent')coins.push({mint:e.mint,signature:s.signature,slot:s.slot,block_time:s.blockTime,source:'confirmed Pump CreateEvent'});
  }
  return {coins:[...new Map(coins.map(x=>[x.mint,x])).values()],scanned:signatures.length,
    newest_signature:signatures[0]?.signature??null,oldest_signature:signatures.at(-1)?.signature??null,
    gaps,complete_sample:gaps.length===0,max_read_transaction_version:1,
    coverage:'bounded RPC sample, not a full launch firehose; listed gaps are not evidence of no launches',sent:false};
}
