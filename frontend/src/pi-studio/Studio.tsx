import { useState, type FormEvent } from 'react';
import { ArrowLeft, BarChart3, Database, Eye, LogOut, RefreshCw, ShieldCheck } from 'lucide-react';
import { config, PiStudioClient, type Section } from './api';

type Tab='overview'|'review'|'training'|'runs';
const sections:Section[]=['datasets','model_runs','artifacts','evaluations','model_releases'];
const object=(v:unknown):v is Record<string,unknown>=>Boolean(v)&&typeof v==='object'&&!Array.isArray(v);
function summarize(v:Record<string,unknown>) {
  const excluded=new Set(['raw','payload','source','config','settings','manifest','input','output','token','url','uri','path','secret','hash','error_detail','remarks','photo','media','vector','metrics']);
  return Object.entries(v).filter(([key,val])=>!excluded.has(key.toLowerCase()) &&
    (typeof val==='string' || typeof val==='number' || typeof val==='boolean' || val===null))
    .slice(0,9);
}
function Count({label,value}:{label:string;value:string}) {
  return <div className="pi-studio-stat"><span>{label}</span><strong>{value}</strong></div>;
}
export default function Studio(){
  const [state]=useState<{client:PiStudioClient|null;error:string}>(()=>{
    try {return {client:new PiStudioClient(config(import.meta.env)),error:''};}
    catch(e){return {client:null,error:e instanceof Error?e.message:'PI configuration unavailable'};}
  });
  const client=state.client;
  const [signed,setSigned]=useState(false);
  const [email,setEmail]=useState('');
  const [password,setPassword]=useState('');
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState(state.error);
  const [tab,setTab]=useState<Tab>('overview');
  const [data,setData]=useState<Record<string,unknown>|null>(null);
  const [topic,setTopic]=useState('');
  async function login(e:FormEvent){
    e.preventDefault();if(!client)return;
    setBusy(true);setError('');
    try{await client.signIn(email,password);setPassword('');setSigned(true);}
    catch(err){setError(err instanceof Error?err.message:'Could not authenticate');}
    finally{setBusy(false);}
  }
  async function load(key:string){
    if(!client)return;
    setBusy(true);setError('');setData(null);setTopic(key);
    try{
      const result=sections.includes(key as Section)?await client.catalog(key as Section):
        key==='triage'?await client.triage():
        key==='evaluations-history'?await client.evaluations():await client.disagreements();
      setData(result);
    }catch(err){setError(err instanceof Error?err.message:'Read unavailable');}
    finally{setBusy(false);}
  }
  function signOut(){client?.signOut();setSigned(false);setData(null);setTopic('');}
  if(!client||!signed)return <div className="pi-studio-login"><section className="pi-studio-login-card">
    <ShieldCheck size={28}/><h1>ACQ Vision</h1><p>Property Intelligence DEV · Independent Studio preview</p>
    <p className="pi-studio-note">Read-only Model Lab inspection. No legacy training worker or database writes.</p>
    {error&&<p role="alert" className="pi-studio-error">{error}</p>}
    {client&&<form onSubmit={login}><label>PI DEV email<input type="email" required value={email} onChange={e=>setEmail(e.target.value)}/></label>
      <label>Password<input type="password" required autoComplete="current-password" value={password} onChange={e=>setPassword(e.target.value)}/></label>
      <button disabled={busy} type="submit">{busy?'Authenticating…':'Sign in to PI DEV'}</button></form>}
    </section></div>;
  return <div className="pi-studio-root"><header className="pi-studio-header">
    <div><ShieldCheck size={20}/><strong>ACQ Vision</strong><span>PI Model Lab preview</span></div>
    <button onClick={signOut}><LogOut size={16}/>Sign out</button></header>
    <div className="pi-studio-body"><nav className="pi-studio-nav" aria-label="Studio sections">
      {([['overview','Overview'],['review','Model Review'],['training','Training'],['runs','Runs & Releases']] as const)
        .map(([key,name])=><button key={key} className={tab===key?'selected':''} onClick={()=>{setTab(key);setData(null);setError('');setTopic('');}}>{name}</button>)}
    </nav><main><span className="pi-studio-eyebrow">CANONICAL PROPERTY INTELLIGENCE · READ ONLY</span>
    <h1>{tab==='overview'?'Model Lab':tab==='review'?'Review evidence':tab==='training'?'Training datasets':'Model runs & releases'}</h1>
    <p>{tab==='overview'?'Inspect the actual hosted model histories, not invented performance metrics.':
      tab==='review'?'Locate condition disagreements and triage. A proposal is not a human-approved label.':
      tab==='training'?'Examine datasets and their recorded readiness without freezing, training or promotion.':
      'Inspect persisted run, evaluation, artifact and release metadata. A completed run does not certify a deployed model.'}</p>
    {tab==='overview'&&<div className="pi-studio-stats">
      <Count label="Vision" value="Inspect actual runs"/><Count label="Text & Structured" value="Inspect histories"/>
      <Count label="Fusion" value="Evaluation required"/></div>}
    <div className="pi-studio-actions">
      {(tab==='review'?[['triage','Property triage'],['disagreements','Condition disagreements'],['evaluations-history','Recorded evaluations']]:
      tab==='training'?[['datasets','Datasets'],['evaluations','Evaluations']]:
      tab==='runs'?[['model_runs','Model runs'],['artifacts','Artifacts'],['model_releases','Releases'],['evaluations','Evaluations']]:
      [['triage','Property triage'],['model_runs','Model runs'],['datasets','Datasets']]).map(([key,label])=>
       <button key={key} disabled={busy} onClick={()=>void load(key)}><Database size={16}/>{label}</button>)}
    </div>
    {busy&&<p role="status">Reading permitted PI Model Lab records…</p>}
    {error&&<p className="pi-studio-error" role="alert">{error}</p>}
    {data&&<section className="pi-studio-results"><div className="pi-studio-section-header">
      <h2>{topic.replaceAll('_',' ')}</h2><button onClick={()=>void load(topic)}><RefreshCw size={15}/>Reload</button>
      </div><p className="pi-studio-note">Only simple permitted metadata fields are shown. Missing results do not imply zero records across the full system.</p>
      {Object.entries(data).filter(([k])=>!['token','secrets','configuration','raw','manifest','source'].includes(k))
        .map(([key,val])=><section key={key} className="pi-studio-response">
        <h3>{key.replaceAll('_',' ')}</h3>
        {Array.isArray(val)?(val.length===0?<p>No items returned in this authorized page.</p>:
        <><p>{val.length} entries returned in this bounded response</p><div className="pi-studio-items">{val.slice(0,50).map((entry,i)=><article key={i}>
          {object(entry)?summarize(entry).map(([field,value])=><div key={field}><span>{field.replaceAll('_',' ')}</span><strong>{value===null?'Unknown':String(value)}</strong></div>):
          <span>Item {i+1} (unsupported metadata shape)</span>}</article>)}</div></>):
        typeof val==='string'||typeof val==='number'||typeof val==='boolean'||val===null?
        <p>{val===null?'Unknown':String(val)}</p>:
        object(val)?<div className="pi-studio-items"><article>{summarize(val).map(([field,value])=><div key={field}>
          <span>{field.replaceAll('_',' ')}</span><strong>{value===null?'Unknown':String(value)}</strong></div>)}</article></div>:
        <p>Not displayed in this bounded preview.</p>}
      </section>)}
    </section>}
    <footer><Eye size={15}/> Model reviews, label writes, training execution and model promotion remain disabled. Existing PI DEV controls remain authoritative.</footer>
    </main></div></div>;
}
