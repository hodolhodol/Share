# 일반 링크 변경 + 짧은 URL 수집

import requests, re, csv, time, certifi, urllib.parse, json
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from typing import List, Tuple, Optional, Callable, Dict, Any
from urllib.parse import urljoin, urlparse, parse_qs, unquote_plus
#from drawio_utils import replace_links_drawio
import os
# 설정
#설정 - 검증서버
load_dotenv()

EMAIL = os.getenv("EMAIL")
API_TOKEN = os.getenv("API_TOKEN")
CERT_PATH = os.getenv("CERT_PATH")


BASE_URL =  os.getenv("BASE_URL") # 또는 내부 도메인
TEST_BASE_URL = os.getenv("TEST_BASE_URL") # 또는 내부 도메인
PAGE_ID = "823654206"  # 테스트할 Confluence 페이지 ID

ROOT_PAGE_ID = "1066435477"  # 테스트할 Confluence 페이지 ID

# ORIGIN_SPACES = ['TR', 'AGILEK', 'DCO']
# TARGET_SPACE = 'Knowledge'
ORIGIN_SPACES = ['TR', 'AGILEK', 'DCO']
TARGET_SPACE = 'ARU'

TESTPAGE = 'TESTPAGE'

auth = (EMAIL, API_TOKEN)
headers = {
    "Authorization": f"Bearer {API_TOKEN}",  # Bearer 토큰 방식으로 인증
   "Content-Type": "application/json",
#    "Accept": "application/json"
}

short_urls = {}
pageid_urls = {}



def get_all_page_ids(space_key):
    ids = []
    start = 0
    while True:
        url = f"{BASE_URL}/rest/api/content?spaceKey={space_key}&limit=50&start={start}&expand=version"
        res = requests.get(url, headers=headers)
        results = res.json().get("results", [])
        if not results: break
        for page in results:
            ids.append((page['id'], page['title']))
        start += 50
    return ids
def get_child_pages(parent_id):
    """지정한 페이지 ID 이하의 모든 하위 페이지 ID+제목 리스트 반환"""
    pages = []
    stack = [parent_id]

    while stack:
        current_id = stack.pop()
        url = f"{BASE_URL}/rest/api/content/{current_id}?expand=children.page"
        #res = requests.get(url, auth=auth)
        res = requests.get(url, headers=headers)
        if res.status_code != 200:
            print(f"❌ Failed to get children of {current_id}")
            continue

        data = res.json()
        title = data.get("title", "Untitled")
        pages.append((current_id, title))

        children = data.get("children", {}).get("page", {}).get("results", [])
        for child in children:
            stack.append(child["id"])
    return pages
  
def replace_links_spacekey(body, prefix=""):
    for space in ORIGIN_SPACES:
        #body = re.sub(rf'{BASE_URL}/display/{space}/', f'{BASE_URL}/display/{TARGET_SPACE}/', body)
        body = re.sub(rf'{prefix+BASE_URL}/display/{space}/', f'{prefix+BASE_URL}/display/{TARGET_SPACE}/', body)         
        body = re.sub(rf'{prefix}/display/{space}/', f'{prefix+BASE_URL}/display/{TARGET_SPACE}/', body)   
        

        body = re.sub(rf'{prefix}/spaces/{space}/pages/', f'{prefix}/spaces/{TARGET_SPACE}/pages/', body)
        body = re.sub(rf'{prefix}https://[^"]+/wiki/display/{space}/', f'{prefix}/display/{TARGET_SPACE}/', body)
        body = re.sub(rf'{prefix}https://[^"]+/wiki/spaces/{space}/pages/', f'{prefix}/spaces/{TARGET_SPACE}/pages/', body)
        body = re.sub(rf'link="[^"]*/spaces/{space}/pages/', lambda m: m.group(0).replace(f'/spaces/{space}/', f'/spaces/{TARGET_SPACE}/'), body)
    return body

def replace_links_tinyui(body, page_id, prefix=""):
    matches = re.findall(rf'{prefix+BASE_URL}/x/[a-zA-Z0-9]+', body)
    for m in matches:
        partial = "".join(m)
        short_url = f"{BASE_URL}{partial}" if partial.startswith('/x') or '/wiki/x' in partial else partial
        if short_url not in short_urls:
            new_url = get_new_short_url(short_url, TARGET_SPACE)
            short_urls[short_url] = new_url
            body = re.sub(short_url, new_url, body)
            print(f"🔗 Replaced {short_url} with {new_url}")

        # else:
        #     print(f"❌ 이 short url은 수정되었어야 함 {short_url}")
        #    continue
    
    return body


def replace_links_short_page_id(body, prefix=""):
    return replace_links_page_id(body, "", "")


