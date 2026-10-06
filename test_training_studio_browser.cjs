const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');

(async()=>{
 const browser=await chromium.launch({headless:true,timeout:20000});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const schema=JSON.parse(fs.readFileSync(path.join(__dirname,'contracts','actvision-v2.schema.json'),'utf8'));
  const html=fs.readFileSync(path.join(__dirname,'training_studio.html'),'utf8');
  const calls=[],saved=[],errors=[];
  let failSave=false;
  let frozenDataset={status:'none'};
  page.on('pageerror',error=>errors.push(error.message));
  const caps={token:'test-csrf',cloud:true,training:false,actor:{id:'verified-reviewer',role:'reviewer'},blockers:['Trained v2 artifacts not configured'],taxonomy:{
   physical_condition:schema.$defs.condition.enum,modernization:schema.$defs.modernization.enum,acquisition_fit:schema.$defs.acquisition_fit.enum,text_signals:schema.$defs.text_signal.enum
  }};
  const details={
   first:{label_evidence_id:'a'.repeat(64),property:{id:'first',address:'<script>unsafe()</script> Example',mls_remarks:'Bring your vision to this home.',metadata:{YearBuilt:1963,ClosePrice:999999,PublicRemarks:'Do not treat me as structured'},review:{revision:2,target_fit:'target',target_score:5,reason_tags:['Advanced: kitchen opportunity'],standout_image_ids:['first:photo']}},images:[{id:'first:photo',sha256:'a'.repeat(64),review:{revision:0},effective:{context:'subject_interior',room:'kitchen'},selection:{included:true}}],historical_source:{blocked:false,timing_verified:true}},
   second:{label_evidence_id:'b'.repeat(64),property:{id:'second',address:'Next property',mls_remarks:'',metadata:{},review:{revision:0}},images:[],historical_source:{blocked:false,timing_verified:false}}
  };
  await page.route('http://studio.test/**',async route=>{
   const request=route.request(),url=new URL(request.url());calls.push({method:request.method(),path:url.pathname});
   if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:html});
   if(url.pathname==='/api/studio/v2/capabilities')return route.fulfill({json:caps});
   if(url.pathname==='/api/studio/image')return route.fulfill({contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1sAAAAASUVORK5CYII=','base64')});
   if(url.pathname==='/api/studio/review-queue')return route.fulfill({json:{items:[{id:'first',address:'Example',status:'unscored'},{id:'second',address:'Next property',status:'unscored'}],total:2,counts:{unscored:2,reviewed:0,photo_match:1,missing_text:2}}});
   if(url.pathname==='/api/studio/v2/property')return route.fulfill({json:details[url.searchParams.get('id')]});
   if(url.pathname==='/api/studio/v2/label'){
    assert.equal(request.headers()['x-review-token'],'test-csrf');
    if(failSave)return route.fulfill({status:409,json:{error:'Evidence changed; reload before saving labels'}});
    saved.push(JSON.parse(request.postData()));return route.fulfill({json:{revision:1}});
   }
   if(url.pathname==='/api/studio/v2/feedback')return route.fulfill({json:{items:[],notice:'No training on review'}});
   if(url.pathname==='/api/studio/v2/releases')return route.fulfill({json:{items:[],promotion_available:caps.actor.role==='operator',reason:'Explicit promotion only'}});
   if(url.pathname==='/api/studio/v2/operations')return route.fulfill({json:{inference_enabled:false,workers:{actvision_v2_model:{online:false,status:'offline'}}}});
   if(url.pathname==='/api/studio/v2/experimental/status')return route.fulfill({json:{request:{status:'none'},candidate:null,tasks:{},review_next:[]}});
   if(url.pathname==='/api/studio/v2/experimental/prediction')return route.fulfill({json:{status:'none'}});
   if(url.pathname==='/api/studio/v2/actvision/prediction')return route.fulfill({json:{status:'none',release:null}});
   if(url.pathname==='/api/studio/v2/training/status')return route.fulfill({json:{request:{status:'none'},runs:{},release:null}});
   if(url.pathname==='/api/studio/v2/dataset/latest')return route.fulfill({json:frozenDataset});
   if(url.pathname==='/api/studio/v2/label/proposal')return route.fulfill({json:{request:{status:'completed'},proposal:{...details.first,label_evidence_id:details.first.label_evidence_id,status:'draft',physical_condition:'C4_AVERAGE_FUNCTIONAL',modernization_state:'ORIGINAL',acquisition_fit:'UNKNOWN',reason:'Draft needs verification',text_signals:[]}}});
   if(url.pathname==='/api/studio/v2/label/propose')return route.fulfill({json:{status:'queued'}});
   if(url.pathname==='/api/studio/v2/dataset/preview')return route.fulfill({json:{policy:'actvision-label-provenance-v2',fingerprint:'preview-hash',trainable:true,counts:{properties:28,splits:{train:20,validation:4,test:4}}}});
   if(url.pathname==='/api/studio/v2/dataset/freeze'){
    assert.deepEqual(JSON.parse(request.postData()),{confirmed:true});
    frozenDataset={id:'dataset-v2',version:1,fingerprint:'frozen-hash',frozen:true};
    return route.fulfill({json:frozenDataset});
   }
   if(url.pathname==='/api/studio/v2/train'){
    assert.deepEqual(JSON.parse(request.postData()),{confirmed:true,dataset_id:'dataset-v2'});
    return route.fulfill({json:{request:{status:'queued'},runs:{},release:null}});
   }
   return route.fulfill({status:404,json:{error:'Unexpected '+url.pathname}});
  });
  await page.goto('http://studio.test/',{waitUntil:'networkidle'});
  assert.equal(await page.title(),'ActVision Training Studio');
  assert.equal(await page.locator('#physical').inputValue(),'UNKNOWN');
  assert.equal(await page.locator('#fit').inputValue(),'TARGET');
  assert.equal(await page.locator('#address script').count(),0);
  assert.match(await page.locator('#address').innerText(),/<script>/);
  assert.equal(await page.locator('#no-photos').isVisible(),false);
  await page.locator('[data-tab="facts"]').click();
  assert.match(await page.locator('#facts').innerText(),/1963/);
  assert.doesNotMatch(await page.locator('#facts').innerText(),/ClosePrice|PublicRemarks/);
  await page.locator('[data-tab="text"]').click();
  await page.getByText('Advanced: add or correct a description quote',{exact:true}).click();
  await page.locator('#signal').selectOption('clear_slate_or_blank_canvas');
  await page.locator('#snippet').fill('invented phrase');
  await page.locator('#add-signal').click();
  assert.match(await page.locator('#message').innerText(),/exactly match/);
  await page.locator('#snippet').fill('Bring your vision');
  await page.locator('#add-signal').click();
  await page.locator('[data-tab="photos"]').click();
  assert.match(await page.locator('#signals').innerText(),/Bring your vision/);
  await page.locator('#physical').selectOption('C3_WELL_MAINTAINED');
  await page.locator('#modernization').selectOption('ORIGINAL');
  await page.locator('#fit').selectOption('UNKNOWN');
  await page.getByText('Advanced review details',{exact:true}).click();
  await page.locator('#evidence-source').selectOption('metadata');
  await page.locator('#reason').fill('Maintained original; insufficient evidence for acquisition fit.');
  await page.locator('#save-next').click();
  await page.waitForFunction(()=>document.getElementById('address').textContent==='Next property');
  assert.equal(saved.length,1);
  assert.equal(saved[0].target_fit,'unsure');
  assert.equal(saved[0].physical_condition,'C3_WELL_MAINTAINED');
  assert.equal(saved[0].modernization_state,'ORIGINAL');
  assert.equal(saved[0].text_signals[0].snippet,'Bring your vision');
  assert.equal(saved[0].text_signals[0].start,0);
  assert.equal(saved[0].text_signals[0].end,17);
  assert.equal(saved[0].text_signals[0].probability,null);
  assert.equal(saved[0].evidence_source,'metadata');
  assert.deepEqual(saved[0].reason_tags,['Advanced: kitchen opportunity']);
  assert.deepEqual(saved[0].standout_image_ids,['first:photo']);
  assert.equal(saved[0].target_score,null);
  await page.locator('[data-tab="photos"]').click();
  assert.equal(await page.locator('#no-photos').isVisible(),true);
  assert.equal(await page.locator('#fit').inputValue(),'UNKNOWN');
  assert.equal(calls.filter(c=>c.method==='POST').length,1);
  await page.locator('[data-page="train"]').click();
  assert.equal(await page.locator('#preview').isDisabled(),false);
  assert.equal(await page.locator('#train-candidate').isDisabled(),true);
  await page.locator('[data-page="releases"]').click();
  await page.locator('#load-releases').click();
  await page.waitForFunction(()=>document.getElementById('release-content').textContent.includes('Explicit promotion only'));
  await page.locator('[data-page="review"]').click();
  await page.locator('#load-feedback').click();
  await page.waitForFunction(()=>document.getElementById('review-content').textContent.includes('No production feedback'));
  await page.locator('[data-page="operations"]').click();
  await page.waitForFunction(()=>document.getElementById('operation-content').textContent.includes('inference_enabled'));
  await page.locator('[data-page="advanced"]').click();
  assert.equal(await page.locator('a[href="/workbench"]').count(),1);
  await page.setViewportSize({width:390,height:844});
  await page.locator('[data-page="label"]').click();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
  failSave=true;
  await page.locator('#reason').fill('Unknown until evidence is verified.');
  await page.locator('#save-next').click();
  await page.waitForFunction(()=>document.getElementById('message').textContent.includes('Evidence changed'));
  assert.equal(await page.locator('#address').innerText(),'Next property');
  assert.equal(await page.locator('#reason').inputValue(),'Unknown until evidence is verified.');
  assert.equal(saved.length,1);
  await page.evaluate(()=>{state.dirty=false});
  caps.actor.role='operator';caps.training=true;
  await page.reload({waitUntil:'networkidle'});
  await page.locator('[data-page="train"]').click();
  await page.locator('#preview').click();
  await page.waitForFunction(()=>document.getElementById('training-result').textContent.includes('preview-hash'));
  assert.equal(await page.locator('#freeze').isDisabled(),false);
  assert.equal(calls.filter(c=>c.path==='/api/studio/v2/dataset/freeze').length,0);
  assert.equal(calls.filter(c=>c.path==='/api/studio/v2/train').length,0);
  await page.locator('#freeze').click();
  await page.waitForFunction(()=>document.getElementById('training-result').textContent.includes('Frozen dataset V1'));
  assert.equal(calls.filter(c=>c.path==='/api/studio/v2/dataset/freeze').length,1);
  await page.locator('#confirm-training').check();
  await page.locator('#train-candidate').click();
  assert.equal(calls.filter(c=>c.path==='/api/studio/v2/train').length,1);
  await page.locator('[data-page="label"]').click();
  await page.getByText('Generate a new AI draft',{exact:true}).click();
  await page.locator('#request-proposal').click();
  assert.match(await page.locator('#message').innerText(),/Confirm/);
  assert.equal(calls.filter(c=>c.path==='/api/studio/v2/label/propose').length,0);
  await page.locator('#confirm-proposal').check();
  await page.locator('#request-proposal').click();
  await page.waitForFunction(()=>document.getElementById('draft-status').textContent.includes('queued'));
  assert.equal(calls.filter(c=>c.path==='/api/studio/v2/label/propose').length,1);
  await page.locator('#refresh-proposal').click();
  await page.waitForFunction(()=>!document.getElementById('use-proposal').disabled);
  const previousSaved=saved.length;
  await page.locator('#use-proposal').click();
  assert.equal(await page.locator('#physical').inputValue(),'C4_AVERAGE_FUNCTIONAL');
  assert.equal(saved.length,previousSaved);
  assert.deepEqual(errors,[]);
  console.log('PASS: review safety, explicit AI drafts, v2 preview/freeze/train state, saved candidate surfaces, release safety, mobile layout.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1});
