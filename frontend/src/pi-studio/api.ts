// PI customer and Model Lab data are accessed via the authorized PI FastAPI facade,
// never browser SQL, legacy Studio cookies, or the legacy Model Training database.
// Auth session is memory-only for this experimental isolated frontend.
const PI_AUTH = 'https://bveulyklycgxbnepehis.supabase.co';
const PI_API = 'https://property-intelligence-dossier-dev.onrender.com';
export type Section = 'datasets' | 'model_runs' | 'artifacts' | 'evaluations' | 'model_releases';
export type Gate = 'not_loaded' | 'loaded' | 'denied';
export function config(env: Record<string,string|undefined>) {
  const auth = env.VITE_PI_SUPABASE_URL;
  const api = env.VITE_PI_API_ORIGIN;
  const key = env.VITE_PI_SUPABASE_PUBLISHABLE_KEY;
  if (auth !== PI_AUTH || api !== PI_API || !key || !key.startsWith('sb_publishable_'))
    throw new Error('A public PI DEV publishable key and exact PI DEV origins are required. No legacy fallback.');
  return {auth,api,key};
}
export class PiStudioClient {
  private token: string | null = null;
  private expiresAt = 0;
  constructor(readonly settings: ReturnType<typeof config>) {}
  get signedIn() { return Boolean(this.token && Date.now() < this.expiresAt) }
  async signIn(email: string, password: string) {
    const response = await fetch(this.settings.auth+'/auth/v1/token?grant_type=password',{
      method:'POST',cache:'no-store',credentials:'omit',
      headers:{'apikey':this.settings.key,'Content-Type':'application/json'},
      body:JSON.stringify({email,password})
    });
    if(!response.ok)throw new Error(response.status===400?'The PI DEV credentials were not accepted.':'PI DEV sign-in is unavailable.');
    const raw:unknown = await response.json();
    if (!raw || typeof raw!=='object') throw new Error('PI DEV authentication returned an invalid response.');
    const v=raw as Record<string,unknown>;
    if(typeof v.access_token!=='string'||typeof v.expires_in!=='number'||v.expires_in<=0)
      throw new Error('PI DEV authentication did not return a valid session.');
    this.token=v.access_token;this.expiresAt=Date.now()+v.expires_in*1000-30000;
  }
  signOut(){this.token=null;this.expiresAt=0;}
  async read(path: string): Promise<Record<string,unknown>> {
    if(!this.signedIn)throw new Error('Sign in again. Your memory-only PI DEV session expired.');
    const response=await fetch(this.settings.api+path,{method:'GET',credentials:'omit',redirect:'error',
      cache:'no-store',headers:{'Authorization':'Bearer '+this.token,'Accept':'application/json'}});
    if(!response.ok)throw new Error(response.status===401?'PI session expired.':response.status===403
      ?'This PI account lacks Model Lab review access.':response.status===422
      ?'PI rejected the requested catalog version.':'PI Model Lab is unavailable (HTTP '+response.status+').');
    if(!response.headers.get('content-type')?.includes('application/json'))throw new Error('PI returned a non-JSON response.');
    const v:unknown=await response.json();
    if(!v||typeof v!=='object'||Array.isArray(v))throw new Error('Invalid PI response envelope.');
    return v as Record<string,unknown>;
  }
  catalog(section:Section){return this.read('/api/model-lab/v1/catalog?contract_version=model-lab-catalog-read-v2&origin=destination&section='+section+'&limit=50');}
  triage(){return this.read('/api/model-lab/v1/property-triage');}
  evaluations(){return this.read('/api/model-lab/v1/property-evaluations');}
  disagreements(){return this.read('/api/model-lab/v9/condition-review-priority');}
}
