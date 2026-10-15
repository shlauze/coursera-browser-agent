"""Observed Coursera My Learning navigation for the course queue.

Only visible course cards with a matching course title link and action are
eligible. The specialization container is never a course. Every navigation is
restricted to the exact Coursera pages used by this workflow.
"""
from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import parse_qs, urlsplit

from course_state import course_identity


MY_LEARNING_URL = "https://www.coursera.org/my-learning?myLearningTab=IN_PROGRESS"
COMPLETED_LEARNING_URL = "https://www.coursera.org/my-learning?myLearningTab=COMPLETED"
MAX_PAGES = 100
POLL_SECONDS = 0.5
PAGE_PROBES = 40


def _site_url(url):
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if (parsed.scheme != "https" or parsed.hostname not in {"www.coursera.org", "coursera.org"}
            or parsed.username or parsed.password or port not in {None, 443}):
        return None
    return parsed


def valid_certificate_url(url):
    """Canonical certificate link from Coursera's completion overlay."""
    parsed = _site_url(url)
    return bool(parsed and not parsed.query and not parsed.fragment and
                re.fullmatch(r"/account/accomplishments/(?:records|verify)/[A-Za-z0-9_-]{4,160}/?", parsed.path))


def course_from_lesson_url(url):
    """Convert an observed course title/lesson link to a validated course root."""
    parsed = _site_url(url)
    if not parsed:
        return None
    match = re.fullmatch(r"/learn/([a-z0-9][a-z0-9-]{0,159})(?:/.*)?", parsed.path)
    if not match:
        return None
    try:
        identity, root = course_identity("https://www.coursera.org/learn/" + match[1])
    except ValueError:
        return None
    return {"id": identity, "url": root}