def replace_links_page_id(body, prefix="", base_url=BASE_URL):
    matches = re.findall(rf'{prefix+base_url}/pages/viewpage\.action\?pageId=\d+', body)
    for m in matches:
        page_id = extract_page_id(m)
        if page_id in pageid_urls:
            continue
#        if page_id == pageid_urls[page_id] : continue #page_id 동일하면 space가 origin_space에 있던게 아니다. 즉, 변경할 필요가 없다.
        page_info = get_page_info_by_id(page_id)
        if page_info is None:
            print(f"❌ Page not found: {page_id}")
            continue
        space = page_info['_expandable']['space'].strip('/').split('/')[-1] #space path의 맨마지막 가지고 옴
        title = page_info['title']
        if space in ORIGIN_SPACES:
                target_page_info = get_page_info_by_title(TARGET_SPACE, title)
                if target_page_info:
                    target_page_id = target_page_info.get('id')
                    old_url = m
                    new_url = f"{base_url}/pages/viewpage.action?pageId={target_page_id}"
                    body = body.replace(old_url, new_url)
                    print(f"🔗 Replaced {old_url} with {new_url}")
                    pageid_urls[page_id] = target_page_id
        else :
            pageid_urls[page_id] = page_id #page_id 동일하면 space가 origin_space에 있던게 아니다. 즉, 변경할 필요가 없다.

    return body

def _list_attachments(page_id: str, limit: int = 500):
    url = f"{BASE_URL}/rest/api/content/{page_id}/child/attachment?limit={limit}&expand=metadata.labels,metadata.mediaType"
    res = requests.get(url, headers=headers)
    res.raise_for_status()
    return res.json().get("results", [])

def _download_attachment_via_link(att: Dict[str, Any]):
    """
    첨부 객체의 _links.download 를 사용해 안전하게 다운로드.
    base_url 은 컨텍스트(/confluence, /wiki 포함)까지 들어간 값을 넘기세요.
    """
    links = att.get("_links", {}) or {}
    dl_path = links.get("download")  # 예: "/download/attachments/12345/diagram?version=2&api=v2"
    if not dl_path:
        raise RuntimeError(f"No download link in attachment: {att.get('id')}")

    url = urljoin(BASE_URL if BASE_URL.endswith("/") else BASE_URL + "/", dl_path.lstrip("/"))
    res = requests.get(url, headers=headers, allow_redirects=True)
    
    if res.status_code == 404:
        # 일부 인스턴스는 컨텍스트 경로 이슈로 루트(host) 기준이 필요한 경우가 있음
        from urllib.parse import urlparse
        p = urlparse(BASE_URL)
        host_root = f"{p.scheme}://{p.netloc}"
        url2 = urljoin(host_root + "/", dl_path.lstrip("/"))
        res = requests.get(url2, headers=headers, allow_redirects=True)
        

    res.raise_for_status()
    return res.content, res.headers.get("Content-Type", "")

# ========= draw.io 파일 처리 =========
def _looks_like_mxfile(data: bytes) -> bool:
    # 간단 휴리스틱: <mxfile …> 헤더 확인
    head = data[:2048].decode("utf-8", errors="ignore")
    return "<mxfile" in head

def _rewrite_single_url(old_url: str) -> Optional[str]:
    """
    old_url → (ORIGIN_SPACES → TARGET_SPACE 동일 제목) 새 URL.
    매핑 실패 시 None.
    """
    # try:
    #     u = _normalize_url(old_url, base_url)
    #     pid = _extract_pageid_from_url(u, session, base_url)
    #     if pid:
    #         new_u = _build_target_url_by_pageid(session, base_url, origin_spaces, target_space, pid)
    #         if new_u:
    #             return new_u

    #     # pageId 미검출: /display/ORG/TITLE 직접 매핑
    #     p = urlparse(u)
    #     m = re.search(r"/display/([^/]+)/(.+)$", p.path or "")
    #     if m and m.group(1) in origin_spaces:
    #         title = _decode_title_slug(m.group(2))
    #         tgt = _build_target_url_by_title(session, base_url, target_space, title)
    #         if tgt:
    #             return tgt

    #     return None
    # except Exception:
    #     return None

def _upload_new_attachment_version( page_id: str,
                                   attachment_id: str, filename: str, data: bytes, content_type: str):
    url = f"{BASE_URL}/rest/api/content/{page_id}/child/attachment/{attachment_id}/data"
    files = {'file': (filename or "diagram.drawio", data, content_type or 'application/octet-stream')}
    
    r = requests.post(url, headers=headers, files=files)
    #r = session.post(url, files=files)
    r.raise_for_status()
    return r.json()

        
