const {chromium}=require('@playwright/test');
const assert=require('node:assert/strict');
const fs=require('node:fs');

(async()=>{
 const browser=await chromium.launch({headless:true,timeout:20000});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:900}});
  const html=fs.readFileSync(__dirname+'/mls_validation_ui.html','utf8');
  const image=Buffer.from('/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////2wBDAf//////////////////////////////////////////////////////////////////////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABBQJ//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAwEBPwF//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAgEBPwF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQAGPwJ//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPyF//9oADAMBAAIAAwAAABAf/8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAwEBPxB//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAgEBPxB//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxB//9k=','base64');
  const records=[
   {id:'one',match_status:'candidate',source_address:'134 Espanas Gln',apn:'229-620-21-00',listing_id:'260002747SD',revision:0},
   {id:'two',match_status:'unresolved',source_address:'615 Elm Ave',apn:'573-200-06-00',listing_id:'80079634',revision:0},
  ];
  const details={
   one:{...records[0],prior_sale_date:'2026-02-18',prior_sale_amount:376500,last_sale_date:'2026-06-22',last_sale_amount:515000,listing_address:'134 Espanas Gln',close_date:'2026-02-27',close_price:376500,listing_remarks:'Original kitchen and baths.',listing_metadata:{StandardStatus:'Closed',YearBuilt:1963,BedroomsTotal:3,BathroomsTotalInteger:2,LivingArea:1400,PhotosCount:24},photos:[{id:'one:kitchen',room:'kitchen'},{id:'one:bath',room:'bathroom'}]},
   two:{...records[1],prior_sale_date:'2026-08-24',prior_sale_amount:610000,last_sale_date:'2026-08-27',last_sale_amount:635000,listing_address:'615 Elm Ave',close_date:null,close_price:null,listing_remarks:'Newly remodeled kitchen and bathrooms.',listing_metadata:{StandardStatus:'Active',YearBuilt:1951,PhotosCount:37,ListPrice:1399000},photos:[]},
  };
  const saved=[];
  await page.route('http://validation.test/**',async route=>{
   const url=new URL(route.request().url());
   if(url.pathname==='/')return route.fulfill({status:200,contentType:'text/html',body:html});
   if(url.pathname==='/api/studio/image')return route.fulfill({status:200,contentType:'image/jpeg',body:image});
   if(url.pathname==='/api/studio/mls-validation-queue'){
    const pending=records.filter(record=>!saved.some(row=>row.id===record.id));
    return route.fulfill({json:{items:pending,total:pending.length,offset:0,limit:20,token:'token',counts:{total:108,reviewed:saved.length,remaining:108-saved.length,candidate:3,unresolved:105}}});
   }
   if(url.pathname==='/api/studio/mls-validation'&&route.request().method()==='GET')return route.fulfill({json:details[url.searchParams.get('id')]});
   if(url.pathname==='/api/studio/mls-validation'&&route.request().method()==='POST'){saved.push(JSON.parse(route.request().postData()));return route.fulfill({json:{revision:1}});}
   return route.fulfill({status:404,json:{error:'Unexpected '+url.pathname}});
  });
  await page.goto('http://validation.test/',{waitUntil:'networkidle'});
  assert.equal(await page.locator('#progress').innerText(),'0 / 108 reviewed · 108 remaining');
  assert.equal(await page.locator('#address').innerText(),'134 Espanas Gln');
  assert.match(await page.locator('#listing-remarks').innerText(),/Original kitchen/);
  assert.match(await page.locator('#metadata-grid').innerText(),/Year built/);
  assert.equal(await page.locator('.photo img').count(),2);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
  await page.locator('#confirm').click();
  await page.locator('#name-input').fill('Reviewer');
  await page.locator('#name-form button.primary').click();
  await page.waitForFunction(()=>document.querySelector('#address').textContent==='615 Elm Ave');
  assert.equal(saved[0].decision,'confirmed');
  assert.equal(await page.locator('#progress').innerText(),'1 / 108 reviewed · 107 remaining');
  assert.equal(await page.locator('.placeholder').count(),2);
  assert.match(await page.locator('#listing-remarks').innerText(),/Newly remodeled/);
  assert.match(await page.locator('.placeholder').first().innerText(),/MLS reports 37 photos/);
  await page.keyboard.press('u');
  await page.waitForFunction(()=>document.querySelector('#empty').hidden===false);
  assert.equal(saved[1].decision,'unsure');
  assert.equal(await page.locator('#progress').innerText(),'2 / 108 reviewed · 106 remaining');
  console.log('PASS: focused 108-record queue, two-photo layout, immediate save/advance, progress, and keyboard actions.');
 }finally{await browser.close();}
})().then(()=>process.exit(0)).catch(error=>{console.error(error);process.exit(1)});
