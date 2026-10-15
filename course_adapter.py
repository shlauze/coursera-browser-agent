"""Bounded discovery from visible Coursera pages, independent of model answers.

The caller configures coursera_start for course_url before using these helpers.
Discovery never treats missing, hidden or unsupported material as completion.
"""

import asyncio
import importlib
import json
import re
from urllib.parse import urlsplit


PROBES = 20
POLL_SECONDS = 0.75


def _engine():
    return importlib.import_module("coursera_start")


def _fail(message, diagnostic=None):
    error = _engine().StopRun(message)
    if diagnostic is not None:
        error.diagnostic = diagnostic
    raise error


def _course(course_url):
    try:
        if not isinstance(course_url, str):
            raise ValueError("Not a URL string")
        parsed = urlsplit(course_url)
        port = parsed.port
    except (TypeError, ValueError):
        _fail("The configured course URL is invalid.")
    if (parsed.scheme != "https" or parsed.hostname not in {"coursera.org", "www.coursera.org"}
            or parsed.username or parsed.password or port not in {None, 443}
            or not re.fullmatch(r"/learn/[a-zA-Z0-9][a-zA-Z0-9_-]*/?", parsed.path)
            or parsed.query or parsed.fragment):
        _fail("Use a plain HTTPS Coursera course URL: https://www.coursera.org/learn/course-slug")
    return parsed.path.rstrip("/")


def _numbers(value, label):
    if (not isinstance(value, (list, tuple)) or not value or
            any(type(number) is not int or number < 1 or number > 1000 for number in value)):
        _fail(label + " must be a nonempty list of module numbers.")
    numbers = sorted(set(value))
    if len(numbers) != len(value) or numbers != list(range(1, numbers[-1] + 1)):
        _fail(label + " must contain every module from 1 without gaps or duplicates.")
    return numbers


_COMMON_JS = r"""
  const clean=x=>(x || '').replace(/\s+/g,' ').trim();
  const visible=e=>{
    if (!e || !e.getClientRects().length) return false;
    for(let p=e;p;p=p.parentElement){
      const style=getComputedStyle(p);
      if(p.hidden || p.inert || p.getAttribute('aria-hidden')==='true' ||
         style.display==='none' || ['hidden','collapse'].includes(style.visibility) || style.opacity==='0') return false;
    }
    return true;
  };
  const sameCourse=u=>u.protocol==='https:' && ['coursera.org','www.coursera.org'].includes(u.hostname) &&
    !u.username && !u.password && (!u.port || u.port==='443') &&
    (u.pathname===request.course_path || u.pathname.startsWith(request.course_path+'/'));
  if(!sameCourse(new URL(location.href))) return {blocked:true,url:location.href,error:'The page left the configured course.'};
  const moduleEvidence=()=>{
    const found=[];
    for(const e of document.querySelectorAll('a[href],button,[role="button"]')){
      // The dedicated course navigation may be collapsed for this viewport.
      // Its DOM links still contain the full module outline. Other hidden
      // elements remain excluded because they may be stale page content.
      const inCourseNavigation=!!e.closest('[data-e2e="courseNavigation"],nav[aria-label="Course navigation"]');
      if(!visible(e) && !inCourseNavigation) continue;
      if(e.matches('a[href]')){
        let u;try{u=new URL(e.href,location.href);}catch{continue;}
        const match=sameCourse(u) && u.pathname.slice(request.course_path.length).match(/^\/home\/module\/([1-9]\d*)\/?$/);
        // The page's "Skip to main content" link points at the current
        // module. It appears before React renders the actual course outline.
        // A URL alone therefore cannot prove that an entire one-module
        // outline has loaded.
        const label=clean(e.innerText || e.getAttribute('aria-label'));
        if(match && Number(match[1])<=1000 &&
           new RegExp('^Module\\s+'+match[1]+'(?:\\s|[,.:]|$)','i').test(label))
          found.push({number:Number(match[1]),source:'link',label,url:u.origin+u.pathname});
      }else{
        const label=clean(e.innerText || e.getAttribute('aria-label'));
        const match=label.match(/^Module\s+([1-9]\d*)(?:\s|:|$)/i);
        if(match && Number(match[1])<=1000) found.push({number:Number(match[1]),source:'button',label});
      }
    }
    return found;
  };
  const moduleNumbers=found=>[...new Set(found.map(x=>x.number))].sort((a,b)=>a-b);
  const mainRoots=()=>[...new Set(document.querySelectorAll('main,[role="main"]'))].filter(visible)
    .filter(e=>![...document.querySelectorAll('main,[role="main"]')].some(other=>other!==e && visible(other) && other.contains(e)));
"""


