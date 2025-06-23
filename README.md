import requests

# 설정
BASE_URL = "https://yourcompany.atlassian.net/wiki"   # 또는 내부 도메인
EMAIL = "your.email@company.com"
API_TOKEN = "your_api_token_here"
PAGE_ID = "123456789"  # 테스트할 Confluence 페이지 ID

# 인증
auth = (EMAIL, API_TOKEN)

# 요청
url = f"{BASE_URL}/rest/api/content/{PAGE_ID}"

response = requests.get(url, auth=auth)

# 결과 출력
if response.status_code == 200:
    data = response.json()
    title = data.get("title", "Untitled")
    print(f"✅ Hello Confluence! Page title is: {title}")
else:
    print(f"❌ Failed to fetch page. Status code: {response.status_code}")
    print(response.text)

