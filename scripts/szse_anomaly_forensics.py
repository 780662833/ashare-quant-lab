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

    # The source audit has used several report schemas over time. Inspect
    # nested scalar fields and issue labels instead of relying on one exact
    # key spelling or a single top-level "type" field.
    ISSUE_KEYS={"type","issuetype","anomalytype","failuretype","code","rule","ruleid","category","kind"}
    URL_KEYS_EXT=URL_KEYS | {"adjuncturl","attachmenturl","documenturl","downloadurl","fileurl","pdfurl","resourceurl","docurl"}
    ID_KEYS_EXT=ID_KEYS | {"sourceid","targetid","nodeid","fromid","toid"}
    MARKERS=("duplicate_id_different_url","unsafe_neighbor_url",
             "edge_channel_mismatch","reciprocity_failure","invalid_seed_url")

    def scalar_pairs(value):
        if isinstance(value,dict):
            for k,v in value.items():
                if isinstance(v,(str,int,float)) or v is None:
                    yield str(k),v
                else:
                    yield from scalar_pairs(v)
        elif isinstance(value,list):
            for v in value: yield from scalar_pairs(v)

    def direct_markers(obj):
        found=set()
        for k,v in obj.items():
            nk=key(k)
            candidates=[str(v)] if isinstance(v,(str,int,float)) else []
            if isinstance(v,list):
                candidates += [str(x) for x in v if isinstance(x,(str,int,float))]
            for marker in MARKERS:
                if marker in nk or any(marker in x.lower() for x in candidates):
                    found.add(marker)
        return found

    URL_RE=re.compile(r"https?://[^\\s\\\"'<>]+|(?<![\\w])//[^\\s\\\"'<>]+",re.I)
    def url_candidates(obj):
        found=[]
        for k,v in scalar_pairs(obj):
            if not isinstance(v,str): continue
            nk=key(k)
            vals=URL_RE.findall(v)
            if not vals and (nk in URL_KEYS_EXT or re.search(r"P0\\d{20,}\\.pdf",v,re.I)):
                if v.strip().startswith(("/", "./", "../")) or re.search(r"\\.(?:pdf|html?)$",v.strip(),re.I):
                    vals=[v.strip()]
            for raw in vals:
                raw=raw.rstrip(").,;]")
                if raw not in found: found.append(raw)
        return found

    def get_values(obj, aliases):
        wanted={key(x) for x in aliases}
        return [str(v) for k,v in scalar_pairs(obj)
                if key(k) in wanted and v not in (None,"")]

    duplicate=[]; unsafe=[]; resources=[]; edges={}; issue_counts=defaultdict(int); requests={}
    for file,loc,obj in rows:
        markers=direct_markers(obj)
        for marker in markers: issue_counts[marker]+=1
        ids=[v for v in get_values(obj,ID_KEYS_EXT) if re.fullmatch(r"\\d+",v)]
        urls=url_candidates(obj)
        sid=next(iter(get_values(obj,{"source_id"})),None)
        tid=next(iter(get_values(obj,{"target_id"})),None)

        # Keep one evidence item per ID/URL group, even when the same finding
        # is repeated in summary and hard-failure sections.
        if "duplicate_id_different_url" in markers or (len(set(ids))==1 and len(set(urls))>1):
            signature=(tuple(sorted(set(ids))),tuple(sorted(set(urls))))
            duplicate.append({"file":file,"location":loc,"ids":list(signature[0]),
                              "urls":list(signature[1]),"issue_type":"duplicate_id_different_url",
                              "record":obj})

        for raw in urls:
            info=url_info(raw)
            item={"file":file,"location":loc,"source_id":sid,"target_id":tid,**info}
            if info["resource_kind"]=="pdf_resource":
                if info["host_trusted"]:
                    item["classification"]="szse_hosted_pdf_resource"
                    item["source_issue_marker"]="unsafe_neighbor_url" if "unsafe_neighbor_url" in markers else None
                    item["interpretation"]="Linked document resource; not an HTML announcement node"
                    resources.append(item)
                elif "unsafe_neighbor_url" in markers or (sid and tid):
                    item["classification"]="unsafe_external_or_untrusted_resource"
                    unsafe.append(item)
            elif (not info["safe_https_szse"] and
                  ("unsafe_neighbor_url" in markers or (sid and tid))):
                item["classification"]="unsafe_or_untrusted_url"
                unsafe.append(item)
            elif ("unsafe_neighbor_url" in markers and
                  info["resource_kind"]!="html_page"):
                item["classification"]="non_html_linked_resource"
                resources.append(item)

        rel=next(iter(get_values(obj,{"relation"})),None)
        if sid and tid and rel:
            edges[(str(sid),str(rel),str(tid))]={"source_id":sid,"relation":rel,"target_id":tid,"record":obj}

        method=next(iter(get_values(obj,METHOD_KEYS)),None)
        rawurl=next((u for u in urls if u.startswith(("http://","https://"))),None)
        if rawurl: requests[rawurl]=str(method or "GET").upper()

    def dedupe(items, signature_fn):
        seen={}
        for item in items:
            sig=signature_fn(item)
            if sig not in seen: seen[sig]=item
            else: seen[sig]["duplicate_occurrences"]=seen[sig].get("duplicate_occurrences",1)+1
        return list(seen.values())

    duplicate=dedupe(duplicate,lambda x:(tuple(x["ids"]),tuple(x["urls"])))
    unsafe=dedupe(unsafe,lambda x:(x.get("source_id"),x.get("target_id"),x.get("raw_url")))
    resources=dedupe(resources,lambda x:(x.get("source_id"),x.get("target_id"),x.get("raw_url")))
    report={"version":"1.0.0","source_dir":str(root),"network_requests_made":0,
      "input_file_sha256":hashes,"input_file_count":len(files),"issue_type_counts":dict(issue_counts),
      "duplicate_id_evidence":duplicate,"unsafe_url_evidence":unsafe,"linked_resource_evidence":resources,"unique_relation_edges":list(edges.values()),
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
        f"- Duplicate-ID evidence records: {len(duplicate)}",f"- Unsafe URL evidence records: {len(unsafe)}",f"- Linked resource records: {len(resources)}",
        f"- Unique relation edges: {len(edges)}","","## Annual market overview PDF resources",
        "- P020201231733275849449.pdf: 2020 年深圳证券市场概况",
        "- P020251231674893217325.pdf: 2025 年深圳证券市场概况",
        "- These are SZSE-hosted PDF resources, not HTML announcement pages.",
        "- Preserve resource links separately from announcement graph nodes."]
    (outdir/"szse_anomaly_forensics.md").write_text("\n".join(md)+"\n",encoding="utf-8")
    print(json.dumps({"status":"ok","input_files":len(files),"output_dir":str(outdir),
       "duplicate_evidence":len(duplicate),"unsafe_url_evidence":len(unsafe),"linked_resource_evidence":len(resources),
       "network_requests_made":report["network_requests_made"]},ensure_ascii=False,indent=2))
if __name__=="__main__": main()
