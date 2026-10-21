request => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const visible = e => !!e && !!e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden'
    && !e.closest('[hidden],[inert],[aria-hidden="true"]');
  const textOf = (element, omit = '') => {
    if (!element) return '';
    const copy = element.cloneNode(true);
    const originals = [element, ...element.querySelectorAll('*')];
    const copies = [copy, ...copy.querySelectorAll('*')];
    originals.forEach((source, index) => {
      const target = copies[index], style = getComputedStyle(source);
      if ((omit && source.matches(omit)) || source.matches('script,style,noscript,[hidden],[inert]')
          || style.display === 'none' || style.visibility === 'hidden') {
        if (index === 0) copy.textContent = ''; else target.remove();
        return;
      }
      if (source.matches('[data-pendo="math-block"],.katex')) {
        const tex = source.querySelector('annotation[encoding="application/x-tex"]')?.textContent;
        if (tex) { if (index === 0) copy.textContent = ' $' + tex + '$ '; else target.replaceWith(document.createTextNode(' $' + tex + '$ ')); return; }
      }
      if (source.getAttribute('aria-hidden') === 'true') target.remove();
    });
    return norm(copy.textContent);
  };
  const label = e => norm(e.getAttribute('aria-label') || [...(e.labels || [])].map(x => textOf(x)).join(' ')
    || (e.getAttribute('aria-labelledby') || '').split(/\s+/).map(id => textOf(document.getElementById(id))).join(' ')
    || textOf(e.closest('label')) || textOf(e));
  const parts = location.pathname.split('/').filter(Boolean);
  if (!['www.coursera.org','coursera.org'].includes(location.hostname) || parts[0] !== 'learn'
      || !['assignment-submission','quiz','exam'].includes(parts[2])) return {error:'Not a recognized assignment page'};
  if (request.expected_path && location.pathname !== request.expected_path) return {error:'Assignment page changed'};
  const pageText = norm(document.body.innerText);
  const attestationNotice = /(?:confirm|certify|declare) (?:that )?(?:this|the) work is (?:your|my) own|I\s*,[^,]{1,160},\s*(?:understand|agree|certify)|I (?:certify|declare) that|submitting work that (?:isn't|isnР В Р вЂ Р В РІР‚С™Р Р†РІР‚С›РЎС›t|is not) my own/i.test(pageText);
  const authorshipBox = e => /honor code|I\s*,[^,]{1,160},\s*(?:understand|agree|certify)|my own work|work is (?:your|my) own/i.test(label(e));
  const authorshipBoxes = [...document.querySelectorAll('input[type="checkbox"],[role="checkbox"]')]
    .filter(e => (visible(e) || [...(e.labels || [])].some(visible)) && authorshipBox(e));
  const attestation = attestationNotice && !(authorshipBoxes.length > 0 && authorshipBoxes.every(e =>
    e.matches('input') ? e.checked : e.getAttribute('aria-checked') === 'true'));
  if (request.action === 'confirm_authorship') {
    if (!attestationNotice || authorshipBoxes.length !== 1) return {ok:false,error:'No unique Coursera confirmation checkbox'};
    const box = authorshipBoxes[0];
    if (box.disabled || box.getAttribute('aria-disabled') === 'true') return {ok:false,error:'Coursera confirmation is disabled'};
    const checked = () => box.matches('input') ? box.checked : box.getAttribute('aria-checked') === 'true';
    if (!checked()) box.click();
    return {ok:checked(),error:checked()?null:'Coursera confirmation was not accepted'};
  }
  const activeDialogs = [...document.querySelectorAll('[role="dialog"],[role="alertdialog"],dialog[open]')].filter(e => visible(e) && norm(e.innerText));
  const scope = activeDialogs.at(-1) || document.querySelector('main') || document.body;
  const buttonEls = [...scope.querySelectorAll('button,[role="button"],a[href],input[type="submit"]')]
    .filter(e => visible(e) && !e.disabled && e.getAttribute('aria-disabled') !== 'true');
  const buttons = buttonEls.map(e => norm(e.innerText || e.value || e.getAttribute('aria-label')));
  const inputs = [...scope.querySelectorAll('input[type="radio"],input[type="checkbox"],input[type="text"],input[type="number"],textarea,select,[role="radio"],[role="checkbox"]')]
    .filter(e => !e.closest('nav,aside,header') && (visible(e) || [...(e.labels || [])].some(visible))
      && !(e.matches('[role="radio"],[role="checkbox"]') && !e.matches('input') && e.querySelector('input')));
  const answerInputs = inputs.filter(e => !authorshipBox(e));
  const roots = [];
  for (const e of answerInputs) {
    let root = e.closest('.rc-FormPartsQuestion,.rc-QuizQuestion,.rc-AssessmentQuestion,fieldset,[data-testid="question"]');
    // Prefer an explicit question-group label over numeric answer heuristics.
    const group = e.closest('[role="group"][aria-labelledby],[role="radiogroup"][aria-labelledby]');
    if (!root && group) {
      const prompts = group.getAttribute('aria-labelledby').split(/\s+/).filter(Boolean).map(id => document.getElementById(id));
      if (prompts.length && prompts.every(p => p && scope.contains(p) && visible(p))) {
        for (let p = group; p && p !== scope; p = p.parentElement) {
          if (prompts.every(prompt => p.contains(prompt))) { root = p; break; }
        }
      }
    }
    if (!root) {
      for (let p = e.parentElement, depth = 0; p && p !== scope && depth < 9; p = p.parentElement, depth++) {
        const text = norm(p.innerText);
        if (/^(?:Question\s+)?\d+[.)\s]/i.test(text) && text.length < 18000) {
          root = p;
          if (p.querySelectorAll('input,textarea,select,[role="radio"],[role="checkbox"]').length >= 2 || e.matches('textarea,input[type="text"],input[type="number"],select')) break;
        }
      }
    }
    if (!root || !scope.contains(root)) return {error:'Cannot associate every answer field with a question',attestation};
    if (!roots.includes(root)) roots.push(root);
  }
  if (roots.some(a => roots.some(b => a !== b && a.contains(b)))) return {error:'Overlapping question containers',attestation};
  if ([...scope.querySelectorAll('[contenteditable="true"]')].some(visible)) return {error:'File upload or rich-text editor needs a dedicated adapter',attestation};
  if (scope.querySelector('input[type="file"]') && answerInputs.length) return {error:"Mixed file and quiz form needs a dedicated adapter",attestation};
  const questions = [], bindings = [], feedbackQuestions = [];
  for (let i = 0; i < roots.length; i++) {
    const root = roots[i], fields = answerInputs.filter(e => root.contains(e));
    if ([...root.querySelectorAll('img,canvas,video')].some(visible)) return {error:'Question includes visual content; text-only model cannot answer it',attestation};
    const disabled = fields.some(e => e.disabled || e.closest('fieldset[disabled]') || e.getAttribute('aria-disabled') === 'true');
    let text = textOf(root, 'input,textarea,select,label,button,[role="radio"],[role="checkbox"],[data-testid="feedback"],.feedback,.points,[data-testid="points"]');
    // Text inputs explicitly reference their prompt. Do not confuse numeric
    // character counters or decorative fieldsets with the question stem.
    if (fields.length === 1 && fields[0].matches('textarea,input[type="text"],input[type="number"],select')) {
      const refs = (fields[0].getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean);
      const prompts = refs.map(id => document.getElementById(id));
      if (prompts.length && prompts.every(p => p && root.contains(p) && visible(p))) {
        text = prompts.map(p => textOf(p)).join(' ');
      }
    }
    const choiceGroup = fields[0]?.closest('[role="radiogroup"][aria-labelledby],[role="group"][aria-labelledby]');
    if (choiceGroup) {
      const prompts = choiceGroup.getAttribute('aria-labelledby').split(/\s+/).filter(Boolean).map(id => document.getElementById(id));
      if (prompts.length && prompts.every(p => p && root.contains(p) && visible(p))) text = prompts.map(p => textOf(p)).join(' ');
    }
    // Exclude only the displayed question number; preserve the actual stem.
    text = text.replace(/^(?:Question\s+)?\d+[.)]\s*/i,'');
    if (text.length < 5) return {error:'Question text is missing',attestation};
    const types = fields.map(e => e.type || e.getAttribute('role'));
    let kind, options = [];
    if (types.every(t => t === 'radio' || t === 'checkbox')) {
      if (new Set(types).size !== 1) return {error:'Mixed choice types in one question',attestation};
      kind = types[0] === 'radio' ? 'single' : 'multiple';
      options = fields.map((e,n) => ({id:`o${n}`,text:label(e)}));
      if (options.length < 2 || options.some(o => !o.text) || (request.allow_random_duplicates !== true && new Set(options.map(o=>norm(o.text))).size !== options.length))
        return {error:'Ambiguous answer options',attestation};
    } else if (fields.length === 1 && fields[0].tagName === 'SELECT') {
      kind = 'select';
      options = [...fields[0].options].filter(o => !o.disabled && o.value !== '').map(o => ({id:o.value,text:norm(o.text)}));
    } else if (fields.length === 1 && ['text','number','textarea'].includes(types[0])) {
      kind = types[0] === 'textarea' ? 'long_text' : types[0];
    } else return {error:'Unsupported or ambiguous answer controls',attestation};
    const question = {id:`q${i}`,text,kind,options,max_length: fields[0].maxLength > 0 ? fields[0].maxLength : 10000};
    if (disabled) {
      const scores = [...root.querySelectorAll('.points,[data-testid="points"],[data-testid="part-points"]')].map(e=>norm(e.innerText))
        .map(t=>t.match(/^(\d+(?:\.\d+)?)\s*\/\s*(\d+(?:\.\d+)?)\s*points?$/i)).filter(Boolean);
      const score = scores.length === 1 ? scores[0] : null;
      let answer = null;
      if (['single','multiple'].includes(kind)) {
        const selected = fields.flatMap((e,n)=>(e.matches('input')?e.checked:e.getAttribute('aria-checked')==='true')?[`o${n}`]:[]);
        if (selected.length) answer = {cannot_answer:false,selected};
      } else if (kind === 'select' && fields[0].value) {
        answer = {cannot_answer:false,selected:[fields[0].value]};
      } else if (['text','number','long_text'].includes(kind) && fields[0].value?.trim()) {
        answer = {cannot_answer:false,text:fields[0].value};
      }
      feedbackQuestions.push({question,answer,earned:score?Number(score[1]):null,possible:score?Number(score[2]):null,feedback:([...root.querySelectorAll('[id$="-grade-feedback"]')].map(e=>textOf(e)).join(' ') || textOf(root)).slice(0,3000)});
      continue;
    }
    questions.push(question);
    bindings.push(fields);
  }
  const resultScope = scope.innerText || '';
  let failed = /You didn['’]t pass|\bFailed\b/i.test(resultScope) && /(?:grade|score|pass)/i.test(resultScope);
  let passed = !failed && /(?:Congratulations[^\n]*passed|You passed|\bPassed\b)/i.test(resultScope)
    && /(?:grade|score|assignment|quiz)/i.test(resultScope);
  let gradeEvidence = null;
  if (location.pathname.endsWith('/view-feedback')) {
    const gradeHeadings = [...scope.querySelectorAll('h1,h2,h3,[role="heading"]')]
      .filter(e => visible(e) && /^Your grade:\s*\d+(?:\.\d+)?%$/.test(norm(e.innerText)));
    if (gradeHeadings.length === 1) {
      const heading = gradeHeadings[0];
      const grade = Number(norm(heading.innerText).match(/(\d+(?:\.\d+)?)%/)[1]);
      const block = norm(heading.parentElement?.parentElement?.innerText);
      const threshold = block.match(/To pass you need at least (\d+(?:\.\d+)?)%/);
      if (threshold && grade >= 0 && grade <= 100 && Number(threshold[1]) > 0 && Number(threshold[1]) <= 100) {
        gradeEvidence = {grade, required: Number(threshold[1]), path: location.pathname};
        passed = grade >= gradeEvidence.required;
        failed = !passed;
      }
    }
  }
  const snapshot = JSON.stringify([location.pathname,questions]);
  const values = questions.map((q,i) => {
    const fields=bindings[i];
    if (q.kind === 'select') return {id:q.id,selected:[fields[0].value]};
    if (['single','multiple'].includes(q.kind)) return {id:q.id,selected:fields.flatMap((e,n)=>(e.matches('input')?e.checked:e.getAttribute('aria-checked')==='true')?[`o${n}`]:[])};
    return {id:q.id,text:fields[0].value};
  });
  const replacementNotice = String.raw`When you submit a new attempt it will replace your previous submission\. This new attempt will only replace your previous grade if it[’']s higher\.`;
  const duration = String.raw`(?:\d+\s*(?:h|hours?)(?:\s*\d+\s*(?:m|minutes?))?|\d+\s*(?:m|minutes?))`;
  const timerNotice = String.raw`You have [1-9]\d* minutes? to submit up to [1-9]\d* times? for this attempt\. You will have \d+ attempts? left after the time(?:r)? runs out\. After that, you will have to wait ` + duration + String.raw` before you can try again\.`;
  const dialogText = norm(scope.innerText);
  const retryConfirmation = activeDialogs.length === 1 && new RegExp(String.raw`^Start new attempt\? ` + replacementNotice + String.raw`(?: ` + timerNotice + String.raw`)? Continue\s*Cancel$`).test(dialogText);
  const startConfirmation = activeDialogs.length === 1 && new RegExp(String.raw`^Start new attempt\? ` + timerNotice + String.raw` Continue\s*Cancel$`).test(dialogText);
  const fileForm = !location.pathname.endsWith('/view-feedback') && [...scope.querySelectorAll('input[type="file"]')].length > 0;
  const fileFeedback = location.pathname.endsWith('/view-feedback') && /No uploaded file/.test(pageText) && /How you were graded/.test(pageText)
    ? {missing_file:true, rubrics:[...pageText.matchAll(/Rubric\s+(\d+)\s+Status:\s*([\d.]+)\s*\/\s*([\d.]+)\s*points/g)].map(m=>({rubric:m[1],earned:Number(m[2]),possible:Number(m[3])}))} : null;
  const result = {file_form:fileForm,file_feedback:fileFeedback,start_confirmation:startConfirmation,retry_confirmation:retryConfirmation,path:location.pathname,questions,snapshot,buttons,values,feedback_questions:feedbackQuestions,attestation,failed,passed,grade_evidence:gradeEvidence,
    pending:/Reviewing your submission|Grading in progress|Submission received/i.test(resultScope),
    confirmation: activeDialogs.length > 0 && /^Ready to submit\? Submit Cancel$/.test(norm(scope.innerText)),
    feedback: norm(resultScope).slice(0,20000)};
  if (!request.action || request.action === 'inspect') return result;
  if (attestation) return {error:'Personal authorship confirmation requires the learner',...result};
  if (request.snapshot !== snapshot) return {error:'Assignment form changed before action',...result};
  if (request.action === 'fill') {
    const index = questions.findIndex(q => q.id === request.question_id);
    if (index < 0) return {error:'Question disappeared',...result};
    const q = questions[index], fields = bindings[index], a = request.answer;
    if (['single','multiple','select'].includes(q.kind)) {
      if (!Array.isArray(a.selected) || !a.selected.length || a.selected.some(id=>!q.options.some(o=>o.id===id))
          || (q.kind !== 'multiple' && a.selected.length !== 1)) return {error:'Invalid option IDs',...result};
      if (q.kind === 'select') {
        fields[0].value = a.selected[0]; fields[0].dispatchEvent(new Event('change',{bubbles:true}));
      } else {
        for (let n=0;n<fields.length;n++) {
          const e=fields[n], selected=e.matches('input')?e.checked:e.getAttribute('aria-checked')==='true';
          if (selected !== a.selected.includes(`o${n}`)) e.click();
        }
      }
    } else {
      if (typeof a.text !== 'string' || !a.text.trim() || a.text.length > q.max_length) return {error:'Invalid text answer',...result};
      const e=fields[0], proto=e.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto,'value').set.call(e,a.text);
      e.dispatchEvent(new Event('input',{bubbles:true})); e.dispatchEvent(new Event('change',{bubbles:true}));
    }
    return {ok:true};
  }
  if (request.action === 'button') {
    const allow = /^(Start assignment|Resume assignment|Start quiz|Resume quiz|Start attempt|Resume attempt|Retry|Retry assignment|Try again|View feedback|Review feedback|View submission|Feedback|Continue|Cancel|Submit|Back|Go to next item)$/i;
    if (!allow.test(request.label)) return {error:'Unsupported assignment control',...result};
    if (request.label === 'Cancel' && !startConfirmation && !retryConfirmation) return {error:'Unknown start confirmation',...result};
    if (request.label === 'Continue' && !((retryConfirmation && request.failed_attempt_verified === true) || (startConfirmation && request.start_attempt_verified === true))) return {error:'Unverified retry confirmation',...result};
    const found = buttonEls.filter((e,i)=>buttons[i]===request.label);
    if (found.length !== 1) return {error:'Assignment control is missing or ambiguous',...result};
    if (request.label === 'Submit' && !questions.length && !result.confirmation) return {error:'Unknown submission confirmation',...result};
    if (request.label === 'Submit' && questions.length) {
      if (!Array.isArray(request.answers) || request.answers.length !== questions.length) return {error:'Missing verified answer plan',...result};
      for (const [q,a] of request.answers) {
        const current=values.find(v=>v.id===q.id);
        if (!current || (a.text !== undefined ? current.text !== a.text : JSON.stringify([...current.selected].sort()) !== JSON.stringify([...a.selected].sort())))
          return {error:'Answers did not persist before submission',...result};
      }
    }
    found[0].click(); return {ok:true};
  }
  return {error:'Unsupported operation'};
}