MODULES_JS = "(request=>{" + _COMMON_JS + r"""
  const evidence=moduleEvidence();
  const outline=[...document.querySelectorAll('[data-testid="rc-WeekNavigationItem"]')];
  return {url:location.href,ready:document.readyState,modules:moduleNumbers(evidence),evidence,
    outline_count:outline.length};
})"""


# The lesson outline on assignment/reading pages keeps the contents of collapsed
# module accordions in the DOM. On the observed Coursera layout, a completed
# lesson has both the accessibility token "Completed" and a green success icon.
# Looking only at the icon is unsafe: other unrelated icons appear in the root.
SIDEBAR_JS = "(request=>{" + _COMMON_JS + r"""
  const headings=[...document.querySelectorAll('[data-testid="module-number-heading"]')].filter(visible);
  const modules=[];
  for(const heading of headings){
    const match=clean(heading.innerText).match(/^Module\s+([1-9]\d*)$/i);
    if(!match) continue;
    const number=Number(match[1]);
    const root=heading.closest('.cds-AccordionRoot-container');
    if(!root || number>1000) continue;
    const items=[];
    for(const a of root.querySelectorAll('a[href]')){
      let u;try{u=new URL(a.href,location.href);}catch{continue;}
      if(!sameCourse(u)) continue;
      const parts=u.pathname.slice(request.course_path.length).split('/').filter(Boolean);
      if(parts.length<2 || !['supplement','lecture','assignment-submission','exam','quiz','team','peer','ungradedLab','coach','programming'].includes(parts[0])) continue;
      const label=clean(a.getAttribute('aria-label'));
      const tokens=label.toLowerCase().split(',').map(x=>x.trim());
      const negative=tokens.some(x=>/\b(?:failed|not passed|not completed|incomplete|not submitted|in progress|not started)\b/.test(x));
      const checked=a.querySelectorAll('[data-testid="learn-item-success-icon"]').length===1;
      const key=(['assignment-submission','exam','quiz','team'].includes(parts[0])?'assignment':parts[0])+':'+parts[1];
      items.push({key,url:u.origin+u.pathname.replace(/\/$/,''),title:label.slice(0,600),
        label,checked,completed:!negative && checked && tokens.some(x=>/(?:^|•\s*)(?:completed|passed)\b/.test(x))});
    }
    modules.push({number,items});
  }
  return {url:location.href,ready:document.readyState,modules};
})"""


async def inspect_sidebar_progress(tab, course_url):
    """Read one course outline without navigation; return None when unavailable.

    A stable outline is required, and the caller must compare item identities
    with configured counts or a previously discovered full roster before skip.
    """
    path = _course(course_url)
    last = None
    for _ in range(5):
        result = await tab.evaluate(SIDEBAR_JS + "(" + json.dumps({"course_path":path}) + ")")
        if not isinstance(result, dict):
            _fail("The course sidebar returned an unreadable page result.")
        if result.get("blocked"):
            return None
        rows = result.get("modules", [])
        if result.get("ready") == "complete" and rows:
            numbers = [row.get("number") for row in rows]
            try:
                _numbers(numbers, "Sidebar modules")
            except _engine().StopRun:
                return None
            signature = tuple((row["number"], tuple((item.get("key"), item.get("completed"))
                                              for item in row.get("items", []))) for row in rows)
            if signature == last:
                return {"url":result.get("url"), "modules":numbers,
                        "rows":{row["number"]:row for row in rows}}
            last = signature
        elif result.get("ready") == "complete":
            return None
        await asyncio.sleep(POLL_SECONDS)
    return None


def verified_sidebar_module(sidebar, number, expected_count=None, expected_roster=None):
    """Return a full green-check roster only when its identity is trustworthy."""
    if not sidebar or number not in sidebar.get("rows", {}):
        return None
    items = sidebar["rows"][number].get("items", [])
    keys = [item.get("key") for item in items]
    if not items or len(keys) != len(set(keys)) or not all(keys):
        return None
    if expected_count is None and expected_roster is None:
        return None
    if expected_count is not None and len(items) != expected_count:
        return None
    if expected_roster is not None:
        prior_keys = {item.get("key") for item in expected_roster}
        if len(prior_keys) != len(expected_roster) or set(keys) != prior_keys:
            return None
    if not all(item.get("completed") and item.get("checked") for item in items):
        return None
    return items


