# -*- coding: utf-8 -*-
import re
import zlib
import base64
import json
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse, parse_qs, unquote_plus

import requests

# -------- URL 패턴 (텍스트 내 일반 URL 검출)
PLAIN_URL_PATTERN = re.compile(r'(https?://[^\s"<]+)')

def replace_links_drawio(new_body: str,
                         page_json: dict,
                         BASE_URL: str,
                         headers: dict,
                         ORIGIN_SPACES: list[str],
                         TARGET_SPACE: str) -> str:
    """
    페이지의 draw.io 첨부(.drawio / .drawio.svg) 내부 URL을
    ORIGIN_SPACES → TARGET_SPACE 의 '동일 제목 매칭' 규칙으로 리라이트하여
    첨부 새 버전을 업로드합니다.
    본문(new_body)은 변경하지 않고 그대로 반환합니다.

    Parameters
    ----------
    new_body : str
        (그대로 반환) 이미 본문 링크 치환이 끝난 문자열
    page_json : dict
        GET /rest/api/content/{id}?expand=... 의 응답 JSON (여기서 id, _links 사용)
    BASE_URL : str
        예: "https://devops-qa.martin.co.kr/confluence"
    headers : dict
        인증/헤더 (예: Authorization: Bearer ...)
    ORIGIN_SPACES : list[str]
        예: ['TR', 'AGILEK', 'DCO']
    TARGET_SPACE : str
        예: 'ARU'
    """
    session = requests.Session()
    session.headers.update(headers)

    page_id = page_json.get("id")
    if not page_id:
        return new_body

    # 1) 첨부 목록 조회
    atts = _list_attachments(session, BASE_URL, page_id)

    # 2) draw.io 계열만 처리
    for att in atts:
        filename = att.get("title") or att.get("metadata", {}).get("filename") or ""
        low = filename.lower()
        if not (low.endswith(".drawio") or low.endswith(".drawio.svg")):
            continue

        try:
            data, ctype = _download_attachment(session, BASE_URL, att["id"])
            if low.endswith(".drawio"):
                status = _process_drawio_file(session, BASE_URL, page_id, att["id"], filename, data,
                                              lambda url: _rewrite_single_url(url, session, BASE_URL, headers, ORIGIN_SPACES, TARGET_SPACE))
            else:
                status = _process_drawio_svg(session, BASE_URL, page_id, att["id"], filename, data,
                                             lambda url: _rewrite_single_url(url, session, BASE_URL, headers, ORIGIN_SPACES, TARGET_SPACE))
            print(f" - draw.io attachment {filename}: {status}")
        except Exception as e:
            print(f" - draw.io attachment {filename}: error: {e}")

    return new_body


# =========================
# 내부 유틸리티
# =========================

def _list_attachments(session: requests.Session, base_url: str, page_id: str, limit: int = 500):
    url = f"{base_url}/rest/api/content/{page_id}/child/attachment?limit={limit}"
    r = session.get(url, headers={"Accept": "application/json"})
    r.raise_for_status()
    return r.json().get("results", [])

def _download_attachment(session: requests.Session, base_url: str, attachment_id: str):
    url = f"{base_url}/rest/api/content/{attachment_id}/download"
    r = session.get(url, allow_redirects=True)
    r.raise_for_status()
    return r.content, r.headers.get("Content-Type", "")

def _upload_new_attachment_version(session: requests.Session, base_url: str, page_id: str,
                                   attachment_id: str, filename: str, data: bytes, content_type: str):
    url = f"{base_url}/rest/api/content/{page_id}/child/attachment/{attachment_id}/data"
    files = {'file': (filename, data, content_type or 'application/octet-stream')}
    r = session.post(url, files=files)
    r.raise_for_status()
    return r.json()

def _get_json(session: requests.Session, url: str, headers=None, allow_redirects=True):
    r = session.get(url, headers=headers, allow_redirects=allow_redirects)
    r.raise_for_status()
    return r.json()

def _get_content_by_id(session: requests.Session, base_url: str, page_id: str, expand: str = "space,title"):
    url = f"{base_url}/rest/api/content/{page_id}?expand={expand}"
    return _get_json(session, url, headers={"Accept": "application/json"})

def _find_content_by_title(session: requests.Session, base_url: str, space_key: str, title: str):
    # 정확 일치 우선
    url = f'{base_url}/rest/api/content?spaceKey={_q(space_key)}&title={_q(title)}&expand=version'
    data = _get_json(session, url, headers={"Accept": "application/json"})
    results = data.get("results", [])
    return results[0] if results else None

def _q(s: str) -> str:
    # URL 인코딩은 Confluence가 내부적으로 처리하므로 간단 처리
    return requests.utils.quote(s, safe="")

# ---------- URL 해석/치환 ----------

def _normalize_url(u: str, base_url: str) -> str:
    if u.startswith("/"):
        return urljoin(base_url, u)
    return u

def _extract_pageid_from_url(url: str, session: requests.Session, base_url: str) -> str | None:
    try:
        p = urlparse(url)
        q = parse_qs(p.query or "")
        if "pageId" in q:
            return q["pageId"][0]

        # /display/SPACE/TITLE
        if "/display/" in p.path:
            m = re.search(r"/display/([^/]+)/(.+)$", p.path)
            if m:
                space = m.group(1)
                title = _decode_title_slug(m.group(2))
                page = _find_content_by_title(session, base_url, space, title)
                return page["id"] if page else None

        # tiny /x/XXXX → 리다이렉트 추적
        if re.search(r"/x/[A-Za-z0-9]+", p.path):
            try:
                r = session.get(url, allow_redirects=True)
                return _extract_pageid_from_url(r.url, session, base_url)
            except Exception:
                return None
    except Exception:
        return None
    return None

