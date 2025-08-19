# -*- coding: utf-8 -*-
"""
drawio_utils.py
- Confluence 페이지의 draw.io 첨부(확장자 유무와 무관) 내부 URL을 일괄 치환
- 라벨(label=drawio) 또는 mediaType(application/drawio, application/vnd.jgraph.mxfile, image/svg+xml)로 판별
- .drawio의 <diagram> payload가 base64+raw-deflate면 자동 해제/재압축
- 본문(new_body)은 변경하지 않고 그대로 반환 (본문은 메인 코드에서 이미 처리)
"""
from typing import List, Tuple, Optional, Callable
import re
import zlib
import base64
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse, parse_qs, unquote_plus

import requests

# 일반 URL 텍스트 탐지 패턴 (draw.io XML 텍스트에도 쓰임)
PLAIN_URL_PATTERN = re.compile(r'(https?://[^\s"<]+)')

# ========= 공개 API (메인에서 호출) =========
def replace_links_drawio(new_body: str,
                         page_json: dict,
                         BASE_URL: str,
                         headers: dict,
                         ORIGIN_SPACES: List[str],
                         TARGET_SPACE: str) -> str:
    """
    해당 페이지의 draw.io 첨부(라벨/미디어타입 기반)를 찾아
    다이어그램 내부 URL을 ORIGIN_SPACES → TARGET_SPACE(동일 제목) 규칙으로 치환.
    변경되면 첨부 새 버전 업로드. 본문 문자열(new_body)은 변경하지 않고 그대로 반환.

    Parameters
    ----------
    new_body : str             # 그대로 반환
    page_json : dict           # GET /rest/api/content/{id}?expand=... 결과 JSON
    BASE_URL : str             # e.g. https://devops-qa.martin.co.kr/confluence
    headers : dict             # 인증/헤더 (Bearer 등)
    ORIGIN_SPACES : list[str]  # 원본 공간 키들
    TARGET_SPACE : str         # 타깃 공간 키
    """
    session = requests.Session()
    session.headers.update(headers)

    page_id = page_json.get("id")
    if not page_id:
        return new_body

    # 1) 첨부 조회 (라벨/미디어타입 함께)
    atts = _list_attachments(session, BASE_URL, page_id)

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
            data, ctype = _download_attachment(session, BASE_URL, att["id"])

            # .drawio (mxfile) 스타일
            if is_drawio_mediatype or low.endswith(".drawio") or _looks_like_mxfile(data):
                status = _process_drawio_file(
                    session, BASE_URL, page_id, att["id"], filename, data,
                    lambda url: _rewrite_single_url(url, session, BASE_URL, ORIGIN_SPACES, TARGET_SPACE)
                )
            # .svg 스타일
            elif is_svg or low.endswith(".drawio.svg"):
                status = _process_drawio_svg(
                    session, BASE_URL, page_id, att["id"], filename, data,
                    lambda url: _rewrite_single_url(url, session, BASE_URL, ORIGIN_SPACES, TARGET_SPACE)
                )
            else:
                status = "skip"

            print(f" - draw.io attachment {filename or att.get('id')}: {status}")

        except Exception as e:
            print(f" - draw.io attachment {filename or att.get('id')}: error: {e}")

    return new_body


# ========= 내부 유틸 (첨부/요청) =========
def _list_attachments(session: requests.Session, base_url: str, page_id: str, limit: int = 500):
    url = f"{base_url}/rest/api/content/{page_id}/child/attachment?limit={limit}&expand=metadata.labels,metadata.mediaType"
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
    files = {'file': (filename or "diagram.drawio", data, content_type or 'application/octet-stream')}
    r = session.post(url, files=files)
    r.raise_for_status()
    return r.json()

def _get_json(session: requests.Session, url: str):
    r = session.get(url, headers={"Accept": "application/json"})
    r.raise_for_status()
    return r.json()

def _find_content_by_title(session: requests.Session, base_url: str, space_key: str, title: str):
    url = f'{base_url}/rest/api/content?spaceKey={_q(space_key)}&title={_q(title)}&expand=version'
    data = _get_json(session, url)
    results = data.get("results", [])
    return results[0] if results else None

def _get_content_by_id(session: requests.Session, base_url: str, page_id: str, expand="space,title"):
    url = f"{base_url}/rest/api/content/{page_id}?expand={expand}"
    return _get_json(session, url)

def _q(s: str) -> str:
    try:
        return requests.utils.quote(s, safe="")
    except Exception:
        return s


# ========= URL 해석/치환 =========
def _normalize_url(u: str, base_url: str) -> str:
    if u.startswith("/"):
        return urljoin(base_url, u)
    return u

def _decode_title_slug(s: str) -> str:
    # + 또는 % 인코딩 혼재
    return unquote_plus(s)

