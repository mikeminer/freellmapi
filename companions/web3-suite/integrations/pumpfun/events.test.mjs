import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {PublicKey} from '@solana/web3.js';
import {PUMP_PROGRAM_ID,PUMP_SDK} from './sdk.mjs';
import {TOKEN_2022_PROGRAM_ID,TOKEN_PROGRAM_ID,NATIVE_MINT} from '@solana/spl-token';
import BN from 'bn.js';
import {parsePumpLogs} from './events.mjs';
const pump=PUMP_PROGRAM_ID.toBase58(),mint=new PublicKey('So11111111111111111111111111111111111111112');
const payload=Buffer.from('synthetic-event-payload');
const event=Buffer.concat([createHash('sha256').update('event:CreateEvent').digest().subarray(0,8),payload]);
const data='Program data: '+event.toString('base64');
test('SDK decoder receives event payload after eight-byte discriminator',()=>{
  let seen;const result=parsePumpLogs([`Program ${pump} invoke [1]`,data,`Program ${pump} success`],'signature',12,{CreateEvent:b=>{seen=b;return {mint};}});
  assert.deepEqual(seen,payload);assert.equal(result.length,1);assert.equal(result[0].mint,mint.toBase58());
  assert.equal(result[0].id,'signature:0');
});
test('foreign CPI and logs outside Pump cannot forge candidates',()=>{
  const decoders={CreateEvent:()=>({mint})};
  assert.deepEqual(parsePumpLogs([data,`Program ${pump} invoke [1]`,'Program 11111111111111111111111111111111 invoke [2]',data,'Program 11111111111111111111111111111111 success',`Program ${pump} success`],'sig',1,decoders),[]);
});
test('unknown event versions or other quote assets are not guessed',()=>{
  const logs=[`Program ${pump} invoke [1]`,data];
  assert.deepEqual(parsePumpLogs(logs,'sig',1,{CreateEvent:()=>{throw Error('unknown schema');}}),[]);
  assert.deepEqual(parsePumpLogs(logs,'sig',1,{CreateEvent:()=>({mint,quoteMint:PUMP_PROGRAM_ID})}),[]);
});
test('real official curve v3 SDK builds unsigned buy and sell instructions offline',async()=>{
  const user=new PublicKey('11111111111111111111111111111111');
  const bondingCurve={quoteMint:NATIVE_MINT,isCashbackCoin:false};
  const buy=await PUMP_SDK.buyExactQuoteInV3Instructions({bondingCurve,associatedUserAccountInfo:null,mint,user,amount:new BN(200000),quoteAmount:new BN(100000),slippage:1,tokenProgram:TOKEN_2022_PROGRAM_ID,quoteTokenProgram:TOKEN_PROGRAM_ID});
  const sell=await PUMP_SDK.sellV3Instructions({bondingCurve,mint,user,amount:new BN(100000),quoteAmount:new BN(200000),slippage:1,tokenProgram:TOKEN_2022_PROGRAM_ID,quoteTokenProgram:TOKEN_PROGRAM_ID});
  for(const [ixs,name] of [[buy,'buy_exact_quote_in_v3'],[sell,'sell_v3']]){
    const trade=ixs.find(ix=>ix.programId.equals(PUMP_PROGRAM_ID));assert.ok(trade);
    assert.equal(trade.data.subarray(0,8).toString('hex'),createHash('sha256').update('global:'+name).digest().subarray(0,8).toString('hex'));
    assert.equal(trade.data.readBigUInt64LE(8),100000n);assert.ok(trade.data.readBigUInt64LE(16)>=190000n);
  }
});