PAGE_JS = r"""(request=>{
  const clean=x=>(x||'').replace(/\s+/g,' ').trim();
  const visible=e=>{
    if(!e || !e.getClientRects().length) return false;
    const rect=e.getBoundingClientRect();
    if(rect.width<=0 || rect.height<=0) return false;
    for(let p=e;p;p=p.parentElement){
      const style=getComputedStyle(p);
      if(p.hidden || p.inert || p.getAttribute('aria-hidden')==='true' ||
         style.display==='none' || ['hidden','collapse'].includes(style.visibility) || style.opacity==='0') return false;
    }
    return true;
  };
  const originOK=u=>u.protocol==='https:' && ['www.coursera.org','coursera.org'].includes(u.hostname) &&
    !u.username && !u.password && (!u.port || u.port==='443');
  const here=new URL(location.href);
  if(!originOK(here) || here.pathname!=='/my-learning' || here.searchParams.get('myLearningTab')!=='IN_PROGRESS')
    return {error:'Not on Coursera My Learning In Progress.',url:location.href};
  const actionName=e=>clean(e.innerText||e.getAttribute('aria-label'));
  const actionKind=e=>{
    const name=actionName(e);
    if(/^Get started(?:\b|$)/i.test(name)) return 'get_started';
    if(/^Resume(?:\b|$)/i.test(name)) return 'resume';
    if(/^Continue learning(?:\b|$)/i.test(name)) return 'continue_learning';
    return null;
  };
  const actions=[...document.querySelectorAll('[data-track-component="enrolled_course_card_link"],a[aria-label^="Get started"],button[aria-label^="Get started"],a[aria-label^="Resume"],button[aria-label^="Resume"],a[aria-label^="Continue learning"],button[aria-label^="Continue learning"]')]
    .filter(e=>visible(e) && actionKind(e) && e.getAttribute('aria-disabled')!=='true' && !e.disabled);
  const cards=[], elementBySlug=new Map();
  for(const action of actions){
    let node=action, titleLink=null;
    for(let depth=0;node && depth<12;node=node.parentElement,depth++){
      const links=[...node.querySelectorAll('a[href]')].filter(e=>{
        if(!visible(e)) return false;
        try{const u=new URL(e.href,location.href);return originOK(u) && /^\/learn\/[a-z0-9][a-z0-9-]{0,159}\/home\/welcome\/?$/.test(u.pathname);}
        catch{return false;}
      });
      if(links.length===1 && /Course(?:\s+\d+\s+of\s+\d+)?\s*[·•-]/i.test(clean(node.textContent))){
        titleLink=links[0];break;
      }
    }
    if(!titleLink || !node) continue;
    const u=new URL(titleLink.href,location.href);
    const slug=u.pathname.match(/^\/learn\/([a-z0-9][a-z0-9-]{0,159})\/home\/welcome\/?$/)?.[1];
    if(!slug) continue;
    const title=clean(titleLink.innerText||titleLink.getAttribute('aria-label'));
    const status=clean(node.textContent).match(/Course(?:\s+\d+\s+of\s+\d+)?\s*[·•-]\s*(?:Not started|\d+(?:\.\d+)?% complete|In progress|Started|Complete(?:d)?)\b/i)?.[0]||'';
    cards.push({id:slug,url:'https://www.coursera.org/learn/'+slug,title:title.slice(0,300),
      source:status,action:actionKind(action),action_name:actionName(action).slice(0,200),
      title_url:u.origin+u.pathname});
    if(!elementBySlug.has(slug)) elementBySlug.set(slug,action);
  }
  // Responsive copies must not cause the same course to be clicked twice.
  const unique=[], seen=new Set();
  for(const card of cards){if(!seen.has(card.id)){seen.add(card.id);unique.push(card);}}
  const current=[...document.querySelectorAll('button[aria-label],a[aria-label]')]
    .filter(visible).find(e=>/^Page number\s+\d+(?:\s*,.*)?$/i.test(e.getAttribute('aria-label')||''));
  const numberedPage=Number((current?.getAttribute('aria-label')||'').match(/^Page number\s+(\d+)/i)?.[1])||null;
  // Coursera also renders compact pagination as "Page 1 of 6" without numbered buttons.
  // Read only the status inside its pagination widget, never unrelated page text.
  const statusPages=[...document.querySelectorAll('[data-testid="mobile-pagination"] [role="status"]')]
    .filter(visible).map(e=>clean(e.textContent).match(/^Page\s+(\d+)\s+of\s+(\d+)$/i))
    .filter(Boolean).map(m=>({page:Number(m[1]),total:Number(m[2])}))
    .filter(x=>x.page>0 && x.total>=x.page && x.total<=100);
  const statusValues=[...new Set(statusPages.map(x=>x.page+'/'+x.total))];
  const statusPage=statusValues.length===1?statusPages[0].page:null;
  const next=[...document.querySelectorAll('button[aria-label],a[aria-label]')]
    .filter(visible).find(e=>e.getAttribute('aria-label')==='Go to next page');
  const hasNext=!!next && !next.disabled && next.getAttribute('aria-disabled')!=='true';
  const pager=[...document.querySelectorAll('button[aria-label],a[aria-label]')].filter(visible)
    .map(e=>e.getAttribute('aria-label')||'').filter(x=>/^Go to page\s+\d+|^Page number\s+\d+/.test(x));
  const activeTab=[...document.querySelectorAll('[role="tab"][aria-selected="true"]')].filter(visible);
  const inProgress=activeTab.length===1 && clean(activeTab[0].innerText)==='In Progress';
  const emptyText=clean((document.querySelector('main')||document.body||document.documentElement)?.textContent);
  const explicitEmpty=/\b(?:no courses? in progress|no courses? currently in progress|you (?:do not|don't) have any courses? in progress)\b/i.test(emptyText);
  const titleLinks=[...document.querySelectorAll('a[href]')].filter(e=>{
    if(!visible(e)) return false;
    try{const u=new URL(e.href,location.href);return originOK(u)
      && /^\/learn\/[a-z0-9][a-z0-9-]{0,159}\/home\/welcome\/?$/.test(u.pathname);}
    catch{return false;}
  });
  const courseLinkCount=titleLinks.length;
  const courseRows=[], rowIDs=new Set();
  for(const link of titleLinks){
    const u=new URL(link.href,location.href);
    const id=u.pathname.split('/')[2];
    if(rowIDs.has(id)) continue;
    let node=link, status='';
    for(let depth=0;node && depth<12;node=node.parentElement,depth++){
      const ownLinks=[...node.querySelectorAll('a[href]')].filter(e=>{
        if(!visible(e)) return false;
        try{return /^\/learn\/[a-z0-9][a-z0-9-]{0,159}\/home\/welcome\/?$/.test(new URL(e.href,location.href).pathname);}
        catch{return false;}
      });
      if(ownLinks.length!==1) continue;
      status=clean(node.textContent).match(/Course(?:\s+\d+\s+of\s+\d+)?\s*[·•-]\s*(?:Not started|\d+(?:\.\d+)?% complete|In progress|Started|Complete(?:d)?)\b/i)?.[0]||'';
      if(status) break;
    }
    // Preserve unknown rows: a changed Coursera label must not hide an
    // unfinished course and make the queue start a different one.
    rowIDs.add(id);courseRows.push({id,status});
  }
  const page=numberedPage && statusPage && numberedPage!==statusPage?null:
    numberedPage || statusPage || (!hasNext && (courseLinkCount>0 || explicitEmpty)?1:null);
  const signature=JSON.stringify({url:location.href,page,cards:unique.map(c=>[c.id,c.action,c.title]),hasNext});
  if(request.action==='click_course'){
    if(request.signature!==signature) return {error:'My Learning changed before course selection.',url:location.href};
    const selected=unique.find(c=>c.id===request.id);
    if(!selected) return {error:'The selected course is no longer on this page.',url:location.href};
    const action=elementBySlug.get(request.id);
    if(!action || !visible(action)) return {error:'Get started for the selected course is no longer visible.',url:location.href};
    if(action.matches('a')) action.target='_self';
    action.click();
    return {clicked:true,id:request.id,title_url:selected.title_url,url:location.href};
  }
  if(request.action==='next_page'){
    if(request.signature!==signature || !hasNext || !next)
      return {error:'My Learning pagination changed before the next page.',url:location.href};
    next.click();return {clicked:true,previous_page:page,url:location.href};
  }
  return {url:location.href,ready:document.readyState,page,has_next:hasNext,pager,cards:unique,course_rows:courseRows,
    in_progress_selected:inProgress,course_link_count:courseLinkCount,explicit_empty:explicitEmpty,
    raw_action_count:actions.length,signature};
})"""


