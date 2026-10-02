import 'server-only';
import {createClient} from '@supabase/supabase-js';
import {createHash,timingSafeEqual} from 'node:crypto';
import {z} from 'zod';

export const profileSchema=z.object({
  name:z.string().min(2).max(100).default('EXTREME, UNBELIEVABLE & FUNNY MOMENTS'),
  positioning:z.string().max(300).default('Something surprising, impressive, funny, intense, or unbelievable in every Short.'),
  pillars:z.array(z.enum(['insane_sports','parkour_freerunning','human_skills','epic_saves','physical_fails','trick_shots','near_misses','crazy_stunts','unexpected_recoveries','satisfying_moments','ai_tools','robotics','engineering','future_technology','automation'])).min(1).default(['insane_sports','parkour_freerunning','human_skills','epic_saves','physical_fails','trick_shots','near_misses','crazy_stunts','unexpected_recoveries','satisfying_moments']),
  timezone:z.string().refine(value=>{try{new Intl.DateTimeFormat('en',{timeZone:value});return true;}catch{return false;}}).default('Asia/Kolkata'),
  enabled:z.boolean().default(false),auto_publish:z.boolean().default(false),production_target:z.literal(3).default(3),
  publication_limit:z.number().int().min(0).max(3).default(2),
  publishing_hours:z.array(z.number().int().min(0).max(23)).min(2).max(3).refine(v=>new Set(v).size===v.length).default([12,19]),
  quality_threshold:z.number().int().min(75).max(100).default(80),exceptional_threshold:z.number().int().min(90).max(100).default(92),
  ai_mode:z.enum(['auto','local','gemini']).default('auto'),voice_profile:z.enum(['Curious','Energetic','Tech Curious','Tech Energetic','Documentary','Fast Explainer']).default('Energetic'),
  tts_engine:z.enum(['pocket','kokoro','edge']).default('pocket'),privacy:z.enum(['private','unlisted','public']).default('private'),
  made_for_kids:z.boolean().default(false),retain_final_days:z.number().int().min(1).max(365).default(30),
  affiliates:z.array(z.object({product:z.string().min(2).max(100),url:z.string().url().startsWith('https://'),disclosure:z.string().min(10).max(300)}).strict()).max(20).default([]),
  rights_policy:z.enum(['user_managed','documented_permission']).default('user_managed'),
  source_rights:z.array(z.object({source_url:z.string().startsWith('https://').max(2000),creator:z.string().min(1).max(200),license:z.enum(['permission','owned','CC0','CC BY 4.0']).default('permission'),license_evidence_url:z.string().startsWith('https://').max(2000),attribution:z.string().min(1).max(500)}).strict()).max(100).default([])
}).strict();

export function normalizeProfile(raw:any){
  if(raw?.name==='AI & Future Tech'&&raw.pillars?.length===5&&['ai_tools','robotics','engineering','future_technology','automation'].every(x=>raw.pillars.includes(x))){
    const defaults=profileSchema.parse({});raw={...raw,name:defaults.name,positioning:defaults.positioning,pillars:defaults.pillars,voice_profile:raw.voice_profile==='Tech Curious'?'Curious':raw.voice_profile==='Tech Energetic'?'Energetic':raw.voice_profile};
  }
  return profileSchema.parse(raw);
}

export function db(){
  const url=process.env.NEXT_PUBLIC_SUPABASE_URL,key=process.env.SUPABASE_SERVICE_ROLE_KEY;
  if(!url||!key)throw new Error('Configure Supabase before using the cloud control plane.');
  return createClient(url,key,{auth:{persistSession:false,autoRefreshToken:false}});
}
export function hash(value:string){return createHash('sha256').update(value).digest('hex');}
export function secureEqual(a:string,b:string){const x=Buffer.from(a),y=Buffer.from(b);return x.length===y.length&&timingSafeEqual(x,y);}
export async function owner(request:Request){
  const token=request.headers.get('authorization')?.replace(/^Bearer /,'');
  if(!token)throw new Error('Unauthorized');
  const {data,error}=await db().auth.getUser(token);
  if(error||!data.user)throw new Error('Unauthorized');
  return data.user.id;
}
export async function worker(request:Request){
  const token=request.headers.get('authorization')?.replace(/^Bearer /,'');
  if(!token||token.length<32)throw new Error('Unauthorized');
  const {data,error}=await db().from('workers').select('*').eq('token_hash',hash(token)).eq('revoked',false).single();
  if(error||!data)throw new Error('Unauthorized');
  return data;
}
export async function body(request:Request){
  const text=await request.text();if(Buffer.byteLength(text)>65536)throw new Error('Payload too large');return JSON.parse(text);
}
export function failure(error:unknown){const message=error instanceof Error?error.message:'Request failed';return Response.json({error:message==='Unauthorized'?message:'Request rejected. Check configuration and input.'},{status:message==='Unauthorized'?401:400});}
