#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only forensic audit of existing SZSE chain-audit reports.

Default is local-only. Inputs are never modified. Optional --fetch replays
only manifest GET URLs after strict HTTPS szse.cn host validation. PDFs are
classified as linked resources, not announcement HTML pages, and never fetched.
"""
from __future__ import annotations
import argparse, csv, hashlib, json, re, time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from pathlib import PurePosixPath

CLASSES = {"confirmed_data_error","historical_channel_transition","url_alias","parser_error","audit_rule_false_positive","unresolved"}
ID_KEYS = {"docid","doc_id","articleid","article_id","announcementid","announcement_id","id"}
URL_KEYS = {"url","targeturl","target_url","sourceurl","source_url","href","link","articleurl"}
METHOD_KEYS = {"method","requestmethod","request_method","httpmethod"}
REQ_URL_KEYS = {"url","requesturl","request_url","fullurl","full_url","uri"}

def key(s): return re.sub(r"[^a-z0-9]","",str(s).lower())
def walk(x, path="$"):
    if isinstance(x,dict):
        yield path,x
        for k,v in x.items(): yield from walk(v,f"{path}.{k}")
    elif isinstance(x,list):
        for i,v in enumerate(x): yield from walk(v,f"{path}[{i}]")
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):
    if p.suffix.lower()==".json": return json.loads(p.read_text(encoding="utf-8-sig"))
    if p.suffix.lower()==".jsonl":
        out=[]
        for n,line in enumerate(p.read_text(encoding="utf-8-sig").splitlines(),1):
            if line.strip():
                try: out.append(json.loads(line))
                except Exception: out.append({"_parse_error":True,"_line":n,"_raw":line})
        return out
    if p.suffix.lower()==".csv":
        with p.open(encoding="utf-8-sig",newline="") as f: return list(csv.DictReader(f))
    return None
def values(obj, aliases):
    wanted={key(x) for x in aliases}
    return [(k,v) for k,v in obj.items() if key(k) in wanted and v not in (None,"")]
def is_szse(host):
    h=(host or "").rstrip(".").lower()
    return h=="szse.cn" or h.endswith(".szse.cn")
def url_info(raw, base=None):
    raw=str(raw).strip() if raw is not None else ""
    out={"raw_url":raw or None,"resolved_url":None,"form":"missing","scheme":None,"host":None,
         "path":None,"suffix":None,"host_trusted":False,"safe_https_szse":False,
         "resource_kind":"unknown","reason":None}
    if not raw: out["reason"]="missing URL"; return out
    if raw.startswith("//"): resolved="https:"+raw; form="scheme_relative"
    elif re.match(r"^https?://",raw,re.I): resolved=raw; form="absolute"
    elif base: resolved=urljoin(base,raw); form="root_relative" if raw.startswith("/") else "relative"
    else: out.update(form="relative_unresolved",reason="relative URL has no base"); return out
    try:
        p=urlsplit(resolved); host=p.hostname; suffix=PurePosixPath(p.path).suffix.lower()
        trusted=is_szse(host)
        safe=p.scheme.lower()=="https" and trusted and p.username is None and p.password is None and p.port in (None,443)
        kind="pdf_resource" if suffix==".pdf" else "html_page" if suffix in (".html",".htm") else "other_resource"
        out.update(resolved_url=resolved,form=form,scheme=p.scheme.lower(),host=host,path=p.path,
                   suffix=suffix,host_trusted=trusted,safe_https_szse=safe,resource_kind=kind)
        out["reason"]="trusted SZSE PDF resource, not announcement HTML" if kind=="pdf_resource" and trusted else ("trusted HTTPS SZSE URL" if safe else "blocked: HTTPS SZSE host, no user-info, standard port required")
    except Exception as e: out["reason"]=f"URL parse error: {e}"
    return out
class SafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        from urllib.parse import urljoin
        destination=url_info(urljoin(req.full_url,newurl))
        if not destination["safe_https_szse"]:
            raise ValueError("redirect blocked before following: "+str(destination["reason"]))
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--report-dir",type=Path,required=True)
    ap.add_argument("--output-dir",type=Path,required=True)
    ap.add_argument("--fetch",action="store_true",help="opt-in GET replay; local-only by default")
    ap.add_argument("--delay",type=float,default=.5)
    ap.add_argument("--max-requests",type=int,default=60)
    args=ap.parse_args()
    root=args.report_dir.resolve(); outdir=args.output_dir.resolve()
    if not root.is_dir(): raise SystemExit(f"report directory not found: {root}")
    if root==outdir or root in outdir.parents: raise SystemExit("output must not be inside source report directory")
    files=sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in {".json",".jsonl",".csv"})
    loaded=[]; hashes={}
    for p in files:
        hashes[str(p.relative_to(root))]=sha(p)
        try: loaded.append((p,read(p)))
        except Exception as e: loaded.append((p,{"_load_error":str(e)}))
    rows=[]
    for p,data in loaded:
        for loc,obj in walk(data): rows.append((str(p.relative_to(root)),loc,obj))

    # Only explicit anomaly records are authoritative for anomaly counts.
    # Generic recursive ID/URL co-occurrence caused parent/child contamination
    # (e.g. a summary node inheriting URLs from every nested child).
    ISSUE_KEYS={"type","issuetype","anomalytype","failuretype","code","rule","ruleid","category","kind"}
    URL_KEYS_EXT=URL_KEYS | {"adjuncturl","attachmenturl","documenturl","downloadurl","fileurl","pdfurl","resourceurl","docurl","neighborurl","previousurl","nexturl","preurl","posturl","unsafeurl","linkedresourceurl","relatedurl"}
    URL_TEXT_KEYS={"message","detail","description","reason","error","rawtext","rawurl"}
    ID_KEYS_EXT=ID_KEYS | {"sourceid","targetid","nodeid","fromid","toid","ids","duplicateids","announcementids"}
    DUPLICATE_ID_KEYS=ID_KEYS | {"duplicateid","duplicateids","conflictingid","conflictingids","conflictid","ids","announcementids","docids","articleids"}
    MARKERS=("duplicate_id_different_url","unsafe_neighbor_url",
             "edge_channel_mismatch","reciprocity_failure","invalid_seed_url")
    URL_RE=re.compile(r'''https?://[^\s"'<>]+|(?<![\w])//[^\s"'<>]+''',re.I)

    def direct_pairs(obj):
        for k,v in obj.items():
            if isinstance(v,(str,int,float)) or v is None:
                yield str(k),v
            elif isinstance(v,list):
                for item in v:
                    if isinstance(item,(str,int,float)) and item is not None:
                        yield str(k),item

    def scalar_pairs(value, parent_key=""):
        if isinstance(value,dict):
            for k,v in value.items():
                if isinstance(v,(str,int,float)) or v is None:
                    yield str(k),v
                else:
                    yield from scalar_pairs(v,str(k))
        elif isinstance(value,list):
            for v in value:
                if isinstance(v,(str,int,float)) or v is None:
                    yield parent_key,v
                else:
                    yield from scalar_pairs(v,parent_key)

    MARKER_VALUE_KEYS={key(x) for x in (
        "type","issue_type","anomaly_type","failure_type","rule","rule_id",
        "code","issue_code","anomaly_code"
    )}

    def direct_markers(obj):
        """Recognize explicit issue records, not aggregate summaries."""
        found=set()
        for k,v in obj.items():
            nk=key(k)
            candidates=[v] if isinstance(v,(str,int,float)) else []
            if isinstance(v,list):
                candidates.extend(x for x in v if isinstance(x,(str,int,float)))
            for marker in MARKERS:
                mk=key(marker)
                # Marker in a field name must be an exact key, not a summary
                # key such as unsafe_neighbor_url_count or issue_type_counts.
                if nk==mk and v not in (None,"",False,0):
                    found.add(marker)
                # Marker values are accepted only from explicit type/code keys
                # and only on exact normalized equality.
                elif nk in MARKER_VALUE_KEYS and any(
                    isinstance(x,(str,int,float)) and key(x)==mk for x in candidates
                ):
                    found.add(marker)
        return found

    def extract_urls(pairs, allow_all_text=False):
        found=[]
        url_aliases={key(x) for x in URL_KEYS_EXT}
        text_aliases={key(x) for x in URL_TEXT_KEYS}
        for k,v in pairs:
            if not isinstance(v,str): continue
            nk=key(k)
            # On an explicitly identified issue record, scan its own scalar
            # values for URLs. On ordinary records, stay with URL-like fields.
            if not allow_all_text and nk not in url_aliases and nk not in text_aliases:
                continue
            vals=URL_RE.findall(v)
            field_looks_like_url=("url" in nk or "href" in nk or "link" in nk)
            if not vals and (nk in url_aliases or field_looks_like_url):
                candidate=v.strip()
                if candidate.startswith(("/", "./", "../", "//")) or re.search(r"\.(?:pdf|html?)$",candidate,re.I):
                    vals=[candidate]
            for raw in vals:
                raw=raw.rstrip(").,;]")
                if raw and raw not in found: found.append(raw)
        return found

    def extract_ids(pairs, allowed=ID_KEYS_EXT):
        aliases={key(x) for x in allowed}
        return sorted({str(v) for k,v in pairs
                       if key(k) in aliases and v not in (None,"")
                       and re.fullmatch(r"\d+",str(v))})

    def value_from_pairs(pairs, aliases):
        wanted={key(x) for x in aliases}
        for k,v in pairs:
            if key(k) in wanted and v not in (None,""):
                return str(v)
        return None

    def direct_value(obj, aliases):
        return value_from_pairs(list(direct_pairs(obj)),aliases)

    duplicate_groups={}
    issue_groups=defaultdict(dict)
    unsafe=[]; resources_by_key={}; source_rule_conflicts=[]
    edges={}; issue_occurrence_counts=defaultdict(int); requests={}

    def issue_identity(marker, sid, tid, rel, reverse_id, duplicate_ids, urls, obj):
        signature=(marker,sid or "",tid or "",rel or "",reverse_id or "",
                   tuple(duplicate_ids),tuple(sorted(urls)))
        if not any((sid,tid,rel,reverse_id,duplicate_ids,urls)):
            raw_obj=json.dumps(obj,sort_keys=True,ensure_ascii=False,default=str)
            signature+=(hashlib.sha256(raw_obj.encode("utf-8")).hexdigest(),)
        return signature

    for file,loc,obj in rows:
        markers=direct_markers(obj)

        # Only explicitly marked issue objects are allowed recursive evidence
        # extraction. Summary/root containers no longer inherit issue markers.
        evidence_pairs=list(scalar_pairs(obj)) if markers else list(direct_pairs(obj))
        ids=extract_ids(evidence_pairs)
        urls=extract_urls(evidence_pairs,allow_all_text=bool(markers))
        sid=direct_value(obj,{"source_id","sourceid","from_id","fromid"})
        tid=direct_value(obj,{"target_id","targetid","to_id","toid"})
        rel=direct_value(obj,{"relation","relation_type","edge_type"})
        reverse_id=direct_value(obj,{"reverse_id_returned","reverseidreturned","reverse_id","reverseid"})
        if markers:
            sid=sid or value_from_pairs(evidence_pairs,{"source_id","sourceid","from_id","fromid"})
            tid=tid or value_from_pairs(evidence_pairs,{"target_id","targetid","to_id","toid"})
            rel=rel or value_from_pairs(evidence_pairs,{"relation","relation_type","edge_type"})
            reverse_id=reverse_id or value_from_pairs(evidence_pairs,{"reverse_id_returned","reverseidreturned","reverse_id","reverseid"})

        duplicate_ids=extract_ids(evidence_pairs,DUPLICATE_ID_KEYS) if "duplicate_id_different_url" in markers else []
        for marker in markers:
            issue_occurrence_counts[marker]+=1
            sig=issue_identity(marker,sid,tid,rel,reverse_id,duplicate_ids,urls,obj)
            group=issue_groups[marker].setdefault(sig,{
                "issue_type":marker,"source_id":sid,"target_id":tid,"relation":rel,
                "reverse_id_returned":reverse_id,"ids":duplicate_ids,"urls":sorted(urls),
                "source_locations":[],"occurrence_count":0
            })
            location={"file":file,"location":loc}
            if location not in group["source_locations"]:
                group["source_locations"].append(location)
            group["occurrence_count"]+=1

        if "duplicate_id_different_url" in markers:
            for doc_id in duplicate_ids:
                group=duplicate_groups.setdefault(doc_id,{"id":doc_id,"urls":set(),
                    "source_locations":[],"evidence_record_count":0})
                group["urls"].update(urls)
                group["source_locations"].append({"file":file,"location":loc})
                group["evidence_record_count"]+=1

        for raw in urls:
            info=url_info(raw)
            item={"file":file,"location":loc,"source_id":sid,"target_id":tid,**info}
            marked_unsafe="unsafe_neighbor_url" in markers
            if info["resource_kind"]=="pdf_resource" and info["host_trusted"] and (marked_unsafe or (sid and tid)):
                item.update(classification="szse_hosted_pdf_resource",
                            interpretation="Linked PDF resource; not an HTML announcement node")
                resource_key=(sid,tid,info["resolved_url"] or raw)
                existing=resources_by_key.get(resource_key)
                if existing is None:
                    resources_by_key[resource_key]=item
                else:
                    existing.setdefault("source_locations",[]).append({"file":file,"location":loc})
            elif marked_unsafe and not info["safe_https_szse"]:
                item["classification"]="unsafe_or_untrusted_url"
                unsafe.append(item)
            elif marked_unsafe and info["safe_https_szse"]:
                item["classification"]="source_rule_conflict_candidate"
                item["interpretation"]="Source marked unsafe_neighbor_url, but URL passes HTTPS/SZSE host policy"
                source_rule_conflicts.append(item)

        # Relations are counted only from the current edge object; fields are
        # never inherited from unrelated parent/child objects.
        if sid and tid and rel:
            edges[(sid,rel,tid)]={"source_id":sid,"relation":rel,"target_id":tid,"record":obj}

        method=direct_value(obj,METHOD_KEYS)
        rawurl=next((u for u in extract_urls(list(direct_pairs(obj))) if u.startswith(("http://","https://"))),None)
        if rawurl: requests[rawurl]=str(method or "GET").upper()

    duplicate=[{"id":g["id"],"urls":sorted(g["urls"]),
                "source_locations":g["source_locations"],
                "evidence_record_count":g["evidence_record_count"],
                "url_evidence_status":"complete" if len(g["urls"])>=2 else "missing_or_incomplete"}
               for _,g in sorted(duplicate_groups.items())]
    resources=list(resources_by_key.values())

    def groups_for(marker):
        return list(issue_groups.get(marker,{}).values())

    duplicate_issue_records=groups_for("duplicate_id_different_url")
    unsafe_issue_records=groups_for("unsafe_neighbor_url")
    unique_issue_counts={marker:len(groups) for marker,groups in issue_groups.items()}
    def dedupe(items, fields):
        seen={}
        for item in items:
            ident=tuple(json.dumps(item.get(k),sort_keys=True,ensure_ascii=False) if k=="record" else str(item.get(k)) for k in fields)
            if ident not in seen: seen[ident]=item
            else: seen[ident]["duplicate_occurrences"]=seen[ident].get("duplicate_occurrences",1)+1
        return list(seen.values())
    unsafe=dedupe(unsafe,["source_id","target_id","raw_url"])
    source_rule_conflicts=dedupe(source_rule_conflicts,["source_id","target_id","raw_url"])
    report={"version":"1.4.0","source_dir":str(root),"network_requests_made":0,
      "input_file_sha256":hashes,"input_file_count":len(files),"issue_type_counts":unique_issue_counts,"issue_type_occurrence_counts":dict(issue_occurrence_counts),
      "evidence_record_counts":{"duplicate_issue_records":len(duplicate_issue_records),
       "duplicate_issue_occurrences":issue_occurrence_counts.get("duplicate_id_different_url",0),
       "unsafe_issue_records":len(unsafe_issue_records),
       "unsafe_issue_occurrences":issue_occurrence_counts.get("unsafe_neighbor_url",0),"unsafe_policy_failing_urls":len(unsafe),
       "source_rule_conflict_candidates":len(source_rule_conflicts),"unique_pdf_resource_links":len(resources)},
      "duplicate_id_evidence":duplicate,"duplicate_issue_records":duplicate_issue_records,
      "unsafe_url_evidence":unsafe,"unsafe_issue_records":unsafe_issue_records,
      "source_rule_conflict_candidates":source_rule_conflicts,"linked_resource_evidence":resources,"unique_relation_edges":list(edges.values()),
      "request_manifest_candidates":[{"url":u,"method":m} for u,m in requests.items()],
      "classifications":sorted(CLASSES),
      "pdf_context":{
       "P020201231733275849449.pdf":"2020 年深圳证券市场概况",
       "P020251231674893217325.pdf":"2025 年深圳证券市场概况",
       "interpretation":"Both are SZSE-hosted annual Shenzhen securities market overview PDF resources, not HTML announcement pages. Keep host trust separate from announcement-node eligibility. Preserve as linked resources; do not classify as external-domain URLs or automatically accept as valid pre/next announcement nodes."},
      "fetch":{"requested":args.fetch,"attempted":0,"results":[]}}
    if args.fetch:
        opener=build_opener(SafeRedirectHandler())
        for u,m in list(requests.items())[:max(0,args.max_requests)]:
            info=url_info(u)
            if m!="GET" or not info["safe_https_szse"] or info["resource_kind"]!="html_page":
                report["fetch"]["results"].append({"url":u,"status":"blocked_by_policy","method":m,"reason":info["reason"]}); continue
            try:
                req=Request(u,headers={"User-Agent":"ashare-quant-lab-szse-forensics/1.0","Accept":"text/html,application/json"})
                with opener.open(req,timeout=20) as resp:
                    final=url_info(resp.geturl())
                    if not final["safe_https_szse"]: raise ValueError("redirect destination rejected")
                    body=resp.read(2_000_000)
                    report["fetch"]["results"].append({"url":u,"status":resp.status,"final_url":resp.geturl(),
                       "content_type":resp.headers.get("Content-Type"),"body_bytes":len(body),"body_sha256":hashlib.sha256(body).hexdigest()})
            except Exception as e: report["fetch"]["results"].append({"url":u,"status":"error","error":str(e)})
            report["fetch"]["attempted"]+=1; time.sleep(max(0,args.delay))
        report["network_requests_made"]=report["fetch"]["attempted"]
    outdir.mkdir(parents=True,exist_ok=True)
    (outdir/"szse_anomaly_forensics.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    md=["# SZSE anomaly forensics","",f"- Input files: {len(files)}",f"- Network requests: {report['network_requests_made']}",
        f"- Unique duplicate-ID groups: {len(duplicate)}",f"- Duplicate issue groups: {len(duplicate_issue_records)}",f"- Duplicate issue report occurrences: {issue_occurrence_counts.get('duplicate_id_different_url',0)}",f"- Unsafe URL records (policy-failing): {len(unsafe)}",f"- Unique source unsafe issue groups: {len(unsafe_issue_records)}",f"- Unsafe issue report occurrences: {issue_occurrence_counts.get('unsafe_neighbor_url',0)}",f"- Source-rule conflict candidates: {len(source_rule_conflicts)}",f"- Linked resource records: {len(resources)}",
        f"- Unique relation edges: {len(edges)}","","## Annual market overview PDF resources",
        "- P020201231733275849449.pdf: 2020 年深圳证券市场概况",
        "- P020251231674893217325.pdf: 2025 年深圳证券市场概况",
        "- These are SZSE-hosted PDF resources, not HTML announcement pages.",
        "- Preserve resource links separately from announcement graph nodes."]
    (outdir/"szse_anomaly_forensics.md").write_text("\n".join(md)+"\n",encoding="utf-8")
    print(json.dumps({"status":"ok","input_files":len(files),"output_dir":str(outdir),
       "duplicate_evidence_groups":len(duplicate),"duplicate_issue_records":len(duplicate_issue_records),
       "duplicate_issue_occurrences":issue_occurrence_counts.get("duplicate_id_different_url",0),
       "unsafe_url_evidence":len(unsafe),"unsafe_issue_records":len(unsafe_issue_records),
       "unsafe_issue_occurrences":issue_occurrence_counts.get("unsafe_neighbor_url",0),
       "source_rule_conflict_candidates":len(source_rule_conflicts),"linked_resource_evidence":len(resources),
       "network_requests_made":report["network_requests_made"]},ensure_ascii=False,indent=2))
if __name__=="__main__": main()