COMPLETED_PAGE_JS = r"""(request=>{
  const clean=x=>(x||'').replace(/\s+/g,' ').trim();
  const visible=e=>{
    if(!e || !e.getClientRects().length) return false;
    const rect=e.getBoundingClientRect();
    if(rect.width<=0 || rect.height<=0) return false;
    for(let p=e;p;p=p.parentElement){const s=getComputedStyle(p);
      if(p.hidden || p.inert || p.getAttribute('aria-hidden')==='true' ||
         s.display==='none' || ['hidden','collapse'].includes(s.visibility) || s.opacity==='0') return false;}
    return true;
  };
  const originOK=u=>u.protocol==='https:' && ['www.coursera.org','coursera.org'].includes(u.hostname) &&
    !u.username && !u.password && (!u.port || u.port==='443');
  const here=new URL(location.href);
  if(!originOK(here) || here.pathname!=='/my-learning' || here.searchParams.get('myLearningTab')!=='COMPLETED')
    return {error:'Not on Coursera My Learning Completed.',url:location.href};
  const certificateURL=e=>{
    try{const u=new URL(e.href,location.href);
      if(originOK(u) && /^\/account\/accomplishments\/(?:records|verify)\/[A-Za-z0-9_-]{4,160}\/?$/.test(u.pathname)
          && !u.search && !u.hash) return u.origin+u.pathname;
    }catch{}return null;
  };
  const certificateLinks=[...document.querySelectorAll('a[href]')]
    .filter(e=>visible(e) && /^View certificate$/i.test(clean(e.innerText||e.getAttribute('aria-label')))
      && certificateURL(e));
  const wantedTitles=(request.titles||[]).map(x=>clean(x).toLocaleLowerCase());
  const courseLinkID=e=>{
    try{const u=new URL(e.href,location.href);
      return originOK(u) ? u.pathname.match(/^\/learn\/([a-z0-9][a-z0-9-]{0,159})(?:\/.*)?$/)?.[1]||null:null;
    }catch{return null;}
  };
  const candidates=[];
  for(const link of certificateLinks){
    const href=certificateURL(link);
    let node=link;
    for(let depth=0;node && depth<12;depth++,node=node.parentElement){
      const scopedCertificates=[...node.querySelectorAll('a[href]')]
        .filter(e=>visible(e) && /^View certificate$/i.test(clean(e.innerText||e.getAttribute('aria-label'))))
        .map(certificateURL).filter(Boolean);
      // A specialization or the whole page can contain several different certificates.
      if(new Set(scopedCertificates).size!==1) continue;
      const links=[...node.querySelectorAll('a[href]')].filter(visible);
      const courseIDs=[...new Set(links.map(courseLinkID).filter(Boolean))];
      const titleElements=[...node.querySelectorAll('h1,h2,h3,h4,h5,h6,a[href],strong,p,span,div,[data-testid*="title"]')]
        .filter(visible).map(e=>clean(e.innerText)).filter(Boolean);
      const titleMatches=titleElements.filter(x=>wantedTitles.includes(x.toLocaleLowerCase()));
      // The nearest matching ancestor binds the certificate to this course card.
      if(courseIDs.includes(request.course_id) || titleMatches.length){
        candidates.push({href,depth,course_ids:courseIDs,title_matches:titleMatches});break;
      }
    }
  }
  const current=[...document.querySelectorAll('button[aria-label],a[aria-label]')]
    .filter(visible).find(e=>/^Page number\s+\d+(?:\s*,.*)?$/i.test(e.getAttribute('aria-label')||''));
  const numberedPage=Number((current?.getAttribute('aria-label')||'').match(/^Page number\s+(\d+)/i)?.[1])||null;
  const statusPages=[...document.querySelectorAll('[data-testid="mobile-pagination"] [role="status"]')]
    .filter(visible).map(e=>clean(e.textContent).match(/^Page\s+(\d+)\s+of\s+(\d+)$/i))
    .filter(Boolean).map(m=>({page:Number(m[1]),total:Number(m[2])}))
    .filter(x=>x.page>0 && x.total>=x.page && x.total<=100);
  const statusValues=[...new Set(statusPages.map(x=>x.page+'/'+x.total))];
  const statusPage=statusValues.length===1?statusPages[0].page:null;
  const next=[...document.querySelectorAll('button[aria-label],a[aria-label]')]
    .filter(visible).find(e=>e.getAttribute('aria-label')==='Go to next page');
  const hasNext=!!next && !next.disabled && next.getAttribute('aria-disabled')!=='true';
  const activeTab=[...document.querySelectorAll('[role="tab"][aria-selected="true"]')].filter(visible);
  const completedSelected=activeTab.length===1 && clean(activeTab[0].innerText)==='Completed';
  const emptyText=clean((document.querySelector('main')||document.body||document.documentElement)?.textContent);
  const explicitEmpty=/\b(?:no completed courses?|no courses? completed|you (?:do not|don't) have any completed courses?)\b/i.test(emptyText);
  const courseLinks=[...document.querySelectorAll('a[href]')].filter(visible).map(courseLinkID).filter(Boolean);
  const courseLinkCount=courseLinks.length;
  const page=numberedPage && statusPage && numberedPage!==statusPage?null:
    numberedPage || statusPage || (!hasNext && (courseLinkCount>0 || certificateLinks.length>0 || explicitEmpty)?1:null);
  const signature=JSON.stringify({url:location.href,page,hasNext,
    candidates:candidates.map(c=>[c.href,c.depth,c.course_ids,c.title_matches]),
    certificate_count:certificateLinks.length,course_link_count:courseLinkCount});
  if(request.action==='next_page'){
    if(request.signature!==signature || !hasNext || !next)
      return {error:'My Learning Completed pagination changed before the next page.',url:location.href};
    next.click();return {clicked:true,previous_page:page,url:location.href};
  }
  return {url:location.href,ready:document.readyState,page,has_next:hasNext,
    completed_selected:completedSelected,explicit_empty:explicitEmpty,
    certificate_count:certificateLinks.length,course_link_count:courseLinkCount,
    candidates,signature};
})"""


