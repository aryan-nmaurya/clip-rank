import {z} from 'zod';
import {db,worker,body,profileSchema,normalizeProfile,failure} from '../../../lib/server';
const updateSchema=z.object({job_id:z.string().uuid().optional(),status:z.enum(['PROCESSING','COMPLETE','FAILED']).optional(),
  summary:z.object({online:z.boolean(),queue:z.number().int().min(0).max(1000),current_job:z.string().max(100).nullable(),
    tasks:z.array(z.object({id:z.string().max(100),title:z.string().max(200),status:z.string().max(50),video_id:z.string().regex(/^[A-Za-z0-9_-]{1,100}$/).nullable()})).max(20)}).strict()}).strict();
export async function GET(request:Request){try{
  const machine=await worker(request),client=db();
  const {data:channel,error}=await client.from('channels').select('profile').eq('owner_id',machine.owner_id).single();
  if(error)throw new Error('Configure channel first');
  const profile=normalizeProfile(channel.profile);
  if(!profile.enabled)return Response.json({profile,job:null});
  const {data:jobs,error:claim}=await client.rpc('claim_cliprank_job',{p_worker:machine.id});
  if(claim)throw new Error('Queue unavailable');return Response.json({profile,job:jobs?.[0]??null});
}catch(error){return failure(error);}}
export async function POST(request:Request){try{
  const machine=await worker(request),input=updateSchema.parse(await body(request)),client=db();
  const {error}=await client.from('workers').update({last_seen:new Date().toISOString(),summary:input.summary}).eq('id',machine.id).eq('revoked',false);
  if(error)throw new Error('Heartbeat unavailable');
  if(input.job_id){
    const {data,error:updated}=await client.from('control_jobs').update({status:input.status??'PROCESSING',lease_until:new Date(Date.now()+120000).toISOString()})
      .eq('id',input.job_id).eq('worker_id',machine.id).eq('status','PROCESSING').select('id');
    if(updated||!data?.length)throw new Error('Lease lost');
  }
  return Response.json({ok:true});
}catch(error){return failure(error);}}
