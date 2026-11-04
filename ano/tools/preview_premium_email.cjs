// Render the local email design fixture; no network or real user browser.
const { chromium } = require('C:/Users/User/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs = require('node:fs');
const path = require('node:path');
(async () => {
  const browser = await chromium.launch({channel:'msedge', headless:true});
  const html = fs.readFileSync(path.resolve('previews/premium-email.html'),'utf8');
  for (const [label,width,height] of [['desktop',900,1100],['mobile',375,950]]) {
    const page = await browser.newPage({viewport:{width,height},deviceScaleFactor:1});
    await page.route('**/*',route=>route.abort());
    await page.setContent(html);
    await page.screenshot({path:`previews/premium-email-${label}.png`,fullPage:true});
    const size = await page.evaluate(()=>({width:innerWidth,content:document.documentElement.scrollWidth}));
    if(size.content>size.width)throw Error(`${label} horizontal overflow: ${JSON.stringify(size)}`);
    console.log(label,JSON.stringify(size));
    await page.close();
  }
  await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
