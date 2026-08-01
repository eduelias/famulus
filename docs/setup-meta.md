# Setting up the WhatsApp side (Meta)

You need a (free) Meta developer account. Total time: ~20 minutes.

## 1. Create the app

1. Go to [developers.facebook.com](https://developers.facebook.com) →
   **Create App** → type **Business**.
2. Add the **WhatsApp** product to the app.

You get a free **test number** immediately. It can message up to 5 recipients
that you register — perfect for a personal assistant. (A production number
requires a phone number not currently on consumer WhatsApp, plus a display-name
review; you can migrate later.)

## 2. Collect the credentials for `.env`

| `.env` key | Where to find it |
|---|---|
| `WA_PHONE_ID` | WhatsApp → API Setup → "Phone number ID" |
| `WA_TOKEN` | see *Tokens* below |
| `WA_APP_SECRET` | App settings → Basic → "App secret" |
| `WA_VERIFY_TOKEN` | you invent it (any random string) |
| `ALLOWED_WA_NUMBERS` | your own number, E.164 without `+` (e.g. `31612345678`) |

### Tokens

The token shown on the API Setup page expires in ~24h — fine for a first test.
For a permanent one:

1. [business.facebook.com](https://business.facebook.com) → Business settings →
   Users → **System users** → create one (role: Admin).
2. Add your app as an asset (full control).
3. **Generate token** → select your app → expiration **never** → check
   `whatsapp_business_messaging` and `whatsapp_business_management`.
4. Put it in `WA_TOKEN`.

## 3. Register your phone as a test recipient

WhatsApp → API Setup → "To" dropdown → **Manage phone number list** → add your
number and enter the code Meta sends you.

## 4. Point the webhook at famulus

Expose famulus publicly over HTTPS first. Any of these works:

- **Cloudflare Tunnel**: `cloudflared tunnel --url http://localhost:8090`
  (the free `trycloudflare.com` URL changes on every restart — fine for
  testing; use a named tunnel for stability)
- **Tailscale Funnel**: `tailscale funnel 8090`
- Your own reverse proxy + domain

Then in the app dashboard: WhatsApp → **Configuration**:

- Callback URL: `https://<your-public-host>/webhook`
- Verify token: the value of your `WA_VERIFY_TOKEN`
- Click *Verify and save* — famulus answers the challenge automatically.
- Under webhook fields, **subscribe to `messages`**.

## 5. Say hi

Send any message from your registered phone to the test number. First reply
can take ~30s if the model is cold; after that it's a few seconds.

If nothing comes back, check `docker compose logs famulus` — the three usual
suspects are an unsubscribed `messages` field, a number missing from
`ALLOWED_WA_NUMBERS`, and a display name still under review (error 131037 in
the logs; outbound is blocked until Meta approves it, usually within a day).
