const { test, expect } = require('@playwright/test');
const path = require('node:path');
test.use({ launchOptions: { executablePath: process.env.CLAWCROSS_TEST_CHROME || '/usr/bin/google-chrome' } });

test.beforeEach(async ({ page }) => {
  await page.route('**/studio', route => route.fulfill({contentType:'text/html', body:'<html><body></body></html>'}));
  await page.goto('/studio');
  await page.addScriptTag({path:path.resolve('src/frontend/static/js/attachment-utils.js')});
});

test('compresses a noisy image below the group limit and reduces dimensions', async ({page}) => {
  const result = await page.evaluate(async () => {
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 1600;
    const ctx = canvas.getContext('2d');
    const pixels = ctx.createImageData(1600,1600);
    for (let i=0; i<pixels.data.length; i+=4) {
      pixels.data[i] = (i*7)%255; pixels.data[i+1] = (i*11)%255;
      pixels.data[i+2] = (i*19)%255; pixels.data[i+3] = 255;
    }
    ctx.putImageData(pixels,0,0);
    const blob = await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));
    const data = await ClawCrossAttachments.prepareImage(new File([blob],'photo.png'), {maxBytes:65536,maxSide:1280});
    const image = new Image(); image.src = data; await image.decode();
    return {bytes:atob(data.split(',')[1]).length,side:Math.max(image.width,image.height)};
  });
  expect(result.bytes).toBeLessThanOrEqual(65536);
  expect(result.side).toBeLessThanOrEqual(1280);
});

test('bad HEIC data reports a readable error and releases the object URL', async ({page}) => {
  const result = await page.evaluate(async () => {
    let released=0; const original=URL.revokeObjectURL;
    URL.revokeObjectURL=url=>{released++;original.call(URL,url);};
    try {await ClawCrossAttachments.prepareImage(new File(['broken'],'photo.heic',{type:'image/heic'}));}
    catch(error){return {error:error.message,released};}
  });
  expect(result.error).toContain('HEIC');
  expect(result.released).toBe(1);
});

test('counts UTF-8 message bytes and rejects oversized attachments before sending', async ({page}) => {
  const result = await page.evaluate(() => {
    ClawCrossAttachments.assertGroupSize('你好',[{type:'image',data:'A'.repeat(88000)}]);
    try {ClawCrossAttachments.assertGroupSize('群聊'.repeat(90000));}
    catch(error){return error.message;}
  });
  expect(result).toContain('512 KiB');
});