def _decode_title_slug(s: str) -> str:
    # +, %XX 혼재 가능
    return unquote_plus(s)

def _build_target_url_by_title(session: requests.Session, base_url: str, TARGET_SPACE: str, title: str) -> str | None:
    target = _find_content_by_title(session, base_url, TARGET_SPACE, title)
    if not target:
        return None
    return f"{base_url}/pages/viewpage.action?pageId={target['id']}"

def _build_target_url_by_pageid(session: requests.Session, base_url: str,
                                ORIGIN_SPACES: list[str], TARGET_SPACE: str, source_page_id: str) -> str | None:
    src = _get_content_by_id(session, base_url, source_page_id, expand="space,title")
    space_key = src["space"]["key"]
    title = src["title"]
    if space_key not in ORIGIN_SPACES:
        return None
    return _build_target_url_by_title(session, base_url, TARGET_SPACE, title)

def _rewrite_single_url(old_url: str,
                        session: requests.Session,
                        base_url: str,
                        headers: dict,
                        ORIGIN_SPACES: list[str],
                        TARGET_SPACE: str) -> str | None:
    """
    old_url → (ORG→TARGET 동일 제목) 새 URL 로 반환. 없으면 None 또는 원문 반환.
    """
    try:
        u = _normalize_url(old_url, base_url)
        pid = _extract_pageid_from_url(u, session, base_url)
        if pid:
            new_u = _build_target_url_by_pageid(session, base_url, ORIGIN_SPACES, TARGET_SPACE, pid)
            return new_u or old_url

        # /display/ORG/TITLE 인데 pageId를 못 뽑았을 때도 시도
        p = urlparse(u)
        m = re.search(r"/display/([^/]+)/(.+)$", p.path or "")
        if m and m.group(1) in ORIGIN_SPACES:
            title = _decode_title_slug(m.group(2))
            tgt = _build_target_url_by_title(session, base_url, TARGET_SPACE, title)
            return tgt or old_url

        return None
    except Exception:
        return None

# ---------- draw.io 파일 처리 ----------

def _try_decompress_drawio_payload(s: str) -> tuple[str, bool]:
    """
    <diagram> 텍스트가 base64 + raw-deflate(-15) 형태면 해제.
    (압축이 아니면 원문/False)
    """
    try:
        raw = base64.b64decode(s)
        text = zlib.decompress(raw, -15).decode("utf-8", errors="replace")
        return text, True
    except Exception:
        return s, False

def _compress_drawio_payload(s: str) -> str:
    co = zlib.compressobj(level=9, wbits=-15)
    data = co.compress(s.encode("utf-8")) + co.flush()
    return base64.b64encode(data).decode("ascii")

def _rewrite_urls_in_text_with_cb(text: str, rewrite_cb) -> str:
    """
    draw.io 내부 XML/텍스트에서 URL 패턴을 찾아 콜백으로 치환.
    - mxCell/@link, userObject/@link, @url, @href 등 속성
    - style="...link=..." 내 값
    - 일반 텍스트 노드의 순수 URL
    """
    # 1) 속성값(link|url|href="...") 치환
    def attr_repl(m):
        before = m.group(2)
        after = rewrite_cb(before) or before
        return f'{m.group(1)}="{after}"'
    out = re.sub(r'(link|url|href)\s*=\s*"([^"]+)"', attr_repl, text)

    # 2) style 속성 내 link=... 치환
    def style_repl(m):
        style = m.group(0)
        style = re.sub(r'link=([^;"]+)', lambda mm: f'link={rewrite_cb(mm.group(1)) or mm.group(1)}', style)
        return style
    out = re.sub(r'style="[^"]+"', style_repl, out)

    # 3) 일반 텍스트에 박힌 URL 치환
    def plain_repl(m):
        old = m.group(1)
        new = rewrite_cb(old)
        return new if new else old
    out = PLAIN_URL_PATTERN.sub(plain_repl, out)

    return out

def _process_drawio_file(session: requests.Session, base_url: str, page_id: str,
                         att_id: str, filename: str, data: bytes, rewrite_cb) -> str:
    """
    .drawio(XML): <mxfile><diagram>payload</diagram></mxfile>
    payload이 압축이면 해제→치환→원래 형식(압축/평문)으로 복원 후 업로드.
    """
    xml = data.decode("utf-8", errors="replace")
    try:
        root = ET.fromstring(xml)
    except Exception as e:
        return f"xml-parse-failed:{e}"

    changed = False
    for diagram in root.findall(".//diagram"):
        payload = diagram.text or ""
        plain, was_compressed = _try_decompress_drawio_payload(payload)
        new_plain = _rewrite_urls_in_text_with_cb(plain, rewrite_cb)
        if new_plain != plain:
            changed = True
            diagram.text = _compress_drawio_payload(new_plain) if was_compressed else new_plain

    if not changed:
        return "nochange"

    new_bytes = ET.tostring(root, encoding="utf-8", method="xml")
    _upload_new_attachment_version(session, base_url, page_id, att_id, filename, new_bytes, "application/xml")
    return "updated"

def _process_drawio_svg(session: requests.Session, base_url: str, page_id: str,
                        att_id: str, filename: str, data: bytes, rewrite_cb) -> str:
    """
    .drawio.svg: SVG(XML) 텍스트 기반 치환 후 업로드.
    """
    text = data.decode("utf-8", errors="replace")
    new_text = _rewrite_urls_in_text_with_cb(text, rewrite_cb)
    if new_text == text:
        return "nochange"
    _upload_new_attachment_version(session, base_url, page_id, att_id, filename,
                                   new_text.encode("utf-8"), "image/svg+xml")
    return "updated"
