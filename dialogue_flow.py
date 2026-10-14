"""Finish Coursera Dialogue lessons through the site's End Dialogue control.

The learner requested the short completion flow: start the Dialogue if needed,
end it with the optional ``No reason provided`` choice, and continue only after
the course outline displays its green completion mark. No Coach messages are
generated or sent by this module.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from urllib.parse import urlsplit

import coursera_start as engine


MAX_SECONDS = 20 * 60
SESSION_PREFIX = (
    "Would you like to end this session? Please note that you will not be able "
    "to resume it, but you can always start a new one from the beginning. "
    "Reason for ending (Optional) We would appreciate your feedback to help us "
    "improve the experience. "
)


DIALOGUE_JS = r"""(request=>{
  const clean=x=>(x||'').replace(/\s+/g,' ').trim();
  const visible=e=>{
    if(!e||!e.getClientRects().length) return false;
    for(let p=e;p;p=p.parentElement){
      const s=getComputedStyle(p);
      if(p.hidden||p.inert||p.getAttribute('aria-hidden')==='true'||
         s.display==='none'||['hidden','collapse'].includes(s.visibility)) return false;
    }
    return true;
  };
  let expected;
  try{expected=new URL(request.url);}catch{return {error:'Invalid Dialogue URL.'};}
  if(location.protocol!=='https:'||!['coursera.org','www.coursera.org'].includes(location.hostname)||
     (location.port&&location.port!=='443')||location.username||location.password||
     location.pathname.replace(/\/$/,'')!==expected.pathname.replace(/\/$/,'')||
     !/^\/learn\/[A-Za-z0-9_-]+\/coach\/[A-Za-z0-9_-]+\/[A-Za-z0-9_-]+$/.test(location.pathname.replace(/\/$/,'')))
    return {error:'Wrong Dialogue page; no action taken.',url:location.href};
  const root=document.querySelector('[data-testid="dialogue-content"]');
  if(!root) return {error:'The Dialogue content is not ready.',url:location.href,ready:document.readyState};
  const buttons=(scope,label)=>[...scope.querySelectorAll('button,[role="button"]')].filter(visible)
    .filter(e=>clean(e.innerText||e.getAttribute('aria-label')).toLowerCase()===label.toLowerCase());
  const enabled=e=>!e.disabled&&e.getAttribute('aria-disabled')!=='true';
  const starts=buttons(root,'Start Dialogue'),ends=buttons(root,'End Dialogue');
  const dialogs=[...document.querySelectorAll('[role="dialog"],[aria-modal="true"]')].filter(visible);
  const courseDrawers=[...document.querySelectorAll(
    '[role="dialog"][aria-modal="true"][data-testid="drawer-toggle-drawer"]')].filter(visible);
  const courseDrawer=courseDrawers.length===1?courseDrawers[0]:null;
  const courseDrawerClose=courseDrawer?[...courseDrawer.querySelectorAll('button,[role="button"]')]
    .filter(visible).filter(e=>clean(e.getAttribute('aria-label'))==='Close'&&enabled(e)):[];
  const modal=dialogs.length===1?dialogs[0]:null;
  const modalText=modal?clean(modal.innerText):'';
  const modalButtons=modal?[...modal.querySelectorAll('button,[role="button"]')].filter(visible):[];
  const modalLabels=modalButtons.filter(enabled).map(e=>clean(e.innerText||e.getAttribute('aria-label')));
  const sessionPrefix='Would you like to end this session? Please note that you will not be able to resume it, but you can always start a new one from the beginning. Reason for ending (Optional) We would appreciate your feedback to help us improve the experience. ';
  // CDS portals the listbox outside its modal and temporarily marks the
  // painted modal aria-hidden. Keep its exact identity separately from the
  // accessibility visibility test used for ordinary controls.
  const sessionModals=[...document.querySelectorAll('[role="dialog"][aria-modal="true"][data-testid="scroll-container"]')]
    .filter(e=>clean(e.innerText).startsWith(sessionPrefix));
  const sessionModal=sessionModals.length===1?sessionModals[0]:null;
  const reasonControls=sessionModal?[...sessionModal.querySelectorAll(
    '[data-testid="end-dialogue-reason-select-field"] [role="button"][aria-haspopup="listbox"]')]:[];
  const reasonControl=reasonControls.length===1?reasonControls[0]:null;
  const listboxes=[...document.querySelectorAll('[role="listbox"]')].filter(visible);
  const mobileMenus=[...document.querySelectorAll('ul[role="menu"][aria-label="Reason for ending"]')].filter(visible);
  const reasonMenuKind=listboxes.length===1&&!mobileMenus.length?'listbox':
    mobileMenus.length===1&&!listboxes.length?'mobile_menu':null;
  const reasonMenuOpen=Boolean(sessionModal&&reasonControl&&
    reasonControl.getAttribute('aria-expanded')==='true'&&reasonMenuKind);
  const session=modalText.startsWith(sessionPrefix)&&
    modalLabels.filter(x=>x==='Yes, end the Dialogue').length===1&&
    modalLabels.filter(x=>x==='Cancel').length===1&&
    modalLabels.every(x=>['Close','Select a reason','No reason provided','Yes, end the Dialogue','Cancel'].includes(x));
  const state=()=>({url:location.href,ready:document.readyState,
    visibility_state:document.visibilityState,
    start_count:starts.length,end_count:ends.length,
    course_drawer_count:courseDrawers.length,
    course_drawer_close_count:courseDrawerClose.length,
    course_drawer_text:courseDrawer?clean(courseDrawer.innerText).slice(0,1200):'',
    reason_menu_open:reasonMenuOpen,
    reason_menu_kind:reasonMenuOpen?reasonMenuKind:null,
    reason_expanded:Boolean(sessionModal&&reasonControl&&reasonControl.getAttribute('aria-expanded')==='true'),
    reason_modal_text:reasonMenuOpen?clean(sessionModal.innerText).slice(0,1200):'',
    dialogs:dialogs.map(e=>({text:clean(e.innerText).slice(0,1200),
      buttons:[...e.querySelectorAll('button,[role="button"]')].filter(visible)
        .map(b=>({label:clean(b.innerText||b.getAttribute('aria-label')),disabled:!enabled(b)}))})).slice(0,2)});
  if(!request.action||request.action==='inspect') return state();
  if(request.action==='finish_frozen_end_modal'){
    if(!session||!sessionModal||sessionModal!==modal||modalText!==request.expected_dialog)
      return {error:'The known Dialogue end modal is not frozen.',...state()};
    const containers=[...document.querySelectorAll('.cds-Modal-container')];
    const container=containers.length===1?containers[0]:null;
    const animation=container?.getAnimations().length===1?container.getAnimations()[0]:null;
    const timing=animation?.effect?.getComputedTiming(),frames=animation?.effect?.getKeyframes()||[];
    const before=container?.getBoundingClientRect();
    const from=frames[0]?.transform||'';
    const transition=/^translateY\((\d+(?:\.\d+)?)px\) translateY\(0px\)$/.exec(from);
    if(!container||!container.contains(sessionModal)||container.style.transform!=='none'||
       animation?.playState!=='running'||animation.currentTime!==0||
       !Number.isFinite(timing?.endTime)||timing.endTime<=0||timing.endTime>1000||
       frames.length!==2||!transition||frames[1].transform!=='none'||
       !before||before.top<innerHeight||Math.abs(Number(transition[1])-innerHeight)>2)
      return {error:'The Dialogue end modal is not the known frozen slide-in.',...state()};
    animation.finish();
    const after=container.getBoundingClientRect();
    if(after.top<0||after.bottom>innerHeight+2||animation.playState!=='finished')
      return {error:'The Dialogue end modal did not enter the viewport.',...state()};
    return {recovered:true,previous_y:before.top,current_y:after.top,...state()};
  }
  if(request.action==='drawer_close_target'||request.action==='drawer_close_offscreen'){
    if(!courseDrawer||dialogs.length!==1||dialogs[0]!==courseDrawer||
       courseDrawerClose.length!==1)
      return {error:'The Coursera course drawer changed or Close is ambiguous.',...state()};
    const close=courseDrawerClose[0],rect=close.getBoundingClientRect();
    const x=rect.left+rect.width/2,y=rect.top+rect.height/2;
    const offscreen=!(rect.width>0&&rect.height>0&&Number.isFinite(x)&&Number.isFinite(y)&&
      x>=0&&y>=0&&x<innerWidth&&y<innerHeight);
    if(request.action==='drawer_close_offscreen'){
      if(!offscreen) return {error:'The Coursera course drawer Close is onscreen.',...state()};
      // Coursera can leave the portal translated below the viewport with the
      // page locked. Scrolling does not move this node; the same exact Close
      // control used by the quiz runner is safe to activate via DOM click.
      close.click();
      return {clicked:'Close',strategy:'offscreen_dom',...state()};
    }
    if(offscreen) return {error:'The Coursera course drawer Close is offscreen.',...state()};
    const hit=document.elementFromPoint(x,y);
    if(!(hit&&(hit===close||close.contains(hit))))
      return {error:'The Coursera course drawer Close is obstructed.',...state()};
    return {drawer_close_target:{x,y},...state()};
  }
  if(request.action==='reason_target'){
    if(!session||!sessionModal||sessionModal!==modal||modalText!==request.expected_dialog||
       !reasonControl||!visible(reasonControl)||!enabled(reasonControl)||
       clean(reasonControl.innerText)!=='Select a reason'||
       reasonControl.getAttribute('aria-expanded')!=='false'||listboxes.length)
      return {error:'The Dialogue reason selector changed.',...state()};
    const rect=reasonControl.getBoundingClientRect(),x=rect.left+rect.width/2,y=rect.top+rect.height/2;
    const hit=document.elementFromPoint(x,y);
    if(!(rect.width>0&&rect.height>0&&Number.isFinite(x)&&Number.isFinite(y)&&
         x>=0&&y>=0&&x<innerWidth&&y<innerHeight&&
         hit&&(hit===reasonControl||reasonControl.contains(hit))))
      return {error:'The Dialogue reason selector is not unobstructed.',...state()};
    return {reason_target:{x,y},...state()};
  }
  if(request.action==='activate_reason_hidden'){
    if(!session||!sessionModal||sessionModal!==modal||modalText!==request.expected_dialog||
       document.visibilityState!=='hidden'||!reasonControl||!visible(reasonControl)||
       !enabled(reasonControl)||clean(reasonControl.innerText)!=='Select a reason'||
       reasonControl.getAttribute('aria-expanded')!=='false'||listboxes.length||
       !/^Select a reason\s*\u200b?\s*Yes, end the Dialogue Cancel$/i.test(
         modalText.slice(sessionPrefix.length)))
      return {error:'The hidden Dialogue reason selector changed.',...state()};
    const rect=reasonControl.getBoundingClientRect(),x=rect.left+rect.width/2,y=rect.top+rect.height/2;
    if(!(rect.width>0&&rect.height>0&&Number.isFinite(x)&&Number.isFinite(y)))
      return {error:'The hidden Dialogue reason selector has no stable bounds.',...state()};
    reasonControl.dispatchEvent(new MouseEvent('mousedown',{
      bubbles:true,cancelable:true,composed:true,button:0,buttons:1,clientX:x,clientY:y}));
    return {dispatched:'mousedown',...state()};
  }
  if(request.action==='choose_no_reason'){
    if(!reasonMenuOpen||clean(sessionModal.innerText)!==request.expected_dialog||
       clean(reasonControl.innerText)!=='Select a reason')
      return {error:'The Dialogue reason selector changed.',...state()};
    const portal=reasonMenuKind==='listbox'?listboxes[0]:mobileMenus[0];
    const choices=[...portal.querySelectorAll('[role="option"]')].filter(visible)
      .filter(e=>clean(e.innerText)==='No reason provided'&&
        e.getAttribute('aria-disabled')!=='true'&&
        (reasonMenuKind==='mobile_menu'||e.getAttribute('data-value')==='DEFAULT'));
    if(choices.length!==1||!enabled(choices[0]))
      return {error:'No reason provided option is not visible.',...state()};
    choices[0].click();return {clicked:'No reason provided',...state()};
  }
  if(request.action==='confirm_end'){
    if(!modal||modalText!==request.expected_dialog)
      return {error:'The Dialogue confirmation changed.',...state()};
    const selected=session&&
      /^No reason provided\s*\u200b?\s*Yes, end the Dialogue Cancel$/i.test(modalText.slice(sessionPrefix.length))&&
      modalLabels.filter(x=>x==='No reason provided').length===1;
    const generic=modalText==='Are you sure you want to End Dialogue?'&&
      modalLabels.length===2&&modalLabels.includes('End Dialogue')&&modalLabels.includes('Cancel');
    if(!selected&&!generic)
      return {error:'The Dialogue confirmation is not ready.',...state()};
    const affirm=buttons(modal,selected?'Yes, end the Dialogue':'End Dialogue').filter(enabled);
    if(affirm.length!==1) return {error:'The Dialogue confirmation button changed.',...state()};
    affirm[0].click();return {clicked:'Confirm End Dialogue',...state()};
  }
  if(dialogs.length) return {error:'A Dialogue dialog is open; the requested action was not applied.',...state()};
  if(request.action==='start'){
    if(starts.length!==1||ends.length||!enabled(starts[0]))
      return {error:'The Start Dialogue control is missing or ambiguous.',...state()};
    starts[0].click();return {clicked:'Start Dialogue',...state()};
  }
  if(request.action==='end'){
    if(ends.length!==1||starts.length||!enabled(ends[0]))
      return {error:'The End Dialogue control changed; nothing was clicked.',...state()};
    ends[0].click();return {clicked:'End Dialogue',...state()};
  }
  return {error:'Unsupported Dialogue action.',...state()};
})"""


def _lesson_url(item):
    url = item.get("url", "")
    try:
        parsed = urlsplit(url)
        valid = (parsed.scheme == "https" and parsed.hostname in {"coursera.org", "www.coursera.org"}
                 and parsed.port in {None, 443} and not parsed.username and not parsed.password
                 and not parsed.query and not parsed.fragment and
                 re.fullmatch(r"/learn/[A-Za-z0-9_-]+/coach/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+/?", parsed.path))
    except (TypeError, ValueError):
        valid = False
    if not valid or item.get("key") != "coach:" + parsed.path.rstrip("/").split("/")[4]:
        raise engine.StopRun("The Dialogue item URL or identity is invalid; no browser action was sent.")
    return url


async def _ui(tab, url, action="inspect", **fields):
    result = await tab.evaluate(DIALOGUE_JS + "(" + json.dumps({"url": url, "action": action, **fields},
                                                        ensure_ascii=False) + ")")
    if not isinstance(result, dict):
        raise engine.StopRun("The Dialogue page returned an unreadable state.")
    return result


def _modal_kind(dialog):
    """Classify only the observed Coursera modal and one older simple variant."""
    if not isinstance(dialog, dict):
        return None
    text, buttons = dialog.get("text"), dialog.get("buttons")
    if not isinstance(text, str) or not isinstance(buttons, list):
        return None
    labels = [b.get("label") for b in buttons if isinstance(b, dict) and not b.get("disabled")]
    if text == "Are you sure you want to End Dialogue?" and sorted(labels) == ["Cancel", "End Dialogue"]:
        return "confirm"
    if not text.startswith(SESSION_PREFIX):
        return None
    if labels.count("Yes, end the Dialogue") != 1 or labels.count("Cancel") != 1:
        return None
    # Once expanded, Coursera may render the menu choices as buttons inside
    # the modal. Only the exact No reason provided option may be clicked, and
    # the eventual confirmation is checked again after the menu closes.
    if labels.count("Select a reason") == 1:
        return "choose_reason"
    if any(label not in {"Close", "No reason provided",
                         "Yes, end the Dialogue", "Cancel"} for label in labels):
        return None
    if labels.count("No reason provided") == 1 and re.fullmatch(
            r"No reason provided\s*\u200b?\s*Yes, end the Dialogue Cancel",
            text[len(SESSION_PREFIX):], flags=re.I):
        return "confirm"
    return None


def _dialogue_ledger(runner, item):
    checkpoint = getattr(runner, "checkpoint", None)
    if not isinstance(checkpoint, dict):
        return None
    return checkpoint.setdefault("dialogues", {}).setdefault(item["key"], {})


def _record_phase(runner, item, ledger, phase):
    if ledger is not None:
        ledger["phase"] = phase
    # QueueRunner.event persists the checkpoint before an irreversible click.
    runner.event("dialogue_" + phase, key=item["key"])


async def _trusted_click(tab, point):
    if not (isinstance(point, dict) and all(type(point.get(k)) in {int, float}
                                            and 0 <= point[k] <= 10000 for k in ("x", "y"))):
        raise engine.StopRun("A Coursera control moved before its trusted click.")
    for kind in ("mousePressed", "mouseReleased"):
        await tab.call("Input.dispatchMouseEvent", {
            "type": kind, "x": point["x"], "y": point["y"],
            "button": "left", "clickCount": 1})


async def _inspect_lesson(runner, item, url, drawer):
    state = await _ui(runner.tab, url)
    if state.get("visibility_state") == "hidden" and not drawer["front_attempted"]:
        # The fixed Coursera target can be a background Chrome tab. React may
        # freeze its modal entrance and omit its portaled listbox until the
        # target is foregrounded. This selects only the already bound tab.
        await runner.tab.call("Page.bringToFront", {})
        drawer["front_attempted"] = True
        runner.event("dialogue_tab_brought_to_front", key=item["key"])
        return {**state, "visibility_refresh_pending": True}
    count = state.get("course_drawer_count", 0)
    if count == 0:
        if drawer["clicked"] and not drawer["cleared"]:
            drawer["cleared"] = True
            runner.event("dialogue_course_drawer_closed", key=item["key"])
        return state
    if count != 1 or state.get("course_drawer_close_count") != 1:
        raise engine.StopRun("The Coursera course sidebar drawer has no unique Close control.")
    if drawer["clicked"]:
        drawer["checks"] += 1
        if drawer["checks"] > 10 or time.monotonic() - drawer["at"] > 12:
            raise engine.StopRun("The Coursera course sidebar remained open after Close was clicked.")
        return state
    target = await _ui(runner.tab, url, "drawer_close_target")
    if not isinstance(target.get("drawer_close_target"), dict):
        if target.get("error") == "The Coursera course drawer Close is offscreen.":
            closed = await _ui(runner.tab, url, "drawer_close_offscreen")
            if closed.get("clicked") != "Close" or closed.get("strategy") != "offscreen_dom":
                raise engine.StopRun("The offscreen Coursera course sidebar Close changed before click: " +
                                     str(closed.get("error", "unknown state")))
            drawer.update(clicked=True, at=time.monotonic())
            runner.event("dialogue_course_drawer_close_clicked", key=item["key"], strategy="offscreen_dom")
            return state
        raise engine.StopRun("The Coursera course sidebar changed before Close: " +
                             str(target.get("error", "unknown state")))
    await _trusted_click(runner.tab, target["drawer_close_target"])
    drawer.update(clicked=True, at=time.monotonic())
    runner.event("dialogue_course_drawer_close_clicked", key=item["key"])
    return state


async def _wait_for_active(runner, item, url, deadline, drawer):
    until = min(deadline, time.monotonic() + 90)
    while time.monotonic() < until:
        state = await _inspect_lesson(runner, item, url, drawer)
        if state.get("visibility_refresh_pending"):
            await asyncio.sleep(.75)
            continue
        if state.get("error") and state["error"] != "The Dialogue content is not ready.":
            raise engine.StopRun("Cannot inspect the started Dialogue: " + state["error"])
        if state.get("course_drawer_count"):
            await asyncio.sleep(1)
            continue
        if state.get("end_count") == 1 and state.get("start_count") == 0:
            return state
        await asyncio.sleep(1.25)
    raise engine.StopRun("Start Dialogue did not expose End Dialogue within 90 seconds.")


async def _settle_pending_end(runner, item, url, deadline, ledger, drawer):
    """Resolve a previous End click without ever repeating End or confirmation."""
    phase = ledger.get("phase", "end_requested") if ledger is not None else "end_requested"
    until = min(deadline, time.monotonic() + 90)
    reason_opened = False
    recovery_checked = False
    expanded_checks = 0
    outline_attempted = False
    for _ in range(55):
        if time.monotonic() >= until:
            break
        if await runner.verified_item(item):
            _record_phase(runner, item, ledger, "completed")
            return
        current = await _inspect_lesson(runner, item, url, drawer)
        if current.get("visibility_refresh_pending"):
            await asyncio.sleep(.75)
            continue
        if current.get("error") and current["error"] != "The Dialogue content is not ready.":
            raise engine.StopRun("Cannot verify the ending Dialogue: " + current["error"])
        if current.get("course_drawer_count"):
            await asyncio.sleep(1)
            continue
        if (phase == "confirmation_requested" and not outline_attempted and
                not current.get("reason_menu_open") and not current.get("dialogs")):
            # On narrow screens Coursera collapses the course outline after
            # the final confirmation. The green link then has no client rect,
            # so PAGE_JS cannot verify it until the exact outline toggle opens.
            # The course-configured script pins the active slug and lesson key.
            outline_attempted = True
            outline = await runner.tab.evaluate(
                engine.LESSON_OUTLINE_JS + "(" + json.dumps(item["key"]) + ")")
            if outline.get("error"):
                raise engine.StopRun("Cannot reopen the Dialogue course outline: " +
                                     str(outline["error"]))
            if outline.get("clicked"):
                runner.event("dialogue_course_outline_opened", key=item["key"])
                await asyncio.sleep(.75)
                continue
        if current.get("reason_menu_open"):
            if phase == "confirmation_requested":
                await asyncio.sleep(1.5)
                continue
            choice = await _ui(runner.tab, url, "choose_no_reason",
                               expected_dialog=current.get("reason_modal_text"))
            if choice.get("clicked") == "No reason provided":
                reason_opened = False
                runner.event("dialogue_no_reason_selected", key=item["key"])
            elif choice.get("error") != "No reason provided option is not visible.":
                raise engine.StopRun("The Dialogue reason option changed before selection.")
            await asyncio.sleep(.75)
            continue
        dialogs = current.get("dialogs") or []
        if dialogs:
            if phase == "confirmation_requested":
                # The prior confirmation outcome is uncertain; never submit twice.
                await asyncio.sleep(1.5)
                continue
            if len(dialogs) != 1:
                raise engine.StopRun("Multiple Dialogue dialogs are open; no confirmation was clicked.")
            dialog = dialogs[0]
            kind = _modal_kind(dialog)
            if kind in {"choose_reason", "confirm"} and not recovery_checked:
                recovery_checked = True
                recovery = await _ui(runner.tab, url, "finish_frozen_end_modal",
                                     expected_dialog=dialog["text"])
                if recovery.get("recovered"):
                    runner.event("dialogue_end_modal_recovered", key=item["key"])
                    await asyncio.sleep(.75)
                    continue
                if recovery.get("error") not in {
                        "The known Dialogue end modal is not frozen.",
                        "The Dialogue end modal is not the known frozen slide-in."}:
                    raise engine.StopRun("The Dialogue end modal recovery was uncertain: " +
                                         str(recovery.get("error", "unknown state")))
            if kind == "choose_reason":
                if current.get("reason_expanded"):
                    expanded_checks += 1
                    if expanded_checks > 10:
                        raise engine.StopRun("The Dialogue reason selector expanded but its listbox did not appear.")
                    await asyncio.sleep(1)
                    continue
                if not reason_opened:
                    if current.get("visibility_state") == "hidden":
                        result = await _ui(runner.tab, url, "activate_reason_hidden",
                                           expected_dialog=dialog["text"])
                        if result.get("dispatched") != "mousedown":
                            raise engine.StopRun("The hidden Dialogue reason selector changed before opening.")
                    else:
                        target = await _ui(runner.tab, url, "reason_target", expected_dialog=dialog["text"])
                        point = target.get("reason_target")
                        if not isinstance(point, dict):
                            raise engine.StopRun("The Dialogue reason selector changed before opening.")
                        # CDS opens on mouseDown. When Chrome reports the tab
                        # visible, a trusted CDP press is accepted.
                        await _trusted_click(runner.tab, point)
                    reason_opened = True
                    runner.event("dialogue_reason_menu_opened", key=item["key"])
                await asyncio.sleep(.75)
                continue
            if kind != "confirm":
                raise engine.StopRun("End Dialogue opened an unfamiliar confirmation dialog. Review it manually: " +
                                     str(dialog.get("text", ""))[:300])
            _record_phase(runner, item, ledger, "confirmation_requested")
            confirmed = await _ui(runner.tab, url, "confirm_end", expected_dialog=dialog["text"])
            if confirmed.get("clicked") != "Confirm End Dialogue":
                if confirmed.get("error"):
                    _record_phase(runner, item, ledger, "end_requested")
                raise engine.StopRun("The Dialogue confirmation changed before it could be clicked.")
            phase = "confirmation_requested"
            runner.event("dialogue_end_confirmed", key=item["key"])
        await asyncio.sleep(1.5)
    raise engine.StopRun("End Dialogue may have been submitted, but Coursera did not show the completed green mark. No final action was retried.")


async def run_dialogue(runner, item, *, max_seconds=MAX_SECONDS):
    """End one Coach lesson and advance only after a verified green mark."""
    url = _lesson_url(item)
    if await runner.verified_item(item):
        return
    if max_seconds <= 0:
        raise ValueError("Invalid Dialogue work limit")
    deadline = time.monotonic() + max_seconds
    ledger = _dialogue_ledger(runner, item)
    drawer = {"clicked": False, "cleared": False, "checks": 0, "at": 0.0,
              "front_attempted": False}
    if ledger is not None and ledger.get("phase") in {"end_requested", "confirmation_requested"}:
        await _settle_pending_end(runner, item, url, deadline, ledger, drawer)
        return
    state = {}
    for _ in range(72):
        state = await _inspect_lesson(runner, item, url, drawer)
        if state.get("visibility_refresh_pending"):
            await asyncio.sleep(.75)
            continue
        error = state.get("error")
        if error and error != "The Dialogue content is not ready.":
            raise engine.StopRun("The Dialogue did not load: " + error)
        if state.get("course_drawer_count"):
            await asyncio.sleep(1)
            continue
        if (not error and state.get("ready") in {"interactive", "complete"} and
                (state.get("start_count") == 1 or state.get("end_count") == 1)):
            break
        await asyncio.sleep(1.25)
    else:
        raise engine.StopRun("The Dialogue did not show Start or End Dialogue within 90 seconds.")
    if state.get("start_count") == 1 and state.get("end_count") == 1:
        raise engine.StopRun("Both Start and End Dialogue are visible; the lesson state is ambiguous.")
    if state.get("start_count") == 1:
        started = await _ui(runner.tab, url, "start")
        if started.get("clicked") != "Start Dialogue":
            raise engine.StopRun("Start Dialogue was not clicked: " + str(started.get("error", "unknown state")))
        runner.event("dialogue_started", key=item["key"])
        state = await _wait_for_active(runner, item, url, deadline, drawer)
    if state.get("end_count") != 1:
        raise engine.StopRun("The active Dialogue has no unambiguous End Dialogue control.")
    _record_phase(runner, item, ledger, "end_requested")
    result = await _ui(runner.tab, url, "end")
    if result.get("clicked") != "End Dialogue":
        if result.get("error"):
            _record_phase(runner, item, ledger, "ready")
        raise engine.StopRun("End Dialogue was not clicked: " + str(result.get("error", "unknown state")))
    runner.event("dialogue_end_clicked", key=item["key"])
    await _settle_pending_end(runner, item, url, deadline, ledger, drawer)
