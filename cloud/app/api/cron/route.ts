import {db,secureEqual,failure} from '../../../lib/server';
export const maxDuration=30;
export async function GET(request:Request){try{
  const secret=process.env.CRON_SECRET;
  if(!secret||!secureEqual(request.headers.get('authorization')??'','Bearer '+secret))throw new Error('Unauthorized');
  const client=db();const {data,error}=await client.from('channels').select('owner_id,profile').eq('profile->>enabled','true');
  if(error)throw new Error('Database unavailable');let queued=0;
  for(const channel of data??[]){
    const day=new Intl.DateTimeFormat('en-CA',{timeZone:channel.profile.timezone}).format(new Date());
    const {error:insert}=await client.from('control_jobs').upsert({owner_id:channel.owner_id,day,kind:'PLAN_DAY',payload:{}},{onConflict:'owner_id,day,kind',ignoreDuplicates:true});
    if(insert)throw new Error('Queue unavailable');queued++;
  }
  return Response.json({queued,compute:'All discovery, AI, rendering and uploads remain on the paired worker.'});
}catch(error){return failure(error);}}