CERTIFICATE_JS = r"""(request=>{
  const clean=x=>(x||'').replace(/\s+/g,' ').trim();
  const visible=e=>{
    if(!e || !e.getClientRects().length) return false;
    for(let p=e;p;p=p.parentElement){const s=getComputedStyle(p);
      if(p.hidden || p.inert || p.getAttribute('aria-hidden')==='true' || s.display==='none' ||
         ['hidden','collapse'].includes(s.visibility)) return false;}
    return true;
  };
  const dialogs=[...document.querySelectorAll('[role="dialog"]')].filter(visible);
  const certificates=dialogs.filter(d=>{
    const text=clean(d.innerText);
    return text.includes('Congratulations!') && text.includes('Certificate ID:') &&
      text.includes('That was the last assignment!') &&
      text.includes("You've now completed ") && text.includes(' and earned the certificate!');
  });
  const completionTitle=certificates.length===1?
    clean(certificates[0].innerText).match(/You've now completed (.+?) and earned the certificate!/)?.[1]||null:null;
  const links=certificates.length===1?[...certificates[0].querySelectorAll('a[href]')]
    .filter(e=>visible(e) && clean(e.innerText)==='View certificate page')
    .map(e=>({element:e,href:e.href})).filter(x=>{
      try{const u=new URL(x.href);return u.protocol==='https:' && ['www.coursera.org','coursera.org'].includes(u.hostname)
        && !u.username && !u.password && (!u.port || u.port==='443')
        && /^\/account\/accomplishments\/(?:records|verify)\/[A-Za-z0-9_-]{4,160}\/?$/.test(u.pathname)
        && !u.search && !u.hash;}
      catch{return false;}
    }):[];
  if(request.action==='click'){
    if(links.length!==1 || links[0].href!==request.href) return {error:'Certificate action changed.',url:location.href};
    links[0].element.target='_self';links[0].element.click();
    return {clicked:true,url:location.href,href:request.href};
  }
  return {url:location.href,ready:document.readyState,completion_title:completionTitle,links:links.map(x=>x.href)};
})"""


MY_LEARNING_LINK_JS = r"""(request=>{
  const clean=x=>(x||'').replace(/\s+/g,' ').trim();
  const links=[...document.querySelectorAll('a[href]')].filter(e=>e.getClientRects().length && clean(e.innerText)==='My Learning')
    .filter(e=>{try{const u=new URL(e.href,location.href);return u.protocol==='https:'
      && ['www.coursera.org','coursera.org'].includes(u.hostname) && u.pathname==='/my-learning';}
      catch{return false;}});
  if(request.action==='click'){
    if(links.length!==1) return {error:'My Learning header link changed.',url:location.href};
    links[0].target='_self';links[0].click();return {clicked:true,url:location.href};
  }
  return {url:location.href,links:links.length};
})"""


CERTIFICATE_PAGE_JS = r"""(request=>{
  const clean=x=>(x||'').replace(/\s+/g,' ').trim();
  const visible=e=>{
    if(!e || !e.getClientRects().length) return false;
    for(let p=e;p;p=p.parentElement){const s=getComputedStyle(p);
      if(p.hidden || p.inert || p.getAttribute('aria-hidden')==='true' ||
         s.display==='none' || ['hidden','collapse'].includes(s.visibility)) return false;}
    return true;
  };
  let here;try{here=new URL(location.href);}catch{return {error:'Invalid certificate page URL.',url:location.href};}
  const match=here.pathname.match(/^\/account\/accomplishments\/verify\/([A-Za-z0-9_-]{4,160})\/?$/);
  if(here.protocol!=='https:' || !['www.coursera.org','coursera.org'].includes(here.hostname) ||
      here.username || here.password || (here.port && here.port!=='443') ||
      !match || match[1]!==request.token)
    return {error:'The expected Coursera certificate verification page is not open.',url:location.href};
  const main=[...document.querySelectorAll('main,[role="main"]')].filter(visible)[0]||document.body;
  if(!main) return {url:location.href,ready:document.readyState,error:'Certificate page is loading.'};
  const headings=[...document.querySelectorAll('h2.course-name')].filter(visible).map(e=>clean(e.innerText)).filter(Boolean);
  const productLinks=[...document.querySelectorAll('a.product-link[href]')].filter(visible)
    .map(e=>{try{return new URL(e.href,location.href).pathname;}catch{return '';}})
    .filter(path=>path==='/learn/'+request.course_id || path==='/learn/'+request.course_id+'/');
  const lines=(main.innerText||'').split(/\r?\n/).map(clean).filter(Boolean);
  const completedBy=lines.find(line=>/^Completed by\s+\S/i.test(line))||null;
  const grade=lines.find(line=>/^Grade Achieved:\s*\d+(?:\.\d+)?%$/i.test(line))||null;
  return {url:location.href,ready:document.readyState,token:match[1],headings,
    product_links:productLinks,completed_by:completedBy,grade,
    signature:JSON.stringify([location.href,headings,productLinks,completedBy,grade])};
})"""


async def _evaluate(tab, script, request):
    result = await tab.evaluate(script + "(" + json.dumps(request, ensure_ascii=False) + ")")
    if not isinstance(result, dict):
        raise RuntimeError("Coursera returned an unreadable page snapshot.")
    return result


async def _trusted_navigate(tab, url):
    response = await tab.call("Page.navigate", {"url": url})
    if response.get("errorText"):
        raise RuntimeError("Coursera navigation failed: " + str(response["errorText"]))


async def _page_targets(tab):
    response = await tab.call("Target.getTargets")
    infos = response.get("targetInfos")
    if not isinstance(infos, list):
        raise RuntimeError("Chrome did not return its page targets.")
    return {info["targetId"]:info for info in infos
            if isinstance(info, dict) and info.get("type") == "page"
            and isinstance(info.get("targetId"), str) and info["targetId"]}


async def _close_new_course_tabs(tab, before_ids, course_id):
    """Close only page targets created by this click for the selected course."""
    closed = []
    for probe in range(10):
        targets = await _page_targets(tab)
        new_ids = set(targets) - before_ids
        matches = [target_id for target_id, info in targets.items()
                   if target_id in new_ids
                   and (course_from_lesson_url(info.get("url")) or {}).get("id") == course_id]
        for target_id in matches:
            response = await tab.call("Target.closeTarget", {"targetId":target_id})
            if response.get("success") is not True:
                # A tab can vanish during a redirect/close race.
                if target_id in await _page_targets(tab):
                    raise RuntimeError("Chrome could not close the extra tab for the selected course.")
            closed.append(target_id)
        if closed:
            break
        if not new_ids and probe >= 2:
            break
        await asyncio.sleep(POLL_SECONDS)
    return closed


