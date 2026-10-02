import {randomBytes} from 'node:crypto';
import {db,owner,hash,failure} from '../../../lib/server';
export async function POST(request:Request){try{
  const id=await owner(request),token=randomBytes(32).toString('base64url');
  const {data,error}=await db().rpc('pair_cliprank_worker',{p_owner:id,p_token_hash:hash(token)});
  if(error)throw new Error('Pairing unavailable');return Response.json({worker_id:data,token},{headers:{'Cache-Control':'no-store'}});
}catch(error){return failure(error);}}