ROSTER_JS = "(request=>{" + _COMMON_JS + r"""
  if(location.pathname.replace(/\/$/,'')!==request.course_path+'/home/module/'+request.number)
    return {error:'The requested module page has not loaded.',error_code:'wrong_path',url:location.href};
  const mains=mainRoots();
  if(mains.length!==1) return {error:'Expected one visible main course area; this layout needs review.',url:location.href};
  const main=mains[0];
  // The observed module's actual material list is nav[aria-label="Lessons"].
  // Upcoming-deadline cards share main but are outside this navigation region.
  const lessonNavigations=Array.from(main.querySelectorAll('nav,[role="navigation"]')).filter(e=>
    visible(e) && /^Lessons$/i.test(clean(e.getAttribute('aria-label'))) &&
    !e.closest('aside') && !e.parentElement?.closest('nav,[role="navigation"]'));
  if(lessonNavigations.length>1) return {error:'Multiple visible lesson navigation regions need review.',url:location.href,
    scope:'ambiguous_lesson_navigation',lesson_navigation_count:lessonNavigations.length};
  const lessonNavigation=lessonNavigations[0] || null;
  // During hydration, main may briefly show course-timeline cards for other
  // modules before the real Lessons list appears. Never accept those cards as
  // this module's roster, even if they remain stable over several polls.
  if(!lessonNavigation) return {error:'The Lessons navigation has not loaded.',
    error_code:'lessons_not_ready',url:location.href,ready:document.readyState,
    scope:'main',lesson_navigation_count:0,items:[]};
  const listRoot=lessonNavigation;
  const inList=e=>visible(e) && lessonNavigation.contains(e) &&
    !e.closest('aside') && e.closest('nav,[role="navigation"]')===lessonNavigation;
  const scope='lesson_navigation';
  // Coursera gives lesson groups course-specific names such as "Course Overview"
  // and "Introduction to Explainable AI". Expand every collapsed group control
  // in this one Lessons navigation, while excluding controls inside lesson links.
  const collapsed=Array.from(listRoot.querySelectorAll('button[aria-expanded="false"],[role="button"][aria-expanded="false"]'))
    .filter(inList).filter(e=>!e.closest('a[href]') && clean(e.innerText || e.getAttribute('aria-label')));
  if(request.action==='expand'){
    const clicked=[];
    for(const e of collapsed){
      if(!e.disabled && e.getAttribute('aria-disabled')!=='true'){
        const label=clean(e.innerText || e.getAttribute('aria-label'));e.click();clicked.push(label);
      }
    }
    return {url:location.href,clicked,scope};
  }
  const excluded=new Set(['home','info','grades','discussions','announcements','resources','calendar','course-inbox','updates','people']);
  const items=[], warnings=[];
  for(const a of listRoot.querySelectorAll('a[href]')){
    if(!inList(a)) continue;
    let u;try{u=new URL(a.href,location.href);}catch{continue;}
    if(!sameCourse(u)) continue;
    const parts=u.pathname.slice(request.course_path.length).split('/').filter(Boolean);
    if(parts.length<2 || excluded.has(parts[0])) continue;
    const labels=[a.getAttribute('aria-label') || '',...Array.from(a.querySelectorAll('[aria-label]')).map(e=>e.getAttribute('aria-label') || '')];
    const title=clean(a.innerText || a.getAttribute('aria-label'));
    if(!title){warnings.push('A visible lesson link has no readable title: '+u.pathname);continue;}
    if(a.getAttribute('aria-disabled')==='true') warnings.push('A lesson link is disabled: '+title);
    items.push({url:u.origin+u.pathname.replace(/\/$/,''),type:parts[0],identity:parts[1],title:title.slice(0,600),
      aria_label:labels[0],evidence:labels.filter(Boolean).map(x=>'aria-label: '+x)});
  }
  const locked=Array.from(listRoot.querySelectorAll('[aria-label]')).filter(inList)
    .map(e=>clean(e.getAttribute('aria-label'))).filter(text=>/\b(?:locked|unavailable)\b/i.test(text));
  return {url:location.href,ready:document.readyState,items,scope,lesson_navigation_count:lessonNavigations.length,
    collapsed:collapsed.length,warnings:[...new Set([...warnings,...locked])]};
})"""