async def _close_new_certificate_tabs(tab, before_ids, token):
    """Close only newly opened pages for this exact certificate token."""
    closed = []
    for probe in range(10):
        targets = await _page_targets(tab)
        new_ids = set(targets) - before_ids
        matches = [target_id for target_id, info in targets.items()
                   if target_id in new_ids and valid_certificate_url(info.get("url"))
                   and urlsplit(info["url"]).path.rstrip("/").split("/")[-1] == token]
        for target_id in matches:
            response = await tab.call("Target.closeTarget", {"targetId":target_id})
            if response.get("success") is not True:
                if target_id in await _page_targets(tab):
                    raise RuntimeError("Chrome could not close the extra certificate tab.")
            closed.append(target_id)
        if closed:
            break
        if not new_ids and probe >= 2:
            break
        await asyncio.sleep(POLL_SECONDS)
    return closed


async def _wait_for_page(tab, expected_page=None):
    previous = None
    for _ in range(PAGE_PROBES):
        snapshot = await _evaluate(tab, PAGE_JS, {"action": "inspect"})
        if (not snapshot.get("error") and snapshot.get("ready") == "complete"
                and snapshot.get("in_progress_selected")
                and (snapshot.get("course_link_count", 0) > 0 or snapshot.get("explicit_empty"))
                and snapshot.get("page") is not None
                and (expected_page is None or snapshot["page"] == expected_page)):
            signature = snapshot.get("signature")
            if signature == previous:
                return snapshot
            previous = signature
        await asyncio.sleep(POLL_SECONDS)
    raise RuntimeError("My Learning In Progress did not show a stable course list and pagination.")


async def open_my_learning(tab):
    """Click My Learning from a certificate page, then select In Progress."""
    current = await _evaluate(tab, MY_LEARNING_LINK_JS, {"action": "inspect"})
    parsed = _site_url(current.get("url"))
    if parsed and re.fullmatch(r"/account/accomplishments/(?:records|verify)/[A-Za-z0-9_-]{4,160}/?", parsed.path):
        if current.get("links") == 1:
            clicked = await _evaluate(tab, MY_LEARNING_LINK_JS, {"action": "click"})
            if not clicked.get("clicked"):
                raise RuntimeError("The certificate page My Learning link could not be clicked.")
            await asyncio.sleep(POLL_SECONDS)
    # The explicit tab query chooses In Progress even when Coursera remembers a
    # different tab or the header link lands on the default view.
    await _trusted_navigate(tab, MY_LEARNING_URL)
    return await _wait_for_page(tab, expected_page=1)


async def open_certificate_page(tab, saved_url=None):
    """Click the completion overlay's certificate link, or use its saved URL."""
    observed = await _evaluate(tab, CERTIFICATE_JS, {"action": "inspect"})
    links = observed.get("links", [])
    observed_url = links[0] if isinstance(links, list) and len(links) == 1 else None
    if observed_url and not valid_certificate_url(observed_url):
        raise RuntimeError("The visible certificate URL was not a Coursera verification page.")
    if saved_url is not None and not valid_certificate_url(saved_url):
        raise ValueError("The saved certificate URL is invalid.")
    if observed_url and saved_url and observed_url != saved_url:
        raise RuntimeError("The visible certificate differs from the saved course certificate.")
    destination = observed_url or saved_url
    if destination is None:
        return None
    before_targets = await _page_targets(tab) if observed_url else None
    if observed_url:
        result = await _evaluate(tab, CERTIFICATE_JS, {"action": "click", "href": observed_url})
        if not result.get("clicked"):
            raise RuntimeError("The visible View certificate page action changed before click.")
    else:
        await _trusted_navigate(tab, destination)
    expected_token = urlsplit(destination).path.rstrip("/").split("/")[-1]
    # Some Coursera links open a new target despite a same-tab click request.
    # Give the click a short chance, then load that exact observed URL here.
    fallback_after = 6 if observed_url else PAGE_PROBES
    for probe in range(PAGE_PROBES):
        current = await _evaluate(tab, MY_LEARNING_LINK_JS, {"action": "inspect"})
        parsed = _site_url(current.get("url"))
        if parsed and re.fullmatch(r"/account/accomplishments/(?:records|verify)/[A-Za-z0-9_-]{4,160}/?", parsed.path):
            if parsed.path.rstrip("/").split("/")[-1] != expected_token:
                raise RuntimeError("View certificate page opened a different certificate.")
            if before_targets is not None:
                await _close_new_certificate_tabs(tab, set(before_targets), expected_token)
            return destination
        if probe + 1 == fallback_after and observed_url:
            await _trusted_navigate(tab, destination)
        await asyncio.sleep(POLL_SECONDS)
    raise RuntimeError("The Coursera certificate page did not load after View certificate page.")


async def observed_certificate_url(tab, course_id, course_title=None):
    """Read a matching course's live completion overlay before navigating away."""
    if not isinstance(course_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,159}", course_id):
        raise ValueError("Invalid course ID")
    observed = await _evaluate(tab, CERTIFICATE_JS, {"action":"inspect"})
    current = course_from_lesson_url(observed.get("url"))
    if not current or current["id"] != course_id:
        return None
    title = observed.get("completion_title")
    if not isinstance(title, str):
        return None
    title_slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    accepted_titles = {course_id}
    if isinstance(course_title, str) and course_title.strip():
        accepted_titles.add(re.sub(r"[^a-z0-9]+", "-", course_title.lower()).strip("-"))
    if title_slug not in accepted_titles:
        return None
    links = observed.get("links")
    if not isinstance(links, list) or len(links) != 1 or not valid_certificate_url(links[0]):
        return None
    return links[0]


