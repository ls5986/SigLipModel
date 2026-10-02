const {chromium}=require('@playwright/test');
const assert=require('node:assert/strict');
const fs=require('node:fs');

(async()=>{
 const browser=await chromium.launch({headless:true,timeout:20000});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:900}});
  const html=fs.readFileSync(__dirname+'/workbench_ui.html','utf8');
  const image=Buffer.from('/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////2wBDAf//////////////////////////////////////////////////////////////////////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABBQJ//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAwEBPwF//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAgEBPwF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQAGPwJ//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPyF//9oADAMBAAIAAwAAABAf/8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAwEBPxB//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAgEBPxB//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxB//9k=','base64');
  const feedback=[],requests=[];
  const result={property_id:'p1',model:{version:'v0'},prediction:{score:.75,mode_used:'images_and_metadata',component_scores:{metadata:.6,images:.8,combined:.75},nearest_examples:[{property_id:'known-1'}],metadata_reason:{kind:'nearest_known_target_similarity',reference_property_id:'known-1',score:.6,comparisons:[{field:'year_built',subject_value:1960,reference_value:1958,field_similarity:.82},{field:'bedrooms',subject_value:3,reference_value:3,field_similarity:1},{field:'property_type',subject_value:'single family',reference_value:'single family',field_similarity:1}]},metadata_input:{YearBuilt:1960,ListPrice:500000,BedroomsTotal:3},public_remarks:'Original kitchen and deferred maintenance.'},condition:{physical_condition:'C4_AVERAGE_FUNCTIONAL',modernization_state:'ORIGINAL'},photos:[{image_id:'p1:1',room:'kitchen',single_photo_target_score:.9}]};
  let dataset=null,training=null,fixedBatch=null,currentRunProperty='p1';
  const challenge={id:'mls:mls-1',listing_key:'mls-1',address:'Unseen Example',city:'San Diego',photo_count:1,metadata:{ListingKey:'mls-1',ListingId:'MLS-1',YearBuilt:1955,ListPrice:650000},images:[{media_key:'photo-1',room:'kitchen',sha256:'b'.repeat(64),bytes:100}],opportunity:{score:82}};
  await page.route('http://workbench.test/**',async route=>{
   const url=new URL(route.request().url()),method=route.request().method();
   if(url.pathname==='/')return route.fulfill({status:200,contentType:'text/html',body:html});
   if(url.pathname==='/api/studio/workbench/challenge-image')return route.fulfill({status:200,contentType:'image/jpeg',body:image});
   if(url.pathname==='/api/studio/image')return route.fulfill({status:200,contentType:'image/jpeg',body:image});
   if(url.pathname.endsWith('/challenge/properties'))return route.fulfill({json:{token:'token',items:[challenge],mode:'random'}});
   if(url.pathname.endsWith('/challenge/property')){await new Promise(resolve=>setTimeout(resolve,150));return route.fulfill({json:challenge});}
   if(url.pathname.endsWith('/challenge/fixed-property'))return route.fulfill({json:{...challenge,address:'Fixed Example',id:'challenge:'+fixedBatch.id+':mls-1',batch_id:fixedBatch.id,fixed_challenge:true}});
   if(url.pathname.endsWith('/challenge/batches'))return route.fulfill({json:{items:fixedBatch?[fixedBatch]:[]}});
   if(url.pathname.endsWith('/challenge/batch')&&method==='POST'){fixedBatch={id:'c'.repeat(32),name:'Browser random five',fingerprint:'d'.repeat(64),counts:{properties:1,photos:1,metadata_only:0},items:[{...challenge,address:'Fixed Example',id:'challenge:'+'c'.repeat(32)+':mls-1',batch_id:'c'.repeat(32),fixed_challenge:true}]};return route.fulfill({json:fixedBatch});}
   if(url.pathname.endsWith('/challenge/batch'))return route.fulfill({json:fixedBatch});
   if(url.pathname.endsWith('/properties'))return route.fulfill({json:{token:'token',total:2,items:[{id:'p1',address:'Example',city:'San Diego',year_built:1960,photo_count:1},{id:'p2',address:'Example 2',city:'La Mesa',year_built:1970,photo_count:1}]}});
   if(url.pathname.endsWith('/property')){const id=url.searchParams.get('id')||'p1';return route.fulfill({json:{property:{id,address:id==='p1'?'Example':'Example 2',city:id==='p1'?'San Diego':'La Mesa',metadata:{ListPrice:id==='p1'?500000:600000,BedroomsTotal:3,BathroomsTotalInteger:2,LivingArea:1400},mls_remarks:id==='p1'?'Original kitchen and deferred maintenance.':'Second property remarks.'},images:[{id:id+':1',room:'kitchen'}]}});}
   if(url.pathname.endsWith('/run')&&method==='POST'){const request=JSON.parse(route.request().postData());requests.push(request);currentRunProperty=request.property_id;return route.fulfill({json:{id:'a'.repeat(32),status:'queued'}});}
   if(url.pathname.endsWith('/run'))return route.fulfill({json:{request:{id:'a'.repeat(32),property_id:currentRunProperty,status:'completed'},result:{...result,property_id:currentRunProperty}}});
   if(url.pathname.endsWith('/feedback')){feedback.push(JSON.parse(route.request().postData()));return route.fulfill({json:{revision:1}});}
   if(url.pathname.endsWith('/errors'))return route.fulfill({json:{items:feedback.length?[{run_id:'a',property_id:'p1',target_label:'NOT_TARGET',hard_negative:true,scores:result.prediction.component_scores,categories:['false_positive','hard_negative'],mode_used:'images_and_metadata'}]:[]}});
   if(url.pathname.endsWith('/dataset/preview'))return route.fulfill({json:{id:'preview',trainable:true,reasons:[],counts:{properties:55,targets:54,not_targets:1,hard_negatives:1,train:40,validation:5,test:10},changes:{added:['p1'],removed:[],changed:[]},change_details:[{property_id:'p1',change:'added',before:null,after:{property_id:'p1'}}],protected_groups_unchanged:true,protected_test_before_sha256:'e'.repeat(64),protected_test_after_sha256:'e'.repeat(64)}});
   if(url.pathname.endsWith('/dataset/freeze')){dataset={dataset_version:'Dataset V1',fingerprint:'f'.repeat(64),trainable:true,reasons:[],counts:{properties:55}};return route.fulfill({json:dataset});}
   if(url.pathname.endsWith('/train')){training={status:'queued'};return route.fulfill({json:training});}
   if(url.pathname.endsWith('/candidates'))return route.fulfill({json:{items:[{id:'v0-positive-similarity',version:'V0',kind:'positive_similarity_baseline'},{id:'v1',version:'V1',kind:'target_classifier_v1'}]}});
   if(url.pathname.endsWith('/compare'))return route.fulfill({json:{notice:'V0 is not directly comparable.',comparable:false,metrics:[{metric:'balanced_accuracy',left:null,right:.7,delta:null}]}});
   if(url.pathname.endsWith('/summary'))return route.fulfill({json:{token:'token',worker:{online:true,actionable:true,status:'ready',age_seconds:2,detail:{model_version:'v0'}},baseline:{version:'v0',metrics:{}},dataset,candidate:null,training}});
   return route.fulfill({status:404,json:{error:'Unexpected '+url.pathname}});
  });
  await page.goto('http://workbench.test/',{waitUntil:'networkidle'});
  await page.locator('#blind').check();
  await page.locator('#run').click();
  await page.waitForFunction(()=>!document.querySelector('#feedback').classList.contains('hidden'));
  assert.equal(await page.locator('#result').isHidden(),true);
  assert.equal(await page.locator('#influential .photo').count(),0);
  assert.equal(await page.locator('#metadata-score').innerText(),'—');
  assert.equal(await page.locator('#vision-score').innerText(),'—');
  assert.equal(await page.locator('#fusion-score').innerText(),'—');
  assert.equal(await page.locator('#explanation').innerText(),'');
  assert.equal(await page.locator('#feedback-photos .photo').count(),1);
  await page.locator('input[name=target][value=NOT_TARGET]').check();
  await page.locator('#hard-negative').check();
  await page.locator('#reviewer').click();
  await page.locator('#name-input').fill('Reviewer');
  await page.locator('#name-form button').click();
  await page.locator('#feedback button.primary').click();
  await page.waitForFunction(()=>!document.querySelector('#result').classList.contains('hidden'));
  await page.waitForFunction(()=>document.querySelector('#address').textContent==='Example 2');
  assert.equal(feedback[0].target_label,'NOT_TARGET');
  assert.equal(feedback[0].hard_negative,true);
  assert.match(await page.locator('#result-label').innerText(),/Previous reviewed result · Example/i);
  assert.match(await page.locator('#metadata-method').innerText(),/not a probability.*3 shared field similarities.*known-1/i);
  assert.deepEqual(await page.locator('#metadata-input .fact strong').allInnerTexts(),['1960','$500,000','3']);
  assert.equal(await page.locator('#metadata-remarks').innerText(),'Original kitchen and deferred maintenance.');
  assert.equal(await page.locator('#feedback').isHidden(),true);
  const asideBox=await page.locator('#test-panel aside').boundingBox();
  const mainBox=await page.locator('#test-panel main').boundingBox();
  const sourceHeight=await page.locator('#property-source').locator('..').evaluate(node=>node.getBoundingClientRect().height);
  assert.ok(asideBox.height<mainBox.height);
  assert.ok(sourceHeight<120);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
  await page.locator('[data-tab=dataset]').click();
  await page.locator('#preview-dataset').click();
  await page.locator('#freeze-dataset').click();
  assert.equal(dataset.dataset_version,'Dataset V1');
  await page.locator('[data-tab=train]').click();
  await page.waitForFunction(()=>!document.querySelector('#train-candidate').disabled);
  await page.locator('#train-candidate').click();
  assert.equal(training.status,'queued');
  await page.locator('[data-tab=compare]').click();
  await page.waitForFunction(()=>document.querySelectorAll('#compare-right option').length===2);
  await page.locator('#compare-models').click();
  await page.waitForFunction(()=>document.querySelectorAll('#compare-table tbody tr').length===1);
  assert.match(await page.locator('#compare-notice').innerText(),/not directly comparable/);
  await page.locator('[data-tab=test]').click();
  await page.locator('#property-source').selectOption('random');
  await page.waitForFunction(()=>document.querySelector('#address').textContent==='Unseen Example');
  assert.equal(await page.locator('#run').isDisabled(),true);
  await page.locator('#batch-name').fill('Browser random five');
  await page.locator('#freeze-challenge').click();
  await page.waitForFunction(()=>document.querySelector('#property-source').value.startsWith('batch:'));
  await page.waitForFunction(()=>!document.querySelector('#run').disabled);
  assert.equal(await page.locator('#address').innerText(),'Fixed Example');
  const fixedValue=await page.locator('#property-source').inputValue();
  await page.locator('#property-source').selectOption('random');
  await page.locator('#property-source').selectOption(fixedValue);
  await page.waitForTimeout(300);
  assert.equal(await page.locator('#address').innerText(),'Fixed Example');
  assert.equal(await page.locator('#property-list button').count(),1);
  await page.locator('#run').click();
  await page.waitForFunction(()=>document.querySelector('#run-status').textContent==='Completed');
  assert.match(requests.at(-1).property_id,/^challenge:/);
  console.log('PASS: blind review, dataset training, and fixed MLS challenge run from workbench UI.');
 }finally{await browser.close();}
})().then(()=>process.exit(0)).catch(error=>{console.error(error);process.exit(1)});