COMPLETION_JS = "(request=>{" + _COMMON_JS + r"""
  if(location.pathname.replace(/\/$/,'')!==request.course_path+'/home')
    return {error:'The course home page has not loaded.',url:location.href};
  const mains=mainRoots();
  if(mains.length!==1) return {error:'Expected one visible main course area to check completion.',url:location.href};
  const lines=(mains[0].innerText || '').split('\n').map(clean).filter(Boolean);
  const wholeCourse=/^(?:Congratulations[!,]?\s*)?(?:You(?:'ve|’ve| have)? (?:successfully )?completed (?:this|the) course[.!]?|(?:This|The) course (?:is )?(?:now )?complete(?:d)?[.!]?|Course completed[.!]?)$/i;
  const evidence=lines.filter(line=>wholeCourse.test(line));
  const congratulations=lines.filter(line=>/^Congratulations\b/i.test(line)).slice(0,8);
  const observed=moduleEvidence();
  return {url:location.href,ready:document.readyState,modules:moduleNumbers(observed),module_evidence:observed,evidence,congratulations};
})"""


async def _read(tab, expression, request):
    result = await tab.evaluate(expression + "(" + json.dumps(request, ensure_ascii=False) + ")")
    if not isinstance(result, dict):
        _fail("Course discovery returned an unreadable page result.")
    if result.get("blocked"):
        _fail(result.get("error", "The selected tab left the configured course."))
    return result


async def discover_modules(tab, course_url, configured_modules=None, minimum_modules=1):
    """Cross-check the complete outline across pages before trusting its size.

    Coursera can report document.readyState == 'complete' before its React
    course navigation appears. A lone current-module link is not a complete
    inventory. The minimum is a durable high-water mark from earlier runs.
    """
    path = _course(course_url)
    configured = _numbers(configured_modules, "Configured modules") if configured_modules is not None else None
    if type(minimum_modules) is not int or not 1 <= minimum_modules <= 1000:
        _fail("The previously observed module count is invalid.")
    base = course_url.rstrip("/")
    targets = [base + "/home/module/1", base + "/home"]
    if minimum_modules > 1:
        targets.append(base + f"/home/module/{minimum_modules}")
    candidates, observed = [], {}
    for target in targets:
        await tab.navigate(target)
        last, stable = None, 0
        for probe in range(PROBES):
            observed = await _read(tab, MODULES_JS, {"course_path":path})
            modules = observed.get("modules", [])
            signature = tuple(modules)
            stable = stable + 1 if signature and signature == last else 1 if signature else 0
            last = signature
            if signature and observed.get("ready") == "complete":
                numbers = _numbers(modules, "Observed modules")
                if configured is not None and numbers == configured and stable >= 2:
                    return numbers
                if configured is None and len(numbers) >= minimum_modules and stable >= 4:
                    # Prefer the largest real outline seen on either page.
                    # A one-module result is probed to the deadline because
                    # later module links may still be hydrating.
                    if len(numbers) > 1 or probe == PROBES - 1:
                        candidates.append((numbers, observed.get("outline_count", 0)))
                        break
            await asyncio.sleep(POLL_SECONDS)
        if (len(candidates) >= 2 and candidates[-1] == candidates[-2] and
                len(candidates[-1][0]) > minimum_modules):
            break
    if configured is None and candidates:
        fullest = max((numbers for numbers, _ in candidates), key=len)
        if len(fullest) > 1 or (len(candidates) >= 2 and
                               all(numbers == [1] for numbers, _ in candidates)):
            return fullest
    _fail("Could not verify the full module list. Observed: " + str(observed.get("modules", [])) +
          "; minimum previously seen: " + str(minimum_modules) +
          "; configured expectation: " + str(configured) + ". Check the visible course outline.")


def _roster_rows(snapshot):
    engine = _engine()
    supported = engine.rows_by_key(snapshot)
    rows = {}
    for raw in snapshot.get("items", []):
        kind = raw.get("type", "")
        canonical = "assignment" if kind in {"assignment-submission", "exam", "quiz", "team"} else kind
        identity = raw.get("identity") or urlsplit(raw.get("url", "")).path.split("/")[4]
        key = canonical + ":" + identity
        row = dict(supported.get(key, raw))
        row.update(key=key, unsupported=canonical not in {"supplement", "lecture", "assignment", "ungradedLab", "coach"} or kind == "team")
        row.setdefault("status", "unknown")
        if row["unsupported"]:
            row["unsupported_reason"] = "No automatic handler for lesson type: " + kind
        elif key not in supported:
            _fail("The runner is not configured for this course, or a supported lesson could not be parsed: " + raw.get("url", ""))
        existing = rows.get(key)
        if existing is None or row["status"] == "not_completed" or existing["status"] == "unknown":
            rows[key] = row
    return list(rows.values())


