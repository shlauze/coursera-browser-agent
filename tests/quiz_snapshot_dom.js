// Offline DOM fixture: execute the actual runner JavaScript, never a browser.
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));

async function inspect(test) {
  const ui = test.ui;
  const clicks = [];
  const groups = new Map();
  const controls = ui.controls.map((c, i) => {
    const key = c.name || c.question;
    let group = groups.get(key);
    if (!group) {
      group = {innerText:c.question, parentElement:null,
        matches:()=>false, contains:e=>e.parentElement===group};
      groups.set(key, group);
    }
    const labels = [{innerText:c.label, scrollIntoView:()=>{},
      getClientRects:()=>[{width:100,height:20}]}];
    return {type:c.type, name:c.name, value:c.value, labels,
      checked:c.selected, disabled:c.disabled, parentElement:group,
      isConnected:true, index:i, matches:s=>s==='input', closest:()=>null,
      getAttribute:()=>null, querySelector:()=>null,
      getClientRects:()=>[{width:100,height:20}],
      compareDocumentPosition(other){return this.index<other.index?4:2;},
      click(){this.checked=!this.checked; clicks.push(c.id);}};
  });
  const document = {
    body:{innerText:ui.page_text || ''},
    getElementById:()=>null,
    querySelector:()=>null,
    querySelectorAll(selector){
      return selector==='input[type="radio"],input[type="checkbox"]'?controls:[];
    }
  };
  const url = new URL(ui.url);
  const sandbox = {document, location:url, Node:{DOCUMENT_POSITION_FOLLOWING:4},
    getComputedStyle:()=>({visibility:'visible',display:'block',opacity:'1'}),
    setTimeout:callback=>callback(), URL};
  const result = await vm.runInNewContext(input.code, sandbox)(test.request ||
    {key:'assignment:oVRfd',action:'inspect',authorized_notice:input.notice});
  return {result,clicks};
}

Promise.all(input.cases.map(inspect)).then(results=>process.stdout.write(JSON.stringify(results)))
  .catch(error=>{console.error(error);process.exit(1);});
