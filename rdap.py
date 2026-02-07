import requests

def rdap_available(domain: str, timeout=5) -> bool:
    url = f"https://rdap.org/domain/{domain}"
    r = requests.get(url, timeout=timeout, headers={"Accept": "application/rdap+json"})
    if r.status_code == 404:
        return True
    if r.status_code == 200:
        return False
    # Other statuses can happen (429 rate limit, 403, 5xx). Treat as unknown.
    raise RuntimeError(f"RDAP unexpected status {r.status_code}: {r.text[:200]}")

print(rdap_available("example.com"))