def _normalized_title(value):
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip() if isinstance(value, str) else ""


async def verify_saved_certificate(tab, saved_url, course_id, course_title=None):
    """Verify a saved certificate page as proof for this specific course.

    Navigation is confined to the saved Coursera records/verify link. The
    records route may redirect to verify. A URL alone is never completion
    proof: the visible verification page must name the expected course and
    state who completed it in two stable snapshots.
    """
    if not valid_certificate_url(saved_url):
        raise ValueError("The saved certificate URL is invalid.")
    if not isinstance(course_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,159}", course_id):
        raise ValueError("Invalid course ID")
    if course_title is not None and (not isinstance(course_title, str) or len(course_title) > 300):
        raise ValueError("Invalid course title")
    token = urlsplit(saved_url).path.rstrip("/").split("/")[-1]
    expected_titles = {_normalized_title(course_id.replace("-", " "))}
    if course_title:
        expected_titles.add(_normalized_title(course_title))
    await _trusted_navigate(tab, saved_url)
    previous = None
    last = None
    for _ in range(PAGE_PROBES):
        snapshot = await _evaluate(tab, CERTIFICATE_PAGE_JS, {"token":token,"course_id":course_id})
        last = snapshot
        if not snapshot.get("error") and snapshot.get("ready") == "complete":
            headings = snapshot.get("headings")
            if (isinstance(headings, list) and len(headings) == 1
                    and _normalized_title(headings[0]) in expected_titles
                    and isinstance(snapshot.get("product_links"), list)
                    and len(snapshot["product_links"]) == 1
                    and snapshot["product_links"][0].rstrip("/") == "/learn/" + course_id
                    and isinstance(snapshot.get("completed_by"), str)
                    and re.fullmatch(r"Completed by\s+\S.*", snapshot["completed_by"], flags=re.I)
                    and snapshot.get("token") == token):
                signature = snapshot.get("signature")
                if signature == previous:
                    return {"verified":True, "method":"certificate", "url":snapshot["url"],
                            "certificate_token":token, "course_id":course_id,
                            "title":headings[0], "completed_by":snapshot["completed_by"],
                            "evidence":[headings[0], snapshot["completed_by"]]}
                previous = signature
            else:
                previous = None
        await asyncio.sleep(POLL_SECONDS)
    reason = last.get("error") if isinstance(last, dict) else None
    raise RuntimeError("The saved certificate did not show verified completion for this course" +
                       (": " + str(reason) if reason else "."))


async def _wait_for_completed_page(tab, course_id, titles, expected_page=None):
    previous = None
    for _ in range(PAGE_PROBES):
        snapshot = await _evaluate(tab, COMPLETED_PAGE_JS,
                                   {"action":"inspect", "course_id":course_id, "titles":titles})
        if (not snapshot.get("error") and snapshot.get("ready") == "complete"
                and snapshot.get("completed_selected")
                and (snapshot.get("certificate_count", 0) > 0
                     or snapshot.get("course_link_count", 0) > 0 or snapshot.get("explicit_empty"))
                and type(snapshot.get("page")) is int
                and (expected_page is None or snapshot["page"] == expected_page)):
            signature = snapshot.get("signature")
            if isinstance(signature, str) and signature == previous:
                return snapshot
            previous = signature
        await asyncio.sleep(POLL_SECONDS)
    raise RuntimeError("My Learning Completed did not show a stable course list and pagination.")


async def _open_temporary_completed_tab(owner):
    """Open a disposable target so certificate lookup preserves the course tab."""
    response = await owner.call("Target.createTarget", {"url":COMPLETED_LEARNING_URL})
    target_id = response.get("targetId")
    if not isinstance(target_id, str) or not target_id:
        raise RuntimeError("Chrome did not create a temporary My Learning tab.")
    temporary = None
    try:
        # Import here to avoid coupling ordinary URL parsing to the browser runtime.
        from coursera_start import CDP_URL, ChromeTab, local_json
        for _ in range(20):
            pages = await asyncio.to_thread(local_json, CDP_URL + "/json/list")
            match = next((page for page in pages if isinstance(page, dict)
                          and page.get("type") == "page" and page.get("id") == target_id
                          and isinstance(page.get("webSocketDebuggerUrl"), str)), None)
            if match:
                temporary = ChromeTab(match["webSocketDebuggerUrl"])
                await temporary.connect()
                return temporary, target_id
            await asyncio.sleep(POLL_SECONDS)
        raise RuntimeError("The temporary My Learning tab did not become available.")
    except BaseException:
        try:
            if temporary is not None:
                await temporary.close()
        finally:
            await owner.call("Target.closeTarget", {"targetId":target_id})
        raise


