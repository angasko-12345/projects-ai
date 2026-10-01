# CodeCraft API Security Report

Generated: 2026-10-01 21:00:53 +08:00

| Check | Status | Details |
|---|---|---|
| HTTPS website | PASS | HTTP 200, 739 ms |
| Website header: Strict-Transport-Security | INFO | Not observed. |
| Website header: Content-Security-Policy | INFO | Not observed. |
| Website header: X-Frame-Options | INFO | Not observed. |
| Website header: X-Content-Type-Options | INFO | Not observed. |
| Website header: Referrer-Policy | INFO | Not observed. |
| Website header: Permissions-Policy | INFO | Not observed. |
| TLS certificate | PASS | Certificate expires 12/12/2026 00:29:57 |
| DNS | INFO | Resolved DNS records. |
| Missing API key rejection | PASS | HTTP 401 returned for a request without credentials. |
| Valid API key | PASS | HTTP 200. Model count=33. |
| Invalid API key rejection | PASS | HTTP 401 returned. |
| x-api-key authentication | PASS | HTTP 200 returned. |
| Inference canary | INFO | Skipped. Set TEST_MODEL to enable. |

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

## Limitations

- These tests cannot prove server-side prompt retention or deletion.
- A successful API request does not establish that upstream providers retain nothing.
- HTTP security headers are observations, not complete security validation.
- A 429 means the relevant authentication test is inconclusive.
