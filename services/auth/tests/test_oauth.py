from urllib.parse import parse_qs, urlparse

import httpx


async def start(client, provider):
    r = await client.get(f"/api/v1/auth/oauth/{provider}/start")
    assert r.status_code == 302, r.text
    query = parse_qs(urlparse(r.headers["location"]).query)
    return r, query


async def test_start_builds_authorization_request(client):
    r, q = await start(client, "google")
    loc = urlparse(r.headers["location"])
    assert f"{loc.scheme}://{loc.netloc}{loc.path}" == "https://accounts.google.com/o/oauth2/v2/auth"
    assert q["client_id"] == ["g-id"] and q["response_type"] == ["code"]
    assert q["redirect_uri"] == ["http://test/api/v1/auth/oauth/google/callback"]
    assert q["code_challenge_method"] == ["S256"] and q["state"] and q["code_challenge"]
    assert "openid" in q["scope"][0]
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("slm_oauth=") and "HttpOnly" in cookie and "SameSite=lax" in cookie


async def test_azure_uses_tenant_in_endpoints(client):
    r, _ = await start(client, "azure_ad")
    assert r.headers["location"].startswith("https://login.microsoftonline.com/contoso/oauth2/v2.0/authorize?")


async def test_unconfigured_or_unknown_provider(client, settings):
    settings.oauth.pop("github")
    r = await client.get("/api/v1/auth/oauth/github/start")
    assert r.status_code == 404 and r.json()["code"] == "oauth_provider_not_configured"
    assert (await client.get("/api/v1/auth/oauth/okta/start")).status_code == 422


async def test_google_callback_creates_then_reuses_user(client, upstream):
    upstream.userinfo = {"sub": "g-123", "email": "Grace@Example.com", "email_verified": True, "name": "Grace"}
    _, q = await start(client, "google")
    r = await client.get("/api/v1/auth/oauth/google/callback", params={"code": "abc", "state": q["state"][0]})
    assert r.status_code == 200, r.text
    tokens = r.json()
    me = (await client.get("/api/v1/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})).json()
    assert me["user"]["email"] == "grace@example.com" and me["role"] == "org_admin"

    form = httpx.QueryParams(upstream.token_requests[0].content.decode())
    assert form["grant_type"] == "authorization_code" and form["code"] == "abc"
    assert form["client_secret"] == "g-secret" and form["code_verifier"]

    _, q = await start(client, "google")
    r = await client.get("/api/v1/auth/oauth/google/callback", params={"code": "def", "state": q["state"][0]})
    me2 = (await client.get("/api/v1/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"})).json()
    assert me2["user"]["id"] == me["user"]["id"]


async def test_callback_rejects_bad_state_and_provider_errors(client, upstream):
    upstream.userinfo = {"sub": "x", "email": "x@example.com", "email_verified": True}
    _, q = await start(client, "google")
    r = await client.get("/api/v1/auth/oauth/google/callback", params={"code": "abc", "state": "forged"})
    assert r.status_code == 400 and r.json()["code"] == "invalid_oauth_state"
    r = await client.get("/api/v1/auth/oauth/github/callback", params={"code": "abc", "state": q["state"][0]})
    assert r.status_code == 400  # state was minted for google
    r = await client.get("/api/v1/auth/oauth/google/callback", params={"error": "access_denied"})
    assert r.status_code == 400 and r.json()["code"] == "oauth_provider_error"
    client.cookies.clear()
    r = await client.get("/api/v1/auth/oauth/google/callback", params={"code": "abc", "state": q["state"][0]})
    assert r.status_code == 400  # no cookie: CSRF protection


async def test_failed_code_exchange_is_a_502(client, upstream):
    upstream.token_status = 400
    _, q = await start(client, "google")
    r = await client.get("/api/v1/auth/oauth/google/callback", params={"code": "bad", "state": q["state"][0]})
    assert r.status_code == 502 and r.json()["code"] == "oauth_token_exchange_failed"


async def test_existing_password_account_linking_needs_verified_email(client, register, upstream):
    existing = await register()
    upstream.userinfo = {"sub": "az-1", "email": existing.email, "email_verified": False, "name": "A"}
    _, q = await start(client, "azure_ad")
    r = await client.get("/api/v1/auth/oauth/azure_ad/callback", params={"code": "c", "state": q["state"][0]})
    assert r.status_code == 409 and r.json()["code"] == "oauth_email_unverified"

    upstream.userinfo["email_verified"] = True
    _, q = await start(client, "azure_ad")
    r = await client.get("/api/v1/auth/oauth/azure_ad/callback", params={"code": "c", "state": q["state"][0]})
    assert r.status_code == 200
    me = (await client.get("/api/v1/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"})).json()
    assert me["user"]["id"] == existing.user_id


async def test_github_uses_verified_primary_email(client, upstream):
    upstream.userinfo = {"id": 4242, "login": "octo", "name": None, "email": None}
    upstream.emails = [
        {"email": "spam@example.com", "primary": False, "verified": True},
        {"email": "octo@example.com", "primary": True, "verified": True},
    ]
    _, q = await start(client, "github")
    r = await client.get("/api/v1/auth/oauth/github/callback", params={"code": "c", "state": q["state"][0]})
    assert r.status_code == 200, r.text
    me = (await client.get("/api/v1/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"})).json()
    assert me["user"]["email"] == "octo@example.com" and me["user"]["display_name"] == "octo"


async def test_github_unverified_email_never_links_to_existing_account(client, register, upstream):
    existing = await register()
    upstream.userinfo = {"id": 7, "login": "ghost", "email": existing.email}
    upstream.emails = [{"email": existing.email, "primary": True, "verified": False}]
    _, q = await start(client, "github")
    r = await client.get("/api/v1/auth/oauth/github/callback", params={"code": "c", "state": q["state"][0]})
    assert r.status_code == 409 and r.json()["code"] == "oauth_email_unverified"
