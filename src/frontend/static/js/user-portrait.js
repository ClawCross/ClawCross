/* Free-form human portraits; attachments are decoded and compressed locally. */
window.UserPortrait = (() => {
  'use strict';
  const t=(zh,en)=>document.documentElement.lang.startsWith('zh')?zh:en;
  const node=(tag,cls,value)=>{const el=document.createElement(tag);if(cls)el.className=cls;if(value)el.textContent=value;return el;};
  function text(profile={}) {
    return profile.text!==undefined?profile.text:[['avatar','标记'],['bio','简介'],['location','地区']].filter(([key])=>profile[key]).map(([key,label])=>label+'：'+profile[key]).join('\n');
  }
  function picture(image,loadImage) {
    const img=node('img','up-picture');img.alt=image.name||t('用户画像图片','User portrait image');img.loading='lazy';
    const use=url=>{if(/^data:image\/(jpeg|png|webp);base64,/.test(url)&&img.isConnected)img.src=url;};
    queueMicrotask(()=>{if(image.url)use(image.url);else if(loadImage)Promise.resolve(loadImage(image)).then(data=>use(data.url||''),()=>{img.alt=t('图片暂时无法加载','Image unavailable');});});
    return img;
  }
  function render(container,profile={},options={}) {
    container.append(node('p','up-text',text(profile)||t('还没有填写用户画像','No user portrait yet')));
    if(profile.images?.length){const images=node('div','up-images');for(const image of profile.images)images.append(picture(image,options.loadImage));container.append(images);}
  }
  function edit(container,profile={},options={}) {
    let images=[...(profile.images||[])],pending=0;
    const root=node('div','up-editor'),label=node('label','hp-field');label.append(node('span','',t('用户画像','User portrait')));
    const textarea=node('textarea','gn-input up-editor-text');textarea.maxLength=16000;textarea.rows=7;textarea.value=text(profile);textarea.placeholder=t('自由描述自己，也可以在这里写简介、地区、偏好，或添加图片。','Describe yourself freely. Add an introduction, region, preferences or pictures.');label.append(textarea);
    const gallery=node('div','up-images'),toolbar=node('div','up-toolbar'),status=node('p','gn-note');status.setAttribute('role','status');
    const input=node('input');input.type='file';input.accept='image/*';input.multiple=true;input.hidden=true;input.setAttribute('aria-label',t('添加画像图片','Add portrait images'));
    const add=node('button','gn-btn gn-btn-secondary',t('添加图片','Add images'));add.type='button';add.addEventListener('click',()=>input.click());
    toolbar.append(add,node('span','gn-note',t('最多 4 张 · 自动压缩','Up to 4 images · compressed automatically')));root.append(label,gallery,toolbar,input,status);container.append(root);
    function draw(){
      gallery.replaceChildren();for(const [index,image] of images.entries()){
        const item=node('div','up-image-item'),remove=node('button','up-image-remove','×');remove.type='button';remove.setAttribute('aria-label',t('移除图片','Remove image')+' '+(index+1));
        remove.addEventListener('click',()=>{images.splice(index,1);draw();});item.append(picture(image,options.loadImage),remove);gallery.append(item);
      }
      add.disabled=images.length>=4||pending>0;
    }
    input.addEventListener('change',async()=>{
      const files=Array.from(input.files||[]);if(!files.length)return;
      if(images.length+files.length>4){status.textContent=t('最多添加 4 张图片','Up to 4 images');input.value='';return;}
      pending++;draw();status.textContent=t('正在处理图片…','Preparing images…');
      try{for(const file of files){const url=await ClawCrossAttachments.prepareImage(file,{maxBytes:256*1024});images.push({name:file.name,url});}status.textContent='';}
      catch(error){status.textContent=error.message;}
      finally{pending--;input.value='';draw();}
    });
    draw();return {value:()=>{if(pending)throw new Error(t('图片还在处理中，请稍候','Images are still being prepared'));return {text:textarea.value.trim(),images};},focus:()=>textarea.focus()};
  }
  return {text,render,edit};
})();
