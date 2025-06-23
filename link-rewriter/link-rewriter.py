# 일반 링크 변경 + 짧은 URL 수집

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

def replace_links(body):
    for space in ORIGIN_SPACES:
        body = re.sub(rf'/display/{space}/', f'/display/{TARGET_SPACE}/', body)
        body = re.sub(rf'/spaces/{space}/pages/', f'/spaces/{TARGET_SPACE}/pages/', body)
        body = re.sub(rf'https://[^"]+/wiki/display/{space}/', f'/display/{TARGET_SPACE}/', body)
        body = re.sub(rf'https://[^"]+/wiki/spaces/{space}/pages/', f'/spaces/{TARGET_SPACE}/pages/', body)
        body = re.sub(rf'link="[^"]*/spaces/{space}/pages/', lambda m: m.group(0).replace(f'/spaces/{space}/', f'/spaces/{TARGET_SPACE}/'), body)
    return body

def detect_short_urls(body, title, page_id):
    matches = re.findall(r'(https?://[^"]+)?(/wiki)?/x/[a-zA-Z0-9]+', body)
    for m in matches:
        partial = "".join(m)
        short_url = f"{BASE_URL}{partial}" if partial.startswith('/x') or '/wiki/x' in partial else partial
        short_url_records.append((title, page_id, short_url))

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
    new_body = replace_links(body)
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
    pages = get_all_page_ids(TARGET_SPACE)
    print(f"🔍 Total pages: {len(pages)}")
    for pid, title in pages:
        update_page(pid, title)
        time.sleep(0.5)

    # short_urls.csv 저장
    with open('short_urls.csv', 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['title', 'page_id', 'short_url'])
        writer.writerows(short_url_records)

    print(f"\n⚠️ Short URLs written to short_urls.csv: {len(short_url_records)}")
