const {chromium}=require('@playwright/test');
const assert=require('node:assert/strict');
const fs=require('node:fs');

const GROUPS=[
 '11111111-1111-4111-8111-111111111111',
 '22222222-2222-4222-8222-222222222222',
 '33333333-3333-4333-8333-333333333333',
];

function event(group,role,index,saved,roles){
 const evidence=`${index}${index}${index}${index}${index}${index}${index}${index}-${index}${index}${index}${index}-4${index}${index}${index}-8${index}${index}${index}-${index}${index}${index}${index}${index}${index}${index}${index}${index}${index}${index}${index}`;
 const answer=saved.get(evidence);
 const identity=roles.get(evidence);
 return {
  role_id:`${index}0000000-0000-4000-8000-00000000000${index}`,
  event_role:role,
  decision:identity?.decision||answer?.role_decision||'candidate',
  audit:{[role==='acquisition'?'Prior_Date_Gap_Days':'Last_Date_Gap_Days']:4,reasons:[]},
  revision:answer?.revision||0,
  role_revision:identity?.revision||0,
  listing_key:`listing-${index}`,
  listing_id:`MLS-${index}`,
  standard_status:'Closed',
  raw_snapshot:{
   YearBuilt:1965+index,
   PropertySubType:'Single Family Residence',
   BedroomsTotal:3,
   BathroomsTotalInteger:2,
   LivingArea:1450,
   ListPrice:500000+index,
   PublicRemarks:role==='acquisition'?'Original kitchen and baths.':'Renovated kitchen and baths.',
   PrivateRemarks:'Verify condition from retained photos.',
  },
  evidence_id:evidence,
  metadata_snapshot:{role,policy:'acquisition-time-only'},
  photo_manifest:[{sha256:String(index).repeat(64),listing_key:`listing-${index}`}],
  evidence_sha256:String(index).repeat(64),
  close_date:role==='acquisition'?'2020-01-02':'2025-02-03',
  recording_date:role==='acquisition'?'2020-01-06':'2025-02-07',
  close_price:500000,
  signals:[],
  review:answer?{
   overall:{decision:answer.overall},
   image:answer.image,
   metadata:answer.metadata,
   notes:{text:answer.notes},
   condition:{decision:answer.condition},
   modernization:{decision:answer.modernization},
   photo_exclusions:{excluded:answer.excluded_photos},
  }:null,
  previous_review:null,
 };
}

(async()=>{
 const browser=await chromium.launch({headless:true,timeout:20000});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:900}});
  const html=fs.readFileSync(__dirname+'/paired_review.html','utf8');
  const image=Buffer.from('/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////2wBDAf//////////////////////////////////////////////////////////////////////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABBQJ//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAwEBPwF//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAgEBPwF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQAGPwJ//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPyF//9oADAMBAAIAAwAAABAf/8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAwEBPxB//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAgEBPxB//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxB//9k=','base64');
  const saved=new Map(),roles=new Map();
  const items=[
   {group_id:GROUPS[0],address:'First property',normalized_apn:null,prior_mls:' ',last_mls:'MLS-2',prior_listing_available:false,last_listing_available:true,protected_review:false,reviewed:false},
   {group_id:GROUPS[1],address:'Paired property',normalized_apn:'123-456',prior_mls:null,last_mls:null,prior_listing_available:true,last_listing_available:true,protected_review:true,reviewed:false},
   {group_id:GROUPS[2],address:'Next property',normalized_apn:'789-012',prior_mls:null,last_mls:null,prior_listing_available:false,last_listing_available:false,protected_review:false,reviewed:false},
  ];
  await page.route('http://review.test/**',async route=>{
   const request=route.request(),url=new URL(request.url());
   if(url.pathname==='/paired-review')return route.fulfill({status:200,contentType:'text/html',body:html});
   if(url.pathname==='/api/studio/paired-review/list')return route.fulfill({json:{items,total:items.length,token:'review-token'}});
   if(url.pathname==='/api/studio/paired-review/property'){
    const group=url.searchParams.get('id');
    const label=items.find(item=>item.group_id===group)?.address;
    return route.fulfill({json:{group_id:group,address:label,apn:'123-456',unit:null,warning:'Review both events.',events:[event(group,'acquisition',1,saved,roles),event(group,'resale',2,saved,roles)]}});
   }
   if(url.pathname==='/api/studio/paired-review/image')return route.fulfill({status:200,contentType:'image/jpeg',body:image});
   if(url.pathname==='/api/studio/paired-review/save'){
    assert.equal(request.headers()['x-review-token'],'review-token');
    const answer=JSON.parse(request.postData());
    answer.revision=answer.expected_revision+1;
    saved.set(answer.evidence_id,answer);
    return route.fulfill({json:{saved:true,revision:answer.revision}});
   }
    if(url.pathname==='/api/studio/paired-review/role'){
     assert.equal(request.headers()['x-review-token'],'review-token');
     const answer=JSON.parse(request.postData()),revision=answer.expected_revision+1;
     roles.set(answer.evidence_id,{decision:answer.role_decision,revision});
     return route.fulfill({json:{saved:true,decision:answer.role_decision,revision}});
    }
   return route.fulfill({status:404,json:{error:`Unexpected ${url.pathname}`}});
  });

  await page.goto('http://review.test/paired-review',{waitUntil:'networkidle'});
  await page.locator('#panels img').nth(1).scrollIntoViewIfNeeded();
  await page.waitForFunction(()=>[...document.querySelectorAll('#panels img')].length===2&&[...document.querySelectorAll('#panels img')].every(img=>img.naturalWidth>0));
  assert.match(await page.locator('#panels').innerText(),/Year built: 1966/);
  assert.match(await page.locator('#panels').innerText(),/Original kitchen and baths/);

  await page.locator('#filter').selectOption('pairs');
  assert.equal(await page.locator('#count').innerText(),'1 of 3 properties');
  assert.equal(await page.locator('#list button').count(),1);
  assert.match(await page.locator('#list button').innerText(),/Paired property · Protected/);

  await page.locator('#filter').selectOption('all');
  await page.locator('#list button').filter({hasText:'First property'}).click();
  const panels=page.locator('#panels .card');
  await panels.nth(0).getByRole('button',{name:/Correct listing/}).click();
  await panels.nth(1).getByRole('button',{name:/Wrong era/}).click();
  await page.waitForFunction(()=>document.querySelector('#address').textContent==='Paired property');
  assert.equal(roles.size,2);

  await page.locator('#list button').filter({hasText:'First property'}).click();
  await page.waitForFunction(()=>document.querySelector('#address').textContent==='First property');
  assert.equal(await panels.nth(0).getByRole('button',{name:/Correct listing/}).evaluate(button=>button.classList.contains('chosen')),true);
  assert.equal(await panels.nth(1).getByRole('button',{name:/Wrong era/}).evaluate(button=>button.classList.contains('chosen')),true);
  console.log('PASS: spreadsheet queue, paired photos and metadata, one-click identity, reload, and auto-next.');
 }finally{
  await browser.close();
 }
})().then(()=>process.exit(0)).catch(error=>{console.error(error);process.exit(1)});