async def find_completed_certificate(tab, course_id, course_title=None):
    """Return a verified certificate URL for an exact course in My Learning.

    This is a recovery path for a course whose completion overlay was missed.
    Matching a card or a certificate URL alone does not prove completion: the
    records link must redirect to a verification page naming this course.
    """
    if not isinstance(course_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,159}", course_id):
        raise ValueError("Invalid course ID")
    if course_title is not None and (not isinstance(course_title, str) or len(course_title) > 300):
        raise ValueError("Invalid course title")
    titles = [course_id.replace("-", " ")]
    if course_title and course_title.strip():
        titles.append(course_title.strip())
    expected_titles = {_normalized_title(title) for title in titles}
    temporary, target_id = await _open_temporary_completed_tab(tab)
    try:
        snapshot = await _wait_for_completed_page(temporary, course_id, titles, expected_page=1)
        seen_pages = set()
        for _ in range(MAX_PAGES):
            page = snapshot["page"]
            if page < 1 or page in seen_pages:
                raise RuntimeError("My Learning Completed pagination repeated or lost its current page.")
            seen_pages.add(page)
            matches = []
            for candidate in snapshot.get("candidates", []):
                if not isinstance(candidate, dict) or not valid_certificate_url(candidate.get("href")):
                    continue
                ids = candidate.get("course_ids")
                title_matches = candidate.get("title_matches")
                if not isinstance(ids, list) or not isinstance(title_matches, list):
                    continue
                # A specialization parent can contain several course cards;
                # accept only a scope with this course's own link, or an exact title.
                if ids and set(ids) != {course_id}:
                    continue
                if course_id not in ids and not any(_normalized_title(title) in expected_titles
                                                    for title in title_matches):
                    continue
                depth = candidate.get("depth")
                if type(depth) is not int or depth < 1 or depth >= 12:
                    continue
                matches.append(candidate)
            if matches:
                nearest = min(candidate["depth"] for candidate in matches)
                urls = {candidate["href"] for candidate in matches if candidate["depth"] == nearest}
                if len(urls) != 1:
                    raise RuntimeError("My Learning Completed showed ambiguous certificates for this course.")
                candidate_url = urls.pop()
                proof = await verify_saved_certificate(temporary, candidate_url, course_id, course_title)
                verified_url = proof.get("url") if isinstance(proof, dict) else None
                if (not isinstance(proof, dict) or proof.get("verified") is not True
                        or proof.get("method") != "certificate" or proof.get("course_id") != course_id
                        or not valid_certificate_url(verified_url)
                        or urlsplit(verified_url).path.rstrip("/").split("/")[-1] !=
                           urlsplit(candidate_url).path.rstrip("/").split("/")[-1]):
                    raise RuntimeError("The Completed card did not verify a matching course certificate.")
                return verified_url
            if not snapshot.get("has_next"):
                return None
            clicked = await _evaluate(temporary, COMPLETED_PAGE_JS,
                                      {"action":"next_page", "course_id":course_id, "titles":titles,
                                       "signature":snapshot["signature"]})
            if not clicked.get("clicked") or clicked.get("previous_page") != page:
                raise RuntimeError("My Learning Completed pagination changed before the next page.")
            snapshot = await _wait_for_completed_page(temporary, course_id, titles, expected_page=page + 1)
        raise RuntimeError("My Learning Completed has more pages than the supported safety limit.")
    finally:
        try:
            await temporary.close()
        finally:
            response = await tab.call("Target.closeTarget", {"targetId":target_id})
            if response.get("success") is not True and target_id in await _page_targets(tab):
                raise RuntimeError("Chrome could not close the temporary My Learning tab.")


def _progress_kind(status, action=None):
    """Classify an observed course row without guessing an unfamiliar label."""
    match = re.fullmatch(r"Course(?:\s+\d+\s+of\s+\d+)?\s*[·•-]\s*(.+)",
                         status.strip(), flags=re.I) if isinstance(status, str) else None
    label = match[1].strip().casefold() if match else ""
    if label in {"complete", "completed"}:
        return "completed"
    percentage = re.fullmatch(r"(\d+(?:\.\d+)?)% complete", label)
    if percentage:
        amount = float(percentage[1])
        if amount > 100:
            return "unknown"
        if amount == 100:
            return "completed"
        if amount > 0:
            return "started"
        label = "not started"
    if label in {"started", "in progress"}:
        return "started"
    if action in {"resume", "continue_learning"}:
        return "started"
    if label == "not started":
        return "new"
    return "unknown"


async def _open_selected_course(tab, snapshot, card):
    """Open only the card selected from the completed cross-page inventory."""
    expected = course_from_lesson_url(card.get("title_url"))
    if (not expected or expected["id"] != card.get("id") or expected["url"] != card.get("url")
            or card.get("action") not in {"get_started", "resume", "continue_learning"}):
        raise RuntimeError("The selected My Learning course card changed before opening.")
    before_targets = await _page_targets(tab)
    clicked = await _evaluate(tab, PAGE_JS,
                              {"action": "click_course", "signature": snapshot["signature"], "id": expected["id"]})
    if not clicked.get("clicked") or clicked.get("id") != expected["id"]:
        raise RuntimeError("Get started changed before the course could be opened.")
    for _ in range(20):
        location = await _evaluate(tab, MY_LEARNING_LINK_JS, {"action": "inspect"})
        actual = course_from_lesson_url(location.get("url"))
        if actual:
            if actual["id"] != expected["id"]:
                raise RuntimeError("Get started opened a different course than its card.")
            await _close_new_course_tabs(tab, set(before_targets), expected["id"])
            return {**expected, "title": str(card.get("title") or expected["id"])[:300],
                    "source": card.get("source", ""), "action": card["action"],
                    "opened_url": location["url"]}
        await asyncio.sleep(POLL_SECONDS)
    # React may open a separate target. The click has been performed and the
    # observed title link supplies the exact lesson for this managed tab.
    await _trusted_navigate(tab, card["title_url"])
    await _close_new_course_tabs(tab, set(before_targets), expected["id"])
    return {**expected, "title": str(card.get("title") or expected["id"])[:300],
            "source": card.get("source", ""), "action": card["action"],
            "opened_url": card["title_url"]}


