# OIDC setup

Login is the only way in, and there is no local-account fallback: the Hub runs
`GenericOAuthenticator` with `allow_all = False` and `allow_existing_users =
False`, and it exits at startup if it has no issuer or no authorisation rule.
A misconfigured Hub CrashLoops; it never comes up serving an open login page.

## What the Hub needs

| Value | Where it comes from | Where it goes |
| :-- | :-- | :-- |
| Issuer URL | your IdP | `airflow-platform-oidc` Secret, key `issuer` |
| Client ID | app registration | same Secret, key `client-id` |
| Client secret | app registration | same Secret, key `client-secret` |
| Allowed groups | your IdP's group names | `jupyterhub.hub.extraEnv.OIDC_ALLOWED_GROUPS` |
| Operator groups | your IdP's group names | `jupyterhub.hub.extraEnv.OIDC_OPERATOR_GROUPS` |

The three authorization endpoints are **discovered**, not configured: at
startup the Hub fetches `<issuer>/.well-known/openid-configuration` and reads
`authorization_endpoint`, `token_endpoint`, `userinfo_endpoint` and
`end_session_endpoint` from it. Override any of them with
`OIDC_AUTHORIZE_URL`, `OIDC_TOKEN_URL`, `OIDC_USERDATA_URL`,
`OIDC_LOGOUT_URL` if your IdP does not publish a discovery document.

A note on the library: oauthenticator 17.4.0 has no `OIDCOAuthenticator`
class — generic OIDC is `GenericOAuthenticator` (`authenticator_class:
generic-oauth`), which is what this chart configures.

## Register the application

Create **one confidential web application** with the authorization-code flow.

- Redirect URI: `https://<your-domain>/hub/oauth_callback` — exactly that, no
  wildcards. The Hub builds this from `PLATFORM_DOMAIN`, which
  `scripts/deploy.sh --domain` sets, so the two cannot drift apart.
- Scopes: `openid`, `profile`, `email`, and whatever your IdP calls groups.
- Require MFA and apply your conditional-access policy.
- PKCE is enabled on the Hub side (`enable_pkce: true`); allow it.

Then create at least two groups:

- `airflow-platform-users` — may sign in and get a workspace.
- `airflow-platform-admins` — additionally gets the JupyterHub
  `platform-operator` role: the admin UI, and the ability to see and stop
  other people's workspaces. Deliberately **not** `admin:users`.

### Okta

Applications → Create App Integration → OIDC → Web Application.

- Issuer: `https://<org>.okta.com/oauth2/default` (or your custom
  authorization server).
- Add a `groups` claim to the **ID token**: Security → API → your
  authorization server → Claims → Add Claim, name `groups`, value type
  `Groups`, filter `Matches regex` `.*` (or a tighter filter — see the token
  size warning below).
- Assign the app to the groups above.

### Microsoft Entra ID

App registrations → New registration → Web.

- Issuer: `https://login.microsoftonline.com/<tenant-id>/v2.0`.
- Token configuration → Add groups claim → Security groups, and set the
  **ID token** to emit group **names** if you want readable values; otherwise
  `OIDC_ALLOWED_GROUPS` must contain group object IDs.
- Entra truncates the groups claim past ~200 groups and replaces it with a
  `_claim_names` overage pointer, which this authenticator does not follow.
  Emit only assigned groups, or use app roles instead.

### Keycloak

Clients → Create → OpenID Connect, access type confidential.

- Issuer: `https://<host>/realms/<realm>`.
- Add a **Group Membership** mapper named `groups`, with *Full group path*
  off (otherwise your group names arrive as `/airflow-platform-users`) and
  *Add to ID token* on.

## Which claim becomes the username

`username_claim` defaults to `sub`, and `sub` is the right default: it is the
only claim OIDC guarantees is unique within an issuer and never reassigned.
The username becomes the pod name, the PVC name and the identity that owns a
workspace's data, so reuse of a username means one person inheriting
another's files.

`sub` is also opaque, which makes the JupyterHub admin UI hard to read. The
chart compensates: `hub.extraConfig.04-workspace-identity` reads the email or
`preferred_username` claim out of `auth_state` and passes it to the workspace
as `WORKSPACE_AIRFLOW_USERNAME`, so the Airflow UI inside the pod shows a
human name even while JupyterHub keys everything on `sub`.

Set `username_claim: email` only if your IdP guarantees an address is never
reassigned after an employee leaves. Most do not.

## Offboarding

Removing someone from the IdP group does three things, on three different
timelines:

1. **New logins fail immediately.**
2. **An existing session loses access within `auth_refresh_age`** (900s by
   default). `refresh_pre_spawn` is on, so their next spawn is re-checked
   against the IdP regardless.
3. **A running workspace keeps running** until it is culled. `cull.maxAge`
   (12 hours in production) is the hard ceiling; `shutdown_on_logout` covers
   the case where they log out themselves.

To revoke immediately, stop the server and delete the user:

```bash
kubectl -n airflow-prod delete pod -l "hub.jupyter.org/username=<username>"
# then, from the Hub admin UI, or:
kubectl -n airflow-prod exec deploy/hub -- \
  jupyterhub token --help   # see `jupyterhub-singleuser` docs for user deletion
```

Their PVC is retained. Decide deliberately whether to snapshot and delete it
or hand it to their manager.

## Verifying it works

```bash
# The Hub logs its resolved OIDC configuration at startup.
kubectl -n airflow-prod logs deploy/hub | grep "OIDC configured"
# -> OIDC configured: issuer=https://... groups=['airflow-platform-users'] operators=[...]
```

Then check, in order:

1. A member of `airflow-platform-users` can log in and gets a workspace.
2. An authenticated account in the tenant that is **not** in that group is
   refused — this is the test people skip, and it is the one that matters.
3. A member of `airflow-platform-admins` sees the admin UI.
4. Logout returns to the IdP and the workspace stops.

## Troubleshooting

| Symptom | Cause |
| :-- | :-- |
| Hub `CrashLoopBackOff`, log says `OIDC discovery failed` | Issuer URL wrong, or the Hub has no egress to the IdP. Check `hub.networkPolicy` and any VPC firewall. |
| Hub exits with `No authorisation rule configured` | `OIDC_ALLOWED_GROUPS` is empty and `OIDC_ALLOW_ALL_AUTHENTICATED` is not `true`. This is the fail-closed guard working. |
| `redirect_uri_mismatch` at the IdP | `PLATFORM_DOMAIN` does not match the registered redirect URI. Re-run `scripts/deploy.sh --domain`. |
| Login succeeds, then "403 Forbidden" | The user authenticated but is in none of `allowed_groups`. Confirm the groups claim actually reaches the Hub: `kubectl logs deploy/hub` with `debug.enabled: true`. |
| Everyone lands in no group | The groups claim is absent from the ID token, or nested. Adjust the IdP mapper, or point `auth_state_groups_key` at the nested path. |
| Certificate stuck `Provisioning` | DNS does not yet resolve to the ingress IP. Unrelated to OIDC, but it looks like a login failure. |
