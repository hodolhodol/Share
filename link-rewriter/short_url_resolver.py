import requests, csv, re, time

# 설정
USERNAME = 'your.email@example.com'
API_TOKEN = 'your_token'
BASE_URL = 'https://yourwiki.com/wiki'
TARGET_SPACE = 'SPACE_B'
auth = (USERNAME, API_TOKEN)
headers = {'Content-Type': 'application/json'}

def resolve_short_url(short_url):
    try:
        res = requests.get(short_url, allow_redirects=True, timeout=5)
        return res.url
    except Exception as e:
        print(f"❌ Failed to resolve {short_url}: {e}")
        return None

def update_page_replace_url(page_id, title, short_url, resolved_url):
    url = f"{BASE_URL}/rest/api/content/{page_id}?expand=body.storage,version"
    res = requests.get(url, auth=auth)
    if res.status_code != 200:
        print(f"❌ Failed to get {title}")
        return

    data = res.json()
    body = data['body']['storage']['value']
    version = data['version']['number']

    if short_url not in body:
        print(f"🚫 Short URL not found in {title}")
        return

    new_body = body.replace(short_url, resolved_url)
    payload = {
        "id": page_id,
        "type": "page",
        "title": title,
        "space": {"key": TARGET_SPACE},
        "body": {"storage": {"value": new_body, "representation": "storage"}},
        "version": {"number": version + 1}
    }

    put_url = f"{BASE_URL}/rest/api/content/{page_id}"
    put_res = requests.put(put_url, json=payload, auth=auth, headers=headers)
    print(f"{'✅ Replaced' if put_res.status_code == 200 else '❌ Failed'}: {title}")

if __name__ == "__main__":
    with open('short_urls.csv', 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    for row in rows:
        title = row['title']
        page_id = row['page_id']
        short_url = row['short_url']
        resolved = resolve_short_url(short_url)
        if resolved:
            update_page_replace_url(page_id, title, short_url, resolved)
            time.sleep(0.5)
