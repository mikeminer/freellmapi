/** Public-key-only worker. No signing API, Keypair, secret input or sends. */
import fs from 'node:fs';
import BN from 'bn.js';
import {Connection, PublicKey, TransactionMessage, VersionedTransaction, ComputeBudgetProgram} from '@solana/web3.js';
import {unpackMint, getExtensionTypes, ExtensionType, TOKEN_PROGRAM_ID, TOKEN_2022_PROGRAM_ID, NATIVE_MINT, getAssociatedTokenAddressSync} from '@solana/spl-token';
import {OnlinePumpSdk,PUMP_SDK,PUMP_PROGRAM_ID,pumpIdl,bondingCurvePda,getBuyTokenAmountFromSolAmount,getSellSolAmountFromTokenAmount,OnlinePumpAmmSdk,PUMP_AMM_SDK,PUMP_AMM_PROGRAM_ID,canonicalPumpPoolPda,pumpAmmJson,buyQuoteInput,sellBaseInput} from './sdk.mjs';
import {parsePumpLogs} from './events.mjs';
import {discoverConfirmed} from './discovery.mjs';

console.log=()=>{};console.warn=()=>{};
const fail=code=>{throw new Error(code);};
const number=value=>{if(typeof value!=='string'||!/^\d{1,20}$/.test(value)||BigInt(value)<=0n||BigInt(value)>18446744073709551615n)fail('invalid_atomic_amount');return new BN(value);};
const int=(n,min,max)=>{if(!Number.isInteger(n)||n<min||n>max)fail('invalid_integer');return n;};
const fetchBounded=(url,opts={})=>fetch(url,{...opts,signal:AbortSignal.timeout(12000)});
const ids={curve:PUMP_PROGRAM_ID.toBase58(),amm:PUMP_AMM_PROGRAM_ID.toBase58()};
const safeKeys=new Set(['command','rpc_url','network','account','mint','side','amount','slippage_bps','priority_micro_lamports','limit','before']);
function serialize(x){if(x===null||x===undefined)return x;if(BN.isBN(x))return x.toString();if(x instanceof PublicKey)return x.toBase58();if(typeof x==='bigint')return x.toString();if(Array.isArray(x))return x.map(serialize);if(typeof x==='object')return Object.fromEntries(Object.entries(x).map(([k,v])=>[k,serialize(v)]));return x;}
function client(a){const u=new URL(a.rpc_url);if(u.protocol!=='https:'||u.username||u.password||!['mainnet','devnet'].includes(a.network))fail('invalid_rpc_scope');return new Connection(u.href,{commitment:'confirmed',disableRetryOnRateLimit:true,fetch:fetchBounded});}
async function inspect(c,a){
  const mint=new PublicKey(a.mint),user=new PublicKey(a.account),curve=bondingCurvePda(mint);
  const [raw,curveRaw]=await c.getMultipleAccountsInfo([mint,curve]);
  if(!raw||(!raw.owner.equals(TOKEN_PROGRAM_ID)&&!raw.owner.equals(TOKEN_2022_PROGRAM_ID)))fail('unsupported_mint_owner');
  if(!curveRaw||!curveRaw.owner.equals(PUMP_PROGRAM_ID))fail('not_a_pump_curve');
  const mintInfo=unpackMint(mint,raw,raw.owner),bc=PUMP_SDK.decodeBondingCurve(curveRaw);
  const ext=getExtensionTypes(mintInfo.tlvData).map(x=>ExtensionType[x]);
  const unsafe=ext.filter(x=>!['MetadataPointer','TokenMetadata'].includes(x));
  const complete=Boolean(bc.complete),pool=canonicalPumpPoolPda(mint,NATIVE_MINT);
  const poolRaw=complete?await c.getAccountInfo(pool):null;
  if(poolRaw&&!poolRaw.owner.equals(PUMP_AMM_PROGRAM_ID))fail('invalid_pool_owner');
  const venue=!complete?'curve':poolRaw?'amm':'migrating';
  const ata=getAssociatedTokenAddressSync(mint,user,false,raw.owner);
  let held='0';const ataRaw=await c.getAccountInfo(ata);if(ataRaw)held=ataRaw.data.readBigUInt64LE(64).toString();
  const result={mint:a.mint,account:a.account,network:a.network,venue,curve:curve.toBase58(),pool:poolRaw?pool.toBase58():null,
    token_program:raw.owner.toBase58(),decimals:mintInfo.decimals,supply:mintInfo.supply.toString(),mayhem:Boolean(bc.isMayhemMode),mint_authority:mintInfo.mintAuthority?.toBase58()??null,
    freeze_authority:mintInfo.freezeAuthority?.toBase58()??null,extensions:ext,unsupported_extensions:unsafe,token_balance:held,token_account:ata.toBase58(),
    virtual_token_reserves:bc.virtualTokenReserves.toString(),virtual_quote_reserves:bc.virtualQuoteReserves?.toString()??bc.virtualSolReserves?.toString(),
    real_token_reserves:bc.realTokenReserves.toString(),real_quote_reserves:bc.realQuoteReserves?.toString()??bc.realSolReserves?.toString(),retrieved_ms:Date.now(),source:'official SDK + confirmed Solana RPC'};
  if(poolRaw){const p=PUMP_AMM_SDK.decodePool(poolRaw);const vaults=await c.getMultipleAccountsInfo([p.poolBaseTokenAccount,p.poolQuoteTokenAccount]);
    if(!vaults[0]||!vaults[1])fail('pool_reserves_unavailable');
    result.pool_base_reserves=vaults[0].data.readBigUInt64LE(64).toString();result.pool_quote_reserves=vaults[1].data.readBigUInt64LE(64).toString();
    result.pool_virtual_quote_reserves=p.virtualQuoteReserves.toString();result.pool_fee_buckets=p.protocolFees.add(p.creatorFees).toString();
  }
  if(a.command==='inspect'){
    try{const largest=await c.getTokenLargestAccounts(mint);const excluded=new Set([getAssociatedTokenAddressSync(mint,curve,true,raw.owner).toBase58()]);
      if(poolRaw){const p=PUMP_AMM_SDK.decodePool(poolRaw);excluded.add(p.poolBaseTokenAccount.toBase58());}
      result.largest_token_accounts=largest.value.filter(x=>!excluded.has(x.address.toBase58())).slice(0,10).map(x=>({address:x.address.toBase58(),amount:x.amount}));
    }catch{result.largest_token_accounts=null;result.risk_data_error='largest_token_accounts_unavailable';}
    result.concentration_note='Token accounts, not deduplicated wallet holders; protocol reserve accounts excluded.';
  }
  return {result,mint,user,bc,mintInfo,tokenProgram:raw.owner,pool};
}
async function quote(c,a){
  const state=await inspect(c,a);const {result,mint,user,bc,mintInfo,tokenProgram,pool}=state;
  if(result.venue==='migrating')return {...result,ok:false,code:'migration_pending',retryable_after_seconds:30,sent:false};
  if(result.unsupported_extensions.length)fail('unsupported_token_extensions');
  const amount=number(a.amount),bps=int(a.slippage_bps,1,500),slip=bps/100;
  if(!['buy','sell'].includes(a.side))fail('invalid_side');
  let ixs,expected,minOut,maxIn;
  if(result.venue==='curve'){
    const online=new OnlinePumpSdk(c);const [global,feeConfig,buy]=await Promise.all([online.fetchGlobal(),online.fetchFeeConfig(),online.fetchBuyState(mint,user,tokenProgram)]);
    if(!buy.quoteMint.equals(NATIVE_MINT))fail('only_sol_quote_supported');
    if(a.side==='buy'){
      expected=getBuyTokenAmountFromSolAmount({global,feeConfig,mintSupply:new BN(mintInfo.supply.toString()),bondingCurve:bc,amount,quoteMint:NATIVE_MINT,quoteTransferFee:buy.quoteTransferFee});
      // Refuse a buy which would cross completion rather than hiding a migration.
      if(expected.gte(bc.realTokenReserves))fail('buy_would_cross_migration');
      minOut=expected.muln(10000-bps).divn(10000);maxIn=amount;
      ixs=await PUMP_SDK.buyExactQuoteInV3Instructions({bondingCurve:bc,associatedUserAccountInfo:buy.associatedUserAccountInfo,mint,user,amount:expected,quoteAmount:amount,slippage:slip,tokenProgram,quoteTokenProgram:buy.quoteTokenProgram});
    }else{
      expected=getSellSolAmountFromTokenAmount({global,feeConfig,mintSupply:new BN(mintInfo.supply.toString()),bondingCurve:bc,amount,quoteTransferFee:buy.quoteTransferFee});
      minOut=expected.muln(10000-bps).divn(10000);maxIn=amount;
      ixs=await PUMP_SDK.sellV3Instructions({bondingCurve:bc,mint,user,amount,quoteAmount:expected,slippage:slip,tokenProgram,quoteTokenProgram:buy.quoteTokenProgram});
    }
  }else{
    const state=await new OnlinePumpAmmSdk(c).swapSolanaState(pool,user);
    if(!state.pool.baseMint.equals(mint)||!state.pool.quoteMint.equals(NATIVE_MINT))fail('pool_mint_scope_mismatch');
    const common={baseReserve:state.poolBaseAmount,quoteReserve:state.poolQuoteAmount,virtualQuoteReserves:state.pool.virtualQuoteReserves,globalConfig:state.globalConfig,baseMintAccount:state.baseMintAccount,baseMint:mint,coinCreator:state.pool.coinCreator,creator:state.pool.creator,feeConfig:state.feeConfig,quoteMint:NATIVE_MINT,isMayhemMode:state.pool.isMayhemMode,creatorFeeBps:state.pool.creatorFeeBps,quoteTransferFee:state.quoteTransferFee};
    if(a.side==='buy'){
      const q=buyQuoteInput({...common,quote:amount,slippage:slip,tradeV2:true});expected=q.base;minOut=q.base.muln(10000-bps).divn(10000);maxIn=q.maxQuote;
      // Explicit v2 exact-input instruction, SDK's minimum-out and spend bounds.
      ixs=await PUMP_AMM_SDK.buyExactQuoteInV2Instructions(state,amount,minOut);
      maxIn=amount;
    }else{
      const q=sellBaseInput({...common,base:amount,slippage:slip,feeBucketsTotal:state.pool.protocolFees.add(state.pool.creatorFees)});
      expected=q.uiQuote;minOut=q.minQuote;maxIn=amount;ixs=await PUMP_AMM_SDK.sellV2Instructions(state,amount,minOut);
    }
  }
  if(minOut.lten(0)||expected.lten(0))fail('zero_quote_output');
  const priority=int(a.priority_micro_lamports??0,0,1000000),cu=300000;
  ixs=[ComputeBudgetProgram.setComputeUnitLimit({units:cu}),ComputeBudgetProgram.setComputeUnitPrice({microLamports:priority}),...ixs];
  const block=await c.getLatestBlockhash('confirmed');const message=new TransactionMessage({payerKey:user,recentBlockhash:block.blockhash,instructions:ixs}).compileToV0Message();
  const tx=new VersionedTransaction(message);let bytes;try{bytes=tx.serialize();}catch(e){fail('transaction_requires_lookup_table_unsupported');}
  if(bytes.length>1232)fail('transaction_requires_lookup_table_unsupported');
  const fee=(await c.getFeeForMessage(message,'confirmed')).value;if(fee===null)fail('fee_unavailable');
  const rent=await c.getMinimumBalanceForRentExemption(165),walletBalance=await c.getBalance(user,'confirmed');
  const instructions=ixs.map(ix=>({program:ix.programId.toBase58(),keys:ix.keys.map(k=>({address:k.pubkey.toBase58(),signer:k.isSigner,writable:k.isWritable})),data:ix.data.toString('base64')}));
  return {...result,side:a.side,input_amount:amount.toString(),expected_output:expected.toString(),minimum_output:minOut.toString(),max_input:maxIn.toString(),slippage_bps:bps,
    fee_lamports:fee,rent_buffer_lamports:rent*3,priority_micro_lamports:priority,wallet_lamports:walletBalance,
    unsigned_transaction:Buffer.from(bytes).toString('base64'),instructions,blockhash:block.blockhash,last_valid_block_height:block.lastValidBlockHeight,sdk_version:'4.3.1',sent:false};
}
async function discover(c,a){
  return discoverConfirmed(c,a,{fetcher:fetchBounded});
}
export async function main(a){
  if(!a||typeof a!=='object'||Array.isArray(a)||Object.keys(a).some(k=>!safeKeys.has(k)))fail('unsupported_or_secret_field');
  if(a.command==='status')return {sdk:'@pump-fun/pump-sdk',version:'4.3.1',programs:ids,signing:false,sending:false};
  const c=client(a);if(a.command==='discover')return discover(c,a);
  if(a.command==='inspect')return (await inspect(c,a)).result;
  if(a.command==='quote')return quote(c,a);fail('unknown_public_command');
}
if(process.argv[1]===new URL(import.meta.url).pathname.replace(/^\/(\w:)/,'$1').replaceAll('/',process.platform==='win32'?'\\':'/')){
  let raw='';for await(const chunk of process.stdin){raw+=chunk;if(raw.length>100000)fail('request_too_large');}
  const timeout=setTimeout(()=>{process.stdout.write(JSON.stringify({ok:false,code:'pump_sdk_timeout',sent:false}));process.exit(0);},45000);
  try{const result=await main(JSON.parse(raw));process.stdout.write(JSON.stringify(serialize(result)));}catch(e){const code=/429/.test(e.message)?'rpc_rate_limited':/^[a-z][a-z0-9_]{1,90}$/.test(e.message)?e.message:'pump_sdk_request_failed';process.stdout.write(JSON.stringify({ok:false,code,error_type:e.constructor.name,sent:false}));}
  finally{clearTimeout(timeout);}
}
