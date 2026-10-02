const {chromium}=require('@playwright/test');
const assert=require('node:assert/strict');
const fs=require('node:fs');

(async()=>{
 const browser=await chromium.launch({headless:true,timeout:20000});
 try{
  const page=await browser.newPage({viewport:{width:1536,height:768}});
  const html=fs.readFileSync(__dirname+'/review_ui.html','utf8');
  const saved=[];
  const image=Buffer.from('/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////2wBDAf//////////////////////////////////////////////////////////////////////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABBQJ//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAwEBPwF//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAgEBPwF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQAGPwJ//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPyF//9oADAMBAAIAAwAAABAf/8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAwEBPxB//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAgEBPxB//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxB//9k=','base64');
  const counts={all:166,verified:19,reviewed:1,photo_match:147,opportunity:19,complete:0,todo:166,tagged:156,unscored:165,ready:0};
  const item={id:'listing-1',address:'1417 S 58th Street',city:'San Diego',image_count:1,hero_image_id:'listing-1:photo',status:'unscored',needs_photo_match:true,review_complete:false,autolabel_status:'completed',tagged_photo_count:1};
  const detail={property:{id:item.id,address:item.address,city:item.city,year_built:1963,property_type:'Single Family Residence',mls_remarks:'Original finishes. Property needs cosmetic updating.',metadata:{ListingId:'PTP2601403',CloseDate:'2026-03-10',YearBuilt:1963,PropertySubType:'Single Family Residence',BedroomsTotal:4,BathroomsTotalInteger:2,LivingArea:1730,ListPrice:560000,DaysOnMarket:0},review:{revision:0}},images:[{id:item.hero_image_id,sequence:1,room:'exterior',condition_draft:'unknown',effective:{room:'exterior',context:'subject',features:{},room_source:'SigLIP suggestion',context_source:'SigLIP suggestion',label_status:'unreviewed'},review:{revision:0},selection:{included:true},sha256:'a'.repeat(64)}],coverage:{kitchen:'unknown',bathroom:'unknown',living:'unknown'},assessment:null,historical_source:{source:{'Prior Sale Date':'2026-02-26','Prior Sale Amount':490000,'Last Sale Date':'2026-07-22','Last Sale Amount':880000},source_rows:[1],evidence_hash:'evidence',review:null,blocked:false,timing_verified:false,photo_coverage:'unknown',acquisition_status:'prior_acquisition_candidate',sale_policy:{target_sale_date:'2026-02-26',selected_close_date:'2026-03-10'}},capabilities:{review:true,assessment:false,autolabel:false,storage:'supabase'}};
  await page.route('http://review.test/**',async route=>{
   const url=new URL(route.request().url());
   if(url.pathname==='/')return route.fulfill({status:200,contentType:'text/html',body:html});
   if(url.pathname.includes('/image')||url.pathname.includes('/thumbnail'))return route.fulfill({status:200,contentType:'image/jpeg',body:image});
   if(url.pathname==='/api/studio/review-queue')return route.fulfill({json:{items:[item],counts,total:1,offset:0,limit:20,token:'token',capabilities:{storage:'supabase'}}});
   if(url.pathname==='/api/studio/property')return route.fulfill({json:detail});
   if(url.pathname==='/api/studio/complete-review'){
    saved.push(JSON.parse(route.request().postData()));
    return route.fulfill({json:{complete:true}});
   }
   return route.fulfill({status:404,json:{error:'Unexpected '+url.pathname}});
  });
  await page.goto('http://review.test/',{waitUntil:'networkidle'});
  await page.waitForFunction(()=>document.querySelector('#hero').naturalWidth>0);
  assert.match(await page.locator('#progress').innerText(),/166 incomplete of 166 matched listings/);
  assert.deepEqual(await page.locator('#queue-summary strong').allInnerTexts(),['166','19','1','166']);
  assert.equal(await page.locator('#dataset-scope').isHidden(),true);
  assert.equal(await page.locator('#queue-filter').isHidden(),true);
  assert.equal(await page.locator('#cloud-queue-controls').isVisible(),true);
  assert.doesNotMatch(await page.locator('body').innerText(),/250-property/i);
  assert.match(await page.locator('#check-evidence-copy').innerText(),/Still needs/);
  assert.equal(await page.locator('#agree').innerText(),'Complete review');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
  await page.locator('#agree').click();
  await page.locator('#name-input').fill('Nontechnical reviewer');
  await page.locator('#name-form button.primary').click();
  await page.locator('#complete-dialog').waitFor({state:'visible'});
  assert.equal(await page.locator('#complete-dialog').evaluate(node=>node.scrollWidth>node.clientWidth+1),false);
  assert.match(await page.locator('#complete-remarks').innerText(),/Original finishes/);
  await page.locator('input[name="complete-era"][value="correct_era"]').check();
  await page.locator('#complete-coverage').selectOption('no_interior');
  await page.locator('#complete-evidence').selectOption('metadata');
  await page.locator('#complete-condition').selectOption('maintained_original');
  await page.locator('#complete-score').selectOption('4');
  await page.locator('#complete-note').fill('Exterior only; remarks and age support original maintained condition.');
  await page.locator('#complete-form button.primary').click();
  await page.waitForFunction(()=>!document.querySelector('#complete-dialog').open);
  assert.equal(saved.length,1);
  assert.equal(saved[0].decision,'correct_era');
  assert.equal(saved[0].photo_coverage,'no_interior');
  assert.equal(saved[0].property_review.evidence_source,'metadata');
  assert.equal(saved[0].property_review.condition_label,'maintained_original');
  assert.equal(saved[0].property_review.target_score,4);
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
  console.log('PASS: clear counts, guided listing/photo/metadata review, atomic payload, desktop and mobile fit.');
 }finally{await browser.close();}
})().then(()=>process.exit(0)).catch(error=>{console.error(error);process.exit(1)});
