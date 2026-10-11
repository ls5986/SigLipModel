const {JSDOM,VirtualConsole}=require('jsdom'),fs=require('fs'),assert=require('assert');
let saved=false,errors=[];
const photo='a'.repeat(64),target={decision:'TARGET',strength:77};
const console=new VirtualConsole();console.on('jsdomError',x=>errors.push(x.message));
const dom=new JSDOM(fs.readFileSync('paired_condition.html','utf8'),{url:'https://studio.test/model-test?batch=benchmark',runScripts:'dangerously',virtualConsole:console,beforeParse(w){w.fetch=async(url,options)=>{
 const u=new URL(url,'https://studio.test');let body={};
 if(u.pathname.endsWith('/status'))body={token:'csrf',request:{status:'completed'},dataset:{id:'d'},candidate:{candidate_id:'c',created_at:new Date().toISOString(),components:{text:true}}};
 if(u.pathname.endsWith('/list'))body={items:[{group_id:'g',address:'Test St',prior_mls:'123',photo_events:[{evidence_id:'e',reviewed:saved,stored:6,usable:6}]},{group_id:'other',address:'Other St',prior_mls:'456',photo_events:[]}],review_batches:{benchmark:{members:[{group_id:'g',evidence_id:'e'}],total:1,completed:saved?1:0,report:{metadata:{correct:0,known:saved?1:0,uncertain:0}}},corrections:{members:[],total:0,completed:0}}};
 if(u.pathname.endsWith('/property'))body={group_id:'g',address:'Test St',events:[{evidence_id:'e',listing_key:'123',photo_manifest:[{sha256:photo}],metadata_snapshot:{structured:{YearBuilt:1970},public_remarks:'Original kitchen'},signals:[{signal_name:'deferred_maintenance',source_field:'PublicRemarks',state:'PRESENT',snippet:'Original kitchen'}],prediction:{status:'completed',image:target,metadata:target,overall:target,photos:[{sha256:photo,...target}]},silver_reference:{image:target,metadata:target,overall:target},review:saved?{image:{decision:'NOT_TARGET'},metadata:{decision:'NOT_TARGET'},overall:{decision:'NOT_TARGET'}}:{}}]};
 if(u.pathname.endsWith('/correct')){saved=true;body={saved:true};assert(options.headers['X-Review-Token']==='csrf');}
 return {ok:true,json:async()=>body};};}});
const d=dom.window.document,$=id=>d.getElementById(id);
async function until(fn){for(let i=0;i<200;i++){if(fn())return;await new Promise(r=>setTimeout(r,10));}throw Error('UI timeout');}
(async()=>{
 await until(()=>$('imageDecision'));
 assert($('batchFilter').value==='benchmark');assert(!$('properties').textContent.includes('Other St'));
 assert($('imageDecision').value===$('metadataDecision').value&&$('metadataDecision').value==='INSUFFICIENT_EVIDENCE');
 assert($('run').disabled);assert(!$('photoResult').textContent.includes('TARGET'));
 assert(!$('property').textContent.includes('How metadata is combined'));
 assert(!$('property').textContent.includes('Remark keyword evidence'));
 $('imageDecision').value='NOT_TARGET';$('imageDecision').dispatchEvent(new dom.window.Event('input'));
 $('metadataDecision').value='NOT_TARGET';$('metadataDecision').dispatchEvent(new dom.window.Event('input'));
 // An unsaved gallery change must not reveal suggestions.
 $('exclude').click();assert($('run').disabled);assert(!$('property').textContent.includes('How metadata is combined'));
 $('batchFilter').value='all';$('batchFilter').dispatchEvent(new dom.window.Event('change'));
 assert($('batchFilter').value==='benchmark');assert($('run').disabled);
 $('save').click();await until(()=>$('batchInfo').textContent.includes('1 / 1 saved'));
 await until(()=>$('property').textContent.includes('Your saved decision'));
 assert($('property').textContent.includes('How metadata is combined'));
 assert($('batchInfo').textContent.includes('0/1 match your known decisions'));
 assert(errors.length===0,errors.join('\n'));
 process.stdout.write('Batch DOM: scoped queue, hidden suggestions, unsaved changes and saved benchmark report passed\n');dom.window.close();
})().catch(e=>{process.stderr.write(e.stack+'\n');dom.window.close();process.exit(1)});
