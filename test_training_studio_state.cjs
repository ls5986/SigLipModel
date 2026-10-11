// Exercise saved-result state without network calls or browser credentials.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync('training_studio.html','utf8');
const script=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].at(-1)[1].replace('guard(boot)();','');
class Element{constructor(){this.children=[];this.value='';this.textContent='';}append(...items){this.children.push(...items)}replaceChildren(...items){this.children=items}setAttribute(){} }
const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id)};
const sandbox={window:{addEventListener(){}},location:{pathname:'/studio',hash:''},document:{getElementById:get,createElement:()=>new Element(),querySelectorAll:()=>[],addEventListener(){}},structuredClone,setTimeout:()=>1,clearTimeout(){},console};
vm.createContext(sandbox);vm.runInContext(script,sandbox);
(async()=>{
 await vm.runInContext(`(async()=>{
 state.detail={property:{id:'fixture',mls_remarks:'',review:{}},label_evidence_id:'current'};
 state.proposal={label_evidence_id:'current',physical_condition:'C4_AVERAGE_FUNCTIONAL',modernization_state:'ORIGINAL',acquisition_fit:'UNKNOWN',reason:'Fixture',text_signals:[],input_sha256:'input',proposal_id:'draft'};
 renderSignals=()=>{};
 applyDraft(true);
 if(state.dirty)throw Error('Automatic draft blocked navigation');
 if(state.assistantProposalId!=='draft')throw Error('Draft provenance lost');
 applyDraft();
 if(!state.dirty)throw Error('Explicit draft replacement must count as an edit');
 state.dirty=false;
 state.detail.property.review={revision:1,status:'approved'};
 $('physical').value='C3_WELL_MAINTAINED';
 api=async()=>({proposal:state.proposal,request:{status:'completed'}});
 await refreshDraft(state.load,true);
 if($('physical').value!=='C3_WELL_MAINTAINED')throw Error('Human correction replaced by draft');
 if(state.dirty)throw Error('Saved human review marked dirty');
 api=async()=>({status:'completed',training_membership:'train',results:{physical_condition:{label:'C5_REHAB_NEEDED',uncalibrated_probability:0.99}}});
 await refreshPropertyPrediction();
 })()`,sandbox);
 assert.match(JSON.stringify(get('property-prediction')),/Significant repairs needed/);
 assert.match(JSON.stringify(get('property-prediction')),/not calibrated confidence/);
 assert.match(JSON.stringify(get('experimental-prediction')),/not an independent test/);
 assert.equal(get('property-predict').disabled,false);
 await vm.runInContext(`(async()=>{
 const before=$('property-prediction').children;
 api=async()=>{state.load++;return {status:'completed',results:{}}};
 await refreshPropertyPrediction(state.load);
 if($('property-prediction').children!==before)throw Error('Stale prediction replaced current property');
 })()`,sandbox);
 await vm.runInContext(`(async()=>{
 state.load=0;
 api=async(path,payload)=>{
  if(payload){if($('property-prediction').children[0].textContent!=='Submitting model analysis…')throw Error('No immediate click feedback');return {status:'queued'}};
  return {status:'queued'};
 };
 await runPropertyModel();
 if(!$('property-prediction').children[0].textContent.includes('queued'))throw Error('No visible queued status');
 api=async()=>{throw Error('Fixture rejection')};
 try{await runPropertyModel()}catch{}
 if($('property-prediction').children[0].textContent!=='Could not run the model')throw Error('Error hidden away from button');
 if($('property-predict').disabled)throw Error('Button stayed disabled after rejection');
 })()`,sandbox);
 assert.match(JSON.stringify(get('property-prediction')),/Fixture rejection/);
 await vm.runInContext(`(async()=>{
 state.caps=null;api=async()=>({actor:{id:'fixture',role:'operator'},cloud:true,taxonomy:{physical_condition:['UNKNOWN'],modernization:['UNKNOWN'],acquisition_fit:['UNKNOWN'],text_signals:['needs_tlc']}});
 loadQueue=async()=>{};refreshBatch=async()=>{};
 await boot();
 if(typeof $('property-predict').onclick!=='function'||typeof $('experimental-predict').onclick!=='function')throw Error('Model button handler was not installed by boot');
 })()`,sandbox);
 console.log('PASS: automatic draft navigation, explicit edits, saved human correction preservation, inline model results, uncalibrated wording, stale response protection');
})().catch(e=>{console.error(e);process.exitCode=1});
