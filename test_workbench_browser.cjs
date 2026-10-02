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
  const result={property_id:'p1',model:{version:'v0'},prediction:{score:.75,mode_used:'images_and_metadata',component_scores:{metadata:.6,images:.8,combined:.75},nearest_examples:[{property_id:'known-1'}]},condition:{physical_condition:'C4_AVERAGE_FUNCTIONAL',modernization_state:'ORIGINAL'},photos:[{image_id:'p1:1',room:'kitchen',influence_score:.9}]};
  let dataset=null,training=null;
  await page.route('http://workbench.test/**',async route=>{
   const url=new URL(route.request().url()),method=route.request().method();
   if(url.pathname==='/')return route.fulfill({status:200,contentType:'text/html',body:html});
   if(url.pathname==='/api/studio/image')return route.fulfill({status:200,contentType:'image/jpeg',body:image});
   if(url.pathname.endsWith('/properties'))return route.fulfill({json:{token:'token',total:1,items:[{id:'p1',address:'Example',city:'San Diego',year_built:1960,photo_count:1}]}});
   if(url.pathname.endsWith('/property'))return route.fulfill({json:{property:{id:'p1',address:'Example',city:'San Diego',metadata:{ListPrice:500000,BedroomsTotal:3,BathroomsTotalInteger:2,LivingArea:1400}},images:[{id:'p1:1',room:'kitchen'}]}});
   if(url.pathname.endsWith('/run')&&method==='POST'){requests.push(JSON.parse(route.request().postData()));return route.fulfill({json:{id:'a'.repeat(32),status:'queued'}});}
   if(url.pathname.endsWith('/run'))return route.fulfill({json:{request:{id:'a'.repeat(32),property_id:'p1',status:'completed'},result}});
   if(url.pathname.endsWith('/feedback')){feedback.push(JSON.parse(route.request().postData()));return route.fulfill({json:{revision:1}});}
   if(url.pathname.endsWith('/errors'))return route.fulfill({json:{items:feedback.length?[{run_id:'a',property_id:'p1',target_label:'NOT_TARGET',hard_negative:true,scores:result.prediction.component_scores,categories:['false_positive','hard_negative'],mode_used:'images_and_metadata'}]:[]}});
   if(url.pathname.endsWith('/dataset/preview'))return route.fulfill({json:{id:'preview',trainable:true,reasons:[],counts:{properties:55,targets:54,not_targets:1,hard_negatives:1,train:40,validation:5,test:10},changes:{added:['p1'],removed:[],changed:[]}}});
   if(url.pathname.endsWith('/dataset/freeze')){dataset={dataset_version:'Dataset V1',fingerprint:'f'.repeat(64),trainable:true,reasons:[],counts:{properties:55}};return route.fulfill({json:dataset});}
   if(url.pathname.endsWith('/train')){training={status:'queued'};return route.fulfill({json:training});}
   if(url.pathname.endsWith('/summary'))return route.fulfill({json:{token:'token',baseline:{version:'v0',metrics:{}},dataset,candidate:null,training}});
   return route.fulfill({status:404,json:{error:'Unexpected '+url.pathname}});
  });
  await page.goto('http://workbench.test/',{waitUntil:'networkidle'});
  await page.locator('#blind').check();
  await page.locator('#run').click();
  await page.waitForFunction(()=>!document.querySelector('#feedback').classList.contains('hidden'));
  assert.equal(await page.locator('#result').isHidden(),true);
  await page.locator('input[name=target][value=NOT_TARGET]').check();
  await page.locator('#hard-negative').check();
  await page.locator('#reviewer').click();
  await page.locator('#name-input').fill('Reviewer');
  await page.locator('#name-form button').click();
  await page.locator('#feedback button.primary').click();
  await page.waitForFunction(()=>!document.querySelector('#result').classList.contains('hidden'));
  assert.equal(feedback[0].target_label,'NOT_TARGET');
  assert.equal(feedback[0].hard_negative,true);
  await page.locator('[data-tab=dataset]').click();
  await page.locator('#preview-dataset').click();
  await page.locator('#freeze-dataset').click();
  assert.equal(dataset.dataset_version,'Dataset V1');
  await page.locator('[data-tab=train]').click();
  await page.waitForFunction(()=>!document.querySelector('#train-candidate').disabled);
  await page.locator('#train-candidate').click();
  assert.equal(training.status,'queued');
  console.log('PASS: blind run, hard-negative feedback, dataset freeze, and training queue from workbench UI.');
 }finally{await browser.close();}
})().then(()=>process.exit(0)).catch(error=>{console.error(error);process.exit(1)});
