#!/usr/bin/env node
'use strict';
// Original direct-CLI implementation. No MCP server or desktop UI automation.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { parseArgs } = require('node:util');
const HOST = 'https://photopea-skill.local/';
const ORIGIN = 'https://www.photopea.com';

const HTML = `<!doctype html><meta charset="utf-8"><title>Photopea API bridge</title>
<style>body{margin:0}iframe{width:100vw;height:100vh;border:0}</style>
<script>
window.pp={ready:false,pending:null};
window.addEventListener('message',function(e){
  const frame=document.getElementById('pp');
  if(!frame||e.origin!=='https://www.photopea.com'||e.source!==frame.contentWindow)return;
  if(e.data==='done')window.pp.ready=true;
  const p=window.pp.pending;
  if(!p)return;
  if(typeof e.data==='string'){
    p.strings.push(e.data);
    if(e.data===p.marker)p.markerSeen=true;
    if(e.data==='done'&&p.markerSeen)p.finished=true;
  }else if(e.data instanceof ArrayBuffer){
    const bytes=new Uint8Array(e.data);let s='';
    for(let i=0;i<bytes.length;i+=32768)s+=String.fromCharCode(...bytes.subarray(i,i+32768));
    p.buffers.push(btoa(s));
  }
  if(p.finished&&(!p.binary||p.buffers.length>0)){
    clearTimeout(p.timer);window.pp.pending=null;
    p.resolve({strings:p.strings,buffers:p.buffers});
  }
});
window.ppCall=function(payload,marker,binary,timeout){
  if(window.pp.pending)return Promise.reject(new Error('Photopea calls must be serialized'));
  return new Promise(function(resolve,reject){
    const p={resolve,reject,marker,binary,markerSeen:false,finished:false,strings:[],buffers:[]};
    p.timer=setTimeout(function(){
      window.pp.pending=null;
      reject(new Error('Photopea timeout; replies='+JSON.stringify(p.strings).slice(0,5000)));
    },timeout);
    window.pp.pending=p;
    document.getElementById('pp').contentWindow.postMessage(payload,'https://www.photopea.com');
  });
};
</script>
<iframe id="pp" src="https://www.photopea.com/#%7B%22environment%22%3A%7B%22lang%22%3A%22en%22%7D%7D"></iframe>`;

function resolvePlaywright(modulePath) {
  if (modulePath) return require(path.resolve(modulePath));
  for (const name of ['playwright', 'playwright-core']) {
    try { return require(name); } catch (e) { if (e.code !== 'MODULE_NOT_FOUND') throw e; }
  }
  throw new Error('Playwright not found. Pass --playwright-module with the bundled package directory, or use NODE_PATH.');
}

