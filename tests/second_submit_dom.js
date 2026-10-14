// Small DOM fixture for the actual runner JS; no browser or network calls.
const fs = require('node:fs');
const vm = require('node:vm');
const payload = JSON.parse(fs.readFileSync(0, 'utf8'));

function splitSelectors(value) {
  const result=[]; let start=0, depth=0, quote='';
  for (let i=0;i<value.length;i++) {
    const ch=value[i];
    if (quote) { if (ch===quote) quote=''; }
    else if (ch==='"' || ch==="'") quote=ch;
    else if (ch==='(' || ch==='[') depth++;
    else if (ch===')' || ch===']') depth--;
    else if (ch===',' && !depth) {result.push(value.slice(start,i));start=i+1;}
  }
  result.push(value.slice(start)); return result.map(x=>x.trim());
}

async function execute(test) {
  const clicks=[]; const nodes=[];
  let certificate=null, hiddenConfirmation=null, drawer=null;
  class Element {
    constructor(tag, text='', attrs={}, children=[]) {
      this.tagName=tag.toUpperCase(); this.ownText=text; this.attrs=attrs;
      this.parentElement=null; this.children=[]; this.labels=[]; this.isConnected=true;
      this.hidden=false; this.inert=false; this.readOnly=false; this.disabled=false;
      this.checked=false; this.type=attrs.type || ''; this.name=attrs.name || '';
      this.value=attrs.value || ''; this.index=nodes.length; nodes.push(this);
      children.forEach(child=>this.append(child));
    }
    append(child) {this.children.push(child);child.parentElement=this;return child;}
    get innerText() {return [this.ownText,...this.children.filter(x=>!x.hidden).map(x=>x.innerText)].filter(Boolean).join(' ');}
    get textContent() {return this.innerText;}
    get childElementCount() {return this.children.length;}
    getAttribute(name) {return this.attrs[name] ?? null;}
    hasAttribute(name) {return Object.hasOwn(this.attrs,name);}
    matches(selector) {
      return splitSelectors(selector).some(part=>{
        const negatives=[...part.matchAll(/:not\(([^)]+)\)/g)].map(m=>m[1]);
        if (negatives.some(value=>this.matches(value))) return false;
        part=part.replace(/:not\([^)]+\)/g,'');
        const tag=part.match(/^[a-z][\w-]*/i)?.[0];
        if (tag && this.tagName!==tag.toUpperCase()) return false;
        for (const match of part.matchAll(/\[([^\]=]+)(?:=["']?([^\]"']+)["']?)?\]/g)) {
          const value=this.getAttribute(match[1]);
          if (value===null || (match[2]!==undefined && value!==match[2])) return false;
        }
        return part==='*' || !!tag || part.startsWith('[');
      });
    }
    querySelectorAll(selector) {
      const result=[];
      for (const child of this.children) {
        if (child.matches(selector)) result.push(child);
        result.push(...child.querySelectorAll(selector));
      }
      return result;
    }
    querySelector(selector) {return this.querySelectorAll(selector)[0] || null;}
    closest(selector) {for(let e=this;e;e=e.parentElement) if(e.matches(selector))return e; return null;}
    contains(other) {for(let e=other;e;e=e.parentElement) if(e===this)return true; return false;}
    getClientRects() {for(let e=this;e;e=e.parentElement)if(e.hidden)return [];return [this.getBoundingClientRect()];}
    getBoundingClientRect() {return {x:0,y:0,left:0,top:0,right:600,bottom:180,width:600,height:180};}
    compareDocumentPosition(other) {return this.index<other.index?4:2;}
    scrollIntoView() {}
    click() {
      clicks.push(this.attrs['data-test-id'] || this.innerText);
      if(this.type==='checkbox')this.checked=!this.checked;
      if(this.attrs['data-test-id']==='certificate-close' && certificate) {
        certificate.hidden=true;
        if(hiddenConfirmation)hiddenConfirmation.hidden=false;
      }
      if(this.attrs['data-test-id']==='drawer-close' && drawer)drawer.hidden=true;
    }
  }
  const el=(tag,text='',attrs={},children=[])=>new Element(tag,text,attrs,children);
  const button=(text,id)=>el('button',text,{'data-test-id':id});
  const main=el('main'); const body=el('body','',{},[main]);
  const attempt=main.append(el('div','',{'role':'dialog','aria-modal':'true'}));
  const question=attempt.append(el('section','Question 1: choose one'));
  for(let i=0;i<2;i++) {
    const label=el('label',i?'Second answer':'First answer');
    const input=el('input','',{type:'radio',name:'question-one',value:String(i)});
    input.checked=i===0;input.labels=[label];label.append(input);question.append(label);
  }
  const noticeLabel=el('label',payload.notice);
  const notice=el('input','',{type:'checkbox'});notice.checked=true;notice.labels=[noticeLabel];
  noticeLabel.append(notice);attempt.append(noticeLabel);
  attempt.append(button('Submit','background-submit'));attempt.append(button('Save draft','save-draft'));
  if(test.courseCover) {
    attempt.hidden=true;
    main.append(button('Start assignment','start-assignment'));
  }
  const popups=[];
  function addPopup(index=0) {
    const attrs=test.certificateOverlay?{'role':'dialog','aria-modal':'true'}:
      test.role?{role:test.role}:{};
    const heading=el('h2',test.text || 'Ready to submit?');
    const submit=button('Submit',`popup-submit-${index}`);submit.disabled=!!test.disabled;
    const cancel=button('Cancel',`popup-cancel-${index}`);
    const actions=el('div','',{},[submit,cancel]);
    if (test.extraSubmit) actions.append(button('Submit',`extra-submit-${index}`));
    const popup=el('div','',attrs,[heading,actions]);
    if(test.certificateOverlay && index===0) {
      popup.hidden=true;
      hiddenConfirmation=popup;
    }
    if(test.extraInput) popup.append(el('input','',{type:'text'}));
    const wrapper=el('div','',{},[popup]);
    if(test.hidden)wrapper.hidden=true;
    attempt.append(wrapper); popups.push({popup,wrapper,heading,submit,cancel});
  }
  if(!test.noPopup)addPopup();
  if(test.duplicatePopup)addPopup(1);
  if(test.certificateOverlay) {
    certificate=attempt.append(el('div','',{'role':'dialog','aria-modal':'true'},[
      el('h2',test.certificateText || 'Congratulations!'),
      el('p',"Completed by Test Learner on October 14, 2026 Verified Learner Certificate ID: TEST123 That was the last assignment! You've now completed Introduction to Philosophy and earned the certificate!"),
      button('Close','certificate-close'),
      el('a','Add to LinkedIn',{'href':'https://www.coursera.org/','data-test-id':'certificate-link'}),
      el('a','View certificate page',{'href':'https://www.coursera.org/account/accomplishments','data-test-id':'certificate-page'})
    ]));
    if(test.extraCertificateClose)certificate.append(button('Close','certificate-close-extra'));
  }
  if(test.courseDrawer) {
    const makeDrawer=(id='drawer-close')=>{
      const close=el('button','',{'aria-label':'Close','data-test-id':id});
      const panel=el('div','',{
        role:'dialog','aria-modal':'true',
        'data-testid':test.wrongDrawerTestid?'other-drawer':'drawer-toggle-drawer'
      },[el('h2','Explainable Machine Learning (XAI)'),close,
          el('p',"Today's goal • 3/3 items Module 1 Model-Agnostic Explainability")]);
      body.append(panel);return panel;
    };
    drawer=makeDrawer();
    if(test.extraDrawerClose)drawer.append(el('button','',{'aria-label':'Close','data-test-id':'drawer-close-extra'}));
    if(test.duplicateDrawer)makeDrawer('drawer-close-other');
    if(test.otherModal)body.append(el('div','Confirm identity',{'role':'dialog','aria-modal':'true'}));
  }
  const document={body,querySelectorAll:selector=>body.querySelectorAll(selector),
    querySelector:selector=>body.querySelector(selector),
    getElementById:id=>nodes.find(e=>e.getAttribute('id')===id) || null};
  const context={document,location:new URL('https://www.coursera.org/learn/philosophy/assignment-submission/UAOb6/what-is-philosophy/attempt'),
    Node:{DOCUMENT_POSITION_FOLLOWING:4},getComputedStyle:e=>({display:e.hidden?'none':'block',visibility:'visible',opacity:'1'}),
    URL,setTimeout:callback=>callback()};
  const call=vm.runInNewContext(payload.code,context);
  const request={key:'assignment:UAOb6',action:'inspect',authorized_notice:payload.notice,...test.request};
  const before=await call(request);
  if(!test.followup)return {result:before,clicks};
  if(test.mutateText)popups[0].heading.ownText=test.mutateText;
  if(test.removePopup)popups[0].wrapper.hidden=true;
  if(test.mutateDrawerText && drawer)drawer.children.at(-1).ownText=test.mutateDrawerText;
  if(test.removeDrawer && drawer)drawer.hidden=true;
  const followup={...request,...test.followup,snapshot_id:before.snapshot_id};
  if(followup.action==='dismiss_course_sidebar' && !Object.hasOwn(followup,'expected_dialog'))
    followup.expected_dialog=before.dialog;
  const result=await call(followup);
  const after=test.inspectAfter?await call(request):null;
  const secondResult=test.secondFollowup && after?
    await call({...request,...test.secondFollowup,snapshot_id:after.snapshot_id}):null;
  return {before,result,after,secondResult,clicks};
}

Promise.all(payload.cases.map(execute)).then(results=>process.stdout.write(JSON.stringify(results)))
  .catch(error=>{console.error(error);process.exit(1);});