async def discover_roster(tab, course_url, number, expected_count=None, expected_roster=None):
    """Expand lesson sections and retain every previously discovered lesson."""
    path = _course(course_url)
    if type(number) is not int or not 1 <= number <= 1000:
        _fail("Invalid module number.")
    if expected_count is not None and (type(expected_count) is not int or expected_count < 1):
        _fail("Expected item count must be a positive integer.")
    prior_keys = set()
    if expected_roster:
        if not isinstance(expected_roster, list) or any(not isinstance(item, dict) or
                not isinstance(item.get("key"), str) or not item["key"] for item in expected_roster):
            _fail("The previously discovered module roster is invalid.")
        prior_keys = {item["key"] for item in expected_roster}
        if len(prior_keys) != len(expected_roster):
            _fail("The previously discovered module roster has duplicate lessons.")
    await tab.navigate(course_url.rstrip("/") + f"/home/module/{number}")
    request = {"course_path":path, "number":number}
    last, stable, observed = None, 0, {}
    for _ in range(PROBES):
        observed = await _read(tab, ROSTER_JS, request)
        if observed.get("collapsed"):
            await _read(tab, ROSTER_JS, {**request, "action":"expand"})
            last, stable = None, 0
        elif (not observed.get("error") and observed.get("ready") == "complete" and
              observed.get("scope") == "lesson_navigation"):
            rows = _roster_rows(observed)
            signature = tuple(sorted((row["key"], row["url"], row["title"]) for row in rows))
            stable = stable + 1 if signature and signature == last else 1 if signature else 0
            last = signature
            if (stable >= 3 and not observed.get("warnings") and
                    (expected_count is None or len(rows) == expected_count) and
                    prior_keys.issubset({row["key"] for row in rows})):
                return rows
        else:
            last, stable = None, 0
        await asyncio.sleep(POLL_SECONDS)
    found_keys = {row["key"] for row in _roster_rows(observed)} if observed.get("items") else set()
    missing_keys = sorted(prior_keys - found_keys)
    diagnostic = (observed.get("error") or "; ".join(observed.get("warnings", [])) or
                  ("Previously discovered lessons are missing: " + ", ".join(missing_keys)
                   if missing_keys else "the Lessons navigation was incomplete or unstable"))
    _fail(f"Module {number} roster needs review: {diagnostic}. Observed links: " +
          str(len(observed.get("items", []))) + "; expected items: " + str(expected_count),
          {"module":number, "expected_count":expected_count, "missing_previous_keys":missing_keys,
           "snapshot":observed})


async def verify_course_completion(tab, course_url, module_numbers, verified_rosters=None):
    """Verify the whole outline or an explicit course-completed message."""
    path = _course(course_url)
    expected = _numbers(module_numbers, "Verified module list")
    if verified_rosters and set(verified_rosters) == set(expected):
        sidebar = await inspect_sidebar_progress(tab, course_url)
        if sidebar and sidebar["modules"] == expected and all(
            verified_sidebar_module(sidebar, number, expected_roster=verified_rosters[number])
            for number in expected
        ):
            return {"verified":True,
                    "evidence":["All expected course-outline lessons show Completed and a success icon."],
                    "modules":expected, "method":"sidebar", "url":sidebar.get("url")}
    await tab.navigate(course_url.rstrip("/") + "/home")
    last, stable, observed = None, 0, {}
    for _ in range(PROBES):
        observed = await _read(tab, COMPLETION_JS, {"course_path":path})
        modules = observed.get("modules", [])
        evidence = observed.get("evidence", [])
        signature = (tuple(modules), tuple(evidence))
        stable = stable + 1 if signature == last else 1
        last = signature
        if not observed.get("error") and observed.get("ready") == "complete" and stable >= 2 and modules == expected and evidence:
            return {"verified":True, "evidence":evidence, "modules":modules,
                    "congratulations":observed.get("congratulations", []), "url":observed.get("url")}
        await asyncio.sleep(POLL_SECONDS)
    reason = observed.get("error") or ("The final visible module list differs from the verified course roster."
              if observed.get("modules", []) != expected else "All requested materials were checked, but the site has not shown explicit whole-course completion.")
    return {"verified":False, "evidence":observed.get("evidence", []), "modules":observed.get("modules", []),
            "congratulations":observed.get("congratulations", []), "reason":reason, "url":observed.get("url")}