def _process_drawio_file(page_id: str,
                         att_id: str, filename: str, data: bytes, rewrite_cb: Callable[[str], Optional[str]]) -> str:
    """
    .drawio(xml): <mxfile><diagram>payload</diagram></mxfile>
    payload이 압축이면 해제 → 치환 → 원형(압축/평문) 복원 후 업로드.
    """
    prefix = '"link="'

    body = data.decode("utf-8", errors="replace")
    org_body = body
    print(f"xml {body}")

    body = replace_links_spacekey(body, prefix)
    body = replace_links_tinyui(body, prefix, page_id)
    body = replace_links_page_id(body, prefix)
    body = replace_links_short_page_id(body, prefix)

    if(org_body != body):
        print(f"drawio file updated: {filename}")
        _upload_new_attachment_version(page_id, att_id, filename, body.encode("utf-8"), "application/xml")
        
    return body


def replace_links_drawio(body, page_json:Dict[str, Any]): 
    page_id = page_json.get("id")
    if not page_id:
        return body

    # 1) 첨부 조회 (라벨/미디어타입 함께)
    atts = _list_attachments(page_id)

    # 2) draw.io 후보만 처리
    for att in atts:
        filename = att.get("title") or (att.get("metadata", {}) or {}).get("filename") or ""
        labels = {lab["name"] for lab in (att.get("metadata", {}) or {}).get("labels", {}).get("results", [])}
        media  = (att.get("metadata", {}) or {}).get("mediaType", "") or ""
        low    = filename.lower()

        is_drawio_label     = "drawio" in labels
        is_drawio_mediatype = media in {"application/drawio", "application/vnd.jgraph.mxfile"}
        is_svg              = (media == "image/svg+xml") or low.endswith(".svg")

        if not (is_drawio_label or is_drawio_mediatype or is_svg or low.endswith(".drawio") or low.endswith(".drawio.svg")):
            continue

        try:
            data, ctype = _download_attachment_via_link(att)

            # .drawio (mxfile) 스타일
            if is_drawio_mediatype or low.endswith(".drawio") or _looks_like_mxfile(data):
                status = _process_drawio_file(
                    page_id, att["id"], filename, data, lambda url: _rewrite_single_url(url)
                )
            # .svg 스타일
            # elif is_svg or low.endswith(".drawio.svg"):
            #     status = _process_drawio_svg(
            #         session, BASE_URL, page_id, att["id"], filename, data,
            #         lambda url: _rewrite_single_url(url, session, BASE_URL, ORIGIN_SPACES, TARGET_SPACE)
            #     )
            # else:
            #     status = "skip"

            print(f" - draw.io attachment {filename or att.get('id')}: {status}")

        except Exception as e:
            print(f" - draw.io attachment {filename or att.get('id')}: error: {e}")

    return body


def resolve_short_url_to_title(short_url):
    """
    short URL을 풀어서 page ID 반환
    :param short_url: e.g. https://your-domain.atlassian.net/x/AbCdE
    :param auth: requests basic auth tuple (username, API token)
    """
    response = requests.get(short_url, headers=headers, allow_redirects=True)
    
    # 최종 리디렉션 URL에서 page ID 추출
    title = response.url.split('/')[-1].replace('+', ' ') # url에서는 스페이스가 +로 나와서.
    return title
    
    return title

    
def resolve_tiny_url(short_url):
    response = requests.get(short_url, allow_redirects=False, headers=headers)
    if response.status_code in [301, 302]:
        return response.headers['Location']
    else:
        raise Exception(f"리디렉션 실패: status code {response.status_code}")

# 2. URL에서 page ID 추출 (e.g. /pages/viewpage.action?pageId=12345678)
def extract_page_id(full_url):
    match = re.search(r"pageId=(\d+)", full_url)
    if match:
        return match.group(1)
    else:
        raise Exception(f"pageId를 찾을 수 없습니다: {full_url}")
    
def get_page_info_by_id(page_id):
    """
    page ID로 title 등 페이지 정보 조회
    """
    url = f"{BASE_URL}/rest/api/content/{page_id}"
    params = {"expand": "title"}
    response = requests.get(url, headers=headers, params=params)
    response.raise_for_status()
    return response.json()
    


