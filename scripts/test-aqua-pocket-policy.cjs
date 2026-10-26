const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const api=vm.createContext({});
vm.runInContext(fs.readFileSync(__dirname+'/../services/AquaPocketPolicy.js','utf8').replace(/^\.pragma library\s*/,''),api);
const item={id:27,path:'/selected/<plain>.txt',name:'<plain>.txt',size:12,modified_ms:20,created_ms:41,source:'picker'};
const envelope={version:1,ok:true,action:'list',items:[item],changed:0};
assert.ok(api.reply(JSON.stringify(envelope),'list',0));
assert.equal(api.reply(JSON.stringify(envelope),'add',0),null);
assert.equal(api.reply(JSON.stringify(envelope),'list',1),null);
for(const bad of [null,[],{}, {...envelope,version:2},{...envelope,ok:'true'},
  {...envelope,items:[item,item]},{...envelope,changed:33},{...envelope,unexpected:'private'}])
  assert.equal(api.reply(JSON.stringify(bad),'list',0),null);
for(const [key,value] of [['id',true],['size',-1],['size',1e20],['path','file:///bad'],
 ['path','/secret\nnew'],['name','bad\u202efile'],['source','model'],['unexpected','secret']])
 assert.equal(api.reply(JSON.stringify({...envelope,items:[{...item,[key]:value}]}),'list',0),null);
assert.ok(api.urls(['file:///chosen%20file.txt']));
for(const values of [null,[],Array(9).fill('file:///a'),['https://example.com'],['file://host/a'],[1]])
 assert.equal(api.urls(values),null);
assert.ok(!api.error('{"error":"private-secret"}').includes('private-secret'));
console.log('COMPANION_POCKET_POLICY_PASS boundedTypedReply noPrivateError noUnknownFields');
