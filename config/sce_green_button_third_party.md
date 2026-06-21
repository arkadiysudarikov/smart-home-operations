# SCE Green Button Third-Party Vendor Setup

This is the no-paid-UtilityAPI path for automated SCE interval refreshes. The
Smart Home monitor can consume direct SCE Green Button Connect values, but SCE
must issue the third-party/OAuth credentials after vendor registration and
connectivity testing.

## Current Position

- Manual SCE Green Button CSV/XML downloads are the working no-cost fallback.
- UtilityAPI historical collection stays disabled unless explicitly approved.
- Direct SCE Green Button Connect is not a personal API key; it requires SCE
  third-party vendor registration, terms acceptance, and a connectivity test.

## SCE Registration Inputs

SCE's third-party registration flow needs these manual inputs:

- Third-party vendor first name and last name.
- A shared vendor email address that is not already an SCE.com User ID.
- A password entered directly into SCE.com.
- Organization legal name.
- Organization TIN.
- Acceptance of SCE third-party terms by an authorized person.
- Connectivity-test endpoint details.

Do not store the SCE password, TIN, or production client secret in this repo.
Use `config/sce_green_button_connect.json` locally and keep it uncommitted.

## Local Config Shape

Copy `config/sce_green_button_connect.example.json` to
`config/sce_green_button_connect.json` after SCE issues the values. The direct
SCE path uses:

```json
{
  "third_party_registration": {
    "vendor_user_email": "shared-sce-green-button-vendor@example.com",
    "organization_name": "Example Organization",
    "connectivity_test_status": "not_started"
  },
  "green_button_connect": {
    "client_id": "issued-by-sce",
    "client_secret": "issued-by-sce",
    "redirect_uri": "https://public.example/sce/green-button/callback",
    "authorization_url": "issued-by-sce",
    "token_url": "issued-by-sce",
    "scope": "FB=4_5_15;IntervalDuration=3600;BlockDuration=monthly;HistoryLength=13",
    "resource_url": "issued-or-discovered-after-oauth",
    "access_token": "current-oauth-access-token"
  }
}
```

Environment variables still override local config:

- `SCE_GBC_RESOURCE_URL`
- `SCE_GBC_ACCESS_TOKEN`

## Monitor Behavior

When direct SCE credentials are missing, `scripts/fetch_sce_green_button_connect.py`
writes `data/latest_sce_api.json` with status `registration_required` and a
`registrationPlan`. Once `green_button_connect.resource_url` and
`green_button_connect.access_token` are configured, `Refresh SCE` downloads the
SCE Green Button payload into `data/sce-downloads/` and the normal energy
refresh imports it.

## Connectivity-Test Notes

SCE's connectivity test likely needs a public HTTPS callback endpoint. For this
repo, keep that endpoint outside the local monitor until SCE provides the exact
OAuth and test requirements. The local monitor only needs the resulting
resource URL and access token to fetch data.

