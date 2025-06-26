Confluence 링크 변환 스크립트 (원본 → 타겟 공간 기반 매핑, Short URL 및 draw.io 지원)

import requests, re, csv, time, json from bs4 import BeautifulSoup

설정

USERNAME = 'your.email@example.com' API_TOKEN = 'your_token' BASE_URL = 'https://yourwiki.com/wiki' ORIGIN_SPACES = ['SPACE_A', 'SPACE_B', 'SPACE_C'] TARGET_SPACE = 'SPACE_TARGET' auth = (USERNAME, API_TOKEN) headers = {'Content-Type': 'application/json'}

LOG = [] short_url_records = []

1. 대상 공간에서 전체 페이지 ID 및 제목 맵 생성 (Title → ID)

def build_target_page_map(root_id): page_map = {} stack = [root_id] while stack: current_id = stack.pop() url = f"{BASE_URL}/rest/api/content/{current_id}?expand=children.page" res = requests.get(url, auth=auth) if res.status_code != 200: continue data = res.json() page_map[data['title']] = data['id'] for child in data.get("children", {}).get("page", {}).get("results", []): stack.append(child['id']) return page_map

2. 링크 수정

def replace_links(body, page_map): soup = BeautifulSoup(body, "html.parser")

for a in soup.find_all("a", href=True):
    old_href = a['href']
    new_href = old_href

    # 상대경로 display → 자동으로 TARGET으로 처리되므로 OK

    # 절대경로 display
    m = re.match(r"https?://[^/]+/wiki/display/([^/]+)/([^"?#]+)", old_href)
    if m:
        src_space, title = m.groups()
        if title in page_map:
            new_href = f"/display/{TARGET_SPACE}/{title}"

    # 절대경로 viewpage + pageId → 변환 안 함 (동일 ID 아님)

    # Short URL: /x/XXX → title로 reverse lookup
    m = re.search(r"/x/([a-zA-Z0-9]+)", old_href)
    if m:
        short_code = m.group(1)
        resolved = resolve_short_url(short_code)
        if resolved and resolved in page_map:
            title = resolved
            new_href = f"/display/{TARGET_SPACE}/{title}"

    # draw.io 링크 내 포함된 href 수정
    if 'drawio' in a.get('class', []):
        # TODO: 파라미터나 SVG 내 링크 수정 추가 구현 가능
        pass

    if new_href != old_href:
        a['href'] = new_href
        LOG.append(f"Link updated: {old_href} → {new_href}")

return str(soup)

3. Short URL 디코딩 → 페이지 제목 (title) 반환

def resolve_short_url(code): url = f"{BASE_URL}/rest/api/shortlink/{code}" res = requests.get(url, auth=auth) if res.status_code == 200: data = res.json() return data.get("title")  # title 기준으로 page_map에서 찾음 return None

4. short URL 수집

def detect_short_urls(body, title, page_id): matches = re.findall(r'(https?://[^"\s]+)?(/wiki)?/x/[a-zA-Z0-9]+', body) for m in matches: partial = "".join(m) short_url = f"{BASE_URL}{partial}" if partial.startswith('/x') or '/wiki/x' in partial else partial short_url_records.append((title, page_id, short_url))

5. 페이지 업데이트

def update_page(pid, title, page_map): url = f"{BASE_URL}/rest/api/content/{pid}?expand=body.storage,version" res = requests.get(url, auth=auth) if res.status_code != 200: print(f"\u274c Failed to get {title}") return

data = res.json()
body = data['body']['storage']['value']
version = data['version']['number']

detect_short_urls(body, title, pid)
new_body = replace_links(body, page_map)
if new_body == body:
    print(f"\U0001f50d No change: {title}")
    return

payload = {
    "id": pid,
    "type": "page",
    "title": title,
    "space": {"key": TARGET_SPACE},
    "body": {"storage": {"value": new_body, "representation": "storage"}},
    "version": {"number": version + 1}
}

put_url = f"{BASE_URL}/rest/api/content/{pid}"
put_res = requests.put(put_url, json=payload, auth=auth, headers=headers)
print(f"{'\u2705 Updated' if put_res.status_code == 200 else '\u274c Failed'}: {title}")

6. 대상 범위 설정 및 실행

if name == "main": ROOT_PAGE_ID = '123456789'  # TARGET 공간 내에서 처리할 시작 페이지 ID page_map = build_target_page_map(ROOT_PAGE_ID) pages = list(page_map.items())

print(f"\U0001f50d Pages to update under {ROOT_PAGE_ID}: {len(pages)}")

for title, pid in pages:
    update_page(pid, title, page_map)
    time.sleep(0.5)

with open('short_urls.csv', 'w', newline='', encoding='utf-8') as f:
    writer = csv.writer(f)
    writer.writerow(['title', 'page_id', 'short_url'])
    writer.writerows(short_url_records)

with open('link_updates.log', 'w', encoding='utf-8') as f:
    f.write("\n".join(LOG))

print(f"\n\u2709️ Logs written to link_updates.log, Short URLs to short_urls.csv")

