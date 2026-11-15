import test from 'node:test';
import assert from 'node:assert/strict';
import {PUMP_PROGRAM_ID} from './sdk.mjs';
import {discoverConfirmed} from './discovery.mjs';
const client={rpcEndpoint:'https://mainnet.helius-rpc.com/?api-key=SYNTHETIC',getSignaturesForAddress:async()=>[{signature:'v1',slot:12},{signature:'future',slot:13},{signature:'v0',slot:14}]};
test('read-only discovery accepts v1 logs and skips unsupported future transactions once',async()=>{
  const calls=[];
  const value=await discoverConfirmed(client,{limit:3},{fetcher:async(url,options)=>{
    const request=JSON.parse(options.body);calls.push(request);
    assert.equal(request.method,'getTransaction');assert.equal(request.params[1].maxSupportedTransactionVersion,1);
    assert.equal(request.params[1].encoding,'json');
    return Response.json(request.params[0]==='future'?{error:{code:-32015,message:'unknown future version'}}:
      {result:{version:request.params[0]==='v1'?1:0,transaction:{message:{accountKeys:[PUMP_PROGRAM_ID.toBase58()]}},meta:{err:null,logMessages:['public logs']}}});
  },parse:(_logs,signature)=>[{kind:'CreateEvent',mint:'MINT-'+signature}]});
  assert.equal(calls.length,3);assert.deepEqual(value.coins.map(x=>x.mint),['MINT-v1','MINT-v0']);
  assert.equal(value.gaps.length,1);assert.equal(value.gaps[0].reason,'unsupported_future_transaction_version');
  assert.equal(value.complete_sample,false);assert.equal(value.sent,false);
});
test('failed or unavailable transaction never pretends complete discovery',async()=>{
  const value=await discoverConfirmed({...client,getSignaturesForAddress:async()=>[{signature:'failed',slot:1},{signature:'missing',slot:2}]},{limit:2},{
    fetcher:async()=>Response.json({result:null}),parse:()=>{throw Error('No parse without confirmed metadata');}});
  assert.equal(value.coins.length,0);assert.equal(value.gaps.length,2);assert.equal(value.complete_sample,false);
});
