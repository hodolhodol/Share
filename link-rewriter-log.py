import requests, re, csv, time

# 설정
USERNAME = 'your.email@example.com'
API_TOKEN = 'your_token'
BASE_URL = 'https://yourwiki.com/wiki'
ORIGIN_SPACES = ['SPACE_A1', 'SPACE_A2', 'SPACE_A3']
TARGET_SPACE = 'SPACE_B'
auth = (USERNAME, API_TOKEN)
headers = {'Content-Type': 'application/json'}

short_url_records = []
link_map_records = []

def get_all_page_ids(space_key):
    ids = []
    start = 0
    while True:
        url = f"{BASE_URL}/rest/api/content?spaceKey={space_key}&limit=50&start={start}&expand=version"
        res = requests.get(url, auth=auth)
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
        res = requests.get(url, auth=auth)
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

def detect_short_urls(body, title, page_id):
    matches = re.findall(r'(https?://[^"]+)?(/wiki)?/x/[a-zA-Z0-9]+', body)
    for m in matches:
        partial = "".join(m)
        short_url = f"{BASE_URL}{partial}" if partial.startswith('/x') or '/wiki/x' in partial else partial
        short_url_records.append((title, page_id, short_url))
        # 로그도 함께 남김
        link_map_records.append({
            'page_id': page_id,
            'page_title': title,
            'from_text': short_url,
            'from_type': 'short',
            'to_text': 'N/A',
            'to_type': 'N/A',
            'status': 'short_logged',
            'note': 'short URL recorded only'
        })

def replace_links(body, title, page_id):
    def log_change(from_link, to_link, from_type, to_type, status, note):
        link_map_records.append({
            'page_id': page_id,
            'page_title': title,
            'from_text': from_link,
            'from_type': from_type,
            'to_text': to_link if to_link else 'N/A',
            'to_type': to_type if to_link else 'N/A',
            'status': status,
            'note': note
        })

    original = body
    changed = False

    # 일반 display, spaces 링크 교체
    for space in ORIGIN_SPACES:
        patterns = [
            (rf'/display/{space}/', f'/display/{TARGET_SPACE}/', 'display'),
            (rf'/spaces/{space}/pages/', f'/spaces/{TARGET_SPACE}/pages/', 'spaces'),
            (rf'https://[^"]+/wiki/display/{space}/', f'/display/{TARGET_SPACE}/', 'abs_display'),
            (rf'https://[^"]+/wiki/spaces/{space}/pages/', f'/spaces/{TARGET_SPACE}/pages/', 'abs_spaces'),
        ]
        for pattern, replacement, ptype in patterns:
            matches = re.findall(pattern, body)
            for match in matches:
                before = match
                after = replacement
                body = body.replace(before, after)
                changed = True
                log_change(before, after, ptype, 'relative', 'converted', f'{space} → {TARGET_SPACE}')

    # draw.io 링크 속성
    matches = re.findall(r'link="([^"]+)"', body)
    for match in matches:
        replaced = match
        for space in ORIGIN_SPACES:
            if f'/spaces/{space}/pages/' in match:
                replaced = match.replace(f'/spaces/{space}/', f'/spaces/{TARGET_SPACE}/')
                changed = True
                body = body.replace(match, replaced)
                log_change(match, replaced, 'drawio', 'relative', 'converted', f'{space} → {TARGET_SPACE}')

    if not changed:
        log_change('N/A', 'N/A', 'none', 'none', 'unchanged', 'No link replaced')

    return body

def update_page(pid, title):
    url = f"{BASE_URL}/rest/api/content/{pid}?expand=body.storage,version"
    res = requests.get(url, auth=auth)
    if res.status_code != 200:
        print(f"❌ Failed to get {title}")
        return

    data = res.json()
    body = data['body']['storage']['value']
    version = data['version']['number']

    detect_short_urls(body, title, pid)
    new_body = replace_links(body, title, pid)

    if new_body == body:
        print(f"🔍 No change: {title}")
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
    print(f"{'✅ Updated' if put_res.status_code == 200 else '❌ Failed'}: {title}")

if __name__ == "__main__":
    ROOT_PAGE_ID = '123456789'
    pages = get_child_pages(ROOT_PAGE_ID)
    print(f"🔍 Pages under root {ROOT_PAGE_ID}: {len(pages)}")

    for pid, title in pages:
        update_page(pid, title)
        time.sleep(0.5)

    with open('short_urls.csv', 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['title', 'page_id', 'short_url'])
        writer.writerows(short_url_records)

    with open('link_map.csv', 'w', newline='', encoding='utf-8') as f:
        fieldnames = [
            'page_id', 'page_title',
            'from_text', 'from_type',
            'to_text', 'to_type',
            'status', 'note'
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(link_map_records)

    print(f"\n📄 link_map.csv 기록 완료 ({len(link_map_records)} entries)")
    print(f"⚠️ short_urls.csv 기록 완료 ({len(short_url_records)} entries)")
