# Test it from the command line

Before the first call, open `/admin/` → **Updates** → **Download**. Startup never downloads weights. The [admin guide](guide-web-ui.md#updates-and-first-download) includes the curl alternative.

Calls Paradee with PowerShell only, no admin page. Port 29321. Replace `<...>` values. The commands were written from the code and not yet run against a live server.

## Get a token

Every call except `/health` needs a token while `SERVICE_ADMIN_PASSWORD` is set. Admin calls take the admin password itself; a token works only on `/v1/*`. Create one token and reuse it. Each `POST /admin/tokens` call makes a new one, so do not put it in a loop. Revoke extras on the admin page.

```powershell
$base = "http://127.0.0.1:29321"
$pw   = @{ "X-Internal-Token" = "<SERVICE_ADMIN_PASSWORD>" }
$tok  = (Invoke-RestMethod -Method Post -Uri "$base/admin/tokens" -Headers $pw -ContentType "application/json" -Body '{"name":"cli-test"}').token
$tok
$h    = @{ "X-Internal-Token" = $tok }
Invoke-RestMethod "$base/health"
```

`$pw` (the admin password) is for `/admin/*` only. `$h` (the token) is for `/v1/*` only; using the password there gives `Invalid token`. `$tok` prints once: save it to skip this step next time, then set `$h` from it. With no admin password set there is no auth: skip `-Headers $h`.

## Call it

```powershell
Invoke-RestMethod -Uri "$base/v1/voices" -Headers $h
```

Pick a name from the list:

```powershell
$req = @{ text = "Hello, I hope you are having a good day."; voice = "<voice from the list>"; speed = 1.0 } | ConvertTo-Json
Invoke-WebRequest -Method Post -Uri "$base/v1/speak" -Headers $h -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($req)) -OutFile "D:\User\Downloads\_temp\paradee.wav"
```

A token's *projects* list holds voice names here, and the token above has none, so it may use all of them.

## Problems

| Answer | Cause |
|---|---|
| `401` | Wrong value: admin routes need the admin password, `/v1/*` needs a token. |
| `403` | The token's *projects* list does not include what you asked for. |
| `429` | Token rate used up or too many calls waiting; wait `Retry-After` seconds. |
| `422` | A field is unknown or out of range; the answer says which. |
| connection refused | Not running, or another port; see the run-path guides. |
