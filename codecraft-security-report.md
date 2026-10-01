# CodeCraft API Security Report

Generated: 2026-10-01 20:36:53 +08:00

> Controlled, non-invasive checks only. PASS means that specific behavior matched the test expectation. It does not establish that the service is secure.

## Results

| Check | Status | Details |
|---|---|---|
| HTTPS root | PASS | HTTP 200, final URL https://codecraftapi.com/, 2490 ms |
| Strict-Transport-Security | INFO | Header not observed on root response. |
| Content-Security-Policy | INFO | Header not observed on root response. |
| X-Content-Type-Options | INFO | Header not observed on root response. |
| X-Frame-Options | INFO | Header not observed on root response. |
| Referrer-Policy | INFO | Header not observed on root response. |
| Permissions-Policy | INFO | Header not observed on root response. |
| TLS certificate | PASS | Expires 12/12/2026 00:29:57 |
| DNS | INFO | Resolved DNS records. |
| robots.txt | PASS | HTTP 200 |
| Legal: Privacy | PASS | Retrieved /privacy |
| Legal: Terms | PASS | Retrieved /terms |
| Legal: Acceptable Use | PASS | Retrieved /acceptable-use |
| Unauthenticated /v1/models | INFO | Returned HTTP 429. |
| Authenticated /v1/models | WARN | HTTP 429: The remote server returned an error: (429) Too Many Requests. |
| Invalid API key | INFO | Returned HTTP 429. |
| CORS | INFO | No Access-Control-Allow-Origin header observed. |
| Failed-request canary reflection | PASS | Unique canary was not reflected in the returned error. |

## TLS

```json
{
    "subject":  "CN=codecraftapi.com",
    "issuer":  "CN=WE1, O=Google Trust Services, C=US",
    "not_before":  "\/Date(1789227140000)\/",
    "not_after":  "\/Date(1797006597000)\/",
    "thumbprint":  "31738F897A0FAAFA942B87076489F15A49459B9F",
    "serial":  "00F45FCF7A3F0347A10E4546B6F4C1EDA2"
}
```

## DNS

```json
{
    "A":  [
              "104.21.15.95",
              "172.67.162.24"
          ],
    "AAAA":  [
                 "2606:4700:3030::6815:f5f",
                 "2606:4700:3033::ac43:a218"
             ],
    "NS":  [
               "cora.ns.cloudflare.com",
               "konnor.ns.cloudflare.com"
           ]
}
```

## Important limitations

- This cannot prove that CodeCraft does not retain prompts.
- It cannot inspect CodeCraft's databases or internal logs.
- A successful inference request only proves that the request reached an upstream model.
- Missing security headers do not automatically mean a vulnerability exists.
- Server-side privacy claims require independent evidence to verify.