def _extract_pageid_from_url(url: str, session: requests.Session, base_url: str) -> Optional[str]:
    try:
        p = urlparse(url)
        q = parse_qs(p.query or "")

        # case 1) ?pageId=123
        if "pageId" in q:
            return q["pageId"][0]

        # case 2) /display/SPACE/TITLE
        if "/display/" in p.path:
            m = re.search(r"/display/([^/]+)/(.+)$", p.path)
            if m:
                space = m.group(1)
                title = _decode_title_slug(m.group(2))
                page = _find_content_by_title(session, base_url, space, title)
                return page["id"] if page else None

        # case 3) tiny /x/.... -> 리다이렉트 따라가 최종 URL 재해석
        if re.search(r"/x/[A-Za-z0-9]+", p.path):
            r = session.get(url, allow_redirects=True)
            return _extract_pageid_from_url(r.url, session, base_url)
    except Exception:
        return None
    return None

def _build_target_url_by_title(session: requests.Session, base_url: str, target_space: str, title: str) -> Optional[str]:
    tgt = _find_content_by_title(session, base_url, target_space, title)
    if not tgt:
        return None
    return f"{base_url}/pages/viewpage.action?pageId={tgt['id']}"

def _build_target_url_by_pageid(session: requests.Session, base_url: str,
                                origin_spaces: List[str], target_space: str, src_page_id: str) -> Optional[str]:
    src = _get_content_by_id(session, base_url, src_page_id, expand="space,title")
    space_key = src["space"]["key"]
    title = src["title"]
    if space_key not in origin_spaces:
        return None
    return _build_target_url_by_title(session, base_url, target_space, title)

def _rewrite_single_url(old_url: str,
                        session: requests.Session,
                        base_url: str,
                        origin_spaces: List[str],
                        target_space: str) -> Optional[str]:
    """
    old_url → (ORIGIN_SPACES → TARGET_SPACE 동일 제목) 새 URL.
    매핑 실패 시 None.
    """
    try:
        u = _normalize_url(old_url, base_url)
        pid = _extract_pageid_from_url(u, session, base_url)
        if pid:
            new_u = _build_target_url_by_pageid(session, base_url, origin_spaces, target_space, pid)
            if new_u:
                return new_u

        # pageId 미검출: /display/ORG/TITLE 직접 매핑
        p = urlparse(u)
        m = re.search(r"/display/([^/]+)/(.+)$", p.path or "")
        if m and m.group(1) in origin_spaces:
            title = _decode_title_slug(m.group(2))
            tgt = _build_target_url_by_title(session, base_url, target_space, title)
            if tgt:
                return tgt

        return None
    except Exception:
        return None


# ========= draw.io 파일 처리 =========
def _looks_like_mxfile(data: bytes) -> bool:
    # 간단 휴리스틱: <mxfile …> 헤더 확인
    head = data[:2048].decode("utf-8", errors="ignore")
    return "<mxfile" in head

def _try_decompress_drawio_payload(s: str) -> Tuple[str, bool]:
    """
    <diagram> 텍스트가 base64 + raw-deflate(-15)인 경우가 많음.
    압축이면 해제, 아니면 (원문, False)
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

def _rewrite_urls_in_text_with_cb(text: str, rewrite_cb: Callable[[str], Optional[str]]) -> str:
    """
    draw.io 내부 XML/텍스트에서 URL 패턴을 찾아 콜백으로 치환.
    - mxCell/@link, userObject/@link, @url, @href 등 속성
    - style="...link=..." 내 값
    - 일반 텍스트 노드의 순수 URL
    """
    # 1) 속성값 치환
    def attr_repl(m):
        before = m.group(2)
        after = rewrite_cb(before) or before
        return f'{m.group(1)}="{after}"'
    out = re.sub(r'(link|url|href)\s*=\s*"([^"]+)"', attr_repl, text)

    # 2) style 내 link=... 치환
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
                         att_id: str, filename: str, data: bytes, rewrite_cb: Callable[[str], Optional[str]]) -> str:
    """
    .drawio(xml): <mxfile><diagram>payload</diagram></mxfile>
    payload이 압축이면 해제 → 치환 → 원형(압축/평문) 복원 후 업로드.
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
                        att_id: str, filename: str, data: bytes, rewrite_cb: Callable[[str], Optional[str]]) -> str:
    """
    .svg(XML) 텍스트 기반 치환 후 업로드.
    """
    text = data.decode("utf-8", errors="replace")
    new_text = _rewrite_urls_in_text_with_cb(text, rewrite_cb)
    if new_text == text:
        return "nochange"
    _upload_new_attachment_version(session, base_url, page_id, att_id, filename,
                                   new_text.encode("utf-8"), "image/svg+xml")
    return "updated"
