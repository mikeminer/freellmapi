import {createHash} from 'node:crypto';
import {PUMP_SDK,PUMP_PROGRAM_ID} from './sdk.mjs';
const kinds=['CreateEvent','TradeEvent','CompleteEvent'];
const discriminator=n=>createHash('sha256').update('event:'+n).digest().subarray(0,8).toString('hex');
const names=new Map(kinds.map(n=>[discriminator(n),n]));
const defaultDecoders={CreateEvent:b=>PUMP_SDK.decodeCreateEventBc(b),TradeEvent:b=>PUMP_SDK.decodeTradeEventBc(b),CompleteEvent:b=>PUMP_SDK.decodeCompleteEventBc(b)};
export function parsePumpLogs(logs,signature,slot,decoders=defaultDecoders){
  const stack=[],out=[];let index=0;
  for(const line of logs){
    const invocation=line.match(/^Program (\w+) invoke/);if(invocation){stack.push(invocation[1]);continue;}
    if(/^Program \w+ (success|failed)/.test(line)){stack.pop();continue;}
    if(stack.at(-1)!==PUMP_PROGRAM_ID.toBase58()||!line.startsWith('Program data: '))continue;
    const bytes=Buffer.from(line.slice(14),'base64'),kind=names.get(bytes.subarray(0,8).toString('hex'));if(!kind)continue;
    try{const e=decoders[kind](bytes.subarray(8));if(!e.mint)continue;
      const q=e.quoteMint?.toBase58();if(q&&!['11111111111111111111111111111111','So11111111111111111111111111111111111111112'].includes(q))continue;
      out.push({id:signature+':'+index++,signature,slot,kind,mint:e.mint.toBase58(),user:e.user?.toBase58()??null,creator:e.creator?.toBase58()??null,
        buy:e.isBuy??null,quote_amount:e.solAmount?.toString()??null,token_amount:e.tokenAmount?.toString()??null,
        virtual_quote_reserves:e.virtualSolReserves?.toString()??e.virtualQuoteReserves?.toString()??null,virtual_token_reserves:e.virtualTokenReserves?.toString()??null,observed_ms:Date.now()});
    }catch{/* Unsupported event version stays unknown; no invented values. */}
  }
  return out;
}
