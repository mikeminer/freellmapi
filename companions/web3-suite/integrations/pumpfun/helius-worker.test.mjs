import test from 'node:test';
import assert from 'node:assert/strict';
import {readHelius} from './helius-worker.mjs';
const args={rpc_url:'https://devnet.helius-rpc.com/?api-key=SYNTHETIC_HELIUS_KEY',network:'devnet',address:'11111111111111111111111111111111'};
const genesis='EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG';
for(const resource of ['network','asset','assets_by_owner','priority_fee'])test('official SDK read-only wire: '+resource,async()=>{
  const calls=[];const result=await readHelius({...args,resource,account_keys:[args.address]}, {transport:async r=>{
    calls.push(r.payload);return {result:r.payload.method==='getGenesisHash'?genesis:r.payload.method==='getSlot'?123:{items:[],uri:args.rpc_url}};
  }});
  assert.equal(result.sent,false);assert.equal(result.ok,true);assert.equal(calls.length,2);
  assert.equal(calls[0].method,'getGenesisHash');
  assert.equal(calls[1].method,{network:'getSlot',asset:'getAsset',assets_by_owner:'getAssetsByOwner',priority_fee:'getPriorityFeeEstimate'}[resource]);
  assert.ok(!JSON.stringify(result).includes('SYNTHETIC_HELIUS_KEY'));
  if(resource==='priority_fee'){assert.equal(calls[1].params[0].options.recommended,true);assert.deepEqual(calls[1].params[0].accountKeys,[args.address]);}
  if(resource==='assets_by_owner')assert.equal(calls[1].params.ownerAddress,args.address);
});
test('reject secret fields, send methods and wrong provider before transport',async()=>{
  let calls=0;const opts={transport:async()=>{calls++;return {result:genesis};}};
  for(const change of [{secret:'NO'},{resource:'sendTransaction'},{rpc_url:'https://dashboard.helius.dev/project/rpc'},{rpc_url:'https://api.devnet.solana.com'},{rpc_url:'http://devnet.helius-rpc.com/?api-key=NO'}])await assert.rejects(readHelius({...args,resource:'asset',...change},opts));
  assert.equal(calls,0);
});
test('wrong genesis halts before DAS or priority-fee reads',async()=>{
  let calls=0;await assert.rejects(readHelius({...args,resource:'asset'},{transport:async()=>{calls++;return {result:'wrong'};}}),/wrong_rpc_genesis/);assert.equal(calls,1);
});
test('provider failure is not retried or converted to zero balance',async()=>{
  let calls=0;await assert.rejects(readHelius({...args,resource:'asset'},{transport:async()=>{calls++;return {error:{code:429,message:'limited'}};}}));assert.equal(calls,1);
});
test('recommended fee request obeys live Helius mutually exclusive options',async()=>{
  const result=await readHelius({...args,resource:'priority_fee',account_keys:[args.address]},{transport:async r=>{
    if(r.payload.method==='getGenesisHash')return {result:genesis};
    const options=r.payload.params[0].options;
    if(options.recommended&&['priorityLevel','includeAllPriorityFeeLevels','lookbackSlots','includeVote'].some(k=>k in options))return {error:{code:-32602,message:'recommended cannot be used with percentile options'}};
    assert.deepEqual(options,{recommended:true});return {result:{priorityFeeEstimate:1000}};
  }});
  assert.equal(result.data.priorityFeeEstimate,1000);assert.equal(result.sent,false);
});
