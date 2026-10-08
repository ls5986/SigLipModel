const {chromium} = require('/opt/codex/runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs = require('fs');
const assert = require('assert');
(async()=>{
 const browser = await chromium.launch({headless:true});
 const page = await browser.newPage({viewport:{width:390,height:844}});
 const errors=[]; page.on('pageerror', e=>errors.push(e.message));
 const photo='a'.repeat(64);
 const result={decision:'TARGET',strength:77};
 const event={evidence_id:'e1',listing_key:'123',event_role:'acquisition',revision:0,
  photo_manifest:[{sha256:photo}],photo_tags:[{sha256:photo,exclusion:'none'}],
  metadata_snapshot:{structured:{YearBuilt:1974,ListPrice:500000,OriginalListPrice:550000},public_remarks:'Original kitchen <script>alert(1)</script>'},
  prediction:{status:'completed',image:result,metadata:result,overall:result,photos:[{sha256:photo,...result}]}};
 let corrections=[];
 await page.route('https://studio.test/**', async route=>{
  const url=new URL(route.request().url());
  if(url.pathname==='/model-test') return route.fulfill({contentType:'text/html',body:fs.readFileSync('paired_condition.html','utf8')});
  if(url.pathname.endsWith('/image'))return route.fulfill({contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a0p8AAAAASUVORK5CYII=','base64')});
  let body={};
  if(url.pathname.endsWith('/status'))body={token:'csrf',request:{status:'completed'},dataset:{id:'d1',name:'frozen',version:1},candidate:{candidate_id:'c1',training_groups:20,created_at:new Date().toISOString(),components:{vision:true,text:true,structured:true,fusion:true}}};
  if(url.pathname.endsWith('/list'))body={items:[{group_id:'g1',address:'123 Test St',prior_mls:'123',last_mls:'456'},{group_id:'g2',address:'456 Next St'}]};
  if(url.pathname.endsWith('/property'))body={group_id:url.searchParams.get('id'),address:url.searchParams.get('id')==='g1'?'123 Test St':'456 Next St',events:[event]};
  if(url.pathname.endsWith('/correct')){corrections.push(route.request().postDataJSON());body={saved:true,revision:1};}
  return route.fulfill({contentType:'application/json',body:JSON.stringify(body)});
 });
 await page.goto('https://studio.test/model-test');
 await page.getByRole('heading',{name:'123 Test St'}).waitFor();
 assert(await page.locator('.hero').count()===1);
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.locator('#imageDecision').selectOption('NOT_TARGET');
 await page.locator('#next').click();
 assert((await page.locator('#message').innerText()).includes('Save your correction'));
 await page.locator('#exclude').click();
 assert(await page.locator('.hero').count()===0);
 await page.getByText('All stored photos / exclusions').click();
 await page.getByRole('button',{name:'Remove my exclusion'}).click();
 assert(await page.locator('.hero').count()===1);
 await page.locator('#saveNext').click();
 await page.getByRole('heading',{name:'456 Next St'}).waitFor();
 assert(corrections.length===1 && corrections[0].image.decision==='NOT_TARGET');
 assert(Object.keys(corrections[0].excluded_photos).length===0);
 assert(errors.length===0,errors.join('\n'));
 await page.screenshot({path:'/tmp/paired-condition-mobile.png',fullPage:true});
 await page.setViewportSize({width:1440,height:1000});
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 console.log('Mobile/desktop layout, photo exclusions, save-next, unsaved-edit protection and JS errors: passed');
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