async def scan_in_progress(tab, excluded_ids: set[str]):
    """Read all In Progress pages and select a course without opening it.

    Pagination is allowed, but a course card is only returned as evidence. A
    pending certificate can therefore be checked after this full scan and
    before an unfinished course is opened. An unreadable card stops discovery
    instead of silently skipping an unfinished course.
    """
    if not isinstance(excluded_ids, set) or any(not isinstance(x, str) for x in excluded_ids):
        raise ValueError("excluded_ids must be a set of course slugs")
    snapshot = await open_my_learning(tab)
    seen_pages = set()
    found = {}
    unhandled = set()
    for _ in range(MAX_PAGES):
        page = snapshot.get("page")
        if type(page) is not int or page < 1 or page in seen_pages:
            raise RuntimeError("My Learning pagination repeated or lost its current page.")
        seen_pages.add(page)
        raw_cards = snapshot.get("cards", [])
        if not isinstance(raw_cards, list):
            raise RuntimeError("My Learning returned an unreadable course card list.")
        cards = {card.get("id"):card for card in raw_cards
                 if isinstance(card, dict) and isinstance(card.get("id"), str)}
        rows = snapshot.get("course_rows", [])
        if not isinstance(rows, list) or (snapshot.get("course_link_count", 0) > 0 and not rows and not cards):
            raise RuntimeError("My Learning course rows could not be classified.")
        row_ids = set()
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                raise RuntimeError("My Learning returned an unreadable course row.")
            identity = row["id"]
            if identity in excluded_ids:
                continue
            row_ids.add(identity)
            card = cards.get(identity)
            action = card.get("action") if card else None
            row_kind = _progress_kind(row.get("status"), action)
            card_kind = _progress_kind(card.get("source"), action) if card else "unknown"
            if row_kind != "unknown" and card_kind != "unknown" and row_kind != card_kind:
                raise RuntimeError("My Learning showed conflicting progress for " + identity)
            kind = row_kind if row_kind != "unknown" else card_kind
            if kind == "completed":
                if identity in found and found[identity][0] != "completed":
                    raise RuntimeError("My Learning showed conflicting progress for " + identity)
                found[identity] = (kind, page, None)
                continue
            if kind == "unknown" or not card or action not in {"get_started", "resume", "continue_learning"}:
                unhandled.add(identity)
                continue
            expected = course_from_lesson_url(card.get("title_url"))
            if not expected or expected["id"] != identity or expected["url"] != card.get("url"):
                unhandled.add(identity)
                continue
            if identity in found and found[identity][0] != kind:
                raise RuntimeError("My Learning showed conflicting progress for " + identity)
            found.setdefault(identity, (kind, page, card))
        for identity, card in cards.items():
            if identity in excluded_ids or identity in row_ids:
                continue
            kind = _progress_kind(card.get("source"), card.get("action"))
            if kind == "completed":
                found.setdefault(identity, (kind, page, None))
                continue
            expected = course_from_lesson_url(card.get("title_url"))
            if (kind == "unknown" or card.get("action") not in {"get_started", "resume", "continue_learning"}
                    or not expected or expected["id"] != identity or expected["url"] != card.get("url")):
                unhandled.add(identity)
                continue
            if identity in found and found[identity][0] != kind:
                raise RuntimeError("My Learning showed conflicting progress for " + identity)
            found.setdefault(identity, (kind, page, card))
        if not snapshot.get("has_next"):
            break
        clicked = await _evaluate(tab, PAGE_JS,
                                  {"action": "next_page", "signature": snapshot["signature"]})
        if not clicked.get("clicked") or clicked.get("previous_page") != page:
            raise RuntimeError("The next My Learning page could not be selected.")
        snapshot = await _wait_for_page(tab, expected_page=page + 1)
    else:
        raise RuntimeError("My Learning has more pages than the supported safety limit.")
    if unhandled:
        raise RuntimeError("My Learning contains unfinished courses without a supported action or known status: " +
                           ", ".join(sorted(unhandled)[:5]))
    selected = next((entry for kind in ("started", "new") for entry in found.values()
                     if entry[0] == kind), None)
    if selected is None:
        return None
    kind, selected_page, selected_card = selected
    return {"id":selected_card["id"], "url":selected_card["url"],
            "title":selected_card["title"], "source":selected_card["source"],
            "action":selected_card["action"], "title_url":selected_card["title_url"],
            "kind":kind, "selected_page":selected_page}


async def open_scanned_course(tab, selected):
    """Revalidate a prior full-scan selection, then open exactly that card."""
    if not isinstance(selected, dict):
        raise ValueError("The selected My Learning course is unreadable.")
    expected = course_from_lesson_url(selected.get("title_url"))
    selected_page = selected.get("selected_page")
    if (not expected or expected["id"] != selected.get("id") or expected["url"] != selected.get("url")
            or type(selected_page) is not int or not 1 <= selected_page <= MAX_PAGES
            or selected.get("action") not in {"get_started", "resume", "continue_learning"}
            or selected.get("kind") not in {"started", "new"}):
        raise ValueError("The selected My Learning course has no valid scan evidence.")
    # The scan may have ended many pages after the selected card. Reopen page
    # one, then require the same page, course, action, and progress before click.
    snapshot = await open_my_learning(tab)
    for expected_page in range(1, selected_page):
        clicked = await _evaluate(tab, PAGE_JS,
                                  {"action": "next_page", "signature": snapshot["signature"]})
        if not clicked.get("clicked") or clicked.get("previous_page") != expected_page:
            raise RuntimeError("My Learning pagination changed before course selection.")
        snapshot = await _wait_for_page(tab, expected_page=expected_page + 1)
    current = next((card for card in snapshot.get("cards", [])
                    if isinstance(card, dict) and card.get("id") == expected["id"]), None)
    if (current is None or current.get("title_url") != selected.get("title_url")
            or current.get("url") != expected["url"]
            or current.get("action") != selected.get("action")
            or current.get("source") != selected.get("source")
            or _progress_kind(current.get("source"), current.get("action")) != selected["kind"]):
        raise RuntimeError("My Learning changed before the selected course could be opened.")
    return await _open_selected_course(tab, snapshot, current)


async def discover_next_course(tab, excluded_ids: set[str]):
    """Compatibility helper for callers that still scan and open in one step."""
    selected = await scan_in_progress(tab, excluded_ids)
    return await open_scanned_course(tab, selected) if selected else None