def get_short_url_by_title(title, space_key, base_url):
    """
    주어진 title로 페이지를 검색하고 short URL(tiny link)을 반환
    """
    # 1. 제목으로 페이지 조회
    url = f"{base_url}/rest/api/search"
    params = {
        "title": title,
        "space": space_key,
        "expand": "version"  # title 존재 유무 확인용
    }
    resp = requests.get(url, headers=headers, params=params)
    resp.raise_for_status()
    data = resp.json()
    
    # 2. 결과 없을 경우 처리
    if not data.get("results"):
        print("Martin", "get_short_url_by_title", "해당 페이지 없음 {title}")
        return None  # or raise Exception("Page not found")
    
    page = data["results"][0]
    page_id = page["id"]
    
    # 3. 페이지 ID로 tiny link 정보 가져오기
    url = f"{base_url}/rest/api/content/{page_id}?expand=shortUrl,tinyui"
    resp = requests.get(url, auth=auth)
    resp.raise_for_status()
    page_data = resp.json()

    # 4. shortUrl 필드가 존재할 경우 반환
    tiny_url = page_data.get("tinyui", {}).get("link")
    if tiny_url:
        return f"{base_url}{tiny_url}"
    else:
        return None
    
def url_encode_query(query_string):
    """
    Encodes the given query string for use in a URL.

    Args:
    query_string (str): The query string to encode.

    Returns:
    str: The URL-encoded query string.
    """
    return urllib.parse.quote_plus(query_string)

def get_page_info_by_title(space_key, title):
    url = f"{BASE_URL}/rest/api/search"
    params = {
        'cql': f'title="{title}" AND space="{space_key}"',
        'limit': 10
    }
    
    response = requests.get(url, headers=headers, params=params)

    if response.status_code != 200:
        raise Exception(f"Request failed with status code {response.status_code}")
    
    data = response.json()
    results = data.get('results', [])
    
    if not results:
        return None
    
    for result in results:
        if result.get('title') == title:
            return {
                "id" :  result['content']['id'],
                "title" :  result['content']['title'],
                "webui" :  result['content']['_links']['webui'],
                "tinyui" :  result['content']['_links']['tinyui']
            }
    return None


def get_new_short_url(short_url, new_space):
    # Step 1: Extract the page ID from the short URL
    title = resolve_short_url_to_title(short_url)
    new_short_url = get_page_info_by_title(TARGET_SPACE, title)['tinyui']   

    if new_short_url:
        return f"{BASE_URL}{new_short_url}"
    else:
        return None
        

def update_page(pid, title):
    url = f"{BASE_URL}/rest/api/content/{pid}?expand=body.storage,version"
    res = requests.get(url, headers=headers)
    if res.status_code != 200:
        print(f"❌ Failed to get {title}")
        return

    data = res.json()
    body = data['body']['storage']['value']
    version = data['version']['number']
    
    new_body = replace_links_spacekey(body)
    new_body = replace_links_tinyui(new_body, pid)
    new_body = replace_links_page_id(new_body)
    new_body = replace_links_drawio(new_body, data)

    if new_body == body:
        print(f"🔍 No change: {title}")
        return


    space = data['_expandable']['space'].strip('/').split('/')[-1] #space path의 맨마지막 가지고 옴
    payload = {
        "id": pid,
        "type": "page",
        "title": title,
        "space": {"key": space},
        # "body": {"storage": {"value": "hello world", "representation": "storage"}},
        "body": {"storage": {"value": new_body, "representation": "storage"}},
        "version": {"number": version + 1}
    }

    put_url = f"{BASE_URL}/rest/api/content/{pid}"
    put_res = requests.put(put_url, json=payload, headers=headers)
    print(f"{'✅ Updated' if put_res.status_code == 200 else '❌ Failed'}: {title}")

def set_variables(mode) :
    global BASE_URL, PAGE_ID, ORIGIN_SPACES, TARGET_SPACE, TESTPAGE
    if mode == "TEST" :
        BASE_URL = TEST_BASE_URL
        PAGE_ID = "1127350378"
        ORIGIN_SPACES = ['TPG']
        TARGET_SPACE = 'ARU'

    if mode == "TEST-DRAWIO" :
        BASE_URL = TEST_BASE_URL
        PAGE_ID = "1128824849"
        ORIGIN_SPACES = ['TPG']
        TARGET_SPACE = 'ARU'
        TESTPAGE = "TechStack View - Draw.io"
if __name__ == "__main__":
#    test_short_url()
    # set_variables("TEST")
    set_variables("TEST-DRAWIO")
    update_page(PAGE_ID, TESTPAGE)

    # pages = get_child_pages(ROOT_PAGE_ID)
    # print(f"🔍 Pages under root {ROOT_PAGE_ID}: {len(pages)}")

    # for pid, title in pages:
    #     update_page(pid, title)
    #     time.sleep(0.5)

    filename = 'short_urls.csv'
    with open(filename, mode='w', newline='', encoding='utf-8') as file:
        fieldnames = ['old_url', 'new_url']
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for old_url, new_url in short_urls.items():
            writer.writerow({'old_url': old_url, 'new_url': new_url})


    print(f"\n⚠️ Short URLs written to short_urls.csv: {len(short_urls)}")
