import {db,owner,body,profileSchema,normalizeProfile,failure} from '../../../lib/server';
export async function GET(request:Request){try{
  const id=await owner(request),client=db();
  const [channel,jobs,workers]=await Promise.all([client.from('channels').select('*').eq('owner_id',id).maybeSingle(),client.from('control_jobs').select('*').eq('owner_id',id).order('created_at',{ascending:false}).limit(30),client.from('workers').select('id,last_seen,summary,revoked').eq('owner_id',id)]);
  if(channel.error||jobs.error||workers.error)throw new Error('Database unavailable');
  return Response.json({profile:normalizeProfile(channel.data?.profile??{}),jobs:jobs.data,workers:workers.data});
}catch(error){return failure(error);}}
export async function POST(request:Request){try{
  const id=await owner(request),profile=profileSchema.parse(await body(request));
  const {error}=await db().from('channels').upsert({owner_id:id,profile},{onConflict:'owner_id'});
  if(error)throw new Error('Database unavailable');return Response.json({profile});
}catch(error){return failure(error);}}