function safeName(name) {
  if (typeof name !== 'string' || !name || name === '.' || name === '..' ||
      /[<>:"/\\|?*\u0000-\u001f]/.test(name) || /[. ]$/.test(name) ||
      /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(name)) {
    throw new Error('Job name must be a portable basename without extension: '+JSON.stringify(name));
  }
  return name;
}

function formatInfo(format = 'png') {
  if (!/^(png|jpg|webp|psd)(?::(?:0(?:\.\d+)?|1(?:\.0+)?|true))?$/.test(format)) {
    throw new Error('Supported formats: png, jpg[:quality], webp[:quality], psd[:true]');
  }
  const ext = format.split(':')[0];
  const suffix = format.split(':')[1];
  if ((ext === 'png' && suffix) || (ext === 'psd' && suffix && suffix !== 'true') ||
      (['jpg','webp'].includes(ext) && suffix === 'true')) throw new Error('Invalid format option: '+format);
  return { format, ext };
}

function imageHeader(bytes, ext) {
  if (ext === 'png' && bytes.length >= 24 && bytes.subarray(0,8).toString('hex') === '89504e470d0a1a0a') {
    return [bytes.readUInt32BE(16),bytes.readUInt32BE(20)];
  }
  if (ext === 'jpg' && bytes.length > 4 && bytes[0] === 255 && bytes[1] === 216) return null;
  if (ext === 'webp' && bytes.length > 12 && bytes.toString('ascii',0,4) === 'RIFF' && bytes.toString('ascii',8,12) === 'WEBP') return null;
  if (ext === 'psd' && bytes.length >= 26 && bytes.toString('ascii',0,4) === '8BPS') {
    return [bytes.readUInt32BE(18),bytes.readUInt32BE(14)];
  }
  throw new Error('Photopea returned an invalid '+ext+' signature');
}

class PhotopeaSession {
  constructor(options = {}) { this.options = options; this.timeout = options.timeout || 45000; this.notes = []; }
  async start(sourceBytes) {
    const { chromium } = resolvePlaywright(this.options.playwrightModule);
    const choices = this.options.executablePath ? [{executablePath:this.options.executablePath}] :
      this.options.channel ? [{channel:this.options.channel}] :
      [{}, ...(process.platform === 'win32' ? [{channel:'msedge'},{channel:'chrome'}] : [])];
    let launchError;
    for (const choice of choices) {
      try { this.browser = await chromium.launch({...choice,headless:!this.options.headed}); break; }
      catch (e) {
        launchError=e;
        if (!/Executable doesn't exist|distribution.*not found|executable.*not found/i.test(e.message)) throw e;
      }
    }
    if (!this.browser) throw launchError;
    this.page = await this.browser.newPage({viewport:{width:1400,height:950}});
    this.page.on('dialog',async dialog=>{this.notes.push(dialog.message().slice(0,1000));await dialog.dismiss();});
    this.page.on('requestfailed',request=>{
      if (request.url().startsWith(ORIGIN+'/')) this.notes.push(request.url().split('?')[0]+' '+request.failure()?.errorText);
    });
    await this.page.route(HOST+'**',async route=>{
      const url = new URL(route.request().url());
      if (url.pathname === '/') return route.fulfill({contentType:'text/html; charset=utf-8',body:HTML});
      if (url.pathname === '/source') return route.fulfill({contentType:'application/octet-stream',body:sourceBytes});
      return route.fulfill({status:404,body:'Not found'});
    });
    await this.page.goto(HOST,{waitUntil:'domcontentloaded',timeout:this.timeout});
    await this.page.waitForFunction(()=>window.pp.ready,null,{timeout:this.timeout});
  }
  async run(script, binary = false) {
    const marker = '__PP_END_'+crypto.randomUUID();
    const payload = script+'\napp.echoToOE('+JSON.stringify(marker)+');';
    const reply = await this.page.evaluate(({payload,marker,binary,timeout})=>window.ppCall(payload,marker,binary,timeout),
      {payload,marker,binary,timeout:this.timeout});
    return {strings:reply.strings.filter(s=>s !== marker && s !== 'done'),buffers:reply.buffers.map(b=>Buffer.from(b,'base64'))};
  }
  async load() {
    await this.page.evaluate(async timeout=>{
      const response=await fetch('/source');
      if (!response.ok) throw new Error('Local source read failed');
      return window.ppCall(await response.arrayBuffer(),'done',false,timeout);
    },this.timeout);
    const reply=await this.run('app.preferences.rulerUnits=Units.PIXELS; app.echoToOE("__INFO__"+JSON.stringify({width:app.activeDocument.width,height:app.activeDocument.height,documents:app.documents.length}));');
    const info=reply.strings.find(s=>s.startsWith('__INFO__'));
    if (!info) throw new Error('Source load produced no document information');
    const result=JSON.parse(info.slice(8));
    if (!Number.isFinite(result.width)||!Number.isFinite(result.height)||result.width<=0||result.height<=0) {
      throw new Error('Invalid source dimensions: '+info);
    }
    return result;
  }
  async diagnostic(outputDir, error) {
    fs.writeFileSync(path.join(outputDir,'photopea-diagnostic.json'),JSON.stringify({error:error.message,notes:this.notes.slice(-20)},null,2));
    if (this.page) await this.page.screenshot({path:path.join(outputDir,'photopea-diagnostic.png')}).catch(()=>{});
  }
  async close() { if (this.browser) await this.browser.close(); }
}

async function exportPlan(options) {
  const source=path.resolve(options.input);
  const outputDir=path.resolve(options.out);
  const sourceBytes=fs.readFileSync(source);
  const plan=JSON.parse(fs.readFileSync(options.plan,'utf8').replace(/^\uFEFF/,''));
  if (!Array.isArray(plan.jobs)||!plan.jobs.length) throw new Error('Plan requires a non-empty jobs array');
  const names=new Set();
  const jobs=plan.jobs.map(job=>{
    const name=safeName(job.name);
    const format=formatInfo(job.format||plan.format||'png');
    const file=name+'.'+format.ext;
    if (names.has(file.toLowerCase())) throw new Error('Duplicate output filename: '+file);
    names.add(file.toLowerCase());
    if (job.script!==undefined&&typeof job.script!=='string') throw new Error('job.script must be a string');
    if (job.box && (!Array.isArray(job.box)||job.box.length!==4||!job.box.every(Number.isInteger)||job.box[2]<=job.box[0]||job.box[3]<=job.box[1])) {
      throw new Error('Invalid pixel box for '+name);
    }
    if (job.expected_size && (!Array.isArray(job.expected_size)||job.expected_size.length!==2||!job.expected_size.every(n=>Number.isInteger(n)&&n>0))) {
      throw new Error('Invalid expected_size for '+name);
    }
    if (!options.overwrite&&fs.existsSync(path.join(outputDir,file))) throw new Error('Output exists: '+file+' (use a fresh directory or --overwrite)');
    return {...job,name,file,...format};
  });
  if (plan.script!==undefined&&typeof plan.script!=='string') throw new Error('plan.script must be a string');
  if (!options.overwrite&&fs.existsSync(path.join(outputDir,'photopea-manifest.json'))) throw new Error('Manifest exists; use a fresh output directory or --overwrite');
  fs.mkdirSync(outputDir,{recursive:true});
  const manifest={engine:'Photopea Live Messaging API',source,source_sha256:crypto.createHash('sha256').update(sourceBytes).digest('hex'),jobs:[]};
  const session=new PhotopeaSession(options);
  try {
    await session.start(sourceBytes);
    for (const job of jobs) {
      // Load original bytes for each independent job. Do not rely on Document.duplicate().
      const info=await session.load();
      if (plan.source_size && JSON.stringify(plan.source_size)!==JSON.stringify([info.width,info.height])) throw new Error('source_size does not match the loaded document');
      if (job.box && (job.box[0]<0||job.box[1]<0||job.box[2]>info.width||job.box[3]>info.height)) throw new Error('Box outside source canvas: '+job.name);
      const code=[job.box?'app.activeDocument.crop('+JSON.stringify(job.box)+');':'',plan.script||'',job.script||'',
        'app.activeDocument.saveToOE('+JSON.stringify(job.format)+');'].join('\n');
      const reply=await session.run(code,true);
      if (reply.buffers.length!==1) throw new Error('Expected exactly one exported image for '+job.name);
      const bytes=reply.buffers[0];
      const size=imageHeader(bytes,job.ext);
      const expected=job.expected_size||((job.box&&!job.script&&!plan.script&&['png','psd'].includes(job.ext))?[job.box[2]-job.box[0],job.box[3]-job.box[1]]:null);
      if (expected && !size) throw new Error('Built-in expected_size validation supports PNG and PSD; use PNG or verify this format separately');
      if (expected && JSON.stringify(expected)!==JSON.stringify(size)) throw new Error('Wrong exported size for '+job.name+': '+JSON.stringify(size));
      fs.writeFileSync(path.join(outputDir,job.file),bytes);
      manifest.jobs.push({name:job.name,file:job.file,box:job.box||null,size,bytes:bytes.length,
        sha256:crypto.createHash('sha256').update(bytes).digest('hex'),source_size:[info.width,info.height],
        crop_only:!job.script&&!plan.script,script:job.script||'',shared_script:plan.script||'',format:job.format,strings:reply.strings});
      console.log(JSON.stringify({exported:job.name,file:job.file,size,bytes:bytes.length}));
    }
    manifest.complete=true;
    fs.writeFileSync(path.join(outputDir,'photopea-manifest.json'),JSON.stringify(manifest,null,2));
    console.log(JSON.stringify({complete:true,count:manifest.jobs.length,out:outputDir}));
    return manifest;
  } catch (error) {
    manifest.complete=false;manifest.error=error.message;
    fs.writeFileSync(path.join(outputDir,'photopea-partial.json'),JSON.stringify(manifest,null,2));
    await session.diagnostic(outputDir,error);
    throw error;
  } finally { await session.close(); }
}

async function main() {
  const {values}=parseArgs({options:{input:{type:'string'},plan:{type:'string'},out:{type:'string'},
    'playwright-module':{type:'string'},channel:{type:'string'},'browser-executable':{type:'string'},
    'timeout-ms':{type:'string'},headed:{type:'boolean'},overwrite:{type:'boolean'},help:{type:'boolean'}}});
  if (values.help) {
    console.log('node photopea.cjs --input IMAGE --plan PLAN.json --out DIRECTORY [--playwright-module PACKAGE_DIR] [--channel msedge|chrome] [--browser-executable PATH] [--timeout-ms 45000] [--headed] [--overwrite]');
    return;
  }
  if (!values.input||!values.plan||!values.out) throw new Error('--input, --plan, and --out are required (see --help)');
  const timeout=Number(values['timeout-ms']||45000);
  if (!Number.isInteger(timeout)||timeout<1000||timeout>60000) throw new Error('--timeout-ms must be 1000..60000');
  await exportPlan({input:values.input,plan:values.plan,out:values.out,playwrightModule:values['playwright-module'],
    channel:values.channel,executablePath:values['browser-executable'],timeout,headed:values.headed,overwrite:values.overwrite});
}
module.exports={PhotopeaSession,exportPlan};
if (require.main===module) main().catch(error=>{console.error(error.message);process.exitCode=1;});
