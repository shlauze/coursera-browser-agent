// Execute the runner's actual JS against local captured-state fixtures.
const fs = require('node:fs');
const vm = require('node:vm');
const payload = JSON.parse(fs.readFileSync(0, 'utf8'));

async function execute(test) {
  const ui = test.ui;
  const clicks = [];
  const actionSelector = 'button,[role="button"],a[href],[role="link"],input[type="submit"],input[type="button"]';
  const groups = new Map();
  const base = (text='') => ({innerText:text,textContent:text,parentElement:null,
    hidden:false,inert:false,isConnected:true,disabled:false,readOnly:false,
    getClientRects:()=>[{width:100,height:20}],
    getBoundingClientRect:()=>({width:1920,height:991}),
    getAttribute:()=>null,matches:()=>false,closest:()=>null,
    querySelector:()=>null,querySelectorAll:()=>[],contains:()=>false,
    scrollIntoView:()=>{}});
  const controls = (ui.controls || []).map((c,i)=>{
    const groupKey=c.name || c.question;
    let group=groups.get(groupKey);
    if (!group) {
      group={...base(c.question),contains:e=>e.parentElement===group};
      groups.set(groupKey,group);
    }
    return {...base(),tagName:'INPUT',type:c.type,name:c.name,value:c.value,
      parentElement:group,labels:[base(c.label)],checked:c.selected,
      disabled:!!c.disabled,index:i,matches:s=>s==='input',
      compareDocumentPosition(other){return this.index<other.index?4:2;},
      click(){this.checked=!this.checked;clicks.push(c.id);}};
  });
  const actions=(ui.observed_controls || []).map((c,i)=>({
    ...base(c.label),tagName:(c.tag || 'button').toUpperCase(),
    disabled:!!c.disabled,getAttribute:name=>name==='href' && c.tag==='a' ? '#':null,
    matches:s=>s==='a[href]' && c.tag==='a',
    click(){clicks.push(c.label);}
  }));
  const allInputs=[...controls];
  if (test.extraInput) allInputs.push({...base(),tagName:'INPUT',type:'text',name:'signature',labels:[base('Signature')]});
  for (const field of test.textareas || [])
    allInputs.push({...base(),tagName:'TEXTAREA',type:'textarea',name:'',
      labels:[base(field.label)],matches:selector=>selector==='textarea'});
  const dialog = ui.dialog ? {...base(ui.dialog),
    getAttribute:name=>name==='aria-modal'?'true':null,
    contains:e=>controls.includes(e) || actions.includes(e) || allInputs.includes(e),
    querySelectorAll(selector){
      if (selector===actionSelector) return actions;
      if (selector===actionSelector+',input,textarea') return [...actions,...allInputs];
      if (selector==='input,textarea,select' || selector==='input,textarea,select,[role="checkbox"],[role="radio"]') return allInputs;
      if (selector.includes('textarea,select,input:not')) return allInputs.filter(e=>!['radio','checkbox'].includes(e.type));
      if (selector==='input,textarea' || selector==='input,select,textarea') return allInputs;
      return [];
    }} : null;
  if (dialog) {
    for (const group of groups.values()) group.parentElement=dialog;
    for (const action of actions) action.parentElement=dialog;
  }
  const document={
    body:{...base(ui.page_text || ui.dialog || '')},
    getElementById:()=>null,querySelector:()=>null,
    querySelectorAll(selector){
      if (selector==='input[type="radio"],input[type="checkbox"]') return controls;
      if (selector==='[role="dialog"],dialog[open]') return dialog?[dialog]:[];
      if (selector===actionSelector) return actions;
      return [];
    }
  };
  const context={document,location:new URL(ui.url),Node:{DOCUMENT_POSITION_FOLLOWING:4},
    getComputedStyle:()=>({display:'block',visibility:'visible',opacity:'1'}),
    URL,setTimeout:callback=>callback()};
  const request=test.request || {key:test.key || 'assignment:oVRfd',action:'inspect',authorized_notice:payload.notice};
  const result=await vm.runInNewContext(payload.code,context)(request);
  return {result,clicks};
}

Promise.all(payload.cases.map(execute)).then(results=>process.stdout.write(JSON.stringify(results)))
  .catch(error=>{console.error(error);process.exit(1);});
