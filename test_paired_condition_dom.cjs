// Run with NODE_PATH pointing to an installed jsdom package.
const {JSDOM,VirtualConsole}=require('jsdom');
const fs=require('fs'),assert=require('assert');
const photo='a'.repeat(64), result={decision:'TARGET',strength:77};
const event={evidence_id:'e1',listing_key:'123',event_role:'acquisition',revision:0,
 photo_manifest:[{sha256:photo}],photo_tags:[{sha256:photo,exclusion:'none'}],
 metadata_snapshot:{structured:{YearBuilt:1974,ListPrice:500000,OriginalListPrice:550000},public_remarks:'Original kitchen <script>alert(1)</script>'},
 prediction:{status:'completed',image:result,metadata:result,overall:result,photos:[{sha256:photo,...result}]}};
let corrections=[],errors=[];
const console=new VirtualConsole();console.on('jsdomError',e=>errors.push(e.message));
const dom=new JSDOM(fs.readFileSync('paired_condition.html','utf8'),{url:'https://studio.test/model-test',runScripts:'dangerously',virtualConsole:console,beforeParse(w){
 w.fetch=async(url,options)=>{
  const path=new URL(url,'https://studio.test'),p=path.pathname;let body={};
  if(p.endsWith('/status'))body={token:'csrf',request:{status:'completed'},dataset:{id:'d1',name:'frozen',version:1},candidate:{candidate_id:'c1',training_groups:20,created_at:new Date().toISOString(),components:{vision:true,text:true,structured:true,fusion:true}}};
  if(p.endsWith('/list'))body={items:[{group_id:'g1',address:'123 Test St',prior_mls:'123',last_mls:'456'},{group_id:'g2',address:'456 Next St'}]};
  if(p.endsWith('/property'))body={group_id:path.searchParams.get('id'),address:path.searchParams.get('id')==='g1'?'123 Test St':'456 Next St',events:[JSON.parse(JSON.stringify(event))]};
  if(p.endsWith('/correct')){assert(options.headers['X-Review-Token']==='csrf');corrections.push(JSON.parse(options.body));body={saved:true,revision:1};}
  return {ok:true,json:async()=>body};
 };
}});
const d=dom.window.document,$=id=>d.getElementById(id);
async function until(fn){for(let i=0;i<200;i++){if(fn())return;await new Promise(r=>setTimeout(r,10));}throw Error('UI did not update');}
(async()=>{
 await until(()=>$('imageDecision'));
 assert(d.querySelector('.hero'));
 assert(d.querySelectorAll('.remarks script').length===0);
 assert(d.querySelector('.remarks').textContent.includes('<script>'));
 $('imageDecision').value='NOT_TARGET';$('imageDecision').dispatchEvent(new dom.window.Event('input'));
 assert($('imageStrength').disabled);
 $('next').click();assert($('message').textContent.includes('Save your correction'));
 $('exclude').click();assert(!d.querySelector('.hero'));
 d.querySelector('[data-include]').click();assert(d.querySelector('.hero'));
 $('saveNext').click();await until(()=>d.querySelector('h2')?.textContent==='456 Next St');
 assert(corrections.length===1&&corrections[0].image.decision==='NOT_TARGET');
 assert(Object.keys(corrections[0].excluded_photos).length===0);
 assert(errors.length===0,errors.join('\n'));
 process.stdout.write('DOM: rendering, XSS escaping, photo exclusion removal, CSRF payload, unsaved edits and save-next passed\n');
 dom.window.close();
})().catch(e=>{process.stderr.write(e.stack+'\n');dom.window.close();process.exit(1)});
